"""PAY1 — Morta-gated payments rail: an irreversible FINANCIAL effect that is
spend-capped, approval-gated, idempotent, audited as an EffectReceipt, and bound to
a WV1 wager → verdict.

Runs on its OWN fresh Kernel: it forges a FINANCIAL capability and moves "money",
so it stays out of the shared kernel's state (and smoke discovers checks by lexical
filename order). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import payments, wager, executor
from decima.kernel import Kernel


def run(_k, line):
    line("\n== PAYMENTS RAIL (FINANCIAL · spend cap · Morta · idempotent · wager→verdict) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    cap_id = payments.install_rail(k, cap=100)                            # hard cap = 100
    decima = lambda: k.weave().get(k.decima_agent_id)
    spent = lambda: k.spent.get(k.decima_agent_id, 0.0)

    # ---- (1) Morta: a payment is DENIED until approved ----------------------
    r0 = payments.pay(k, decima(), cap_id, amount=60, payee="acme",
                      idempotency_key="ord-1")
    assert "denied" in r0 and "approval" in r0["denied"].lower(), r0
    assert payments.find_payment(k.weave(), "ord-1") is None              # nothing charged
    line(f"  pre-approval: pay(60) DENIED — {r0['denied']}")

    k.approve(cap_id)                                                     # human/Morta approves
    line("  (a human approves the FINANCIAL capability — Morta gate)")

    # ---- (2) over-cap spend is REFUSED -------------------------------------
    rbig = payments.pay(k, decima(), cap_id, amount=150, payee="acme",
                        idempotency_key="ord-big")
    assert "denied" in rbig and "budget" in rbig["denied"].lower(), rbig
    assert spent() == 0.0, spent()
    line(f"  over-cap: pay(150) with cap 100 REFUSED — {rbig['denied']}")

    # ---- in-cap payment runs only AFTER approval, on the Weft --------------
    r1 = payments.pay(k, decima(), cap_id, amount=60, payee="acme",
                      idempotency_key="ord-1", prediction=60)
    assert r1["status"] == executor.SUCCEEDED and not r1.get("denied"), r1
    assert spent() == 60.0
    receipt = k.weave().get(r1["result_cell"])
    assert receipt.content["effect_class"] == payments.FINANCIAL
    assert receipt.content["status"] == executor.SUCCEEDED
    assert receipt.content["idempotency_key"] == "ord-1" and receipt.content["amount"] == 60
    line(f"  approved: pay(60) → receipt {r1['result_cell'][:8]} "
         f"(class={receipt.content['effect_class']}, spent={int(spent())}/100) ✓")

    # ---- (3) a duplicate (same idempotency key) does NOT double-spend ------
    dup = payments.pay(k, decima(), cap_id, amount=60, payee="acme",
                       idempotency_key="ord-1", prediction=60)
    assert dup["idempotent_replay"] and dup["result_cell"] == r1["result_cell"], dup
    assert spent() == 60.0, spent()                                      # unchanged
    fin = [c for c in k.weave().of_type("result")
           if c.content.get("effect_class") == payments.FINANCIAL]
    assert len(fin) == 1, len(fin)                                       # one charge, not two
    line(f"  duplicate ord-1 → idempotent replay (same receipt, spent still "
         f"{int(spent())}); FINANCIAL receipts on Weft: {len(fin)} ✓")

    # ---- running spend cap: a second distinct payment over the cap refused -
    r2 = payments.pay(k, decima(), cap_id, amount=60, payee="beta",
                      idempotency_key="ord-2")
    assert "denied" in r2 and "budget" in r2["denied"].lower(), r2
    line(f"  running cap: pay(60) more (60+60>100) REFUSED — cap is a cumulative cap ✓")

    # ---- (4)+(5) the payment bound a wager; settle it with a verdict -------
    w = k.weave().get(r1["wager"])
    assert w is not None and w.type == wager.WAGER
    assert any(e["dst"] == cap_id for e in k.weave().edges_from(w.id, "wagers_on"))
    v = payments.settle(k, r1, observed=60)                             # outcome matched prediction
    assert v and v["hit"], v
    vc = k.weave().get(v["verdict"])
    assert vc.content["wager"] == r1["wager"] and vc.content["hit"] is True
    line(f"  bet→verify: wager {r1['wager'][:8]} (bound to the cap) → verdict "
         f"{v['verdict'][:8]} hit={v['hit']}; calibration={wager.calibration(k)['hit_rate']} ✓")
