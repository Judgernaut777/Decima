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

STRONGEST-AVAILABLE OS isolation, per the profile (any Linux host with unprivileged user
namespaces supports it — aarch64 and x86_64 alike — so it is MANDATORY for PURE: a failure
fails closed, never a silent downgrade):
  - a user + mount namespace with a chroot into the scratch jail ⇒ an ordinary path lookup
    cannot open ~/.ssh, /etc, or any host path — the filesystem outside the jail is not
    there. RESIDUAL: this bound holds against reaching THROUGH the jail, not against
    LEAVING it. chroot() does not move the cwd and the worker holds CAP_SYS_CHROOT over its
    own user namespace, so a second chroot plus a `..` walk re-roots it on the host
    filesystem (verified; see docs/design/syscall-filtering.md §4.3 and SECURITY.md). The
    fix is pivot_root, or dropping CAP_SYS_CHROOT once the jail is built — neither is done
    here yet, and the seccomp denylist below does not deny chroot;
  - for a WORKSPACE-class profile, exactly ONE caller-declared host subtree MS_BIND-mounted
    at /workspace inside that namespace before the chroot, nosuid+nodev+noexec (read-only
    when the mount says so), with the mounted inode re-verified against an O_PATH fd the
    parent pinned so the source cannot be swapped between the check and the mount. The
    bind is the worker's cwd, so its writes are real writes on that subtree — and on
    nothing else. A profile that requires the bind and is handed no subtree is REFUSED;
  - a network namespace (for a network-denied profile) ⇒ no route out;
  - a PID namespace (CLONE_NEWPID + a fork so the effect runs as PID 1 behind a thin
    reaper) ⇒ the worker cannot see or signal ANY host process — an out-of-jail PID is not
    in its namespace, so a kill() against it is ESRCH. Mandatory alongside the other
    namespaces (fail closed if the fork cannot enter the new namespace).

BEST-EFFORT syscall-surface reduction (aarch64-only defense-in-depth; degrades gracefully,
never fails the worker):
  - a seccomp-bpf DENYLIST of 32 syscall numbers over a DEFAULT-ALLOW program
    (PR_SET_SECCOMP + a raw BPF program built with ctypes, no libseccomp) that returns EPERM
    for escape / kernel-attack syscalls a pure-compute worker never needs (ptrace,
    setns/unshare, mount family, module load, bpf, perf_event_open, keyrings, reboot/kexec,
    cross-process memory, …). The filter's BPF arch guard and asm-generic syscall numbers
    are arm64, so on any non-aarch64 host it is SKIPPED (never installed); if the kernel
    refuses the filter (or the arch is unfiltered) the worker still runs and the manifest
    records seccomp ABSENT — either way this layer never destabilizes the mandatory floor.
    Read honestly, this layer is SMALL: what it denies, a worker confined by the floor above
    could not usefully call anyway, and what it ALLOWS includes openat/socket/execve/clone/
    chroot — so it does not stop a deliberate escape on either arch. A default-DENY
    allowlist would (measured footprint: 13 syscalls); that is scoped, not built, in
    docs/design/syscall-filtering.md.

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
import stat as stat_mod
import subprocess
import sys
import tempfile
import time
from typing import Any

from decima.kernel import hashing
from decima.workers.lease import LeaseGuard
from decima.workers.mount import WorkspaceMount
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
    "cpu_seconds": 5,  # soft → SIGXCPU; hard = soft+1 → SIGKILL
    "address_space": 1 << 30,  # 1 GiB VA — a memory bomb hits MemoryError
    "open_files": 64,  # RLIMIT_NOFILE
    "nproc": 64,  # RLIMIT_NPROC (the worker itself does not fork)
    "fsize": 8 << 20,  # 8 MiB max file the worker may create
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


# ---------------------------------------------------------------------------
# The containment matrix, as data. `containment_report` is PURE (no spawn, no
# side effects): it derives — from a profile + the merged limits — exactly the
# confinement layers `_spawn` enforces, each row tagged with the enforcing code,
# the fail behavior, and (for a live layer) the in-child manifest key + engaged
# value that PROVES it. Diagnostics and the containment-matrix tests read this so
# the doc (docs/architecture/worker-containment.md) and the code cannot drift:
# every ENFORCED row with a `manifest_proof` is asserted against a real worker
# manifest, and every honestly-NOT-enforced row is asserted absent.
# ---------------------------------------------------------------------------
CONTAINMENT_MATRIX_VERSION = 3

# The seccomp-bpf deny filter is aarch64-only: its BPF arch guard KILLs on any other
# AUDIT_ARCH, and the deny-list uses asm-generic (arm64) syscall numbers. This single
# predicate is the ONE source of truth for "does this host's CPU support the filter",
# shared by `containment_report` (so the report never claims the layer on a host that
# skips it) and the in-child `install_seccomp` (which carries the same literal check,
# since it runs in a separate `python -I -c` process and cannot import this module).
_SECCOMP_ARCH = "aarch64"


def _seccomp_arch_supported() -> bool:
    return os.uname().machine == _SECCOMP_ARCH


