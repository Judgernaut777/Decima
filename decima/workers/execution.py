"""Isolated effect execution — the ONLY door through which a worker runs an effect.

`run_worker` takes a validated `WorkerRequest`, the effect's implementation SOURCE (bound
by `implementation_digest`), and a `WorkerProfile`, and runs the effect's entrypoint in a
fresh child process that inherits NONE of the parent's authority (invariant 7, handoff §5):

MANDATORY layers (a failure to engage kills the spawn — fail closed, verified in-child):
  - a dedicated tmp working directory (the scratch jail), verified as the cwd;
  - a SCRUBBED minimal environment — no inherited HOME, SSH_AUTH_SOCK, tokens, or any
    parent secret; the child aborts if any un-allowed key leaked in;
  - resource limits (RLIMIT_CPU / RLIMIT_AS / RLIMIT_NOFILE / RLIMIT_NPROC / RLIMIT_FSIZE),
    each SET and then READ BACK via getrlimit;
  - no inherited file descriptors beyond stdio and the three worker pipes;
  - prctl(PR_SET_NO_NEW_PRIVS) — a privilege ceiling, read back;
  - a new session, so the whole worker group is killable on timeout.

STRONGEST-AVAILABLE OS isolation, per the profile (this aarch64 box supports it, so it is
MANDATORY for PURE — a failure fails closed, never a silent downgrade):
  - a user + mount namespace with a chroot into the scratch jail ⇒ the worker cannot open
    ~/.ssh, /etc, or any host path — the filesystem outside its jail simply is not there;
  - a network namespace (for a network-denied profile) ⇒ no route out.

The implementation is BOUND BY DIGEST: `run_worker` recomputes the content digest of the
source it was handed and refuses (DigestMismatch, fail closed) if it does not equal the
request's `implementation_digest` — an ungranted/undigested implementation never runs.

`decima.workers` is NOT part of the trusted kernel (the architecture import-boundary guard
scans only `decima/kernel/`), so this module may hold the process/namespace primitives the
kernel must never touch. It imports the kernel only for the content-address digest.
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

from decima.kernel import hashing
from decima.workers.lease import LeaseGuard
from decima.workers.profiles import PURE, WorkerProfile
from decima.workers.protocol import (
    FAILED,
    SUCCEEDED,
    UNKNOWN,
    WorkerRequest,
    WorkerResponse,
)

DEFAULT_TIMEOUT = 10  # wall-clock seconds (int — never a float)

# The confinement budget. All ints (invariant 6: ints, not floats).
DEFAULT_LIMITS: dict[str, int] = {
    "cpu_seconds": 5,          # soft → SIGXCPU; hard = soft+1 → SIGKILL
    "address_space": 1 << 30,  # 1 GiB VA — a memory bomb hits MemoryError
    "open_files": 64,          # RLIMIT_NOFILE
    "nproc": 64,               # RLIMIT_NPROC (the worker itself does not fork)
    "fsize": 8 << 20,          # 8 MiB max file the worker may create
}

_SAFE_PATH = "/usr/bin:/bin"  # pinned; never the parent's ambient PATH

_DIGEST_KIND = "worker-impl"


class WorkerError(Exception):
    """A worker could not be dispatched or its result could not be trusted — fail closed."""


class IsolationError(WorkerError):
    """A mandatory confinement layer could not be engaged and verified; nothing ran (or
    the worker was killed). Fail closed, fail loud."""


class WorkerTimeout(WorkerError):
    """The worker exceeded its wall-clock budget and its whole session was SIGKILLed. Any
    effect it attempted is UNOBSERVED — the honest outcome is UNKNOWN."""


class DigestMismatch(WorkerError):
    """The implementation handed to the worker does not match the request's
    `implementation_digest`. The effect is undigested/ungranted — it never runs."""


def compute_digest(source: str) -> str:
    """The content-address digest that binds an implementation. A request's
    `implementation_digest` MUST equal `compute_digest(source)` for that source to run."""
    return hashing.blob_id(source.encode("utf-8"), kind=_DIGEST_KIND)


def _minimal_env(scratch: str) -> dict[str, str]:
    """The ONLY environment a worker sees — no inherited secrets. HOME/TMPDIR jail-local."""
    return {
        "PATH": _SAFE_PATH,
        "HOME": scratch,
        "TMPDIR": scratch,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _validate_int(name: str, val: Any) -> int:
    if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
        raise IsolationError(f"{name} must be a positive int (ints, not floats), got {val!r}")
    return val


def _merge_limits(limits: dict[str, int] | None) -> dict[str, int]:
    merged = dict(DEFAULT_LIMITS)
    if limits:
        if not isinstance(limits, dict):
            raise IsolationError("limits must be a dict")
        unknown = sorted(set(limits) - set(DEFAULT_LIMITS))
        if unknown:
            raise IsolationError(f"unknown limit keys {unknown}")
        merged.update(limits)
    for key, val in merged.items():
        _validate_int(f"limit {key!r}", val)
    return merged


# ---------------------------------------------------------------------------
# The in-child bootstrap. Runs as `python -I -c BOOTSTRAP cfg_fd manifest_fd result_fd`
# with the scrubbed env / jailed cwd already arranged by the parent; it VERIFIES those,
# applies the process-local + namespace layers, writes an HONEST manifest built from
# in-child read-backs, then runs the digest-bound implementation and writes the result.
# A mandatory failure → {"fatal": ...} on the manifest pipe and exit 97. Pure stdlib.
# ---------------------------------------------------------------------------
_BOOTSTRAP = r'''
import ctypes, fcntl, json, os, resource, sys

cfg_fd, manifest_fd, result_fd = (int(a) for a in sys.argv[1:4])

buf = b""
while True:
    chunk = os.read(cfg_fd, 65536)
    if not chunk:
        break
    buf += chunk
os.close(cfg_fd)
cfg = json.loads(buf)

def fatal(msg):
    try:
        os.write(manifest_fd, json.dumps({"fatal": msg}).encode())
        os.close(manifest_fd)
    except OSError:
        pass
    os._exit(97)

manifest = {"seam": "decima.workers", "effect": cfg["effect"], "profile": cfg["profile"]}

# -- new session (kill-the-whole-group on timeout) --------------------------
if os.getsid(0) != os.getpid():
    fatal("worker is not a session leader (start_new_session missing)")
manifest["new_session"] = True

# -- scrubbed minimal environment (verified, not assumed) --------------------
allowed_env = set(cfg["allowed_env"])
leaked = sorted(set(os.environ) - allowed_env)
if leaked:
    fatal("environment not scrubbed; leaked keys: %r" % (leaked,))
manifest["env_keys"] = sorted(os.environ)

# -- working-directory jail ---------------------------------------------------
scratch = os.path.realpath(cfg["scratch"])
if os.path.realpath(os.getcwd()) != scratch:
    fatal("cwd is not the scratch jail")
manifest["cwd_jail"] = scratch

# -- closed fds: only stdio + the three worker pipes may be open --------------
allowed_fds = {0, 1, 2, manifest_fd, result_fd}
fds = []
for name in os.listdir("/proc/self/fd"):
    fd = int(name)
    try:
        fcntl.fcntl(fd, fcntl.F_GETFD)   # the listdir dirfd is gone by now
    except OSError:
        continue
    fds.append(fd)
fds = sorted(fds)
if set(fds) - allowed_fds:
    fatal("unexpected inherited fds: %r" % (fds,))
manifest["open_fds"] = fds

# -- rlimits: set, then READ BACK — the manifest reports what getrlimit says --
want = cfg["limits"]
RES = {
    "cpu_seconds": resource.RLIMIT_CPU,
    "address_space": resource.RLIMIT_AS,
    "open_files": resource.RLIMIT_NOFILE,
    "nproc": resource.RLIMIT_NPROC,
    "fsize": resource.RLIMIT_FSIZE,
}
applied = {}
for key, res_id in RES.items():
    n = want[key]
    lim = (n, n + 1) if key == "cpu_seconds" else (n, n)
    try:
        resource.setrlimit(res_id, lim)
    except (ValueError, OSError) as e:
        fatal("setrlimit(%s) failed: %s" % (key, e))
    got = resource.getrlimit(res_id)
    if tuple(got) != lim:
        fatal("rlimit %s read-back mismatch: wanted %r got %r" % (key, lim, got))
    applied[key] = list(got)
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except (ValueError, OSError) as e:
    fatal("setrlimit(core=0) failed: %s" % e)
applied["core"] = list(resource.getrlimit(resource.RLIMIT_CORE))
manifest["rlimits"] = applied

# -- prctl(PR_SET_NO_NEW_PRIVS, 1) — verified via PR_GET_NO_NEW_PRIVS --------
libc = ctypes.CDLL(None, use_errno=True)
PR_SET_NO_NEW_PRIVS, PR_GET_NO_NEW_PRIVS = 38, 39
if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
    fatal("prctl(PR_SET_NO_NEW_PRIVS) failed: errno %d" % ctypes.get_errno())
if libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1:
    fatal("no_new_privs read-back != 1")
manifest["no_new_privs"] = True

# -- STRONGEST OS isolation: user+mount namespace chroot, + net namespace ----
# A single unshare() takes the combined flags (a user namespace can be unshared
# only once); uid/gid maps are written before any chroot (they live under /proc).
def apply_namespaces():
    CLONE_NEWNS   = 0x00020000
    CLONE_NEWUSER = 0x10000000
    CLONE_NEWNET  = 0x40000000
    want_fs  = bool(cfg["filesystem_jail"])
    want_net = not bool(cfg["network"])
    report = {"requested_fs_jail": want_fs, "requested_net_isolation": want_net,
              "engaged": False, "fs_jail": False, "net_isolated": False}
    if not (want_fs or want_net):
        report["detail"] = "profile requests no namespace isolation"
        report["engaged"] = True
        return report
    flags = CLONE_NEWUSER | (CLONE_NEWNS if want_fs else 0) | (CLONE_NEWNET if want_net else 0)
    euid, egid = os.geteuid(), os.getegid()
    ctypes.set_errno(0)
    if libc.unshare(flags) != 0:
        report["detail"] = "unshare failed (errno %d)" % ctypes.get_errno()
        return report
    report["user_ns"] = True
    try:
        with open("/proc/self/setgroups", "w") as f:
            f.write("deny")
    except OSError:
        pass
    try:
        with open("/proc/self/uid_map", "w") as f:
            f.write("0 %d 1" % euid)
        with open("/proc/self/gid_map", "w") as f:
            f.write("0 %d 1" % egid)
    except OSError as e:
        report["detail"] = "uid/gid map write failed: %s" % e
        return report
    report["net_isolated"] = want_net
    if want_fs:
        MS_REC, MS_PRIVATE = 0x4000, (1 << 18)
        if libc.mount(b"none", b"/", None, MS_REC | MS_PRIVATE, None) != 0:
            report["detail"] = "make-rprivate failed (errno %d)" % ctypes.get_errno()
            return report
        if libc.chroot(scratch.encode()) != 0:
            report["detail"] = "chroot failed (errno %d)" % ctypes.get_errno()
            return report
        os.chdir("/")
        report["fs_jail"] = True
    report["engaged"] = True
    report["detail"] = "namespace isolation engaged"
    return report

iso = apply_namespaces()
manifest["namespaces"] = iso
if cfg["namespaces_mandatory"] and not iso.get("engaged"):
    fatal("mandatory namespace isolation did not engage: %s" % iso.get("detail"))

# -- hand off the honest manifest BEFORE running the effect ------------------
os.write(manifest_fd, json.dumps(manifest).encode())
os.close(manifest_fd)

# -- run the DIGEST-BOUND implementation (untrusted DATA runs here, confined) --
result = {"status": "FAILED", "output": None, "diagnostics": {}}
try:
    glb = {"__name__": "__worker__"}
    exec(compile(cfg["implementation"], "<worker-impl>", "exec"), glb)
    fn = glb.get(cfg["entrypoint"])
    if not callable(fn):
        result["diagnostics"] = {"error": "entrypoint %r is not callable" % cfg["entrypoint"]}
    else:
        out = fn(**cfg["arguments"])
        try:
            json.dumps(out)
        except (TypeError, ValueError):
            out = repr(out)
        result = {"status": "SUCCEEDED", "output": out, "diagnostics": {}}
except BaseException as e:  # noqa: BLE001 — any failure is a FAILED effect, never a fake pass
    result = {"status": "FAILED", "output": None,
              "diagnostics": {"error": type(e).__name__ + ": " + str(e)}}

os.write(result_fd, json.dumps(result).encode())
os.close(result_fd)
os._exit(0)
'''


def _read_to_eof(fd: int, deadline: float, proc: subprocess.Popen[bytes]) -> bytes:
    chunks: list[bytes] = []
    while True:
        waitfor = deadline - time.monotonic()
        if waitfor <= 0 or not select.select([fd], [], [], waitfor)[0]:
            _kill_group(proc)
            raise WorkerTimeout("worker produced no output within its wall-clock budget")
        b = os.read(fd, 65536)
        if not b:
            return b"".join(chunks)
        chunks.append(b)


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the worker's whole session (it is its own session leader)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def _spawn(
    *,
    effect: str,
    implementation: str,
    entrypoint: str,
    arguments: dict[str, Any],
    profile: WorkerProfile,
    limits: dict[str, int],
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Spawn the isolated child, returning (manifest, result). Raises IsolationError /
    WorkerTimeout on a mandatory-layer failure or a blown budget."""
    scratch = tempfile.mkdtemp(prefix="decima-worker-")
    cfg = {
        "effect": effect,
        "profile": profile.name,
        "implementation": implementation,
        "entrypoint": entrypoint,
        "arguments": arguments,
        "limits": limits,
        "allowed_env": sorted(_minimal_env(scratch)),
        "network": profile.network,
        "filesystem_jail": profile.filesystem_jail,
        "namespaces_mandatory": profile.namespaces_mandatory,
        "scratch": scratch,
    }
    cfg_bytes = json.dumps(cfg).encode("utf-8")

    cfg_r, cfg_w = os.pipe()
    man_r, man_w = os.pipe()
    res_r, res_w = os.pipe()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _BOOTSTRAP, str(cfg_r), str(man_w), str(res_w)],
            cwd=scratch,
            env=_minimal_env(scratch),
            close_fds=True,
            pass_fds=(cfg_r, man_w, res_w),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Parent side of each pipe closes so EOF is observable.
        os.close(cfg_r)
        os.close(man_w)
        os.close(res_w)
        # Ship the config, then close so the child's read loop sees EOF.
        os.write(cfg_w, cfg_bytes)
        os.close(cfg_w)
        cfg_w = -1

        deadline = time.monotonic() + timeout
        manifest_raw = _read_to_eof(man_r, deadline, proc)
        if not manifest_raw:
            stderr = b""
            try:
                _, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
            raise IsolationError(
                f"isolation bootstrap died without a manifest (exit {proc.returncode}): "
                f"{stderr.decode('utf-8', 'replace').strip()[:400]}"
            )
        manifest = json.loads(manifest_raw)
        if "fatal" in manifest:
            raise IsolationError(f"isolation bootstrap refused: {manifest['fatal']}")

        result_raw = _read_to_eof(res_r, deadline, proc)
        try:
            proc.communicate(timeout=max(1, int(deadline - time.monotonic()) + 1))
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            raise WorkerTimeout(
                "worker exceeded its wall-clock budget after producing a manifest"
            ) from None
        if not result_raw:
            # The result pipe closed with no result. If the child was killed by a signal
            # (SIGXCPU from the CPU rlimit, SIGKILL from an OOM/nproc backstop), the effect
            # was cut off mid-flight and its outcome is UNOBSERVABLE → UNKNOWN, never a
            # fabricated FAILED (WEFT §8.3). A clean exit with no result is a real FAILED.
            rc = proc.returncode
            if rc is not None and rc < 0:
                raise WorkerTimeout(
                    f"worker killed by signal {-rc} mid-effect — outcome unobservable"
                )
            return manifest, {
                "status": "FAILED", "output": None,
                "diagnostics": {"error": "worker produced no result"},
            }
        return manifest, json.loads(result_raw)
    finally:
        for fd in (cfg_w,):
            if fd >= 0:
                os.close(fd)
        for fd in (man_r, res_r):
            with contextlib.suppress(OSError):
                os.close(fd)
        if proc is not None and proc.poll() is None:
            _kill_group(proc)
            proc.wait()
        shutil.rmtree(scratch, ignore_errors=True)


