"""Worker isolation seam — THE ONLY door through which Decima spawns a worker.

Phase 1 (Enforcement, VISION.md): replace the allowlist-only worker sandbox with a
real confinement boundary. `spawn_worker(argv, ...)` is the single spawn path for
`executor` / `cli_worker`; those modules hold NO raw spawn capability of their own
(`assert_no_raw_spawn` refuses, at import time, any raw spawn path in them). A
worker is launched through a stdlib bootstrap that APPLIES and then VERIFIES each
confinement layer in the child before exec'ing the real argv:

MANDATORY layers (a failure to engage kills the spawn — fail closed, never soft):
  - resource rlimits: CPU seconds (SIGXCPU/SIGKILL), address space, open fds,
    nproc=1 (fork/clone denied by the kernel), fsize, core=0 — each SET and then
    READ BACK via getrlimit;
  - prctl(PR_SET_NO_NEW_PRIVS, 1) via ctypes — READ BACK via PR_GET_NO_NEW_PRIVS;
  - scrubbed minimal environment (the child VERIFIES its env is exactly the
    allowed minimal set — a leaked parent env aborts the spawn);
  - working-directory jail into a fresh scratch dir (verified in the child);
  - closed fds (the child verifies only stdio + the manifest pipe are open);
  - a new session (start_new_session, verified via getsid) so the whole worker
    process group can be killed on timeout.

OPTIONAL layers (attempted where the kernel offers them, degraded HONESTLY):
  - Landlock (raw syscalls via ctypes): filesystem WRITE access confined beneath
    the scratch jail;
  - seccomp-BPF (prctl PR_SET_SECCOMP via ctypes): socket creation denied (EPERM).

The seam returns an HONEST manifest of which layers actually engaged, built from
IN-CHILD read-backs — never a claim about a layer that did not apply (honesty over
theater). The manifest travels back with the worker's output so callers can attach
it to the execution record on the Weft (provenance).

Laws upheld: worker stdout stays untrusted DATA (callers mark it); no ambient
authority reaches the child (env scrubbed, fds closed, PATH pinned); all limits
are ints, never floats; the unsafe path raises, it is not merely discouraged.
"""
import ast
import contextlib as _contextlib
import inspect
import json as _json
import os as _os
import select as _select
import shutil as _shutil
import signal as _signal
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile
import threading as _threading
import time as _time


class IsolationError(Exception):
    """The seam refused to spawn, or a mandatory confinement layer failed to
    engage. Nothing ran (or the worker was killed) — fail closed, fail loud."""
    pass


class WorkerTimeout(IsolationError):
    """The worker exceeded its wall-clock budget and its whole process group was
    killed. The outcome of any effect it attempted is UNOBSERVED — callers map
    this to their honest timeout semantics (ExecError / Ambiguous)."""
    pass


DEFAULT_TIMEOUT = 10                     # wall-clock seconds (int — never a float)

# Default confinement budget for a worker. All values are ints (ints, not floats).
DEFAULT_LIMITS = {
    "cpu_seconds": 5,                    # soft → SIGXCPU kill; hard = soft+1 → SIGKILL
    "address_space": 1 << 30,            # 1 GiB of VA — a memory bomb gets MemoryError
    "open_files": 64,                    # RLIMIT_NOFILE
    "nproc": 1,                          # fork/clone → EAGAIN: no worker sub-processes
    "fsize": 8 << 20,                    # 8 MiB max file the worker may create
}

_OPTIONAL_LAYERS = ("landlock", "seccomp")

_SAFE_PATH = "/usr/bin:/bin"             # pinned; never the parent's ambient PATH


