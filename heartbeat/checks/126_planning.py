"""PLAN1 — planning / decomposition into a task DAG.

Planning STRUCTURES work; it never executes it. This check proves:
  - an objective decomposes into several subtask Cells wired by `depends_on` edges;
  - `ready_steps` returns exactly the dependency-free step(s) — the delegable frontier;
  - marking those done makes the NEXT layer become ready (and the layer after that);
  - a topological order respects every dependency (each prereq precedes its dependents);
  - a plan containing a CYCLE is REJECTED (fail closed) — and writes nothing;
  - the plan, its steps, and its edges all live on the Weft (provenance);
  - a ready step is shaped as a delegation brief, but planning grants/invokes nothing.

Contract: run(k, line). Fail loud.
"""
from decima import planning


def run(k, line):
    line("\n== PLANNING / DECOMPOSITION (objective → acyclic task DAG) — PLAN1 ==")
    w = lambda: k.weave()

    # Decompose: build → (test, docs) both depend on build; ship depends on test+docs.
    #   build ─┬─▶ test ─┐
    #          └─▶ docs ─┴─▶ ship
    p = planning.plan(k, "release v1", [
        {"key": "build", "objective": "compile the project", "capability": "shell"},
        {"key": "test",  "objective": "run the test suite", "depends_on": ["build"],
         "capability": "shell"},
        {"key": "docs",  "objective": "generate the docs", "depends_on": ["build"],
         "capability": "shell"},
        {"key": "ship",  "objective": "publish the release",
         "depends_on": ["test", "docs"], "capability": "shell"},
    ])
    sid = p["steps"]
    plan_cell = w().get(p["plan"])
    assert plan_cell is not None and plan_cell.type == "plan"
    assert plan_cell.content["step_count"] == 4
    line(f"  decomposed 'release v1' → {plan_cell.content['step_count']} steps "
         f"(build→test/docs→ship) ✓")

    # On the Weft: plan, steps, membership edges, and dependency edges all present.
    steps_on_weft = [c for c in w().of_type("plan_step")
                     if c.content.get("plan") == p["plan"]]
    assert len(steps_on_weft) == 4
    has_step = w().edges_from(p["plan"], planning.HAS_STEP)
    assert len(has_step) == 4, has_step
    dep_edges = w().edges_from(sid["ship"], planning.DEPENDS_ON)
    assert {e["dst"] for e in dep_edges} == {sid["test"], sid["docs"]}, dep_edges
    line(f"  on the Weft: 1 plan + 4 plan_step cells, {len(has_step)} has_step edges, "
         f"depends_on arcs wired ✓")

    # Frontier #1: only the dependency-free step is ready.
    ready = planning.ready_steps(k, p["plan"])
    assert [r["step"] for r in ready] == [sid["build"]], ready
    brief = ready[0]
    assert brief["objective"] == "compile the project" and brief["capability"] == "shell"
    line(f"  ready_steps → exactly [{brief['key']}] (the only dep-free step); "
         f"shaped as a brief: cap={brief['capability']!r} ✓")

    # Mark build done → the next LAYER (test AND docs) becomes ready together.
    planning.mark_done(k, sid["build"])
    ready = planning.ready_steps(k, p["plan"])
    assert {r["step"] for r in ready} == {sid["test"], sid["docs"]}, ready
    assert sid["ship"] not in {r["step"] for r in ready}   # ship still blocked
    line(f"  marked build done → next layer ready = {{test, docs}}; ship still blocked ✓")

    # Finish test+docs → ship (the final layer) becomes ready.
    planning.mark_done(k, sid["test"])
    planning.mark_done(k, sid["docs"])
    ready = planning.ready_steps(k, p["plan"])
    assert [r["step"] for r in ready] == [sid["ship"]], ready
    line(f"  marked test+docs done → ready = [ship] (the final layer) ✓")

    # A topological order respects every depends_on edge.
    topo = planning.topological_order(k, p["plan"])
    assert set(topo) == set(sid.values()) and len(topo) == 4
    pos = {s: i for i, s in enumerate(topo)}
    assert pos[sid["build"]] < pos[sid["test"]]
    assert pos[sid["build"]] < pos[sid["docs"]]
    assert pos[sid["test"]] < pos[sid["ship"]]
    assert pos[sid["docs"]] < pos[sid["ship"]]
    order_keys = [w().get(s).content["key"] for s in topo]
    line(f"  topological order respects all deps: {' → '.join(order_keys)} ✓")

    # Plan is complete once every step is done.
    planning.mark_done(k, sid["ship"])
    st = planning.plan_status(k, p["plan"])
    assert st["complete"] and st["done"] == 4 and st["pending"] == 0, st
    line(f"  plan_status → complete ({st['done']}/{st['total']} done) ✓")

    # A CYCLIC plan is REJECTED (fail closed) and writes nothing to the Weft.
    plans_before = len(w().of_type("plan"))
    steps_before = len(w().of_type("plan_step"))
    rejected = False
    try:
        planning.plan(k, "impossible loop", [
            {"key": "a", "objective": "do a", "depends_on": ["c"]},
            {"key": "b", "objective": "do b", "depends_on": ["a"]},
            {"key": "c", "objective": "do c", "depends_on": ["b"]},   # a→b→c→a
        ])
    except ValueError as e:
        rejected = True
        reason = str(e)
    assert rejected, "a cyclic plan MUST be rejected"
    assert len(w().of_type("plan")) == plans_before, "cyclic plan must not write a plan cell"
    assert len(w().of_type("plan_step")) == steps_before, "cyclic plan must not write steps"
    line(f"  cyclic plan (a→b→c→a) → REJECTED, nothing committed ✓  ({reason[:48]}...)")

    # An unknown dependency is also rejected (fail closed).
    bad_dep = False
    try:
        planning.plan(k, "dangling", [
            {"key": "x", "objective": "do x", "depends_on": ["nope"]},
        ])
    except ValueError:
        bad_dep = True
    assert bad_dep, "a dep on an unknown step MUST be rejected"
    line("  step depending on an unknown step → REJECTED ✓")

    line("  → planning structures an objective into an acyclic task DAG on the Weft; "
         "the frontier is delegable but nothing here grants, invokes, or executes.")
