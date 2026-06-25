"""SCHED1 — Scheduling / reminders (a due event fires a disposition).

Logical ticks (ints), no wall-clock: `due(k, now)` takes the clock as a parameter.
This check proves:
  - schedule events at integer ticks;
  - due(now) returns ONLY at <= now (a future event is excluded);
  - firing routes the action through disposition (on the Weft) and marks fired (LWW);
  - a fired event is NOT returned by due() again;
  - a repeating event reschedules itself to at + interval (due again at the new tick).

Contract: run(k, line). Fail loud.
"""
from decima import scheduling as sched


def run(k, line):
    line("\n== SCHEDULING / REMINDERS (schedule → due(now) → fire → reschedule) — SCHED1 ==")
    w = lambda: k.weave()
    ids = lambda cells: {c.id for c in cells}

    # 1. Schedule three reminders: two due by tick 10, one in the future.
    past = sched.schedule(k, "Stand-up sync", at=5)
    soon = sched.schedule(k, "Renew TLS cert", at=10)
    future = sched.schedule(k, "Quarterly review", at=100)
    assert w().get(past).content["at"] == 5 and isinstance(w().get(past).content["at"], int)
    line("  scheduled 3 events at ticks 5, 10, 100 (ints) ✓")

    # 2. due(now=10): only at <= 10 — the future event (at=100) is excluded.
    d = sched.due(k, now=10)
    assert ids(d) == {past, soon}, ids(d)
    assert future not in ids(d), "future event (at=100) must NOT be due at now=10"
    line(f"  due(now=10) → {len(d)} events (at<=10); future at=100 excluded ✓")

    # 3. Fire the at=5 event: routes through disposition (on the Weft) + marks fired.
    res = sched.fire(k, past, now=10)
    routed = res["disposition"]
    assert routed["action"] == "task", routed          # trusted request → task
    task = w().get(routed["produced"])
    assert task is not None and task.content["status"] == "open"
    edges = w().edges_from(routed["intake"], "disposed_as")   # provenance on the Weft
    assert edges and edges[0]["dst"] == routed["disposition"]
    assert w().get(past).content["fired"] is True and w().get(past).content["fired_at"] == 10
    line(f"  fired at=5 → disposition {routed['action']} (task open, disposed_as edge on Weft) ✓")

    # 4. A fired event is NOT returned by due() again.
    d2 = sched.due(k, now=10)
    assert past not in ids(d2), "a fired event must not be due again"
    assert soon in ids(d2)
    line("  fired event no longer in due(now=10) ✓")

    # 5. A repeating event reschedules to at + interval after firing.
    rep = sched.schedule(k, "Daily backup", at=20, repeat_every=15)
    assert rep in ids(sched.due(k, now=20))
    fired = sched.fire(k, rep, now=20)
    assert w().get(rep).content["fired"] is True
    nxt = fired["rescheduled"]
    assert nxt is not None and nxt != rep, "a repeating event must reschedule to a new cell"
    assert w().get(nxt).content["at"] == 35 and w().get(nxt).content["fired"] is False
    assert rep not in ids(sched.due(k, now=35)), "the fired occurrence stays fired"
    assert nxt in ids(sched.due(k, now=35)), "the rescheduled occurrence is due at tick 35"
    line(f"  repeating event fired at 20 → rescheduled to tick 35 (interval 15), due again ✓")

    # 6. Fail-loud: firing an already-fired event is rejected.
    try:
        sched.fire(k, past, now=11)
        raise AssertionError("firing an already-fired event must raise")
    except ValueError:
        pass
    line("  re-firing a fired event raises (fail-loud) ✓")
    line("  → reminders are data on the Weft; due(now) is a clock-parameterized projection; "
         "a due event fires a disposition (Decima's decision); repeats reschedule deterministically.")
