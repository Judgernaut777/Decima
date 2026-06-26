"""BRAIN1 — the brain is pattern-aware: PATTERN1/DISPATCH1 + PLAN1 wired into decide.

The brain's observe→decide→act turn was a bare single-agent loop. BRAIN1 makes it
consult an orchestration pattern per turn, and — for a complex/multi-step task —
DECOMPOSE the work into ordered subtasks (a PLAN1 DAG) BEFORE acting. The whole
choice is recorded with provenance (a `dispatch_run` linked to the PATTERN1
`pattern_choice`, plus the `plan`/`plan_step` cells). This check proves:

  - a COMPLEX / multi-step task triggers PLANNING (a decomposed plan is recorded) and
    a pattern is SELECTED via dispatch/patterns;
  - a SIMPLE task still runs the existing single-agent path — back-compat: the brain's
    `decide` is untouched, and the hook declines (no plan, no dispatch run);
  - the chosen pattern + plan are recorded with PROVENANCE (the dispatch_run links to
    the pattern_choice; the plan + its steps live on the Weft);
  - a FAILURE in the new path does NOT break the brain — the hook is INERT (returns
    None, never raises), exactly like OR1's orientation hook.

The new path is ADVICE: it grants nothing — `capability.authorize` still gates effects.

Contract: run(k, line). Fail loud.
"""
from decima import agent as A
from decima import dispatch as D
from decima import patterns as P
from decima import planning as PL


def run(k, line):
    line("\n== BRAIN IS PATTERN-AWARE (decide consults DISPATCH + PLANNING) — BRAIN1 ==")
    brain = A.RuleBrain()

    # 1. A COMPLEX / multi-step task → PLANNING fires + a pattern is selected. ────────
    runs_before = len(D.runs_on(k))
    plans_before = len(k.weave().of_type(PL.PLAN))
    complex_task = "ingest the data then transform it then publish the report"
    res = brain.plan_and_dispatch(k, complex_task)
    assert res is not None, "a complex/multi-step task must engage the new path"
    assert res["dispatched"] and res["pattern"], res
    assert res["multi_step"] and res["plan"] is not None, res
    assert len(res["plan_steps"]) >= 2, res
    # A new dispatch_run AND at least our brain plan were recorded (the chosen
    # strategy may itself compose further PLAN1 plans — so count is monotonic up).
    assert len(D.runs_on(k)) == runs_before + 1, "dispatch must record a run"
    assert len(k.weave().of_type(PL.PLAN)) >= plans_before + 1, "planning must record a plan"
    assert k.weave().get(res["plan"]) is not None, "the brain's decomposition plan must be on the Weft"
    line(f"  complex task → pattern={res['pattern']!r}, PLAN decomposed into "
         f"{len(res['plan_steps'])} ordered steps ✓")

    # The plan is a real acyclic DAG on the Weft (a chain: s0 → s1 → s2).
    plan_cell = k.weave().get(res["plan"])
    assert plan_cell is not None and plan_cell.type == PL.PLAN
    assert plan_cell.content["step_count"] == len(res["plan_steps"])
    topo = PL.topological_order(k, res["plan"])
    assert topo == res["plan_steps"], (topo, res["plan_steps"])
    line(f"  plan on the Weft: {plan_cell.content['step_count']}-step DAG, "
         f"topological order respected ✓")

    # 2. PROVENANCE — the dispatch_run links to the PATTERN1 pattern_choice cell. ─────
    run_cell = k.weave().get(res["run"])
    assert run_cell is not None and run_cell.type == D.DISPATCH_RUN
    assert run_cell.content["pattern"] == res["pattern"]
    dispatched = k.weave().edges_from(res["run"], D.DISPATCHED)
    assert len(dispatched) == 1, dispatched
    choice_cid = dispatched[0]["dst"]
    choice_cell = k.weave().get(choice_cid)
    assert choice_cell is not None and choice_cell.type == P.PATTERN_CHOICE
    assert choice_cell.content["pattern"] == res["pattern"]
    assert choice_cell.content["reason"]  # the deciding reason is recorded
    line(f"  provenance: dispatch_run ─dispatched→ pattern_choice "
         f"(reason recorded) ✓")

    # 3. BACK-COMPAT — a SIMPLE task still runs the existing single-agent path. ───────
    runs_after_complex = len(D.runs_on(k))
    plans_after_complex = len(k.weave().of_type(PL.PLAN))
    simple = "echo hello"
    simple_res = brain.plan_and_dispatch(k, simple)
    assert simple_res is None, "a simple task must NOT engage the new planning/dispatch path"
    # Nothing extra recorded — the new path stayed inert for the simple turn.
    assert len(D.runs_on(k)) == runs_after_complex, "simple task wrote a dispatch run"
    assert len(k.weave().of_type(PL.PLAN)) == plans_after_complex, "simple task wrote a plan"
    # AND the existing decide path is unchanged: it still resolves echo to an invoke.
    agent = k.weave().get(k.decima_agent_id)
    action = brain.decide(simple, k.weave(), agent)
    assert action.kind in ("invoke", "respond"), action
    line(f"  simple task ({simple!r}) → hook declines (None); existing decide path "
         f"intact (action={action.kind}) ✓")

    # 4. INERT ON FAILURE — a fault in the new path NEVER breaks the brain. ───────────
    # Force the planning layer to raise; the hook must swallow it and return None,
    # exactly like OR1's orientation hook. The brain stays usable afterward.
    orig_plan = PL.plan
    PL.plan = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("induced planning fault"))
    try:
        faulted = brain.plan_and_dispatch(k, complex_task)
    finally:
        PL.plan = orig_plan
    assert faulted is None, "a failure in the new path must be inert (return None)"
    # The brain still decides normally after the induced fault — nothing corrupted.
    still_ok = brain.decide(simple, k.weave(), k.weave().get(k.decima_agent_id))
    assert still_ok.kind in ("invoke", "respond"), still_ok
    line("  induced planning fault → hook returns None (inert); brain still decides ✓")

    # 5. The hook grants NO authority — it is advice, like the router/PATTERN1. ───────
    # (The dispatch run + plan carry no capability/grant; authorize still gates effects.)
    assert "capability" not in (run_cell.content or {})
    line("  → the brain is pattern-aware: it selects an orchestration pattern and plans "
         "complex work, recorded with provenance; simple work is unchanged; the path "
         "is inert on failure and grants nothing.")
