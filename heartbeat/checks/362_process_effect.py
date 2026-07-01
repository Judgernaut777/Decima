"""Real subprocess `process` effect — the generalized, gated "integrate any CLI tool" seam.

Proves (with its OWN fresh Kernel, deterministic, fail-loud) that `process_effect` wraps a
REAL local CLI tool as a Morta-gated, sandboxed, ALLOWLISTED capability:

  - install_tool wires a Morta-gated PROCESS capability in one call;
  - UNAPPROVED invoke → denied, and NO process runs (gate fires before the executor);
  - after k.approve(cap) → the invoke runs the REAL subprocess; the receipt records the
    stdout as UNTRUSTED DATA (instruction_eligible False) with exit code 0, class PROCESS;
  - a declared slot fills from its allowlist and drives the real argv;
  - an arg OUTSIDE the allowlist → FAILED (injection refused, no arbitrary command run);
  - a command that exits non-zero → FAILED, carrying the exit code;
  - a timeout → UNKNOWN (outcome unobservable, never fabricated as success/failure).

Uses only `python3` (sys.executable) with FIXED `-c` scripts — portable, deterministic,
no network, no exotic binaries. Contract: run(k, line). Fail loud.
"""
import os
import sys
import tempfile

from decima.kernel import Kernel
from decima import process_effect


def _agent(kk):
    # Fetch a FRESH agent cell — each install_tool grows Decima's envelope, so the
    # cell must be re-folded before every invoke or the new cap won't be in scope.
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL PROCESS EFFECT (allowlisted subprocess, Morta-gated) — integrate any CLI tool ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    py = sys.executable

    # 1. SUCCESS + Morta gate + untrusted DATA. ─────────────────────────────────────────
    cap_ok = process_effect.install_tool(
        kk, name="proc_hello",
        spec={"argv": [py, "-c", "print('hello from a real subprocess')"]})

    # Morta: no approval yet → denied, and NO process runs (gate before the executor).
    denied = kk.invoke(_agent(kk), cap_ok, {})
    assert "denied" in denied and "approval" in denied["denied"], denied
    line("  unapproved invoke → denied (Morta gate), no process run ✓")

    kk.approve(cap_ok)
    ok = kk.invoke(_agent(kk), cap_ok, {})
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["out"] == "hello from a real subprocess", rc
    assert rc["code"] == 0, rc
    assert rc["effect_class"] == "PROCESS", rc
    assert rc["instruction_eligible"] is False, rc     # stdout is DATA, never obeyed
    assert rc["untrusted"] is True, rc
    line("  approved → REAL subprocess ran; receipt records stdout as UNTRUSTED DATA "
         "(instruction_eligible False), code 0, class PROCESS ✓")

    # 2. ALLOWLISTED SLOT — a declared slot fills from its allowlist and drives the argv. ─
    slot_spec = {
        "argv": [py, "-c", "import sys; print('MODE=' + sys.argv[1])"],
        "slots": ["mode"],
        "allow": {"mode": ["upper", "lower"]},
    }
    cap_slot = process_effect.install_tool(kk, name="proc_slot", spec=slot_spec)
    kk.approve(cap_slot)
    good = kk.invoke(_agent(kk), cap_slot, {"mode": "upper"})
    assert good["status"] == "SUCCEEDED", good
    grc = kk.weave().get(good["result_cell"]).content
    assert grc["out"] == "MODE=upper", grc
    line("  allowlisted slot: value 'upper' passed validation → drove real argv ✓")

    # 3. INJECTION REFUSED — an arg OUTSIDE the allowlist → FAILED, no command run. ──────
    bad = kk.invoke(_agent(kk), cap_slot, {"mode": "; rm -rf / #"})
    assert bad.get("status") == "FAILED", bad
    assert "denied" in bad and "allowlist" in bad["denied"], bad
    line("  arg outside the allowlist ('; rm -rf / #') → FAILED (injection refused, "
         "no arbitrary command run) ✓")

    # 4. NON-ZERO EXIT → FAILED (carrying the exit code). ────────────────────────────────
    cap_fail = process_effect.install_tool(
        kk, name="proc_fail",
        spec={"argv": [py, "-c", "import sys; sys.exit(3)"]})
    kk.approve(cap_fail)
    failed = kk.invoke(_agent(kk), cap_fail, {})
    assert failed.get("status") == "FAILED", failed
    assert "exited 3" in failed["denied"], failed
    frc = kk.weave().get(failed["result_cell"]).content
    assert frc["status"] == "FAILED", frc
    line("  non-zero exit (3) → FAILED receipt carrying the exit code ✓")

    # 5. TIMEOUT → UNKNOWN (outcome unobservable, never fabricated). ─────────────────────
    cap_slow = process_effect.install_tool(
        kk, name="proc_slow",
        spec={"argv": [py, "-c", "import time; time.sleep(30)"]},
        timeout=1)
    kk.approve(cap_slow)
    unk = kk.invoke(_agent(kk), cap_slow, {})
    assert unk["status"] == "UNKNOWN", unk
    urc = kk.weave().get(unk["result_cell"]).content
    assert urc["status"] == "UNKNOWN" and urc.get("out") is None, urc
    line("  timeout (1s) → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    line("  → any local CLI tool becomes a capability in ONE install_tool call: "
         "allowlisted argv (never shell), Morta-gated + sandboxed, output untrusted DATA, "
         "SUCCEEDED/FAILED/UNKNOWN with exit code.")
