"""SHOP1 — commerce/orders on the payments rail: placing an order is a Morta-gated,
spend-capped, idempotent FINANCIAL payment (no double-charge), with the order + its
receipt linked on the Weft.

Proves an order COMPOSES PAY1 rather than reinventing money movement:
  - add a catalog product (int price, minor units);
  - an order is Morta-gated: DENIED before the rail is approved → APPROVE → PLACED,
    with the receipt linked on the Weft;
  - a duplicate (same idempotency_key) does NOT double-charge (idempotent replay,
    one FINANCIAL receipt, spend unchanged);
  - an over-cap order is REFUSED (the spend cap bites).

Runs on its OWN fresh Kernel (it forges a FINANCIAL capability and moves "money").
Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import shop, payments, executor
from decima.kernel import Kernel


def run(_k, line):
    line("\n== SHOP / ORDERS (an order is a Morta-gated, spend-capped, idempotent payment) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    rail = payments.install_rail(k, cap=100, name="shop.pay")            # hard cap = 100
    decima = lambda: k.weave().get(k.decima_agent_id)
    spent = lambda: k.spent.get(k.decima_agent_id, 0.0)

    # ---- (1) catalog: add a product (int price, minor units) ----------------
    pid = shop.add_item(k, "widget", "Widget", price=40)
    prod = shop.product(k.weave(), "widget")
    assert prod is not None and prod.content["price"] == 40, prod
    line(f"  catalog: product 'widget' @40 → {pid[:8]} (price is INT minor units) ✓")

    # ---- (2) Morta: an order is DENIED until the rail is approved ------------
    o0 = shop.order(k, decima(), "widget", 1, idempotency_key="ord-1", pay_cap=rail)
    assert not o0["placed"] and o0["status"] == "denied", o0
    assert "approval" in (o0["denied"] or "").lower(), o0
    assert spent() == 0.0, spent()
    line(f"  pre-approval: order(widget x1=40) DENIED — {o0['denied']}")

    k.approve(rail)                                                      # human/Morta approves
    line("  (a human approves the FINANCIAL rail capability — Morta gate)")

    # ---- order is PLACED only AFTER approval, receipt linked on the Weft -----
    o1 = shop.order(k, decima(), "widget", 1, idempotency_key="ord-1", pay_cap=rail)
    assert o1["placed"] and o1["status"] == "placed", o1
    assert o1["amount"] == 40 and spent() == 40.0, (o1, spent())
    receipt = k.weave().get(o1["receipt"])
    assert receipt.content["effect_class"] == payments.FINANCIAL
    assert receipt.content["status"] == executor.SUCCEEDED
    oc = k.weave().get(o1["order"])
    assert any(e["dst"] == o1["receipt"] for e in k.weave().edges_from(oc.id, "paid_by")), oc
    line(f"  approved: order PLACED → receipt {o1['receipt'][:8]} "
         f"(class={receipt.content['effect_class']}, spent={int(spent())}/100); paid_by edge on Weft ✓")

    # ---- (3) a duplicate (same idempotency_key) does NOT double-charge -------
    dup = shop.order(k, decima(), "widget", 1, idempotency_key="ord-1", pay_cap=rail)
    assert dup["idempotent_replay"] and dup["receipt"] == o1["receipt"], dup
    assert spent() == 40.0, spent()                                     # unchanged — no double-charge
    fin = [c for c in k.weave().of_type("result")
           if c.content.get("effect_class") == payments.FINANCIAL]
    assert len(fin) == 1, len(fin)                                      # one charge, not two
    line(f"  duplicate ord-1 → idempotent replay (same receipt, spent still "
         f"{int(spent())}); FINANCIAL receipts on Weft: {len(fin)} — no double-order ✓")

    # ---- (4) an over-cap order is REFUSED (the spend cap bites) --------------
    over = shop.order(k, decima(), "widget", 3, idempotency_key="ord-big", pay_cap=rail)
    assert not over["placed"] and over["status"] == "refused", over
    assert "budget" in (over["denied"] or "").lower(), over
    assert spent() == 40.0, spent()                                    # nothing more charged
    line(f"  over-cap: order(widget x3=120, 40+120>100) REFUSED — {over['denied']}")

    # ---- order history folds from the Weft ----------------------------------
    hist = shop.orders(k)
    placed = [o for o in hist if o.content.get("placed")]
    assert len(placed) >= 1, hist
    line(f"  history: {len(hist)} order Cells on the Weft ({len(placed)} placed) — "
         f"an order is a Morta-gated, idempotent payment; receipts fold from the Weft ✓")
