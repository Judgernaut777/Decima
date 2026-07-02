"""WORKER ISOLATION — the seam is the only door, and the confinement really bites.

Phase 1 (Enforcement): `decima.isolation.spawn_worker` replaces the allowlist-only
worker sandbox with a real boundary. This check is an adversarial detector, not a
tautology — it verifies enforcement from INSIDE the worker (reading /proc, calling
getrlimit, attempting the forbidden thing) so removing or weakening any layer
fails loud. Proves:

  (a) the seam is MANDATORY — executor/cli_worker hold no raw spawn path (the
      import-time AST audit passes on them, and demonstrably REFUSES a module
      that imports subprocess / launders a spawn attribute), and their real
      spawns route through `isolation.spawn_worker` (observed via a spy);
  (b) mandatory layers BITE — no_new_privs really set (read from
      /proc/self/status IN the worker); a worker exceeding RLIMIT_AS gets
      MemoryError instead of its allocation; a CPU-spinning worker is KILLED by
      the kernel (SIGXCPU/SIGKILL); fork/clone is denied (nproc); a parent env
      canary never reaches the child (scrub verified in-child); cwd is jailed
      into a fresh scratch dir; only stdio fds survive into the worker; the
      wall-clock backstop kills a hung worker; malformed seam inputs (floats,
      non-str argv, unknown programs) are refused BEFORE anything runs;
  (c) the manifest is HONEST — optional layers (landlock, seccomp) are reported
      engaged ONLY when the forbidden action is really denied in the child
      (cross-validated both ways: engaged ⇒ denied, not-engaged ⇒ allowed), and
      un-requested layers may not be claimed at all; rlimit values in the
      manifest equal the child's own getrlimit read-backs;
  (d) provenance — the manifest rides the execution receipt onto the Weft.

Deterministic and offline: landlock/seccomp are tested CONDITIONALLY against the
manifest's own honest report (skips are printed, mandatory layers are asserted
unconditionally). No live network; no dependence on wall-clock values.

Contract: run(k, line). Fail loud via assert.
"""
import importlib.util
import json
import os
import signal
import sys
import tempfile

from decima import cli_worker, executor, isolation
from decima.kernel import Kernel


# Runs INSIDE the worker: observe every layer from the child's side and print JSON.
_PROBE = r'''
import json, os, resource, fcntl

obs = {}
for ln in open("/proc/self/status"):
    if ln.startswith("NoNewPrivs:"):
        obs["no_new_privs"] = int(ln.split()[1])
obs["cwd"] = os.path.realpath(os.getcwd())
obs["env_keys"] = sorted(os.environ)
fds = []
for name in os.listdir("/proc/self/fd"):
    fd = int(name)
    try:
        fcntl.fcntl(fd, fcntl.F_GETFD)      # the listdir dirfd is closed by now
    except OSError:
        continue
    fds.append(fd)
obs["fds"] = sorted(fds)
obs["rlimits"] = {
    "cpu_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)),
    "address_space": list(resource.getrlimit(resource.RLIMIT_AS)),
    "open_files": list(resource.getrlimit(resource.RLIMIT_NOFILE)),
    "nproc": list(resource.getrlimit(resource.RLIMIT_NPROC)),
    "fsize": list(resource.getrlimit(resource.RLIMIT_FSIZE)),
    "core": list(resource.getrlimit(resource.RLIMIT_CORE)),
}
try:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.close()
    obs["socket"] = "allowed"
except PermissionError:
    obs["socket"] = "denied"
outside = "/tmp/decima_394_probe_%d" % os.getuid()
try:
    with open(outside, "w") as f:
        f.write("x")
    os.unlink(outside)
    obs["write_outside"] = "allowed"
except PermissionError:
    obs["write_outside"] = "denied"
with open("inside.txt", "w") as f:                      # the jail must stay usable
    f.write("ok")
obs["write_inside"] = open("inside.txt").read()
print(json.dumps(obs))
'''


def _load_module(tmpdir: str, name: str, src: str):
    path = os.path.join(tmpdir, name + ".py")
    with open(path, "w") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _probe(**kw):
    res = isolation.spawn_worker([sys.executable, "-c", _PROBE], timeout=60, **kw)
    assert res["code"] == 0, (res["code"], res["stderr"])
    return json.loads(res["stdout"]), res["manifest"]