def run_worker(
    request: WorkerRequest,
    implementation: str,
    entrypoint: str,
    *,
    now: int,
    profile: WorkerProfile = PURE,
    lease_guard: LeaseGuard | None = None,
    limits: dict[str, int] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> WorkerResponse:
    """Run one bounded effect in an isolated worker and return a WorkerResponse.

    Fail-closed gates, in order (nothing runs until all pass):
      1. a `capability_proof` must be present — an effect with NO authority is refused
         (no ambient authority, invariant 3);
      2. the `lease` must validate at `now` and not be replayed — an expired or replayed
         lease fails closed (LeaseError propagates);
      3. `compute_digest(implementation)` must equal `request.implementation_digest` — an
         undigested/ungranted implementation fails closed (DigestMismatch).

    Only then is the digest-bound implementation dispatched into the confined child. A
    completed effect ⇒ SUCCEEDED with its output in `receipt_data`; a raising effect ⇒
    FAILED (definite no-fabricated-success); a worker killed by the wall-clock/CPU backstop
    ⇒ UNKNOWN (the outcome is unobservable — never invented). The honest in-child isolation
    manifest rides back in `diagnostics` as provenance for the Weft receipt.
    """
    if not isinstance(request, WorkerRequest):
        raise WorkerError("run_worker requires a WorkerRequest")
    if not request.capability_proof:
        raise WorkerError(
            "no capability_proof — a worker mints no authority; an unauthorized effect "
            "never runs (invariant 3)"
        )
    if not isinstance(implementation, str) or not implementation:
        raise WorkerError("implementation source must be a non-empty str")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise WorkerError("entrypoint must be a non-empty str")

    # 2. lease validation (expired / replayed / malformed → fail closed)
    guard = lease_guard if lease_guard is not None else LeaseGuard()
    guard.consume(request.lease, now=now, expected_step_id=request.lease.get("step_id"))

    # 3. digest binding — the implementation is bound; a mismatch never runs
    computed = compute_digest(implementation)
    if computed != request.implementation_digest:
        raise DigestMismatch(
            f"implementation digest mismatch for effect {request.effect!r}: "
            f"request declared {request.implementation_digest!r} but the source hashes to "
            f"{computed!r} — an undigested implementation fails closed"
        )

    merged = _merge_limits(limits)
    _validate_int("timeout", timeout)

    try:
        manifest, result = _spawn(
            effect=request.effect,
            implementation=implementation,
            entrypoint=entrypoint,
            arguments=dict(request.arguments),
            profile=profile,
            limits=merged,
            timeout=timeout,
        )
    except WorkerTimeout as exc:
        # Killed by the backstop: the outcome is unobservable — UNKNOWN, never a fake pass.
        return WorkerResponse(
            invocation_id=request.invocation_id,
            status=UNKNOWN,
            output_refs=[],
            receipt_data={},
            diagnostics={"timeout": True, "error": str(exc), "isolation": None},
        )

    status = result.get("status")
    mapped = SUCCEEDED if status == "SUCCEEDED" else FAILED
    receipt = {"output": result.get("output"), "effect": request.effect, "profile": profile.name}
    diagnostics = {
        "isolation": manifest,
        "worker_diagnostics": result.get("diagnostics", {}),
    }
    return WorkerResponse(
        invocation_id=request.invocation_id,
        status=mapped,
        output_refs=[],
        receipt_data=receipt,
        diagnostics=diagnostics,
    )