def containment_report(
    profile: WorkerProfile = PURE,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return the ENFORCED containment subset for `profile` as structured data.

    Deterministic and side-effect-free — it spawns nothing. Its ONLY host read is the CPU
    architecture (`os.uname().machine`), because the best-effort seccomp layer is
    aarch64-only; the report reflects that so it never claims a layer the worker skips on
    this host. Each row
    reports one confinement dimension: whether it is `enforced`, the `mechanism`, the
    `fail_mode` when the confined code hits it, the `degradation` when the layer is
    unavailable on the host, the enforcing `code` symbol, and — for a layer verified
    in-child — a `manifest_proof` `{key: engaged_value}` that a live worker manifest
    must satisfy. Rows the code does NOT enforce are listed with `enforced=False` and a
    `gap` note (honesty: never claim isolation the code does not apply). The top-level
    `warnings` list is loud, cross-cutting text (empty on aarch64): it flags a network-
    permitted profile on an arch without the seccomp filter — the worst case, where the
    best-effort syscall floor is absent WHILE network is permitted.
    """
    merged = _merge_limits(limits)
    fs_jail = bool(profile.filesystem_jail)
    net_isolated = not bool(profile.network)
    mandatory = bool(profile.namespaces_mandatory)
    ns_fail = "fail_closed_isolation_error" if mandatory else "degrade_reported_in_manifest"

    def _ns_row(
        dimension: str,
        mechanism: str,
        code: str,
        *,
        enforced: bool,
        proof_key: str,
        gap: str,
        degradation: str,
    ) -> dict[str, Any]:
        """A namespace-derived row: enforced ⇒ a boolean manifest proof and NO gap; not
        enforced (the profile does not request this layer) ⇒ a documented gap and NO proof.
        The two are mutually exclusive so the matrix can never claim a layer it omits."""
        row: dict[str, Any] = {
            "dimension": dimension,
            "mechanism": mechanism,
            "enforced": enforced,
            "code": code,
        }
        if enforced:
            row["fail_mode"] = ns_fail
            row["degradation"] = degradation
            row["manifest_proof"] = {proof_key: True}
        else:
            row["gap"] = gap
        return row

    def _seccomp_row() -> dict[str, Any]:
        """The best-effort seccomp layer. Unlike the mandatory namespace floors it is
        aarch64-only, so on any other arch it is an HONEST gap (enforced=False, no
        manifest_proof) — the worker genuinely skips it there, and the matrix must say so
        rather than overclaim a filter that never installs."""
        row: dict[str, Any] = {
            "dimension": "syscall_filter",
            "mechanism": (
                "seccomp-bpf deny filter (PR_SET_SECCOMP + raw ctypes BPF, no libseccomp); "
                "EPERM for escape/kernel-attack syscalls a pure-compute worker never needs"
            ),
            "posture": "best_effort",
            "code": "decima/workers/execution.py:_BOOTSTRAP install_seccomp (PR_SET_SECCOMP)",
        }
        if _seccomp_arch_supported():
            row["enforced"] = True
            row["fail_mode"] = (
                "a denied syscall (ptrace/setns/unshare/mount family/module load/bpf/"
                "perf_event_open/keyrings/reboot/kexec/process_vm_*/…) returns EPERM to the caller"
            )
            row["degradation"] = (
                "BEST-EFFORT: if the kernel refuses the filter the worker STILL runs and the "
                "manifest records seccomp absent — this layer never fails the worker closed, "
                "unlike the mandatory namespace floor"
            )
            row["manifest_proof"] = {"seccomp.engaged": True}
        else:
            row["enforced"] = False
            row["gap"] = (
                f"the seccomp-bpf deny filter is aarch64-only (its BPF arch guard + "
                f"asm-generic syscall numbers); on {os.uname().machine} it is SKIPPED and the "
                "manifest records seccomp absent. The MANDATORY namespace / rlimit / "
                "no-new-privs / non-dumpable floors still engage — only this best-effort "
                "defense-in-depth layer is unavailable."
            )
        return row

    def _workspace_row(binds: bool) -> dict[str, Any]:
        """The workspace bind-mount row. ENFORCED exactly when the profile REQUIRES a
        declared subtree (WORKSPACE); an honest gap for every profile that does not.

        This row is the one place the matrix would be easiest to lie in: flipping it to
        True is a one-character edit, and nothing about a PURE run would look different.
        So it is derived from `profile.workspace_bind` — the same field `run_worker` gates
        the dispatch on and `_spawn` acts on — rather than written as a literal, and its
        `manifest_proof` is checked against a real bound worker in the matrix tests."""
        row: dict[str, Any] = {
            "dimension": "workspace_bind_mount",
            "mechanism": (
                "one caller-declared host subtree MS_BIND-mounted at /workspace inside the "
                "mount namespace before the chroot (nosuid+nodev+noexec always, MS_RDONLY when "
                "the mount is read-only), the mounted inode re-verified against the O_PATH fd "
                "the parent pinned, and the jail cwd set to it"
            ),
            "enforced": binds,
            "code": "decima/workers/execution.py:_BOOTSTRAP bind_workspace (MS_BIND)",
        }
        if binds:
            row["fail_mode"] = "fail_closed_isolation_error"
            row["degradation"] = (
                "none, by construction: a WORKSPACE dispatch with no declared subtree, a bind "
                "that will not engage, an inode that does not match the pinned fd, or a "
                "read-back that contradicts the requested posture all REFUSE the spawn. There "
                "is no path on which this profile runs as PURE"
            )
            row["manifest_proof"] = {"workspace_bind.engaged": True}
        else:
            row["gap"] = (
                f"profile {profile.name!r} declares no workspace subtree, so nothing of the "
                "host filesystem is mapped into the jail at all — the chroot is the empty "
                "scratch dir. This is a STRONGER posture than a bind, not a weaker one; the "
                "row is a gap only in the sense that this layer is not the one doing the work."
            )
        return row

    rows: list[dict[str, Any]] = [
        {
            "dimension": "environment_scrub",
            "mechanism": "minimal allow-listed env; child aborts if any un-allowed key leaked",
            "enforced": True,
            "detail": sorted(_minimal_env("<scratch>")),
            "fail_mode": "fail_closed_isolation_error",
            "degradation": "none — process-local, always available",
            "code": "decima/workers/execution.py:_minimal_env / _BOOTSTRAP env check",
            "manifest_proof": {"env_keys": sorted(_minimal_env("<scratch>"))},
        },
        {
            "dimension": "working_directory_jail",
            "mechanism": "cwd is a fresh per-run tmp scratch dir, verified as realpath(getcwd)",
            "enforced": True,
            "fail_mode": "fail_closed_isolation_error",
            "degradation": "none — process-local, always available",
            "code": "decima/workers/execution.py:_spawn (tempfile.mkdtemp) / _BOOTSTRAP cwd check",
            "manifest_proof": {"cwd_jail": "present"},
        },
        {
            "dimension": "fd_closure",
            "mechanism": "close_fds + pass_fds; child asserts only stdio + 2 worker pipes open",
            "enforced": True,
            "fail_mode": "fail_closed_isolation_error",
            "degradation": "none — process-local, always available",
            "code": "decima/workers/execution.py:_spawn(close_fds) / _BOOTSTRAP fd check",
            "manifest_proof": {"open_fds": "present"},
        },
        {
            "dimension": "session_isolation",
            "mechanism": "start_new_session; the whole worker session is SIGKILLed on timeout",
            "enforced": True,
            "fail_mode": "fail_closed_isolation_error",
            "degradation": "none — process-local, always available",
            "code": "decima/workers/execution.py:_spawn(start_new_session) / _kill_group",
            "manifest_proof": {"new_session": True},
        },
        {
            "dimension": "no_new_privs",
            "mechanism": "prctl(PR_SET_NO_NEW_PRIVS,1); no setuid/fscaps can raise privilege",
            "enforced": True,
            "fail_mode": "fail_closed_isolation_error",
            "degradation": "none — process-local, always available",
            "code": "decima/workers/execution.py:_BOOTSTRAP (PR_SET_NO_NEW_PRIVS)",
            "manifest_proof": {"no_new_privs": True},
        },
        {
            "dimension": "non_dumpable",
            "mechanism": "prctl(PR_SET_DUMPABLE,0); no ptrace-attach by a peer, no core dump",
            "enforced": True,
            "fail_mode": "fail_closed_isolation_error",
            "degradation": "none — process-local, always available",
            "code": "decima/workers/execution.py:_BOOTSTRAP (PR_SET_DUMPABLE)",
            "manifest_proof": {"non_dumpable": True},
        },
        {
            "dimension": "resource_limits",
            "mechanism": "RLIMIT_CPU/AS/NOFILE/NPROC/FSIZE set then getrlimit read-back; CORE=0",
            "enforced": True,
            "detail": dict(merged),
            "fail_mode": (
                "CPU→SIGXCPU then SIGKILL (UNKNOWN); AS→MemoryError (FAILED); "
                "FSIZE→SIGXFSZ/OSError; NOFILE/NPROC→errno at the syscall"
            ),
            "degradation": "none — POSIX rlimits, always available",
            "code": "decima/workers/execution.py:DEFAULT_LIMITS / _BOOTSTRAP setrlimit",
            "manifest_proof": {"rlimits": "present"},
        },
        _ns_row(
            "filesystem_isolation",
            "user+mount namespace, make-rprivate, chroot into the scratch jail",
            "decima/workers/execution.py:_BOOTSTRAP apply_namespaces (chroot)",
            enforced=fs_jail,
            proof_key="namespaces.fs_jail",
            degradation=(
                "if user/mount namespaces are unavailable: fail closed (mandatory) — never a "
                "silent downgrade to the host filesystem"
            ),
            gap="this profile does not request a filesystem jail (filesystem_jail=False)",
        ),
        _ns_row(
            "user_namespace",
            "CLONE_NEWUSER with setgroups=deny and a single-entry uid/gid map",
            "decima/workers/execution.py:_BOOTSTRAP apply_namespaces (unshare)",
            enforced=fs_jail or net_isolated,
            proof_key="namespaces.user_ns",
            degradation="if unprivileged userns is unavailable: fail closed (mandatory)",
            gap="this profile requests neither a filesystem jail nor network isolation",
        ),
        _ns_row(
            "mount_namespace",
            "CLONE_NEWNS so the chroot + rprivate remount cannot affect the host",
            "decima/workers/execution.py:_BOOTSTRAP apply_namespaces (CLONE_NEWNS)",
            enforced=fs_jail,
            proof_key="namespaces.fs_jail",
            degradation="if mount namespaces are unavailable: fail closed (mandatory)",
            gap="this profile does not request a filesystem jail (no mount namespace)",
        ),
        _ns_row(
            "network_isolation",
            "CLONE_NEWNET ⇒ no interfaces, no route out (network-denied profile)",
            "decima/workers/execution.py:_BOOTSTRAP apply_namespaces (CLONE_NEWNET)",
            enforced=net_isolated,
            proof_key="namespaces.net_isolated",
            degradation="if network namespaces are unavailable: fail closed (mandatory)",
            gap=(
                "this profile PERMITS network (e.g. PROVIDER): there is no network namespace and "
                "NO egress mediation in this phase — do not route real provider traffic through it"
            ),
        ),
        _ns_row(
            "pid_namespace",
            "CLONE_NEWPID + a fork so the effect runs as PID 1 behind a thin reaper",
            "decima/workers/execution.py:_BOOTSTRAP apply_namespaces (CLONE_NEWPID) + fork",
            enforced=fs_jail or net_isolated,
            proof_key="pid_namespace.engaged",
            degradation=(
                "mandatory alongside the other namespaces: if the PID namespace cannot be "
                "unshared or the reaper fork fails, the spawn fails closed — never a host-PID-"
                "visible downgrade"
            ),
            gap="this profile requests no namespace isolation (no PID namespace)",
        ),
        _seccomp_row(),
        {
            "dimension": "wallclock_timeout",
            "mechanism": "parent select() deadline; a worker over budget has its session SIGKILLed",
            "enforced": True,
            "fail_mode": "killed mid-effect ⇒ UNKNOWN status (outcome unobservable, never faked)",
            "degradation": "none — enforced by the parent, independent of host namespaces",
            "code": "decima/workers/execution.py:_read_to_eof / run_worker (WorkerTimeout→UNKNOWN)",
            "manifest_proof": None,
        },
        # ── honestly NOT enforced (documented gaps; never claimed as isolation) ──
        {
            "dimension": "cgroup_resource_control",
            "mechanism": "cgroup v2 cpu/memory/pids controllers",
            "enforced": False,
            "gap": (
                "resource bounds are POSIX rlimits only, applied per-process. There is no cgroup "
                "accounting, so aggregate limits across any descendant set are NOT enforced."
            ),
            "code": "decima/workers/execution.py:DEFAULT_LIMITS (rlimits, not cgroups)",
        },
        {
            "dimension": "egress_mediation",
            "mechanism": "a redaction/mediation seam on a network-permitted (PROVIDER) worker",
            "enforced": False,
            "gap": (
                "the PROVIDER profile permits network but this phase wires NO egress mediation. "
                "Do not route real provider traffic through a network-permitted worker until the "
                "mediation seam lands. (Not applicable to network-denied profiles.)"
            ),
            "code": "decima/workers/profiles.py:PROVIDER (structure, not wired)",
        },
        _workspace_row(bool(profile.workspace_bind)),
    ]

    # A network-permitted profile on an arch without the seccomp filter has NEITHER the
    # best-effort syscall floor NOR (this phase) an egress-mediation seam. Surface that
    # worst-case combination as a LOUD, structured top-level warning so a caller reading
    # the per-row gaps in isolation cannot miss it. Empty on aarch64, where the filter engages.
    warnings: list[str] = []
    if bool(profile.network) and not _seccomp_arch_supported():
        warnings.append(
            f"network-permitted profile {profile.name!r} on {os.uname().machine}: the "
            "seccomp syscall filter is aarch64-only and UNAVAILABLE on this host, and this "
            "phase wires no egress mediation — the best-effort defense-in-depth syscall "
            "floor is absent WHILE network is permitted. Do not route real provider traffic "
            "through this worker on this host."
        )

    return {
        "version": CONTAINMENT_MATRIX_VERSION,
        "profile": profile.name,
        "network_permitted": bool(profile.network),
        "namespaces_mandatory": mandatory,
        "warnings": warnings,
        "platform": {
            "requires": "Linux unprivileged user + mount + network namespaces",
            "verified_arch": "aarch64",
            "host_arch": os.uname().machine,
            "seccomp_supported": _seccomp_arch_supported(),
            "on_host_without_userns": (
                "PURE/WORKSPACE fail closed (mandatory); nothing runs degraded"
                if mandatory
                else "the missing layer is reported un-engaged in the manifest"
            ),
        },
        "dimensions": rows,
    }


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
_BOOTSTRAP = r"""
import ctypes, fcntl, json, os, resource, stat, sys

cfg_fd, manifest_fd, result_fd = (int(a) for a in sys.argv[1:4])
# -1 unless the parent pinned a workspace subtree open for us (see bind_workspace).
ws_fd = int(sys.argv[4]) if len(sys.argv) > 4 else -1

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
if ws_fd >= 0:
    allowed_fds.add(ws_fd)
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
# Defined here but APPLIED in the PID-namespace child (after the reaper fork below):
# RLIMIT_NPROC would otherwise deny the reaper fork itself on a busy host. The tight
# per-process budget must bind the effect-runner, which is the child.
def apply_rlimits():
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
    return applied

# -- prctl(PR_SET_NO_NEW_PRIVS, 1) — verified via PR_GET_NO_NEW_PRIVS --------
libc = ctypes.CDLL(None, use_errno=True)
PR_SET_NO_NEW_PRIVS, PR_GET_NO_NEW_PRIVS = 38, 39
if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
    fatal("prctl(PR_SET_NO_NEW_PRIVS) failed: errno %d" % ctypes.get_errno())
if libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1:
    fatal("no_new_privs read-back != 1")
manifest["no_new_privs"] = True

# -- the WORKSPACE bind: one declared host subtree, mapped in, and PROVEN ----
# Runs inside the mount namespace (so the host mount table is untouched), after the tree
# is made rprivate, before the chroot. Four things have to be true or nothing runs:
#
#   1. THE TARGET IS OURS. It must be a real directory, not a symlink, and must sit inside
#      the scratch dir the parent minted for this run and nothing else.
#   2. THE SOURCE IS THE ONE THE PARENT VERIFIED. The parent resolved the subtree, checked
#      it against the operator's containment root, opened an O_PATH fd on it and KEPT IT
#      OPEN for our whole lifetime — which pins the inode so its number cannot be recycled.
#      This kernel refuses /proc/self/fd/N as a mount source (EINVAL), so we cannot mount
#      the fd directly; instead we mount by path and then re-verify, comparing
#      stat(target) against fstat(ws_fd). The comparison is against the FD, not against
#      anything the config claims, so a swapped path between the parent's check and our
#      mount lands a DIFFERENT inode and is caught here. On a mismatch the mount is
#      detached again and the spawn fails closed — the swapped tree is never exposed.
#   3. THE POSTURE IS READ BACK. nosuid+nodev+noexec are applied to every bind regardless
#      of tier, MS_RDONLY additionally when the mount is read-only, and statvfs is then
#      consulted. A remount that silently did not take is a refusal, not a warning.
#   4. NOTHING IS OPTIONAL. Every failure path returns engaged=False, and the caller turns
#      that into a refused spawn. There is no branch on which a WORKSPACE worker runs with
#      its workspace missing.
def bind_workspace():
    want = cfg.get("workspace")
    if want is None:
        return {"requested": False, "engaged": False, "detail": "no workspace subtree declared"}

    report = {"requested": True, "engaged": False, "read_only": bool(want["read_only"]),
              "jail_path": "/" + want["target"]}
    if ws_fd < 0:
        report["detail"] = "workspace declared but the parent passed no pinned fd"
        return report

    # (2a) the pinned identity, taken from the FD — never from the config.
    try:
        pinned = os.fstat(ws_fd)
    except OSError as e:
        report["detail"] = "cannot fstat the pinned workspace fd: %s" % e
        return report
    if not stat.S_ISDIR(pinned.st_mode):
        report["detail"] = "the pinned workspace fd is not a directory"
        return report

    # (1) the target must be a real directory we own, inside this run's scratch dir.
    target = os.path.join(scratch, want["target"])
    if os.path.islink(target) or not os.path.isdir(target):
        report["detail"] = "workspace target is not a plain directory: %r" % target
        return report
    if os.path.realpath(target) != target or os.path.dirname(target) != scratch:
        report["detail"] = "workspace target is not inside this run's scratch dir"
        return report

    MS_BIND, MS_REC, MS_REMOUNT = 0x1000, 0x4000, 32
    MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
    MNT_DETACH = 2
    ctypes.set_errno(0)
    if libc.mount(want["source"].encode(), target.encode(), None, MS_BIND | MS_REC, None) != 0:
        report["detail"] = "MS_BIND failed (errno %d)" % ctypes.get_errno()
        return report

    # (2b) the swap detector: what we actually mounted must BE the pinned inode.
    try:
        got = os.stat(target)
    except OSError as e:
        libc.umount2(target.encode(), MNT_DETACH)
        report["detail"] = "cannot stat the bound workspace: %s" % e
        return report
    if (got.st_dev, got.st_ino) != (pinned.st_dev, pinned.st_ino):
        libc.umount2(target.encode(), MNT_DETACH)
        report["detail"] = (
            "bound inode %d:%d does not match the fd the parent pinned (%d:%d) — the source "
            "path was swapped between the parent's check and this mount"
            % (got.st_dev, got.st_ino, pinned.st_dev, pinned.st_ino))
        return report
    report["inode_verified"] = True

    # (3) harden the bind, then READ THE POSTURE BACK.
    hard = MS_REMOUNT | MS_BIND | MS_NOSUID | MS_NODEV | MS_NOEXEC
    if want["read_only"]:
        hard |= MS_RDONLY
    ctypes.set_errno(0)
    if libc.mount(b"none", target.encode(), None, hard, None) != 0:
        libc.umount2(target.encode(), MNT_DETACH)
        report["detail"] = "hardening remount failed (errno %d)" % ctypes.get_errno()
        return report
    vfs = os.statvfs(target)
    posture = {"nosuid": bool(vfs.f_flag & os.ST_NOSUID), "nodev": bool(vfs.f_flag & os.ST_NODEV),
               "noexec": bool(vfs.f_flag & os.ST_NOEXEC),
               "rdonly": bool(vfs.f_flag & os.ST_RDONLY)}
    report["posture"] = posture
    wanted = {"nosuid": True, "nodev": True, "noexec": True, "rdonly": bool(want["read_only"])}
    if posture != wanted:
        libc.umount2(target.encode(), MNT_DETACH)
        report["detail"] = "mount posture read-back %r != requested %r" % (posture, wanted)
        return report

    report["engaged"] = True
    report["detail"] = "workspace subtree bound and verified"
    return report


# -- STRONGEST OS isolation: user+mount namespace chroot, + net namespace ----
# A single unshare() takes the combined flags (a user namespace can be unshared
# only once); uid/gid maps are written before any chroot (they live under /proc).
def apply_namespaces():
    CLONE_NEWNS   = 0x00020000
    CLONE_NEWUSER = 0x10000000
    CLONE_NEWPID  = 0x20000000
    CLONE_NEWNET  = 0x40000000
    want_fs  = bool(cfg["filesystem_jail"])
    want_net = not bool(cfg["network"])
    # A PID namespace rides along whenever we already unshare a user namespace: it costs
    # nothing extra to request and gives the worker its own PID 1 (entered by the fork below).
    want_pid = want_fs or want_net
    report = {"requested_fs_jail": want_fs, "requested_net_isolation": want_net,
              "requested_pid_ns": want_pid, "engaged": False, "fs_jail": False,
              "net_isolated": False, "pid_ns_unshared": False}
    if not (want_fs or want_net):
        report["detail"] = "profile requests no namespace isolation"
        report["engaged"] = True
        return report
    flags = (CLONE_NEWUSER | (CLONE_NEWNS if want_fs else 0)
             | (CLONE_NEWNET if want_net else 0) | (CLONE_NEWPID if want_pid else 0))
    euid, egid = os.geteuid(), os.getegid()
    ctypes.set_errno(0)
    if libc.unshare(flags) != 0:
        report["detail"] = "unshare failed (errno %d)" % ctypes.get_errno()
        return report
    report["user_ns"] = True
    report["pid_ns_unshared"] = want_pid
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
        # The workspace bind happens HERE — inside the mount namespace, after the tree is
        # rprivate (so nothing we do propagates back to the host mount table) and BEFORE the
        # chroot (so the host-side source path is still resolvable). Order is load-bearing.
        ws = bind_workspace()
        report["workspace"] = ws
        if cfg.get("workspace") is not None and not ws.get("engaged"):
            report["detail"] = "workspace bind refused: %s" % ws.get("detail")
            return report
        # -- PIVOT_ROOT, NOT CHROOT (wave S0) -------------------------------------------
        # `chroot` was an escapable jail and this is the whole reason S0 exists. chroot()
        # does not move the caller's cwd, and a task that is root in its own user namespace
        # holds CAP_SYS_CHROOT unconditionally — so `mkdir e; chroot e; chdir ../../..;
        # chroot .` re-rooted on the HOST filesystem. Verified from a PURE worker before this
        # change: read /etc/passwd, listed host /, wrote a host file, effect SUCCEEDED.
        #
        # The fix is not to forbid the second chroot; it is to make it POINT NOWHERE.
        # pivot_root moves the process's root to the scratch mount and puts the old root at a
        # known path, which we then detach: after `umount2(MNT_DETACH)` the host tree is no
        # longer reachable from this mount namespace AT ALL. A hostile effect may still call
        # chroot as often as it likes — every path it can name resolves inside the jail,
        # because the host mounts are gone rather than merely stepped over. That is a
        # structural containment property rather than a capability check, which is what makes
        # it worth the extra syscalls: it survives a regained capability and it does not
        # depend on the seccomp filter (absent on x86_64) denying anything.
        #
        # Three preconditions, all already satisfied here, which is why the call sits exactly
        # at this point: we are in our own mount namespace (CLONE_NEWNS above), the tree is
        # MS_REC|MS_PRIVATE (so nothing propagates to the host mount table), and the workspace
        # bind has already happened while the host-side source path was still resolvable.
        # pivot_root additionally requires new_root to BE a mount point and to differ from the
        # current root's mount, hence the bind of scratch onto itself first.
        MS_BIND = 0x1000
        MNT_DETACH = 2
        # No glibc wrapper for pivot_root on every libc we support, so it goes through
        # syscall(2). The number is per-arch: an ARCH TABLE, never a bare constant — a wrong
        # number is a silently different syscall.
        _PIVOT_ROOT_NR = {"x86_64": 155, "aarch64": 41, "armv7l": 218, "s390x": 217}
        pivot_nr = _PIVOT_ROOT_NR.get(os.uname().machine)
        if pivot_nr is None:
            report["detail"] = (
                "no pivot_root syscall number known for arch %r, and chroot alone is an "
                "escapable jail (S0) — refusing rather than running with weaker containment "
                "than the profile promises" % os.uname().machine
            )
            return report
        try:
            scratch_ino = os.stat(scratch).st_ino
        except OSError as e:
            report["detail"] = "scratch stat failed before pivot: %s" % e
            return report
        if libc.mount(scratch.encode(), scratch.encode(), None, MS_BIND | MS_REC, None) != 0:
            report["detail"] = "bind of scratch onto itself failed (errno %d)" % (
                ctypes.get_errno()
            )
            return report
        old_root = os.path.join(scratch, ".decima-oldroot")
        try:
            os.mkdir(old_root, 0o700)
        except FileExistsError:
            pass
        except OSError as e:
            report["detail"] = "oldroot mkdir failed: %s" % e
            return report
        if libc.syscall(pivot_nr, scratch.encode(), old_root.encode()) != 0:
            report["detail"] = "pivot_root failed (errno %d)" % ctypes.get_errno()
            return report
        os.chdir("/")
        # DETACH, then verify. An undetached old root would leave the whole host tree one
        # `chdir` away, so this is not cleanup — it is the containment step, and a failure
        # here fails the worker closed rather than logging a warning.
        if libc.umount2(b"/.decima-oldroot", MNT_DETACH) != 0:
            report["detail"] = "old-root detach failed (errno %d)" % ctypes.get_errno()
            return report
        try:
            os.rmdir("/.decima-oldroot")
        except OSError:
            # Cosmetic only: the mount is already detached, so an empty directory is all
            # that can remain. Never fatal — it would fail a contained worker for tidiness.
            pass
        # Read the property back rather than trusting two return codes: `/` must now BE the
        # scratch directory, compared against the inode captured before the pivot. Note what
        # this is not — comparing `stat("/")` with `stat(".")` would be vacuous, because the
        # chdir above makes them the same by construction whether or not the pivot took.
        try:
            if os.stat("/").st_ino != scratch_ino:
                report["detail"] = "post-pivot root is not the scratch jail (read-back)"
                return report
        except OSError as e:
            report["detail"] = "post-pivot root read-back failed: %s" % e
            return report
        report["pivoted"] = True
        # A bound worker's cwd IS its workspace: that is what makes the bind load-bearing
        # rather than decorative — ordinary relative-path writes land on the host subtree.
        os.chdir(ws["jail_path"] if ws.get("engaged") else "/")
        report["fs_jail"] = True
    report["engaged"] = True
    report["detail"] = "namespace isolation engaged"
    return report

# -- BEST-EFFORT seccomp-bpf deny filter (raw ctypes BPF, no libseccomp) ------
# Returns EPERM for escape / kernel-attack syscalls a pure-compute worker never
# needs. Requires no_new_privs (already engaged). If the kernel refuses the filter
# the worker STILL runs and the manifest records seccomp absent — this layer never
# fails the worker closed. The syscall numbers are arm64 (asm-generic) and are all
# ones normal Python execution never invokes, so the filter cannot break the effect.
# The BPF program and DENY table are aarch64-specific (the arch guard KILLs on any
# other value, and asm-generic numbers differ per arch), so on a non-aarch64 host the
# filter is SKIPPED — installing it would kill the worker at its very next syscall,
# turning a best-effort layer into a total-availability cliff. A port must supply a
# per-arch (AUDIT_ARCH constant, syscall table) pair, not just swap the constant.
def install_seccomp():
    report = {"requested": True, "engaged": False}
    machine = os.uname().machine
    if machine != "aarch64":
        report["detail"] = (
            "skipped: filter table is aarch64-only, host is %s (best-effort layer; "
            "worker runs without it)" % machine)
        return report

    class sock_filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8),
                    ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]

    class sock_fprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(sock_filter))]

    BPF_LD, BPF_W, BPF_ABS = 0x00, 0x00, 0x20
    BPF_JMP, BPF_JEQ, BPF_K = 0x05, 0x10, 0x00
    BPF_RET = 0x06
    AUDIT_ARCH_AARCH64 = 0xC00000B7
    KILL, ALLOW, ERRNO_EPERM = 0x00000000, 0x7FFF0000, (0x00050000 | 1)
    # arm64 syscall numbers (asm-generic/unistd.h) — escape/escalation & kernel-attack
    # primitives; NONE are used by CPython startup or a pure-compute effect.
    DENY = sorted({
        117,             # ptrace
        268, 97,         # setns, unshare  (no joining/creating further namespaces)
        40, 39, 41,      # mount, umount2, pivot_root
        442, 428, 430,   # mount_setattr, open_tree, fsopen  (new mount API)
        142, 104, 294,   # reboot, kexec_load, kexec_file_load
        105, 273, 106,   # init_module, finit_module, delete_module
        224, 225,        # swapon, swapoff
        280, 241,        # bpf, perf_event_open
        217, 219, 218,   # add_key, keyctl, request_key  (kernel keyrings)
        89, 161, 162,    # acct, sethostname, setdomainname
        112, 266, 171,   # clock_settime, clock_adjtime, adjtimex
        272, 60,         # kcmp, quotactl
        270, 271,        # process_vm_readv, process_vm_writev
    })
    prog = [
        sock_filter(BPF_LD | BPF_W | BPF_ABS, 0, 0, 4),                # A = seccomp_data.arch
        sock_filter(BPF_JMP | BPF_JEQ | BPF_K, 1, 0, AUDIT_ARCH_AARCH64),
        sock_filter(BPF_RET | BPF_K, 0, 0, KILL),                      # foreign arch → kill
        sock_filter(BPF_LD | BPF_W | BPF_ABS, 0, 0, 0),                # A = seccomp_data.nr
    ]
    n = len(DENY)
    for i, nr in enumerate(DENY):
        prog.append(sock_filter(BPF_JMP | BPF_JEQ | BPF_K, n - i, 0, nr))
    prog.append(sock_filter(BPF_RET | BPF_K, 0, 0, ALLOW))            # default: allow
    prog.append(sock_filter(BPF_RET | BPF_K, 0, 0, ERRNO_EPERM))     # denied: EPERM
    arr = (sock_filter * len(prog))(*prog)
    fprog = sock_fprog(len(prog), arr)
    PR_SET_SECCOMP, PR_GET_SECCOMP, SECCOMP_MODE_FILTER = 22, 21, 2
    ctypes.set_errno(0)
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0) != 0:
        report["detail"] = "PR_SET_SECCOMP refused (errno %d)" % ctypes.get_errno()
        return report
    if libc.prctl(PR_GET_SECCOMP, 0, 0, 0, 0) != SECCOMP_MODE_FILTER:
        report["detail"] = "seccomp mode read-back != filter"
        return report
    report.update({"engaged": True, "mode": SECCOMP_MODE_FILTER, "action": "ERRNO(EPERM)",
                   "arch": "aarch64", "denied_syscalls": n, "detail": "seccomp filter installed"})
    return report