def run(k, line):
    line("\n== WORKER ISOLATION (the seam is the only door; confinement really bites) ==")

    # (a) THE SEAM IS MANDATORY ────────────────────────────────────────────────
    # The shipped worker-spawning modules pass the raw-spawn audit...
    isolation.assert_no_raw_spawn(executor, cli_worker)
    for mod in (executor, cli_worker):
        assert not hasattr(mod, "subprocess") and not hasattr(mod, "os"), \
            f"{mod.__name__} must not hold a spawn-capable module"
    # ...and the audit is a REAL detector, not a tautology: a module that imports
    # subprocess, or launders a spawn primitive through another module's
    # attribute, is REFUSED (IsolationError).
    tmpdir = tempfile.mkdtemp()
    bad_import = _load_module(tmpdir, "bad_raw_import", "import subprocess\n")
    bad_launder = _load_module(
        tmpdir, "bad_launder",
        "from decima import isolation as iso\n"
        "def sneak():\n    return iso.subprocess\n")
    for bad in (bad_import, bad_launder):
        try:
            isolation.assert_no_raw_spawn(bad)
            assert False, f"audit must refuse raw spawn path in {bad.__name__}"
        except isolation.IsolationError:
            pass
    line("  seam mandatory: executor/cli_worker hold no raw spawn path; the audit "
         "demonstrably refuses one ✓")

    # Real spawns actually ROUTE through the seam (observed, not assumed).
    seen = []
    real_spawn = isolation.spawn_worker

    def spy(argv, **kw):
        res = real_spawn(argv, **kw)
        seen.append(list(res["argv"]))
        return res

    isolation.spawn_worker = spy
    try:
        r1 = executor.execute("shell", None, {"cmd": "uname"})
        handler = cli_worker.make_handler()
        r2 = handler(None, {"text": "isolation probe"})
    finally:
        isolation.spawn_worker = real_spawn
    assert len(seen) == 2, f"expected exactly 2 spawns through the seam, saw {len(seen)}"
    assert r1["status"] == executor.SUCCEEDED and r1["code"] == 0 and r1["out"], r1
    assert r1["isolation"]["no_new_privs"] is True, "shell receipt must carry the manifest"
    assert "codex-shim reviewed: isolation probe" in r2["out"], r2
    assert r2["sandbox"]["mode"] == "isolation-seam", r2["sandbox"]
    assert r2["sandbox"]["manifest"]["no_new_privs"] is True, r2["sandbox"]
    line("  executor 'shell' and cli_worker both spawn THROUGH the seam; the honest "
         "manifest rides each receipt ✓")

    # (b) MANDATORY LAYERS BITE (verified from INSIDE the worker) ──────────────
    canary = "DECIMA_ISOLATION_CANARY"
    os.environ[canary] = "must-not-leak"
    try:
        obs, m = _probe()
    finally:
        del os.environ[canary]
    assert obs["no_new_privs"] == 1, "child /proc/self/status must show NoNewPrivs: 1"
    assert m["no_new_privs"] is True
    assert canary not in obs["env_keys"], "parent env leaked into the worker"
    assert set(obs["env_keys"]) == {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}, \
        f"worker env is not the scrubbed minimal set: {obs['env_keys']}"
    assert obs["cwd"] == m["cwd_jail"] != os.path.realpath(os.getcwd()), \
        "worker cwd must be jailed into the scratch dir"
    assert "decima-worker-" in obs["cwd"]
    assert obs["fds"] == [0, 1, 2], f"only stdio may reach the worker, got fds {obs['fds']}"
    assert obs["write_inside"] == "ok", "the jail itself must stay writable"
    # rlimits: what the child reads back == what the manifest claims == the request.
    dl = isolation.DEFAULT_LIMITS
    expect = {"cpu_seconds": [dl["cpu_seconds"], dl["cpu_seconds"] + 1],
              "address_space": [dl["address_space"]] * 2,
              "open_files": [dl["open_files"]] * 2,
              "nproc": [dl["nproc"]] * 2,
              "fsize": [dl["fsize"]] * 2,
              "core": [0, 0]}
    assert obs["rlimits"] == expect == m["rlimits"], \
        (obs["rlimits"], m["rlimits"], expect)
    line("  in-child read-backs: NoNewPrivs=1, env scrubbed (canary gone), cwd jailed, "
         "fds=[0,1,2], rlimits exact (core=0) ✓")

    # A memory bomb: 512 MiB allocation under a 256 MiB address-space budget must
    # get MemoryError — remove the rlimit and the allocation SUCCEEDS (fails here).
    bomb = isolation.spawn_worker(
        [sys.executable, "-c",
         "data = bytearray(512 * 1024 * 1024)\nprint('ALLOCATED')"],
        timeout=60, limits={"address_space": 256 * 1024 * 1024})
    assert bomb["code"] != 0 and "ALLOCATED" not in bomb["stdout"], bomb
    assert "MemoryError" in bomb["stderr"], bomb["stderr"][-200:]
    line("  RLIMIT_AS bites: a 512 MiB bomb under a 256 MiB budget dies with MemoryError ✓")

    # A CPU spinner is KILLED BY THE KERNEL (SIGXCPU at the soft limit, SIGKILL at
    # the hard backstop) — not politely asked to stop.
    spin = isolation.spawn_worker([sys.executable, "-c", "while True: pass"],
                                  timeout=60, limits={"cpu_seconds": 1})
    assert spin["code"] in (-signal.SIGXCPU, -signal.SIGKILL), \
        f"CPU-hog worker must be signal-killed, exited {spin['code']}"
    line(f"  RLIMIT_CPU bites: the spinning worker was killed by signal "
         f"{-spin['code']} ({signal.Signals(-spin['code']).name}) ✓")

    # fork/clone is denied by the kernel (RLIMIT_NPROC): no worker sub-processes.
    forkres = isolation.spawn_worker(
        [sys.executable, "-c",
         "import os\n"
         "try:\n    os.fork()\n    print('FORKED')\n"
         "except OSError:\n    print('FORK_BLOCKED')"],
        timeout=60)
    assert "FORK_BLOCKED" in forkres["stdout"] and "FORKED" not in forkres["stdout"], forkres
    line("  RLIMIT_NPROC bites: fork() inside the worker → EAGAIN, no sub-processes ✓")

    # The wall-clock backstop kills a hung worker's whole session.
    try:
        isolation.spawn_worker([sys.executable, "-c", "import time; time.sleep(300)"],
                               timeout=1)
        assert False, "a hung worker must raise WorkerTimeout"
    except isolation.WorkerTimeout:
        pass
    line("  wall-clock backstop: a hung worker's session is killed → WorkerTimeout ✓")

    # Fail closed at the seam's mouth: malformed requests never spawn anything.
    refusals = [
        lambda: isolation.spawn_worker("echo hi"),                       # not a list
        lambda: isolation.spawn_worker(["/usr/bin/env", 7]),             # non-str argv
        lambda: isolation.spawn_worker(["no-such-prog-394"]),            # unknown program
        lambda: isolation.spawn_worker([sys.executable, "-c", "pass"],
                                       limits={"cpu_seconds": 1.5}),     # float — refused
        lambda: isolation.spawn_worker([sys.executable, "-c", "pass"],
                                       timeout=2.5),                     # float — refused
        lambda: isolation.spawn_worker([sys.executable, "-c", "pass"],
                                       limits={"lol": 1}),               # unknown key
        lambda: isolation.spawn_worker([sys.executable, "-c", "pass"],
                                       optional=("chroot",)),            # unknown layer
    ]
    for attempt in refusals:
        try:
            attempt()
            assert False, "the seam must refuse a malformed spawn request"
        except isolation.IsolationError:
            pass
    line("  fail closed: floats, non-str argv, unknown programs/keys/layers are all "
         "refused before anything runs (ints, not floats) ✓")

    # (c) THE MANIFEST IS HONEST (optional layers, cross-validated both ways) ──
    for name, forbidden in (("seccomp", "socket"), ("landlock", "write_outside")):
        entry = m[name]
        assert isinstance(entry, dict) and entry.get("attempted") is True, entry
        assert isinstance(entry.get("engaged"), bool) and entry.get("detail"), entry
        if entry["engaged"]:
            assert obs[forbidden] == "denied", \
                f"manifest claims {name} engaged but the child was NOT confined: {obs}"
            line(f"  {name}: engaged, and the forbidden action really is denied "
                 f"in-child ({entry['detail']}) ✓")
        else:
            assert obs[forbidden] == "allowed", \
                f"manifest says {name} not engaged, yet the child was confined — dishonest: {obs}"
            line(f"  {name}: honestly reported NOT engaged ({entry['detail']}) — "
                 "conditional test skipped, honesty asserted ✓")

    # Un-requested optional layers may not be attempted OR claimed.
    obs2, m2 = _probe(optional=())
    for name in ("landlock", "seccomp"):
        assert m2[name] == {"attempted": False, "engaged": False,
                            "detail": "not requested"}, m2[name]
    assert obs2["socket"] == "allowed" and obs2["write_outside"] == "allowed", \
        "with optional layers off, the probe actions must succeed (proves the probes are sound)"
    assert obs2["no_new_privs"] == 1 and obs2["fds"] == [0, 1, 2], \
        "mandatory layers still engage when optional ones are off"
    line("  un-requested optional layers: never attempted, never claimed — and the "
         "mandatory layers still hold ✓")

    # (d) PROVENANCE — the manifest lands on the Weft with the execution record ─
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    cli_worker.integrate(kk)
    kk.say("delegate codex-shim as IsoWorker: codex-shim: prove isolation")
    w = kk.weave()
    tasks = [t for t in w.of_type("task") if t.content.get("worker_name") == "IsoWorker"]
    assert tasks, "delegated worker task was not recorded"
    receipt = w.get(tasks[-1].content["result"])
    assert receipt is not None and receipt.type == "result", "no receipt on the Weft"
    rman = receipt.content["sandbox"]["manifest"]
    assert receipt.content["sandbox"]["mode"] == "isolation-seam"
    assert rman["no_new_privs"] is True and rman["rlimits"]["core"] == [0, 0], rman
    assert isinstance(rman["seccomp"]["engaged"], bool), rman
    line("  provenance: the in-child-verified manifest rides the receipt Cell on the Weft ✓")

    line("  → worker isolation: one mandatory door, kernel-enforced confinement, "
         "an honest manifest — enforcement, not convention.")
