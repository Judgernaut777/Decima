"""Isolated execution: digest binding, honest manifest, resource bounds, outcome mapping."""

from __future__ import annotations

import pytest

from decima.workers import execution
from decima.workers.execution import (
    DigestMismatch,
    WorkerError,
    compute_digest,
    run_worker,
)
from decima.workers.lease import LeaseError, LeaseGuard
from decima.workers.profiles import PURE
from decima.workers.protocol import FAILED, SUCCEEDED, UNKNOWN, WorkerRequest

_DOUBLE = "def go(x):\n    return {'doubled': x * 2}\n"


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


def _request(source: str = _DOUBLE, *, digest: str | None = None, **kw) -> WorkerRequest:
    base = dict(
        invocation_id="inv-1",
        job_id="job-1",
        effect="pure_compute",
        implementation_digest=digest if digest is not None else compute_digest(source),
        arguments={"x": 21},
        lease=_lease(),
        capability_proof={"grant_id": "g1"},
    )
    base.update(kw)
    return WorkerRequest(**base)


def test_a_digest_bound_effect_runs_and_succeeds():
    resp = run_worker(_request(), _DOUBLE, "go", now=0, profile=PURE)
    assert resp.status == SUCCEEDED
    assert resp.receipt_data["output"] == {"doubled": 42}


def test_manifest_is_honest_and_layers_engaged():
    resp = run_worker(_request(), _DOUBLE, "go", now=0, profile=PURE)
    manifest = resp.diagnostics["isolation"]
    assert manifest["new_session"] is True
    assert manifest["no_new_privs"] is True
    assert manifest["cwd_jail"]  # a real scratch path
    # env is scrubbed to exactly the minimal allowed set — no inherited secrets
    assert set(manifest["env_keys"]) == {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"}
    # rlimits were set AND read back
    assert manifest["rlimits"]["cpu_seconds"][0] == execution.DEFAULT_LIMITS["cpu_seconds"]
    # strongest OS isolation genuinely engaged on this box
    ns = manifest["namespaces"]
    assert ns["engaged"] and ns["fs_jail"] and ns["net_isolated"]


def test_undigested_implementation_fails_closed():
    req = _request(digest="0" * 32)  # a digest that does not match the source
    with pytest.raises(DigestMismatch):
        run_worker(req, _DOUBLE, "go", now=0, profile=PURE)


def test_swapped_implementation_fails_closed():
    # Request bound to _DOUBLE's digest, but a DIFFERENT (malicious) source is handed in.
    evil = "def go(x):\n    return {'pwned': True}\n"
    req = _request(source=_DOUBLE)  # digest binds _DOUBLE
    with pytest.raises(DigestMismatch):
        run_worker(req, evil, "go", now=0, profile=PURE)


def test_missing_capability_proof_fails_closed():
    req = _request(capability_proof={})
    with pytest.raises(WorkerError, match="no capability_proof"):
        run_worker(req, _DOUBLE, "go", now=0, profile=PURE)


def test_expired_lease_fails_closed_before_execution():
    req = _request(lease=_lease(expiry=10))
    with pytest.raises(LeaseError, match="expired"):
        run_worker(req, _DOUBLE, "go", now=20, profile=PURE)


def test_replayed_lease_fails_closed_with_shared_guard():
    guard = LeaseGuard()
    req = _request()
    run_worker(req, _DOUBLE, "go", now=0, profile=PURE, lease_guard=guard)
    with pytest.raises(LeaseError, match="replayed lease"):
        run_worker(req, _DOUBLE, "go", now=1, profile=PURE, lease_guard=guard)


def test_raising_effect_maps_to_failed_never_fabricated_success():
    src = "def go(x):\n    raise ValueError('boom')\n"
    req = _request(source=src)
    resp = run_worker(req, src, "go", now=0, profile=PURE)
    assert resp.status == FAILED
    assert "ValueError" in resp.diagnostics["worker_diagnostics"]["error"]


def test_memory_bomb_is_bounded_by_rlimit_as():
    # Allocate far more than the address-space limit → MemoryError inside the child → FAILED.
    src = "def go(x):\n    big = bytearray(3 * 1024 * 1024 * 1024)\n    return {'len': len(big)}\n"
    req = _request(source=src)
    resp = run_worker(req, src, "go", now=0, profile=PURE, limits={"address_space": 256 << 20})
    assert resp.status == FAILED
    assert "MemoryError" in resp.diagnostics["worker_diagnostics"]["error"]


def test_wall_clock_timeout_maps_to_unknown():
    # A CPU/wall backstop kill leaves the outcome unobservable → UNKNOWN (never a fake pass).
    src = "def go(x):\n    while True:\n        pass\n"
    req = _request(source=src)
    resp = run_worker(req, src, "go", now=0, profile=PURE, timeout=2, limits={"cpu_seconds": 1})
    assert resp.status == UNKNOWN
    assert resp.diagnostics["timeout"] is True
