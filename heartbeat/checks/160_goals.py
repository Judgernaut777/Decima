"""GOALS1 — Goals & habits (a goal is a wager on yourself; a habit is a recurring nudge).

A composing module: it binds WV1 (wager/verdict) and SCHED1 (recurring reminders) to
user-facing intent. This check proves:
  - set_goal binds a goal Cell to a WV1 wager (a `goal_wager` edge on the Weft);
  - progress updates (LWW) advance an INT counter; reaching the target settles the wager's
    verdict to a HIT and marks the goal done; further progress is idempotent;
  - habit schedules a REPEATING reminder via scheduling (a `habit_schedule` edge), which is
    due, fires, and reschedules deterministically;
  - record_done counts an INT streak up;
  - every signed value is an int; all of it lives on the Weft.

Contract: run(k, line). Fail loud.
"""
from decima import goals
from decima import wager as wv
from decima import scheduling as sched


def run(k, line):
    line("\n== GOALS & HABITS (a goal is a wager on yourself) — GOALS1 ==")
    w = lambda: k.weave()
    ids = lambda cells: {c.id for c in cells}

    # 1. Set a goal: target 10_000 minor units ($100.00), 80% confident — binds a WV1 wager.
    gid = goals.set_goal(k, "Save $100 buffer", target=10_000, confidence=800_000)
    g = w().get(gid)
    assert g.type == "goal" and g.content["target"] == 10_000 and g.content["progress"] == 0
    assert isinstance(g.content["target"], int) and isinstance(g.content["progress"], int)
    bound = w().edges_from(gid, "goal_wager")
    wid = g.content["wager"]
    assert bound and bound[0]["dst"] == wid, bound          # provenance: goal → wager
    assert w().get(wid).type == wv.WAGER and w().get(wid).content["prediction"] == 10_000
    line(f"  goal 'Save $100 buffer' target=10000 (int) → wager {wid[:8]} bound "
         f"(goal_wager edge ✓); status={g.content['status']}")

    # 2. Progress partway: LWW-updates the counter, goal still open, wager unsettled.
    r = goals.progress(k, gid, 6_000)
    assert r["progress"] == 6_000 and not r["done"] and r["verdict"] is None
    assert w().get(gid).content["progress"] == 6_000      # newest version folded (LWW)
    line(f"  progress → 6000/10000 (LWW); goal open, wager still unsettled ✓")

    # 3. Reach the target: settles the wager's verdict to a HIT, marks the goal done.
    r2 = goals.progress(k, gid, 10_000)
    assert r2["done"] and r2["status"] == "done", r2
    assert r2["verdict"] is not None and r2["verdict"]["hit"], r2["verdict"]
    vprov = w().edges_from(r2["verdict"]["verdict"], "verdict_of")
    assert vprov and vprov[0]["dst"] == wid                # verdict_of → the goal's wager
    assert w().get(wid).content["status"] == "resolved" and w().get(wid).content["hit"]
    line(f"  progress → 10000/10000 → goal DONE; wager settled HIT "
         f"(delta {r2['verdict']['delta']:+d}, verdict_of→{wid[:8]} ✓)")

    # 3b. Idempotent once done: further progress doesn't re-settle.
    r3 = goals.progress(k, gid, 12_000)
    assert r3["status"] == "done" and r3["verdict"] is None, r3
    line("  further progress is idempotent (no re-settle) ✓")

    # 4. A habit: a recurring reminder via scheduling — due, fires, reschedules.
    h = goals.habit(k, "Morning run", every=7, at=7)
    hid, eid = h["habit"], h["event"]
    hsched = w().edges_from(hid, "habit_schedule")
    assert hsched and hsched[0]["dst"] == eid              # provenance: habit → schedule
    ev = w().get(eid)
    assert ev.type == sched.SCHEDULED_EVENT and ev.content["repeat_every"] == 7
    assert eid in ids(sched.due(k, now=7)), "the habit reminder is due at tick 7"
    fired = sched.fire(k, eid, now=7)
    nxt = fired["rescheduled"]
    assert nxt is not None and w().get(nxt).content["at"] == 14, fired   # reschedule to 7+7
    line(f"  habit 'Morning run' every=7 → recurring reminder (habit_schedule edge ✓); "
         f"fired at 7 → rescheduled to tick 14")

    # 5. record_done advances an INT streak.
    s0 = w().get(hid).content["streak"]
    assert s0 == 0 and isinstance(s0, int)
    s1 = goals.record_done(k, hid)
    s2 = goals.record_done(k, hid)
    s3 = goals.record_done(k, hid)
    assert (s1, s2, s3) == (1, 2, 3), (s1, s2, s3)
    assert w().get(hid).content["streak"] == 3 and isinstance(w().get(hid).content["streak"], int)
    line(f"  record_done ×3 → streak 0→1→2→3 (int, LWW on the Weft) ✓")

    line("  → a goal is a wager on yourself (settles HIT on completion); a habit is a recurring "
         "reminder with a streak. Composed from WV1 + SCHED1, all ints, all on the Weft.")
