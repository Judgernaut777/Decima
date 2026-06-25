"""SUBS1 — Subscriptions tracker by composition over scheduling + budget (D3.4).

Proves `decima.subscriptions` is read-only analytics over a recurring-charge clock, not
a new authority: it adds subscriptions (each scheduling a *recurring* renewal reminder),
projects the renewals due at/before a given tick, totals the recurring cost (int, with a
by-category breakdown), and composes BUDGET1's caps to flag a category over its cap (one
under it is NOT flagged). All state lands on the Weft; ints throughout.

Contract: run(k, line). Fail loud.
"""
from decima import subscriptions as subs
from decima import scheduling, budget


def run(k, line):
    line("\n== SUBSCRIPTIONS (add → due_renewals(now) → monthly_cost → check_budget) — SUBS1 ==")
    w = lambda: k.weave()

    # 1. Add three subscriptions: two streaming (one due soon, one later), one cloud.
    netflix = subs.add_subscription(k, "Netflix", amount=1_500, every=30,
                                    category="streaming", next_at=30)
    spotify = subs.add_subscription(k, "Spotify", amount=1_000, every=30,
                                    category="streaming", next_at=100)
    cloud = subs.add_subscription(k, "Cloud backup", amount=600, every=30,
                                  category="cloud", next_at=30)
    nf = w().get(netflix)
    assert nf.content["amount"] == 1_500 and isinstance(nf.content["amount"], int)
    assert nf.content["every"] == 30 and isinstance(nf.content["every"], int)
    # the renewal is a recurring scheduled_event (the renewal clock).
    rem = w().get(nf.content["reminder"])
    assert rem is not None and rem.type == scheduling.SCHEDULED_EVENT
    assert rem.content["repeat_every"] == 30 and rem.content["at"] == 30, rem.content
    # provenance on the Weft: a renews_via edge to the reminder.
    edges = w().edges_from(netflix, "renews_via")
    assert edges and edges[0]["dst"] == nf.content["reminder"], edges
    line("  added 3 subscriptions (Netflix/Spotify streaming, Cloud backup); "
         "each schedules a recurring renewal reminder (renews_via edge on Weft) ✓")

    # 2. due_renewals(now=30): only renewals at <= 30 — Spotify (at=100) excluded.
    due = subs.due_renewals(k, now=30)
    due_ids = {s.id for s in due}
    assert netflix in due_ids and cloud in due_ids, due_ids
    assert spotify not in due_ids, "Spotify (next_at=100) must NOT be due at now=30"
    line(f"  due_renewals(now=30) → {len(due)} due (Netflix, Cloud backup); "
         "Spotify at=100 excluded ✓")

    # 3. monthly_cost totals correctly (INT) with a by-category breakdown.
    mc = subs.monthly_cost(k)
    assert mc["total"] == 3_100, mc                     # 1500 + 1000 + 600
    assert isinstance(mc["total"], int), "total is int minor units"
    cat = mc["by_category"]
    assert cat["streaming"]["total"] == 2_500 and cat["streaming"]["count"] == 2, cat
    assert cat["cloud"]["total"] == 600, cat
    # every number traces to the subscription Cells it summed.
    for sid in cat["streaming"]["provenance"]:
        assert w().get(sid).type == subs.SUBSCRIPTION
    line(f"  monthly_cost: total={mc['total']} "
         f"streaming={cat['streaming']['total']} cloud={cat['cloud']['total']} "
         "(ints, provenance to subscription Cells) ✓")

    # 4. check_budget composes BUDGET1 caps: flag OVER, not UNDER.
    budget.set_budget(k, "streaming", 2_000)            # cost 2500 > 2000  → OVER
    budget.set_budget(k, "cloud", 2_000)                # cost  600 ≤ 2000  → under
    over = subs.check_budget(k)
    assert "streaming" in over, over
    assert over["streaming"]["spent"] == 2_500 and over["streaming"]["cap"] == 2_000
    assert over["streaming"]["over"] == 500, over
    assert "cloud" not in over, "a category UNDER its cap is not flagged"
    line(f"  check_budget: streaming OVER by {over['streaming']['over']} "
         "(2500 vs cap 2000); cloud under cap → not flagged ✓")

    line("  → subscriptions compose scheduling (recurring renewal clock) + budget "
         "(authority-free caps); ints throughout, provenance on the Weft, no new authority.")
