"""TAX1 — progressive tax ESTIMATION over the ledger by composition (B1 finance).

Proves `decima.tax` is the advisory tier of the money vertical: it sets a progressive
bracket schedule (int thresholds, int basis-point rates), captures deductible and
non-deductible spend via the PUBLIC EXPENSE1 API, and estimates the tax owed — taxable
income = income minus only the DEDUCTIBLE spend (floored at zero), the progressive
brackets applied with INTEGER arithmetic. It checks the bracket math by hand, confirms
a deductible category reduces taxable income (a non-deductible one does not), splits
the annual estimate into 4 integer quarterly payments that sum back EXACTLY (no
rounding loss), and traces the estimate to the entries it summed (provenance on the
Weft). Ints throughout, deterministic, advisory only — no money moves.

Runs on its OWN fresh Kernel (it asserts expense/tax analytic Cells; keep it out of
the shared kernel's state). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import tax, expense
from decima.kernel import Kernel


def run(_k, line):
    line("\n== TAX1 (progressive brackets · deductible spend · estimate · quarterly split) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated

    # ---- (1) set a progressive schedule: int thresholds, int bps rates -------
    # 0% up to 10000, 10% (1000 bps) on 10000..50000, 20% (2000 bps) above 50000.
    bid = tax.set_brackets(k, brackets=[(0, 0), (10_000, 1_000), (50_000, 2_000)])
    sched = tax.brackets(k)
    assert [b["threshold"] for b in sched] == [0, 10_000, 50_000], sched
    assert [b["rate"] for b in sched] == [0, 1_000, 2_000], sched
    assert all(isinstance(b["threshold"], int) and isinstance(b["rate"], int)
               for b in sched), "thresholds + rates are ints (minor units / bps)"
    try:
        tax.set_brackets(k, brackets=[(0, 0.1)])             # a float rate is refused
        raise AssertionError("set_brackets accepted a float rate")
    except TypeError:
        pass
    line("  schedule set: 0%/10000, 10%/[10000,50000), 20%/50000+ (int bps); float rate refused ✓")

    # ---- (2) capture deductible + non-deductible spend (INTS) via EXPENSE1 ----
    d1 = expense.capture(k, "RedCross", 4_000, "charity")        # deductible
    d2 = expense.capture(k, "Clinic", 3_000, "medical")          # deductible
    nd = expense.capture(k, "WholeFoods", 9_000, "groceries")    # NOT deductible
    assert "charity" in tax.deductible_categories(k)
    assert "groceries" not in tax.deductible_categories(k)
    line("  captured spend: charity 4000 + medical 3000 (deductible), groceries 9000 (not) ✓")

    # ---- (3) estimate: taxable = income − DEDUCTIBLE spend, floored, int -----
    income = 60_000
    est = tax.estimate(k, year=2025, income=income)
    # only the 7000 deductible spend reduces taxable income; groceries does NOT.
    assert est["deductible"] == 7_000, est                       # 4000 + 3000
    assert est["taxable"] == 60_000 - 7_000, est                 # 53000
    # progressive tax on 53000, BY HAND (integer arithmetic):
    #   0%  on [0,10000)      = 0
    #   10% on [10000,50000)  = 40000 * 1000 // 10000 = 4000
    #   20% on [50000,53000)  =  3000 * 2000 // 10000 =  600
    #   total = 4600
    assert est["tax"] == 4_600, est
    assert isinstance(est["tax"], int) and isinstance(est["taxable"], int)
    # effective rate: 4600 * 10000 // 60000 = 766 bps (int), NOT a float
    assert est["effective_rate"] == (4_600 * 10_000) // 60_000 == 766, est
    assert isinstance(est["effective_rate"], int)
    line(f"  estimate y2025: taxable={est['taxable']} (income 60000 − deductible 7000), "
         f"tax={est['tax']}, effrate={est['effective_rate']}bps (int) ✓")

    # ---- (3b) the deductible categories DID reduce taxable income ------------
    # same income, but pretend NOTHING were deductible → taxable is the full income,
    # so the tax is strictly higher. Proves deductions actually bite.
    bare_taxable = income                                        # 60000, no deduction
    #   10% on 40000 = 4000 ; 20% on 10000 = 2000 ; total 6000
    bare_tax = (40_000 * 1_000) // 10_000 + (10_000 * 2_000) // 10_000
    assert bare_tax == 6_000
    assert est["tax"] < bare_tax, (est["tax"], bare_tax)
    saved = bare_tax - est["tax"]
    line(f"  deductible spend reduced tax: {bare_tax} (no deductions) → {est['tax']} "
         f"(saved {saved}); non-deductible groceries left taxable income alone ✓")

    # ---- (4) provenance: the estimate cites the deductible entries summed -----
    prov = set(est["provenance"])
    assert prov == {d1, d2}, (prov, d1, d2)                      # charity + medical only
    assert nd not in prov, "a non-deductible expense is NOT in the provenance"
    edges = k.weave().edges_from(est["estimate"], tax.SUMMED)
    assert {e["dst"] for e in edges} == {d1, d2}, edges          # on the Weft too
    for eid in prov:
        assert k.weave().get(eid).type == "expense", eid
    line(f"  provenance: estimate ← 'summed' edges to {len(prov)} deductible entries "
         f"(charity+medical); groceries excluded ✓")

    # ---- (5) quarterly split sums back EXACTLY (int, no rounding loss) --------
    q = tax.quarterly(k, 2025)
    assert q["annual"] == est["tax"] == 4_600
    assert len(q["quarters"]) == 4 and all(isinstance(x, int) for x in q["quarters"])
    assert sum(q["quarters"]) == q["annual"], (q["quarters"], q["annual"])  # ties exactly
    assert q["quarters"] == [1_150, 1_150, 1_150, 1_150], q                 # 4600/4 even

    # a remainder case: force an annual not divisible by 4 and confirm it still ties,
    # with the remainder handed to the EARLY quarters (deterministic).
    k2 = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    tax.set_brackets(k2, brackets=[(0, 1_000)])                  # flat 10% on all income
    q4 = tax.quarterly(k2, 2027, income=10_017)                  # 10% of 10017 = 1001
    assert q4["annual"] == 1_001, q4
    assert sum(q4["quarters"]) == 1_001, q4                      # 251,250,250,250 → ties
    assert q4["quarters"] == [251, 250, 250, 250], q4            # remainder to early Qs
    line(f"  quarterly y2025: {q['quarters']} sum {sum(q['quarters'])}=={q['annual']}; "
         f"odd case {q4['quarters']} sum {sum(q4['quarters'])}=={q4['annual']} (no loss) ✓")

    line("  → TAX1 is an advisory fold: progressive int brackets over the ledger, "
         "deductions trace to signed entries, quarterly splits tie back to the cent.")
