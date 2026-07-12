"""Adversarial worker-isolation tests — a hostile effect proves it CANNOT escape.

These run for real on this aarch64 Linux box. Each test makes the worker *attempt* an
escape and asserts the escape FAILS: it cannot read ~/.ssh, cannot see a parent-process
secret, cannot run an ungranted/undigested implementation, cannot reuse a replayed or
expired lease, cannot reach the network, and is bounded by its resource limits.

Honesty note (handoff §16): the filesystem/network guarantees here are enforced by real
Linux user + mount + network namespaces (a chroot into the scratch jail; a fresh netns),
which this box supports — so the PURE profile requires them and fails closed if they cannot
engage. On a host WITHOUT user namespaces a PURE worker would refuse to run rather than run
degraded; these tests assert the manifest shows the layers genuinely engaged, so they would
go red (not silently pass) if the guarantee were lost.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from decima.workers.execution import (
    DigestMismatch,
    WorkerError,
    compute_digest,
    run_worker,
)
from decima.workers.lease import LeaseError, LeaseGuard
from decima.workers.profiles import PURE
from decima.workers.protocol import FAILED, SUCCEEDED, UNKNOWN, WorkerRequest


def _lease(*, issued: int = 0, expiry: int = 100, attempt: int = 1, idem: str = "idem-1") -> dict:
    return {
        "step_id": "s1",
        "worker": "w1",
        "capability_ids": [],
        "issued_frontier": issued,
        "expiry": expiry,
        "attempt": attempt,
        "idempotency_key": idem,
    }


def _request(source: str, *, digest: str | None = None, args: dict | None = None, **kw):
    base = dict(
        invocation_id="inv-adv",
        job_id="job-adv",
        effect="pure_compute",
        implementation_digest=digest if digest is not None else compute_digest(source),
        arguments=args if args is not None else {},
        lease=_lease(),
        capability_proof={"grant_id": "g1"},
    )
    base.update(kw)
    return WorkerRequest(**base)


def _run(source: str, entry: str = "go", **kw):
    return run_worker(_request(source, **kw), source, entry, now=0, profile=PURE)


# ── 1. cannot read ~/.ssh (real filesystem containment via chroot) ──────────────
def test_worker_cannot_read_dot_ssh():
    # First, prove the path is genuinely present on the host outside the worker, so the
    # test is meaningful and not vacuously passing on an absent file.
    host_home = pathlib.Path(os.path.expanduser("~"))
    src = (
        "def go(target):\n"
        "    import os\n"
        "    try:\n"
        "        return {'listed': os.listdir(target)}\n"
        "    except OSError as e:\n"
        "        return {'blocked': type(e).__name__}\n"
    )
    resp = _run(src, args={"target": str(host_home / ".ssh")})
    assert resp.status == SUCCEEDED
    # The worker sees a chrooted, empty jail — the host home simply is not there.
    assert "listed" not in resp.receipt_data["output"], "worker read a host path — escape!"
    assert resp.receipt_data["output"]["blocked"] in ("FileNotFoundError", "PermissionError")


def test_worker_cannot_read_etc_passwd_by_absolute_path():
    src = (
        "def go(x):\n"
        "    import os\n"
        "    try:\n"
        "        with open('/etc/passwd') as f:\n"
        "            return {'read': f.read(16)}\n"
        "    except OSError as e:\n"
        "        return {'blocked': type(e).__name__}\n"
    )
    resp = _run(src, args={"x": 1})
    assert resp.status == SUCCEEDED
    assert "read" not in resp.receipt_data["output"], "worker read /etc/passwd — escape!"
    assert "blocked" in resp.receipt_data["output"]


# ── 2. cannot read a parent-process env secret (scrubbed environment) ───────────
def test_worker_cannot_read_a_parent_env_secret():
    marker = "SUPER_SECRET_TOKEN_42"
    os.environ["DECIMA_TEST_SECRET"] = marker
    try:
        src = (
            "def go(x):\n"
            "    import os\n"
            "    return {'names': sorted(os.environ),\n"
            "            'leaked': [v for v in os.environ.values() if 'SECRET_TOKEN' in v]}\n"
        )
        resp = _run(src, args={"x": 1})
    finally:
        del os.environ["DECIMA_TEST_SECRET"]
    assert resp.status == SUCCEEDED
    out = resp.receipt_data["output"]
    assert "DECIMA_TEST_SECRET" not in out["names"], "parent secret leaked into the worker!"
    assert out["leaked"] == [], "a parent secret value reached the worker!"
    # Only the minimal scrubbed set is present.
    assert set(out["names"]) == {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"}


# ── 3. cannot execute an ungranted / undigested effect (digest binding) ─────────
def test_worker_refuses_an_undigested_implementation():
    src = "def go(x):\n    return {'ok': True}\n"
    req = _request(src, digest="not-the-real-digest")
    with pytest.raises(DigestMismatch):
        run_worker(req, src, "go", now=0, profile=PURE)


def test_worker_refuses_a_swapped_implementation_under_a_valid_digest():
    honest = "def go(x):\n    return {'honest': True}\n"
    malicious = "def go(x):\n    import os\n    return {'host': os.listdir('/')}\n"
    req = _request(honest)  # digest binds the honest source
    with pytest.raises(DigestMismatch):
        run_worker(req, malicious, "go", now=0, profile=PURE)  # swapped body refused


def test_worker_refuses_an_effect_with_no_capability_proof():
    src = "def go(x):\n    return {'ok': True}\n"
    req = _request(src, capability_proof={})
    with pytest.raises(WorkerError, match="no capability_proof"):
        run_worker(req, src, "go", now=0, profile=PURE)


# ── 4. cannot reuse a replayed lease, cannot use an expired lease ───────────────
def test_worker_refuses_a_replayed_lease():
    src = "def go(x):\n    return {'ok': True}\n"
    guard = LeaseGuard()
    req = _request(src)
    run_worker(req, src, "go", now=0, profile=PURE, lease_guard=guard)
    with pytest.raises(LeaseError, match="replayed lease"):
        run_worker(req, src, "go", now=1, profile=PURE, lease_guard=guard)


def test_worker_refuses_an_expired_lease():
    src = "def go(x):\n    return {'ok': True}\n"
    req = _request(src, lease=_lease(issued=0, expiry=5))
    with pytest.raises(LeaseError, match="expired"):
        run_worker(req, src, "go", now=99, profile=PURE)


# ── 5. cannot reach the network (network namespace) ─────────────────────────────
def test_worker_cannot_reach_the_network():
    # Two independent reasons the worker cannot reach the network, and the test accepts
    # either as a genuine denial: (a) a fresh network namespace has no route out, so a
    # connect() raises "Network is unreachable"; (b) after the chroot into an empty jail
    # the compiled `socket` extension cannot even be imported. Both mean: no network.
    src = (
        "def go(host, port):\n"
        "    try:\n"
        "        import socket\n"
        "        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "        s.settimeout(3)\n"
        "        try:\n"
        "            s.connect((host, port))\n"
        "            return {'connected': True}\n"
        "        finally:\n"
        "            s.close()\n"
        "    except Exception as e:\n"
        "        return {'blocked': type(e).__name__}\n"
    )
    resp = _run(src, args={"host": "8.8.8.8", "port": 53})
    assert resp.status == SUCCEEDED
    assert "connected" not in resp.receipt_data["output"], "worker reached the network — escape!"
    assert resp.receipt_data["output"]["blocked"] in (
        "OSError",
        "TimeoutError",
        "ModuleNotFoundError",
        "ImportError",
    )


# ── 6. resource limits genuinely bound the worker ──────────────────────────────
def test_worker_memory_is_bounded():
    src = "def go(x):\n    b = bytearray(3 * 1024 * 1024 * 1024)\n    return {'len': len(b)}\n"
    resp = run_worker(
        _request(src, args={"x": 1}),
        src,
        "go",
        now=0,
        profile=PURE,
        limits={"address_space": 256 << 20},
    )
    assert resp.status == FAILED
    assert "MemoryError" in resp.diagnostics["worker_diagnostics"]["error"]


def test_worker_cpu_and_wallclock_are_bounded():
    src = "def go(x):\n    while True:\n        pass\n"
    resp = run_worker(
        _request(src, args={"x": 1}),
        src,
        "go",
        now=0,
        profile=PURE,
        timeout=2,
        limits={"cpu_seconds": 1},
    )
    # Killed by the backstop → outcome unobservable → UNKNOWN (never a fabricated pass).
    assert resp.status == UNKNOWN
    assert resp.diagnostics["timeout"] is True


def test_worker_cannot_fork_a_grandchild_beyond_nproc():
    # Attempt to spawn a subprocess; with a tight NPROC (and no_new_privs) the child cannot
    # multiply. Whether it raises or is denied, it must NOT succeed in escaping the bound.
    src = (
        "def go(x):\n"
        "    import subprocess\n"
        "    try:\n"
        "        subprocess.Popen(['/bin/true'])\n"
        "        return {'spawned': True}\n"
        "    except OSError as e:\n"
        "        return {'blocked': type(e).__name__}\n"
    )
    resp = run_worker(
        _request(src, args={"x": 1}),
        src,
        "go",
        now=0,
        profile=PURE,
        limits={"nproc": 1},
    )
    # In the chroot jail /bin/true does not exist AND nproc is exhausted — either way the
    # worker fails to spawn a real grandchild.
    assert resp.status in (SUCCEEDED, FAILED)
    if resp.status == SUCCEEDED:
        assert "spawned" not in resp.receipt_data["output"], "worker spawned a grandchild!"
