"""FITNESS1 — private workouts, composed from health/goals/scheduling.

Proves `decima.fitness` honors HEALTH1's recall-vs-instruct law and composes the existing
capabilities:
  - log_workout records PRIVATE `workout` Cells (every one instruction_eligible=False,
    recallable=False, in a private scope, int duration/metrics, provenance on the Weft);
  - plan schedules a RECURRING session via scheduling (a `plan_schedule` edge), which is due,
    fires, and reschedules deterministically;
  - progress folds a correct INT summary (count/total/latest/delta);
  - a general / out-of-scope recall does NOT surface the private workout data;
  - link_goal ties a fitness goal via GOALS1 — a goal is a wager on yourself.

Composes only PUBLIC APIs (fitness/memory/goals/scheduling/wager). Contract: run(k, line). Fail loud.
"""
from decima import fitness, memory, goals
from decima import scheduling as sched
from decima import wager as wv


def run(k, line):
    line("\n== FITNESS1 (private workouts · DATA not instruction · recurring plan · int trends · goal=wager) ==")
    w = lambda: k.weave()
    ids = lambda cells: {c.id for c in cells}

    # ---- (1) log several workouts — all private, none instruction-eligible ----
    wids = [
        fitness.log_workout(k, "run", duration=1_800, metrics={"distance": 5_000}),  # 30min, 5km
        fitness.log_workout(k, "run", duration=2_100, metrics={"distance": 6_000}),
        fitness.log_workout(k, "run", duration=2_400, metrics={"distance": 7_000}),
        fitness.log_workout(k, "lift", duration=3_600, metrics={"reps": 120}),
        fitness.log_workout(k, "lift", duration=3_300, metrics={"reps": 110}),
    ]
    cells = [w().get(i) for i in wids]
    assert all(c is not None and c.type == fitness.WORKOUT for c in cells)
    assert all(c.content["instruction_eligible"] is False for c in cells), \
        "a workout is sensitive DATA — never instruction-eligible"
    assert all(c.content["recallable"] is False for c in cells), \
        "a workout must not surface in general recall"
    assert all(c.content["scope"].startswith(fitness.SCOPE_PREFIX) for c in cells), \
        "every workout lives in a private fitness scope"
    assert all(isinstance(c.content["duration"], int) for c in cells), "durations are ints"
    assert all(all(isinstance(v, int) for v in c.content["metrics"].values()) for c in cells), \
        "metrics are ints in minor units"
    assert all(w().edges_from(c.id, "supported_by") for c in cells), "workouts carry evidence"
    line(f"  logged {len(wids)} workouts (run x3, lift x2) — all instruction_eligible=False, "
         f"recallable=False, private scope, int duration+metrics, provenance ✓")

    # a float duration is refused outright — ints only (WEFT §4/§7).
    try:
        fitness.log_workout(k, "run", duration=30.5)
        raised = False
    except TypeError:
        raised = True
    assert raised, "a float workout duration must be refused"
    # a float metric is refused too.
    try:
        fitness.log_workout(k, "run", duration=1_800, metrics={"distance": 5.0})
        raised_m = False
    except TypeError:
        raised_m = True
    assert raised_m, "a float metric must be refused"
    line("  float duration & float metric refused → ints in minor units only ✓")

    # ---- (2) plan schedules a RECURRING session via scheduling ----
    p = fitness.plan(k, kind="run", every=7, at=7)
    pid, eid = p["plan"], p["event"]
    assert w().get(pid).type == fitness.PLAN and w().get(pid).content["every"] == 7
    psched = w().edges_from(pid, "plan_schedule")
    assert psched and psched[0]["dst"] == eid, psched          # provenance: plan → schedule
    ev = w().get(eid)
    assert ev.type == sched.SCHEDULED_EVENT and ev.content["repeat_every"] == 7
    assert eid in ids(sched.due(k, now=7)), "the planned session is due at tick 7"
    fired = sched.fire(k, eid, now=7)
    nxt = fired["rescheduled"]
    assert nxt is not None and w().get(nxt).content["at"] == 14, fired   # reschedule to 7+7
    line(f"  plan run every=7 → recurring scheduled_event (plan_schedule edge ✓); "
         f"due at 7 → fired → rescheduled to tick 14")

    # ---- (3) progress folds a correct INT summary (count/total/latest/delta) ----
    pr = fitness.progress(k, "run")
    assert pr["count"] == 3 and pr["total"] == 1_800 + 2_100 + 2_400, pr
    assert pr["min"] == 1_800 and pr["max"] == 2_400, pr
    assert pr["latest"] == 2_400 and pr["first"] == 1_800, pr
    assert pr["delta"] == 600, pr                              # 2400 - 1800
    assert all(isinstance(pr[kk], int) for kk in ("count", "total", "min", "max", "latest", "first", "delta"))
    prl = fitness.progress(k, "lift")
    assert prl["count"] == 2 and prl["delta"] == -300, prl     # 3300 - 3600
    assert fitness.progress(k, "swim") is None, "no workouts → no progress"
    line(f"  progress(run): count={pr['count']} total={pr['total']}s latest={pr['latest']}s "
         f"delta={pr['delta']:+d}s (int) · progress(lift).delta={prl['delta']:+d}s ✓")

    # ---- (4) general / out-of-scope recall does NOT leak the private workout data ----
    # `run` may match unrelated claims other checks seeded; the invariant is that NO
    # `workout` Cell (and nothing in a private fitness scope) ever surfaces via recall —
    # the points are a non-taxonomy type, recallable=False, in a private scope.
    def _leak(cells):
        return [c for c in cells
                if c.type == fitness.WORKOUT
                or str(c.content.get("scope", "")).startswith(fitness.SCOPE_PREFIX)]
    leak_general = memory.recall(w(), "run")                            # claims taxonomy
    leak_typed = memory.recall(w(), "run", memory_types=memory.MEMORY_TYPES)
    leak_realm = memory.recall(w(), "run", scope="realm:default",
                               memory_types=memory.MEMORY_TYPES)
    # even naming the private fitness scope through the general recall path yields no workout:
    # recallable=False means the retriever skips the point.
    leak_scoped = memory.recall(w(), "run", scope=fitness.fitness_scope("run"),
                                memory_types=memory.MEMORY_TYPES)
    for tag, res in (("general", leak_general), ("taxonomy", leak_typed),
                     ("realm", leak_realm), ("fitness-scope", leak_scoped)):
        assert _leak(res) == [], (tag, [c.id for c in _leak(res)])
    # the data IS reachable through the private fitness API (scope-authorized).
    assert len(fitness.history(k, "run")) == 3
    line(f"  general recall('run') surfaces {len(leak_general)} unrelated cell(s), "
         f"ZERO of type workout / private fitness scope (taxonomy/realm/fitness-scope all clean); "
         f"private API still returns {len(fitness.history(k, 'run'))} workouts ✓")

    # ---- (5) link_goal ties a fitness goal via GOALS1 (a goal is a wager on yourself) ----
    # target: run 100km cumulative (100_000 metres), 80% confident.
    fg = fitness.link_goal(k, target=100_000, kind="run", confidence=800_000)
    gid, wid = fg["goal"], fg["wager"]
    g = w().get(gid)
    assert g.type == goals.GOAL and g.content["target"] == 100_000, g.content
    assert isinstance(g.content["target"], int)
    bound = w().edges_from(gid, "goal_wager")
    assert bound and bound[0]["dst"] == wid, bound             # GOALS1: goal → wager
    fgedge = w().edges_from(gid, "fitness_goal")
    assert fgedge and fgedge[0]["dst"] == wid, fgedge          # FITNESS1: marked a fitness goal
    assert w().get(wid).type == wv.WAGER and w().get(wid).content["prediction"] == 100_000
    line(f"  link_goal 'run 100km' target=100000 (int) → wager {wid[:8]} bound "
         f"(goal_wager + fitness_goal edges ✓); a goal is a wager on yourself")

    # reaching the target settles the wager to a HIT (GOALS1 accountability spine).
    r = goals.progress(k, gid, 100_000)
    assert r["done"] and r["verdict"] is not None and r["verdict"]["hit"], r
    assert w().get(wid).content["status"] == "resolved" and w().get(wid).content["hit"]
    line(f"  goal reached → wager settled HIT (delta {r['verdict']['delta']:+d}) — "
         f"a training target completes by settling the wager ✓")

    line("  → workouts are sensitive DATA in a private scope (never instruction, never recalled, "
         "ints throughout); plans recur via scheduling; trends are deterministic ints; a fitness "
         "goal is a wager on yourself. Composed from HEALTH1's pattern + GOALS1 + SCHED1.")