def _minimal_env(scratch: str) -> dict:
    """The ONLY environment a worker sees. HOME/TMPDIR point into its jail."""
    return {
        "PATH": _SAFE_PATH,
        "HOME": scratch,
        "TMPDIR": scratch,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


# ---------------------------------------------------------------------------
# The in-child bootstrap. Runs as `python -I -c BOOTSTRAP <config-json>` with the
# scrubbed env / jailed cwd already arranged by the parent; it VERIFIES those,
# applies the process-local layers, writes the honest manifest to the manifest
# pipe, then exec()s the real worker argv. Any mandatory failure → exit 97 with a
# {"fatal": ...} manifest; the parent raises IsolationError. Pure stdlib.
# ---------------------------------------------------------------------------
_BOOTSTRAP = r'''
import ctypes, fcntl, json, os, resource, sys

cfg = json.loads(sys.argv[1])
mfd = cfg["manifest_fd"]

def fatal(msg):
    try:
        os.write(mfd, json.dumps({"fatal": msg}).encode())
        os.close(mfd)
    except OSError:
        pass
    os._exit(97)

manifest = {"seam": "decima.isolation", "argv0": cfg["argv"][0]}

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

# -- closed fds: only stdio + the manifest pipe may be open ------------------
fds = []
for name in os.listdir("/proc/self/fd"):
    fd = int(name)
    try:
        fcntl.fcntl(fd, fcntl.F_GETFD)   # the listdir dirfd is gone by now
    except OSError:
        continue
    fds.append(fd)
fds = sorted(fds)
if set(fds) - {0, 1, 2, mfd}:
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
if tuple(resource.getrlimit(resource.RLIMIT_CORE)) != (0, 0):
    fatal("rlimit core read-back != (0, 0)")
applied["core"] = [0, 0]
manifest["rlimits"] = applied

# -- prctl(PR_SET_NO_NEW_PRIVS, 1) — verified via PR_GET_NO_NEW_PRIVS --------
libc = ctypes.CDLL(None, use_errno=True)
PR_SET_NO_NEW_PRIVS, PR_GET_NO_NEW_PRIVS = 38, 39
if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
    fatal("prctl(PR_SET_NO_NEW_PRIVS) failed: errno %d" % ctypes.get_errno())
if libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1:
    fatal("no_new_privs read-back != 1")
manifest["no_new_privs"] = True

# -- OPTIONAL: Landlock — fs WRITE access confined beneath the scratch jail --
def try_landlock():
    SYS_create, SYS_add_rule, SYS_restrict = 444, 445, 446
    ctypes.set_errno(0)
    abi = libc.syscall(SYS_create, None, 0, 1)   # LANDLOCK_CREATE_RULESET_VERSION
    if abi < 0:
        return {"attempted": True, "engaged": False, "abi": None,
                "detail": "landlock unavailable (errno %d)" % ctypes.get_errno()}
    WRITE_FILE, REMOVE_DIR, REMOVE_FILE = 1 << 1, 1 << 4, 1 << 5
    MAKE_ANY = (1 << 6) | (1 << 7) | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 12)
    handled = WRITE_FILE | REMOVE_DIR | REMOVE_FILE | MAKE_ANY
    if abi >= 3:
        handled |= 1 << 14                        # LANDLOCK_ACCESS_FS_TRUNCATE
    class RulesetAttr(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]
    class PathBeneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    attr = RulesetAttr(handled)
    rfd = libc.syscall(SYS_create, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if rfd < 0:
        return {"attempted": True, "engaged": False, "abi": abi,
                "detail": "create_ruleset failed (errno %d)" % ctypes.get_errno()}
    try:
        dfd = os.open(scratch, os.O_PATH)
        try:
            pb = PathBeneath(handled, dfd)
            if libc.syscall(SYS_add_rule, rfd, 1, ctypes.byref(pb), 0) != 0:
                return {"attempted": True, "engaged": False, "abi": abi,
                        "detail": "add_rule failed (errno %d)" % ctypes.get_errno()}
        finally:
            os.close(dfd)
        if libc.syscall(SYS_restrict, rfd, 0) != 0:
            return {"attempted": True, "engaged": False, "abi": abi,
                    "detail": "restrict_self failed (errno %d)" % ctypes.get_errno()}
    finally:
        os.close(rfd)
    return {"attempted": True, "engaged": True, "abi": abi,
            "detail": "fs write access confined beneath the scratch jail"}

# -- OPTIONAL: seccomp-BPF — socket creation denied with EPERM ----------------
def try_seccomp():
    machine = os.uname().machine
    TABLES = {
        "aarch64": {"audit_arch": 0xC00000B7, "socket": 198, "socketpair": 199},
        "x86_64": {"audit_arch": 0xC000003E, "socket": 41, "socketpair": 53},
    }
    t = TABLES.get(machine)
    if t is None:
        return {"attempted": True, "engaged": False,
                "detail": "no syscall table for machine %r" % machine}
    class SockFilter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8),
                    ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]
    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(SockFilter))]
    LD_W_ABS, JEQ_K, RET_K = 0x20, 0x15, 0x06
    RET_ALLOW, RET_KILL, RET_EPERM = 0x7FFF0000, 0x80000000, 0x00050000 | 1
    insns = [
        (LD_W_ABS, 0, 0, 4),                       # load audit arch
        (JEQ_K, 1, 0, t["audit_arch"]),            # wrong arch → kill (no aliasing)
        (RET_K, 0, 0, RET_KILL),
        (LD_W_ABS, 0, 0, 0),                       # load syscall nr
        (JEQ_K, 1, 0, t["socket"]),
        (JEQ_K, 0, 1, t["socketpair"]),
        (RET_K, 0, 0, RET_EPERM),                  # socket/socketpair → EPERM
        (RET_K, 0, 0, RET_ALLOW),
    ]
    arr = (SockFilter * len(insns))(*[SockFilter(*i) for i in insns])
    prog = SockFprog(len(insns), arr)
    PR_SET_SECCOMP, SECCOMP_MODE_FILTER = 22, 2
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0) != 0:
        return {"attempted": True, "engaged": False,
                "detail": "seccomp filter install failed (errno %d)" % ctypes.get_errno()}
    return {"attempted": True, "engaged": True,
            "detail": "socket creation denied (EPERM) via seccomp-BPF"}

optional = set(cfg["optional"])
manifest["landlock"] = (try_landlock() if "landlock" in optional else
                        {"attempted": False, "engaged": False, "detail": "not requested"})
manifest["seccomp"] = (try_seccomp() if "seccomp" in optional else
                       {"attempted": False, "engaged": False, "detail": "not requested"})

# -- hand off: write the honest manifest, drop the pipe, become the worker ----
os.write(mfd, json.dumps(manifest).encode())
os.close(mfd)
try:
    os.execv(cfg["argv"][0], cfg["argv"])
except OSError as e:
    sys.stderr.write("isolation bootstrap: exec failed: %s\n" % e)
    os._exit(96)
'''


def _validate_int(name: str, val) -> int:
    """Ints, not floats — and never bools masquerading as ints."""
    if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
        raise IsolationError(
            f"{name} must be a positive int (ints, not floats), got {val!r}")
    return val


def _merge_limits(limits) -> dict:
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


def _resolve_argv(argv) -> list:
    if not (isinstance(argv, list) and argv
            and all(isinstance(a, str) for a in argv)):
        raise IsolationError("argv must be a non-empty list of str "
                             "(untrusted text rides as single argv elements, never a shell)")
    prog = argv[0]
    if not _os.path.isabs(prog):
        found = _shutil.which(prog, path=_SAFE_PATH)   # pinned PATH, never ambient
        if not found:
            raise IsolationError(f"program {prog!r} not found on the pinned PATH {_SAFE_PATH}")
        prog = found
    if not (_os.path.isfile(prog) and _os.access(prog, _os.X_OK)):
        raise IsolationError(f"program {prog!r} is not an executable file")
    return [prog, *argv[1:]]


def spawn_worker(argv, *, timeout: int = DEFAULT_TIMEOUT, limits: dict | None = None,
                 optional: tuple = _OPTIONAL_LAYERS) -> dict:
    """Spawn a worker through the isolation boundary — the ONLY spawn path.

    Runs `argv` (a fixed list, NEVER a shell) inside the confinement described in
    the module docstring and returns::

        {"stdout": str, "stderr": str, "code": int,   # negative = killed by signal
         "manifest": {...honest, in-child-verified layer report...},
         "argv": [resolved argv]}

    Raises IsolationError if any MANDATORY layer cannot be engaged and verified
    (fail closed — the worker never runs unconfined), and WorkerTimeout if the
    wall-clock budget is exceeded (the whole worker session is SIGKILLed).
    Optional layers (landlock, seccomp) degrade gracefully and are reported in
    the manifest exactly as they engaged — never claimed when they did not.
    """
    argv = _resolve_argv(argv)
    merged = _merge_limits(limits)
    _validate_int("timeout", timeout)
    optional = tuple(optional)
    for name in optional:
        if name not in _OPTIONAL_LAYERS:
            raise IsolationError(f"unknown optional layer {name!r}")

    scratch = _tempfile.mkdtemp(prefix="decima-worker-")
    r, w = _os.pipe()
    proc = None
    try:
        cfg = {"argv": argv, "scratch": scratch, "manifest_fd": w,
               "limits": merged, "allowed_env": sorted(_minimal_env(scratch)),
               "optional": list(optional)}
        # The ONE sanctioned spawn: mark it so an active spawn_firewall() lets THIS
        # Popen through while still blocking every other (laundered) spawn on the thread.
        with sanctioned_spawn():
            proc = _subprocess.Popen(
                [_sys.executable, "-I", "-c", _BOOTSTRAP, _json.dumps(cfg)],
                cwd=scratch,                  # the jail
                env=_minimal_env(scratch),    # the scrub
                close_fds=True,               # nothing ambient leaks
                pass_fds=(w,),                # ...except the manifest pipe
                start_new_session=True,       # killable as a group; verified in-child
                stdin=_subprocess.DEVNULL,
                stdout=_subprocess.PIPE, stderr=_subprocess.PIPE, text=True)
        _os.close(w)
        w = -1

        # The bootstrap writes the manifest and closes the pipe BEFORE exec, so
        # this drains quickly; the deadline guards a bootstrap that never spoke.
        deadline = _time.monotonic() + timeout
        chunks = []
        while True:
            waitfor = deadline - _time.monotonic()
            if waitfor <= 0 or not _select.select([r], [], [], waitfor)[0]:
                _kill_group(proc)
                raise WorkerTimeout(
                    f"worker {argv[0]!r} produced no isolation manifest within {timeout}s")
            b = _os.read(r, 65536)
            if not b:
                break
            chunks.append(b)
        raw = b"".join(chunks).decode("utf-8", "replace")

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except _subprocess.TimeoutExpired:
            _kill_group(proc)
            raise WorkerTimeout(
                f"worker {argv[0]!r} exceeded {timeout}s wall clock — session killed") from None

        if not raw:
            raise IsolationError(
                f"isolation bootstrap died without a manifest (exit {proc.returncode}): "
                f"{(stderr or '').strip()[:400]}")
        manifest = _json.loads(raw)
        if "fatal" in manifest:
            raise IsolationError(f"isolation bootstrap refused: {manifest['fatal']}")
        for name in _OPTIONAL_LAYERS:
            entry = manifest.get(name)
            if not isinstance(entry, dict) or not isinstance(entry.get("engaged"), bool):
                raise IsolationError(f"manifest missing an honest {name!r} report")
            if name not in optional and (entry["engaged"] or entry.get("attempted")):
                raise IsolationError(
                    f"manifest overclaims: {name!r} was not requested but reports "
                    f"{entry!r} — honesty over theater")
        return {"stdout": stdout, "stderr": stderr, "code": proc.returncode,
                "manifest": manifest, "argv": argv}
    finally:
        if w >= 0:
            _os.close(w)
        _os.close(r)
        if proc is not None and proc.poll() is None:
            _kill_group(proc)
            proc.wait()
        _shutil.rmtree(scratch, ignore_errors=True)


def _kill_group(proc) -> None:
    """SIGKILL the worker's whole session (it is its own session leader)."""
    try:
        _os.killpg(proc.pid, _signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    try:
        proc.wait(timeout=5)
    except _subprocess.TimeoutExpired:
        pass


# ---------------------------------------------------------------------------
# The mandatory-seam audit: worker-spawning modules must hold NO raw spawn path.
# executor / cli_worker call this on THEMSELVES at import time, so re-adding a
# raw `subprocess`/`os` spawn to either refuses to even load — the unsafe path
# raises, it does not merely violate a convention.
# ---------------------------------------------------------------------------
_RAW_SPAWN_IMPORTS = frozenset({
    "subprocess", "os", "posix", "pty", "multiprocessing", "_posixsubprocess",
    "popen2", "commands", "importlib",
})
_RAW_SPAWN_NAMES = frozenset({"__import__", "eval", "exec", "execfile", "compile"})
_RAW_SPAWN_ATTRS = frozenset({
    "subprocess", "Popen", "call", "check_call", "check_output", "getoutput",
    "getstatusoutput", "system", "popen", "fork", "forkpty", "fork_exec",
    "posix_spawn", "posix_spawnp",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
})
# Beyond name-matching (Batch D): the static scan above catches a spawn primitive
# spelled literally, but a determined re-adder can LAUNDER one — build the attribute
# name from a computed string, or fish a spawn-capable module out of a namespace — so
# no literal `.system` / `import subprocess` ever appears in the AST. The three sets
# below close that surface for the worker-spawning modules (which have zero legitimate
# need for reflection): reflective attribute/namespace access, the interpreter's
# module/namespace tables, and — narrowly — `sys.modules[...]` with any index OTHER
# than the sanctioned `sys.modules[__name__]` self-reference idiom. This is defence in
# depth for the STATIC guard; the runtime firewall below is the floor a scan cannot be.
_RAW_SPAWN_REFLECTION = frozenset({"getattr", "setattr", "delattr", "vars", "globals", "locals"})
_RAW_SPAWN_NAMESPACES = frozenset({"__builtins__", "__globals__", "__dict__", "__loader__"})


def assert_no_raw_spawn(*modules) -> None:
    """Refuse (IsolationError) if any given module contains a raw spawn path.

    Scans the module's AST: an import of a spawn-capable module (`subprocess`,
    `os`, `pty`, ...), a dynamic-import/eval name, or an attribute reference that
    reaches a spawn primitive (e.g. laundering through another module's
    `.subprocess`/`.Popen`) all refuse. Beyond that literal name-matching it also
    refuses the LAUNDERING surface a re-adder would use to keep a spawn primitive out
    of the AST: reflective access (`getattr`/`setattr`/`vars`/`globals`/`locals`),
    the interpreter's namespace tables (`__builtins__`/`__globals__`/`__dict__`), and
    `sys.modules[...]` indexed by anything but the sanctioned `__name__` self-pass.
    Worker-spawning modules may ONLY spawn through `decima.isolation.spawn_worker`
    (which is why they have no legitimate need for any of the above). This is the
    STATIC guard; `spawn_firewall()` below is the runtime floor a scan cannot be."""
    for mod in modules:
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in _RAW_SPAWN_IMPORTS:
                        raise IsolationError(
                            f"raw spawn path in {mod.__name__}: `import {alias.name}` — "
                            "workers must go through decima.isolation.spawn_worker")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in _RAW_SPAWN_IMPORTS:
                    raise IsolationError(
                        f"raw spawn path in {mod.__name__}: `from {node.module} import ...` — "
                        "workers must go through decima.isolation.spawn_worker")
            elif isinstance(node, ast.Name) and node.id in _RAW_SPAWN_NAMES:
                raise IsolationError(
                    f"dynamic import/eval {node.id!r} in {mod.__name__} — refused")
            elif isinstance(node, ast.Name) and node.id in _RAW_SPAWN_REFLECTION:
                raise IsolationError(
                    f"reflective access {node.id!r} in {mod.__name__} — a spawn primitive "
                    "could be laundered through it; refused (workers go through spawn_worker)")
            elif isinstance(node, ast.Attribute) and node.attr in _RAW_SPAWN_ATTRS:
                raise IsolationError(
                    f"spawn primitive reference .{node.attr} in {mod.__name__} — "
                    "workers must go through decima.isolation.spawn_worker")
            elif isinstance(node, ast.Attribute) and node.attr in _RAW_SPAWN_NAMESPACES:
                raise IsolationError(
                    f"namespace-table access .{node.attr} in {mod.__name__} — a "
                    "spawn-capable object could be fished out of it; refused")
            elif isinstance(node, ast.Subscript) and _is_sys_modules_indirection(node):
                raise IsolationError(
                    f"sys.modules[...] indirection in {mod.__name__} — only the "
                    "sys.modules[__name__] self-reference is allowed; refused")


def _is_sys_modules_indirection(node: ast.Subscript) -> bool:
    """True for `sys.modules[X]` where X is anything but the bare `__name__` Name.
    `sys.modules[__name__]` is the sanctioned self-pass (a module handing ITSELF to the
    audit — it launders no spawn capability); `sys.modules['os']` or `sys.modules[expr]`
    can reach a spawn-capable module and is refused."""
    tgt = node.value
    if not (isinstance(tgt, ast.Attribute) and tgt.attr == "modules"
            and isinstance(tgt.value, ast.Name) and tgt.value.id == "sys"):
        return False
    idx = node.slice
    if isinstance(idx, ast.Index):                # py<3.9 compat: unwrap Index
        idx = idx.value
    return not (isinstance(idx, ast.Name) and idx.id == "__name__")


# ---------------------------------------------------------------------------
# The RUNTIME spawn firewall — the floor a static scan can never be.
#
# `assert_no_raw_spawn` reads SOURCE, so it is only ever as strong as what the AST
# reveals; a spawn assembled at runtime (a name built from bytes, a callable pulled
# from a C-level object, code handed to `exec` from data) leaves nothing for it to
# see. `spawn_firewall()` closes that gap by moving enforcement to the SYSCALL: a
# process-audit hook (PEP 578, `sys.addaudithook`) fires on the spawn-family audit
# events the interpreter raises no matter HOW the call was spelled, and — while a
# firewall is active on this thread and we are not inside a sanctioned spawn — turns
# the attempt into an IsolationError BEFORE the child exists.
#
# It is SCOPED, not global-permanent, and deliberately so: the interpreter also hosts
# LEGITIMATE raw spawners (`process_effect`'s gated real-CLI effect, the MCP stdio
# transport) and, in the wider gate, the test runner itself — a blanket ban armed on
# import would break them. So the firewall is inert until a caller opts a region into
# it; `spawn_worker` marks its own Popen sanctioned so the one true door stays open
# even at the strictest setting. Audit hooks cannot be removed once added, so the hook
# is installed lazily (first firewall entry) and is a pure thread-local read otherwise
# — zero cost and zero behavior change when no firewall is active.
# ---------------------------------------------------------------------------
_spawn_state = _threading.local()

# The audit events the interpreter raises for the spawn family, whatever the surface
# syntax (subprocess, os.system/exec*/spawn*/fork, posix_spawn, pty). Matched by prefix
# so os.exec / os.spawn / os.posix_spawn variants are all covered.
_SPAWN_AUDIT_EVENTS = (
    "subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
    "os.fork", "os.forkpty", "pty.spawn",
)

_hook_installed = False
_hook_lock = _threading.Lock()


def _sanctioned_depth() -> int:
    return getattr(_spawn_state, "sanctioned", 0)


def _firewall_depth() -> int:
    return getattr(_spawn_state, "firewall", 0)


def _spawn_audit_hook(event: str, args) -> None:
    """Refuse a spawn-family syscall while a firewall is active on this thread and we
    are not inside a sanctioned `spawn_worker`. Runs on EVERY audit event, so it does
    the cheapest possible thing off the hot path: a prefix check only once a firewall
    is actually armed on this thread."""
    if _firewall_depth() <= 0 or _sanctioned_depth() > 0:
        return
    if event.startswith(_SPAWN_AUDIT_EVENTS):
        raise IsolationError(
            f"raw spawn blocked by the runtime firewall: audit event {event!r} outside "
            "decima.isolation.spawn_worker — the syscall never happened (fail closed)")


def _ensure_audit_hook() -> None:
    global _hook_installed
    if _hook_installed:
        return
    with _hook_lock:
        if not _hook_installed:
            _sys.addaudithook(_spawn_audit_hook)
            _hook_installed = True


@_contextlib.contextmanager
def sanctioned_spawn():
    """Mark the enclosed region as a SANCTIONED spawn (the one true door). Only
    `spawn_worker` uses this; it lets the confined worker launch even under an active
    firewall, while every other spawn on the thread stays blocked."""
    _spawn_state.sanctioned = _sanctioned_depth() + 1
    try:
        yield
    finally:
        _spawn_state.sanctioned = _sanctioned_depth() - 1


@_contextlib.contextmanager
def spawn_firewall():
    """Arm the runtime spawn firewall for the enclosed region (this thread). Inside it,
    ANY spawn-family syscall raises IsolationError unless it is inside
    `sanctioned_spawn()` — regardless of how the call was assembled, because
    enforcement is at the interpreter's audit boundary, not in the source. Use it to
    run in-process code that must not be able to shell out. Reentrant; inert outside
    the region; installs the (unremovable) audit hook lazily on first use."""
    _ensure_audit_hook()
    _spawn_state.firewall = _firewall_depth() + 1
    try:
        yield
    finally:
        _spawn_state.firewall = _firewall_depth() - 1