iso = apply_namespaces()
manifest["namespaces"] = iso
# Promoted to a top-level manifest key because it is a containment CLAIM in its own right
# (the matrix proves `workspace_bind.engaged` against it), not a detail of namespace setup.
manifest["workspace_bind"] = iso.pop(
    "workspace", {"requested": False, "engaged": False, "detail": "no workspace subtree declared"}
)
if cfg["namespaces_mandatory"] and not iso.get("engaged"):
    fatal("mandatory namespace isolation did not engage: %s" % iso.get("detail"))
# A declared workspace is never optional: if the profile asked for one and it did not bind,
# nothing runs. Checked separately from the namespace gate so the refusal names the real
# cause instead of hiding a bind failure inside "namespaces did not engage".
if cfg.get("workspace") is not None and not manifest["workspace_bind"].get("engaged"):
    fatal("declared workspace subtree did not bind: %s" % manifest["workspace_bind"].get("detail"))

# -- PID namespace: enter it via fork -----------------------------------------
# CLONE_NEWPID (unshared above) takes effect for the FIRST child: that child becomes
# PID 1 of a fresh PID namespace and cannot see or signal ANY host process (a host PID
# is simply not in its namespace ⇒ kill() → ESRCH). The parent stays behind ONLY as a
# thin reaper: it drops its copies of the manifest/result pipe write-ends so the parent
# still observes EOF, waits for PID 1, and mirrors its exit status. Mandatory alongside
# the other namespaces — a failed fork fails closed rather than running host-PID-visible.
if iso.get("pid_ns_unshared"):
    try:
        _child = os.fork()
    except OSError as e:
        fatal("PID-namespace reaper fork failed: %s" % e)
    if _child > 0:
        os.close(manifest_fd)
        os.close(result_fd)
        _, _status = os.waitpid(_child, 0)
        if os.WIFEXITED(_status):
            os._exit(os.WEXITSTATUS(_status))
        # The child (PID 1) was killed by a signal — e.g. SIGXCPU/SIGKILL from the CPU or
        # memory backstop mid-effect. Re-raise that signal on ourselves so the parent sees a
        # signal death (returncode < 0) and maps it to UNKNOWN, never a fabricated FAILED.
        os.kill(os.getpid(), os.WTERMSIG(_status))
        os._exit(97)
    # In the child (PID 1 of the new namespace) everything below runs confined.
    _inner = os.getpid()
    if _inner != 1:
        fatal("PID namespace did not engage: inner pid %d != 1" % _inner)
    manifest["pid_namespace"] = {"engaged": True, "requested": True, "inner_pid": _inner}
