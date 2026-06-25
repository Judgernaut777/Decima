"""BUDGET1 — finance analytics by composition over signed receipts + the portfolio.

Proves `decima.budget` is a read-only fold (D3.4), not a new authority: it sets a
spend cap, totals the FINANCIAL EffectReceipts that `payments`/`trading` wrote, flags
a category over its cap (and one under it is NOT flagged), computes a position's P&L
against a marked price, and traces every number to the signed receipts that justify it.

Runs on its OWN fresh Kernel (it forges a FINANCIAL capability and moves "money", so
it stays out of the shared kernel's state). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import budget, payments, trading
from decima.kernel import Kernel


def run(_k, line):
    line("\n== BUDGET1 (spend report · overspend flag · portfolio P&L · provenance) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- seed real money movement via the PUBLIC payment/trade APIs ----------
    rail = payments.install_rail(k, cap=100_000)         # int minor units (cents)
    k.approve(rail)                                       # Morta gate

    # two plain bills in distinct categories (payee = category)
    p1 = payments.pay(k, decima(), rail, amount=3_000, payee="groceries",
                      idempotency_key="bill-1")
    p2 = payments.pay(k, decima(), rail, amount=4_500, payee="groceries",
                      idempotency_key="bill-2")
    p3 = payments.pay(k, decima(), rail, amount=1_200, payee="transit",
                      idempotency_key="bill-3")
    assert all(p["status"] == payments.executor.SUCCEEDED for p in (p1, p2, p3))

    # a buy fills on the rail → a FINANCIAL receipt (commission) under "exchange",
    # and a position with an int cost basis on the portfolio Cell.
    b = trading.buy(k, decima(), rail, symbol="AAPL", qty=10, price=150,
                    idempotency_key="buy-aapl-1")
    assert b["filled"] and b["positions"]["AAPL"]["qty"] == 10, b
    line(f"  seeded: 3 bills (groceries x2, transit) + a filled AAPL buy")

    # ---- (1) spend_report by category totals the receipts correctly (INTS) ---
    rep = budget.spend_report(k, by="category")
    assert rep["groceries"]["total"] == 7_500, rep            # 3000 + 4500
    assert rep["groceries"]["count"] == 2, rep
    assert rep["transit"]["total"] == 1_200, rep
    assert isinstance(rep["groceries"]["total"], int), "totals are int minor units"
    assert "exchange" in rep, rep                              # the trade commission
    line(f"  spend_report by=category: groceries={rep['groceries']['total']} "
         f"transit={rep['transit']['total']} exchange={rep['exchange']['total']} (ints) ✓")

    # spend_report by period groups by a deterministic bucket; totals reconcile.
    per = budget.spend_report(k, by="period")
    assert sum(g["total"] for g in per.values()) \
        == sum(g["total"] for g in rep.values()), (per, rep)
    line(f"  spend_report by=period: {len(per)} bucket(s), totals reconcile with by=category ✓")

    # ---- (2) set caps; overspend() flags OVER and not UNDER ------------------
    budget.set_budget(k, "groceries", 5_000)     # spent 7500 > 5000  → OVER
    budget.set_budget(k, "transit", 5_000)       # spent 1200 ≤ 5000  → under
    over = budget.overspend(k)
    assert "groceries" in over, over
    assert over["groceries"]["spent"] == 7_500 and over["groceries"]["cap"] == 5_000
    assert over["groceries"]["over"] == 2_500, over
    assert "transit" not in over, "a category UNDER its cap is not flagged"
    line(f"  overspend: groceries OVER by {over['groceries']['over']} "
         f"(7500 vs cap 5000); transit under cap → not flagged ✓")

    # ---- (3) portfolio_pnl: cost basis vs a marked price → int P&L ----------
    # bought 10 @ 150 = 1500 cost; mark at 180 → value 1800, P&L +300.
    pnl = budget.portfolio_pnl(k, prices={"AAPL": 180})
    assert pnl["AAPL"]["cost"] == 1_500 and pnl["AAPL"]["value"] == 1_800, pnl
    assert pnl["AAPL"]["pnl"] == 300 and isinstance(pnl["AAPL"]["pnl"], int), pnl
    line(f"  portfolio_pnl AAPL: cost={pnl['AAPL']['cost']} mark=180 "
         f"value={pnl['AAPL']['value']} P&L={pnl['AAPL']['pnl']:+d} (int) ✓")

    # ---- (4) provenance: every number traces to signed FINANCIAL receipts ----
    prov = rep["groceries"]["provenance"]
    assert len(prov) == 2, prov
    for rid in prov:                                       # each is a real receipt cell
        rc = k.weave().get(rid)
        assert rc is not None and rc.type == payments.RESULT
        assert rc.content["effect_class"] == payments.FINANCIAL
        assert rc.content["status"] == payments.executor.SUCCEEDED
    assert pnl["AAPL"]["provenance"], "position P&L traces to its fill receipt(s)"
    fill_rc = k.weave().get(pnl["AAPL"]["provenance"][0])
    assert fill_rc.content["effect_class"] == payments.FINANCIAL, fill_rc.content
    line(f"  provenance: groceries total ← {len(prov)} signed receipts; "
         f"AAPL P&L ← receipt {pnl['AAPL']['provenance'][0][:8]} ✓")

    line("  → budgeting is a read-only fold over signed receipts + the portfolio; "
         "ints throughout, provenance to receipts, no new authority.")
