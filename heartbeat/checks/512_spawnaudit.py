"""SPAWN AUDIT — hardened beyond name-matching (Batch D).

`decima.isolation.assert_no_raw_spawn` is the import-time guard that makes the worker
seam MANDATORY: executor / cli_worker hold NO raw spawn path of their own, so re-adding
one refuses to even load. But a static AST scan is only ever as strong as what the
source reveals — a determined re-adder can LAUNDER a spawn primitive so no literal
`import subprocess` / `.system` ever appears in the tree (build the attribute name from
a string, fish a spawn-capable module out of `sys.modules`, reach a namespace table).
The BACKLOG names this exactly: "harden `assert_no_raw_spawn` beyond name-matching."

This lane closes it two ways, and this check proves both are load-bearing:

  (A) STATIC — the scan now also refuses the laundering surface (reflective access
      `getattr`/`setattr`/`vars`/`globals`/`locals`; namespace tables `__globals__`/
      `__builtins__`/`__dict__`; and `sys.modules[X]` for any index but the sanctioned
      `sys.modules[__name__]` self-pass). The COUNTERFACTUAL is shown, not asserted in
      the abstract: three laundered modules — each containing NO literal spawn token, so
      the ORIGINAL four-rule scan (reconstructed here) PASSES them — are all REFUSED by
      the hardened scan; while the real executor/cli_worker and the `sys.modules[__name__]`
      self-pass idiom still pass (no false positive).

  (B) RUNTIME — because no source scan can catch a spawn assembled purely at runtime,
      `spawn_firewall()` moves enforcement to the interpreter's audit boundary (PEP 578):
      inside an armed region, a spawn-family syscall raises IsolationError no matter HOW
      it was spelled. Proven with a genuinely laundered call — `getattr(os, "sys"+"tem")
      ("true")`, whose AST holds no `.system` — blocked BEFORE the child exists; while
      the sanctioned door (`spawn_worker`) still opens under the same active firewall;
      and the firewall is SCOPED (inert outside its region), so it cannot break the
      interpreter's legitimate raw spawners (process_effect's gated CLI effect, the MCP
      stdio transport) or the wider test runner.

MUTATION → RED (the properties this guards):
  • revert `assert_no_raw_spawn` to the four original rules (drop the reflection /
    namespace / sys.modules cases) → (A)'s laundered modules stop being refused → the
    `assert ... refused` lines fail;
  • make `_spawn_audit_hook` a no-op, or stop wrapping spawn_worker's Popen in
    `sanctioned_spawn()` → (B) breaks: the laundered runtime spawn is no longer blocked,
    or the sanctioned worker can no longer spawn under a firewall.
Verified by hand while drafting; reverted before landing.

Owns a temp dir for its throwaway modules; touches no shared kernel state.

Contract: run(k, line). Fail loud (assert).
"""
import ast
import importlib.util
import os
import tempfile

from decima import cli_worker, executor, isolation


