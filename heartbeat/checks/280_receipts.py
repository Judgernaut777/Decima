"""RECEIPT-HARDENING — EffectReceipts grow an integer `cost`, multi-attempt
reconciliation of an UNKNOWN by a later definite receipt, and two new statuses
COMPENSATED and CANCELLED (WEFT §8). Pure stdlib.

Runs on its OWN fresh Kernel: it appends result receipts and exercises the cost
guard, so it stays out of the shared kernel's state (other checks count/scan
of_type('result')). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import executor, model
from decima.kernel import Kernel
from decima.hashing import content_id


def run(_k, line):
    line("\n== EFFECT RECEIPTS (int cost · UNKNOWN→definite reconciliation · COMPENSATED · CANCELLED) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    w = lambda: k.weave()
    decima = lambda: w().get(k.decima_agent_id)
    cap_named = lambda n: next(c for c in w().of_type("capability")
                               if c.content.get("name") == n)
    shell = cap_named("shell")     # budget 100, no approval — Decima holds it
    echo = cap_named("echo")       # read-only, no cost

    # ---- (1) cost: a spend-bearing invoke records an int cost ---------------
    r = k.invoke(decima(), shell.id, {"cmd": "date", "cost": 60})
    assert r["status"] == executor.SUCCEEDED, r
    rc = w().get(r["result_cell"]).content
    assert rc["cost"] == 60 and isinstance(rc["cost"], int) and not isinstance(rc["cost"], bool), rc
    line(f"  cost: shell:date cost=60 → receipt {r['result_cell'][:8]} cost={rc['cost']} (int) ✓")

    # a read-only / no-cost invoke defaults to cost == 0
    r0 = k.invoke(decima(), echo.id, {"text": "no charge"})
    rc0 = w().get(r0["result_cell"]).content
    assert rc0["cost"] == 0 and isinstance(rc0["cost"], int), rc0
    line(f"  cost: echo (no cost arg) → receipt cost={rc0['cost']} ✓")

    # a float or negative cost is rejected AND writes no receipt
    for bad in (3.5, -5, True):
        before = len(w().of_type("result"))
        try:
            k.invoke(decima(), shell.id, {"cmd": "date", "cost": bad})
            assert False, f"cost={bad!r} should have raised ValueError"
        except ValueError:
            pass
        after = len(w().of_type("result"))
        assert after == before, f"cost={bad!r} wrote a receipt ({before}→{after})"
    line("  cost: float 3.5 / negative -5 / bool True each raise ValueError, write NO receipt ✓")

    # ---- (2) reconciliation: a later definite receipt supersedes an UNKNOWN --
    key = "logical-op-" + os.urandom(4).hex()
    # An earlier attempt whose outcome was unobservable → an UNKNOWN receipt.
    unk_id = content_id({"unknown_attempt": key})
    model.assert_content(k.weft, k.executor.id, unk_id, "result", {
        "of": None, "cap": "shell", "status": executor.UNKNOWN,
        "executor": k.executor.id, "attempt": 0, "idempotency": key,
        "effect_class": "READ", "out": None,
        "error": {"code": "ambiguous", "retryable": False, "message": "timeout"}})
    assert w().canonical_for_idempotency(key) is None, "all-UNKNOWN must fold to None"

    # A later attempt of the SAME logical op resolves definitely (reuses the key).
    r2 = k.invoke(decima(), shell.id, {"cmd": "date", "cost": 3, "idempotency": key})
    assert r2["status"] == executor.SUCCEEDED, r2
    definite = w().canonical_for_idempotency(key)
    assert definite is not None and definite.id == r2["result_cell"], definite
    assert definite.content["status"] == executor.SUCCEEDED
    assert definite.content.get("supersedes") == unk_id, definite.content
    # the earlier UNKNOWN is still present (additive — not retracted)
    assert w().get(unk_id) is not None and w().get(unk_id).content["status"] == executor.UNKNOWN
    line(f"  reconcile: UNKNOWN {unk_id[:8]} → definite {definite.id[:8]} "
         f"(supersedes={definite.content['supersedes'][:8]}); canonical = the definite one ✓")

    # ---- (3) COMPENSATED: a compensating action undoes a SUCCEEDED effect ----
    original = r["result_cell"]
    comp_id = k.compensate(original, reason="refund", cost=60)
    comp = w().get(comp_id).content
    assert comp["status"] == executor.COMPENSATED and comp["compensates"] == original, comp
    assert comp["reason"] == "refund" and comp["cost"] == 60, comp
    # the original is unchanged; both appear in of_type('result')
    assert w().get(original).content["status"] == executor.SUCCEEDED, "original must be untouched"
    rids = {c.id for c in w().of_type("result")}
    assert original in rids and comp_id in rids, "both receipts must fold"
    assert any(e["dst"] == original for e in w().edges_from(comp_id, "compensates"))
    line(f"  compensate: {comp_id[:8]} COMPENSATED compensates→{original[:8]} "
         f"(original still SUCCEEDED, both on Weft) ✓")

    # ---- (4) CANCELLED: an effect cancelled before submission ---------------
    canc_id = k.cancel("shell", reason="budget exhausted", cost=0)
    canc = w().get(canc_id).content
    assert canc["status"] == executor.CANCELLED and canc["cap"] == "shell", canc
    assert canc["reason"] == "budget exhausted" and canc["cost"] == 0, canc
    assert canc["out"] is None, "a cancelled effect never ran — no output"
    line(f"  cancel: {canc_id[:8]} CANCELLED (cap=shell, reason='{canc['reason']}', never submitted) ✓")

    line("  → five statuses hold: SUCCEEDED/FAILED/UNKNOWN (executor) + COMPENSATED/CANCELLED "
         "(explicit kernel records); receipts carry an int cost; a definite receipt reconciles "
         "an earlier UNKNOWN by the same idempotency key — all additive.")