else:
    manifest["pid_namespace"] = {"engaged": False, "requested": False,
                                 "detail": "profile requests no namespace isolation"}

# -- rlimits bind the effect-runner (the child) — set + getrlimit read-back ----
manifest["rlimits"] = apply_rlimits()

# -- PR_SET_DUMPABLE(0) — verified via PR_GET_DUMPABLE -----------------------
# Applied AFTER the namespace setup + fork so it cannot change the ownership of
# /proc/self/uid_map before that map is written. A non-dumpable process cannot be
# ptrace-attached by another same-uid process and produces no core dump, so the
# untrusted implementation's address space (any argument bytes it holds) cannot be
# exfiltrated by an outside observer or spilled to a core file. Additive hardening:
# it never affects the worker's own ability to run its digest-bound code.
PR_SET_DUMPABLE, PR_GET_DUMPABLE = 4, 3
if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
    fatal("prctl(PR_SET_DUMPABLE, 0) failed: errno %d" % ctypes.get_errno())
if libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) != 0:
    fatal("dumpable read-back != 0")
manifest["non_dumpable"] = True

# -- BEST-EFFORT seccomp filter, installed LAST so the manifest reports the truth ---
manifest["seccomp"] = install_seccomp()

# -- CLOSE THE PINNING FD BEFORE THE UNTRUSTED CODE RUNS (wave S0) -----------
# `ws_fd` is the O_PATH handle the PARENT opened on the declared subtree, and it is what made
# the swap detector above a real identity check (stat(target) vs fstat(ws_fd)). Its work is
# finished the moment the bind is verified — and leaving it open was escape B:
#
#   * it is inherited by everything below, including the untrusted implementation;
#   * its NUMBER is visible, because `sys.argv[4]` is readable from inside the effect;
#   * it cannot be `fchdir`'d (O_PATH), but it IS a valid `openat` dirfd against the
#     ORIGINAL mount — so `openat(ws_fd, "../..")` reached above the operator's containment
#     root, and writes through it succeeded against a subtree mounted MS_RDONLY, whose
#     read-only posture had read back correctly.
#
# No syscall filter can fix that: seccomp cannot tell where a dirfd points. The fix is that
# the descriptor stops existing before any untrusted byte runs, and `allowed_fds` stops
# listing it so the fd-closure audit below re-verifies exactly that.
if ws_fd >= 0:
    try:
        os.close(ws_fd)
    except OSError as e:
        fatal("workspace pin fd close failed: %s" % e)
    allowed_fds.discard(ws_fd)
    manifest["workspace_bind"]["pin_fd_closed"] = True
    # Read it back: an fd we believe is closed but is not would be the whole escape again,
    # so this is verified rather than assumed — the same discipline as every other layer.
    try:
        os.fstat(ws_fd)
        fatal("workspace pin fd still open after close")
    except OSError:
        pass