def _load_module(tmpdir, name, src):
    """Write `src` to a real .py file and import it, so `inspect.getsource` (used by
    assert_no_raw_spawn) sees exactly these bytes — same technique as 394_isolation."""
    path = os.path.join(tmpdir, name + ".py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The ORIGINAL four-rule scan, reconstructed verbatim, so the counterfactual is real:
# a laundered module that THIS passes but the hardened scan refuses proves the
# hardening added reach, not noise.
def _original_scan_passes(mod) -> bool:
    import inspect
    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in isolation._RAW_SPAWN_IMPORTS:
                    return False
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in isolation._RAW_SPAWN_IMPORTS:
                return False
        elif isinstance(node, ast.Name) and node.id in isolation._RAW_SPAWN_NAMES:
            return False
        elif isinstance(node, ast.Attribute) and node.attr in isolation._RAW_SPAWN_ATTRS:
            return False
    return True


# Laundered modules — each reaches a spawn capability WITHOUT a literal spawn token,
# so the original scan is blind to them.
_LAUNDERED = {
    "launder_getattr": (
        "import sys\n"
        "def sneak():\n"
        "    return getattr(sys.modules['os'], 'sys' + 'tem')\n"
    ),
    "launder_sysmodules": (
        "import sys\n"
        "def sneak(name):\n"
        "    return sys.modules[name]\n"
    ),
    "launder_namespace": (
        "def sneak(fn):\n"
        "    return fn.__globals__['__builtins__']\n"
    ),
}


def run(k, line):
    line("\n== SPAWN AUDIT — hardened beyond name-matching (Batch D) ==")
    tmpdir = tempfile.mkdtemp(prefix="decima-spawnaudit-")

    # (A) STATIC — real modules + the self-pass idiom pass; laundered forms are refused.
    isolation.assert_no_raw_spawn(executor, cli_worker)
    self_pass = _load_module(
        tmpdir, "selfpass",
        "import sys\n"
        "from decima import isolation\n"
        "def audit():\n"
        "    isolation.assert_no_raw_spawn(sys.modules[__name__])\n")
    isolation.assert_no_raw_spawn(self_pass)          # sys.modules[__name__] is allowed
    line("  static: executor/cli_worker and the sys.modules[__name__] self-pass all "
         "pass (no false positive) ✓")

    refuted = 0
    for name, src in _LAUNDERED.items():
        mod = _load_module(tmpdir, name, src)
        # The COUNTERFACTUAL: the original four-rule scan is blind to this laundering...
        assert _original_scan_passes(mod), (
            f"{name} was supposed to evade the ORIGINAL scan — the counterfactual is void")
        # ...but the hardened scan refuses it.
        try:
            isolation.assert_no_raw_spawn(mod)
            raise AssertionError(f"hardened scan MISSED laundered spawn path in {name}")
        except isolation.IsolationError:
            refuted += 1
    line(f"  static: {refuted}/{len(_LAUNDERED)} laundered modules that EVADE the "
         "original name-match scan are refused by the hardened scan ✓")

    # (B) RUNTIME — the floor a scan cannot be.
    # Inert by default: outside a firewall the audit hook does not interfere (so the
    # interpreter's legitimate raw spawners and the test runner are untouched).
    name = "sys" + "tem"                              # built at runtime — no literal in any AST
    assert hasattr(os, name)
    ran_free = os.system("true")                      # runs normally outside a firewall
    assert ran_free == 0, "a normal spawn outside the firewall must not be blocked"

    # Inside the firewall: the SAME laundered call is blocked at the audit boundary,
    # before the child exists — enforcement beyond static analysis entirely.
    blocked = False
    with isolation.spawn_firewall():
        try:
            getattr(os, name)("true")
        except isolation.IsolationError:
            blocked = True
    assert blocked, "the runtime firewall did NOT block a laundered spawn"

    # subprocess.Popen and os.posix_spawn are covered too, however spelled.
    import subprocess
    for label, attempt in (
        ("subprocess.Popen", lambda: subprocess.Popen(["true"])),
        ("os.posix_spawn", lambda: os.posix_spawn("/bin/true", ["true"], {})),
    ):
        denied = False
        with isolation.spawn_firewall():
            try:
                attempt()
            except isolation.IsolationError:
                denied = True
        assert denied, f"firewall failed to block {label}"

    # The sanctioned door still opens under an active firewall (spawn_worker marks its
    # own Popen sanctioned) — the confinement seam is never locked out by the floor.
    with isolation.spawn_firewall():
        res = isolation.spawn_worker(["true"])
    assert res["code"] == 0, "spawn_worker must still run under an active firewall"
    assert res["manifest"]["no_new_privs"] is True, "the worker is still fully confined"
    line("  runtime: a laundered spawn (getattr(os,'sys'+'tem')) is blocked at the audit "
         "boundary; subprocess/posix_spawn too; spawn_worker still opens (sanctioned) ✓")

    # Scoped, not global: once the region exits, spawns run again — so the floor can
    # never break a legitimate raw spawner elsewhere in the process.
    assert os.system("true") == 0, "the firewall must be inert outside its region"
    line("  runtime: the firewall is scoped — inert outside its region, so legitimate "
         "raw spawners (process_effect / MCP stdio) and the test runner are untouched ✓")
    line("  the seam is mandatory in source AND at the syscall — laundering has no door ✓")