# Note there is deliberately no second /proc/self/fd sweep here: after the pivot there is no
# /proc in the jail (nothing mounts one), which is itself part of the containment. The fd
# audit ran earlier, against `allowed_fds`, while /proc was still readable — and the fstat
# read-back above is what proves this particular descriptor is gone.

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
"""


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
    workspace: WorkspaceMount | None = None,
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
        "workspace": None,
    }

    # The workspace subtree, PINNED. `ws_fd` is an O_PATH handle on the resolved source that
    # stays open for the child's entire lifetime: while it is open the kernel cannot recycle
    # that inode number, which is what makes the child's stat-vs-fstat comparison a real
    # identity check rather than a number that could be re-used underneath it. The path in
    # cfg is only how the mount syscall names the source; the fd is what decides whether the
    # thing it named is the thing we verified.
    ws_fd = -1
    if workspace is not None:
        try:
            ws_fd = os.open(workspace.host_root, os.O_PATH | os.O_DIRECTORY)
        except OSError as exc:
            shutil.rmtree(scratch, ignore_errors=True)
            raise IsolationError(
                f"cannot pin the declared workspace subtree {workspace.host_root!r}: {exc}"
            ) from exc
        try:
            if not stat_mod.S_ISDIR(os.fstat(ws_fd).st_mode):
                raise IsolationError(
                    f"declared workspace subtree {workspace.host_root!r} is not a directory"
                )
            os.mkdir(os.path.join(scratch, workspace.target), 0o700)
        except BaseException:
            os.close(ws_fd)
            shutil.rmtree(scratch, ignore_errors=True)
            raise
        cfg["workspace"] = {
            "source": workspace.host_root,
            "target": workspace.target,
            "read_only": bool(workspace.read_only),
        }

    cfg_bytes = json.dumps(cfg).encode("utf-8")

    cfg_r, cfg_w = os.pipe()
    man_r, man_w = os.pipe()
    res_r, res_w = os.pipe()
    proc: subprocess.Popen[bytes] | None = None
    try:
        argv = [sys.executable, "-I", "-c", _BOOTSTRAP, str(cfg_r), str(man_w), str(res_w)]
        passed = [cfg_r, man_w, res_w]
        if ws_fd >= 0:
            argv.append(str(ws_fd))
            passed.append(ws_fd)
        proc = subprocess.Popen(
            argv,
            cwd=scratch,
            env=_minimal_env(scratch),
            close_fds=True,
            pass_fds=tuple(passed),
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
                "status": "FAILED",
                "output": None,
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
        # Held open until the child is finished: the inode pin has to outlive the child's
        # verification, not merely the parent's.
        if ws_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(ws_fd)
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
    workspace: WorkspaceMount | None = None,
) -> WorkerResponse:
    """Run one bounded effect in an isolated worker and return a WorkerResponse.

    Fail-closed gates, in order (nothing runs until all pass):
      0. the `profile` must not PERMIT network — no egress mediation/redaction seam is wired
         in this phase, so a network-permitted profile (e.g. PROVIDER) is refused
         (IsolationError) rather than spawning an unmediated networked worker;
      0b. `profile.workspace_bind` and `workspace` must AGREE — a bind-requiring profile with
         no declared subtree, or a declared subtree under a profile that binds none, are both
         refused (IsolationError). Neither a silently-empty jail nor a silently-dropped mount;
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

    # Structural profile precondition (fail closed, at the primitive, for EVERY caller): a
    # network-PERMITTED profile (e.g. PROVIDER) has NO egress mediation/redaction seam wired
    # in this phase (see profiles.py:PROVIDER and containment_report's egress_mediation gap).
    # Spawning one would place a networked worker on the host with no mediation — the
    # fail-OPEN shape we must never have. Refuse it until the egress seam lands.
    if profile.network:
        raise IsolationError(
            f"worker profile {profile.name!r} permits network but no egress mediation seam "
            "is wired — a network-permitted worker is refused (fail closed) until egress "
            "mediation lands"
        )

    # The workspace precondition, in BOTH directions, at the primitive. The dangerous
    # direction is the first: a profile that requires a bound subtree and is handed none
    # would otherwise run as an empty-jail PURE worker while the receipt still said
    # "workspace" — the profile silently decaying into the one below it, which is exactly
    # the shape a reviewer cannot see. The second direction matters too: a mount handed to a
    # profile that does not bind one would be silently DISCARDED, and a caller who believed
    # their subtree was mapped in would get a jail with nothing in it and no error.
    if profile.workspace_bind and workspace is None:
        raise IsolationError(
            f"worker profile {profile.name!r} requires a declared workspace subtree but none "
            "was given — refused (fail closed) rather than run with an empty jail under a "
            "profile name that promises a bound one"
        )
    if workspace is not None and not profile.workspace_bind:
        raise IsolationError(
            f"a workspace subtree was declared but profile {profile.name!r} binds none — "
            "refused rather than silently dropping the mount the caller asked for"
        )
    if profile.workspace_bind and not profile.filesystem_jail:
        raise IsolationError(
            f"profile {profile.name!r} declares a workspace bind without a filesystem jail: "
            "the bind happens inside the mount namespace the jail creates, so this profile "
            "is incoherent and is refused rather than partially applied"
        )

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
            workspace=workspace,
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
