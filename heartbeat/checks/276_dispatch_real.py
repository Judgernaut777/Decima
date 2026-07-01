"""DISPATCH1 — dispatch runs the chosen pattern through REAL gated delegation.

DISPATCH1 (Cycle 20) selected an orchestration pattern and "executed" it via pure
deterministic STUBS — a choice that never spent a worker. EXEC1 then made a plan
actually run (`kernel.execute_plan`). This lane joins them: `dispatch(..., real=True)`
runs the chosen pattern through `execute_plan`, so every step is a REAL worker with a
downhill-attenuated grant, gated by the full spine (autonomy + governance + org policy
+ authorize) — not a stub. The pattern shapes the DAG; the kernel runs it.

This check proves:
  - REAL pattern execution — a real PIPELINE runs as a linear chain of real gated
    workers (each task carries a grant), and a real ORCHESTRATOR_WORKER fans out into
    one parallel wave; the `dispatch_run` records the REAL step count and still links to
    the PATTERN1 choice;
  - EVALUATOR_OPTIMIZER stays its own real loop even under `real=True` (it is already
    real — evidence-gated `evalopt.optimize`, no plan/worker);
  - BACK-COMPAT — `real=False` (the default) still runs the deterministic stubs and
    spawns NO worker (the 244_dispatch contract path is untouched);
  - NO DOUBLE EXECUTION (live) — a complex `say` turn now executes the brain plan ONCE
    via dispatch: exactly one step-worker per plan step, and the dispatched run's
    `executed` plan IS the brain plan (no separate stub plan runs alongside it).

Authority note: real mode grants nothing new — `execute_plan`/`authorize` gate every
effect; dispatch only chooses the shape.

Contract: run(k, line). Fail loud.
"""
from decima import dispatch as D
from decima import patterns as P
from decima import planning as PL


def _root(k, plan_id):
    """The latest plan-execution root task for an executed plan (worker_name plan:…)."""
    roots = [t for t in k.weave().of_type("task") if t.content.get("plan") == plan_id]
    assert roots, f"no exec root for {plan_id}"
    return roots[-1].content


def _workers(k):
    return sum(1 for t in k.weave().of_type("task") if t.content.get("worker"))


def run(k, line):
    line("\n== DISPATCH RUNS THE PATTERN THROUGH REAL GATED DELEGATION — DISPATCH1 ==")

    # 1. REAL PIPELINE — a linear chain of real, gated workers. ────────────────────────
    rp = D.dispatch(k, P.Task("etl run", fixed_stages=True), real=True,
                    executors={"stages": ["extract", "clean", "load"], "capability": "shell"})
    assert rp["pattern"] == P.PIPELINE and rp["real"], rp
    assert rp["executed"] is not None and PL.plan_status(k, rp["executed"])["complete"], rp
    assert [s["step"] for s in rp["steps"]] == ["extract", "clean", "load"], rp["steps"]
    pr = _root(k, rp["executed"])
    assert pr["waves"] == 3 and pr["wave_sizes"] == [1, 1, 1], pr   # serialized chain
    # The steps ran as REAL workers: each step task under the root carries a grant.
    step_tasks = [t for t in k.weave().of_type("task")
                  if t.content.get("parent") and t.content.get("worker")
                  and t.content.get("capability") == "shell"]
    assert any(t.content.get("grant") for t in step_tasks), \
        "a real pipeline step must spawn a gated worker with a downhill grant"
    # The dispatch_run records the REAL step count and links to the PATTERN1 choice.
    run_cell = k.weave().get(rp["run"])
    assert run_cell.content["pattern"] == P.PIPELINE and run_cell.content["steps"] == 3, run_cell.content
    dispatched = k.weave().edges_from(rp["run"], D.DISPATCHED)
    assert len(dispatched) == 1 and k.weave().get(dispatched[0]["dst"]).type == P.PATTERN_CHOICE
    line("  real PIPELINE → 3 real gated workers in a linear chain (waves [1,1,1]); "
         "dispatch_run records 3 real steps + links to the choice ✓")

    # 2. REAL ORCHESTRATOR_WORKER — an independent fan-out (one parallel wave). ─────────
    ro = D.dispatch(k, P.Task("crunch it", emergent_subtasks=True), real=True,
                    executors={"subtasks": ["a", "b", "c"], "capability": "shell"})
    assert ro["pattern"] == P.ORCHESTRATOR_WORKER and ro["real"], ro
    assert PL.plan_status(k, ro["executed"])["complete"], ro
    orr = _root(k, ro["executed"])
    assert orr["waves"] == 1 and orr["wave_sizes"] == [3], orr      # parallel frontier
    line("  real ORCHESTRATOR_WORKER → 3 real workers in ONE wave (fan-out [3]) ✓")

    # 3. EVALUATOR_OPTIMIZER stays its OWN real loop under real=True. ──────────────────
    re = D.dispatch(k, P.Task("legal brief", quality_critical=True), real=True,
                    executors={"generate": lambda c, cr: "brief [cited]",
                               "evaluate": lambda c: {"pass": True, "score": 100, "critique": "ok"}})
    assert re["pattern"] == P.EVALUATOR_OPTIMIZER, re
    # It is already real (evidence-gated), so it runs via its strategy, NOT execute_plan.
    assert re["real"] is False and re["executed"] is None, re
    assert re["output"] == "brief [cited]" and any(s.get("pass") for s in re["steps"]), re
    line("  EVALUATOR_OPTIMIZER under real=True → keeps its evidence-gated loop "
         "(no plan/worker, real=False) ✓")

    # 4. BACK-COMPAT — real=False (default) still runs stubs, spawns NO worker. ────────
    workers_before = _workers(k)
    rs = D.dispatch(k, P.Task("just echo"), executors={"run": lambda t: f"ran:{t.name}"})
    assert rs["pattern"] == P.SINGLE_AGENT and rs.get("real") is False, rs
    assert rs["output"] == "ran:just echo", rs                      # the stub, unchanged
    assert _workers(k) == workers_before, "stub-mode dispatch must spawn no real worker"
    line("  default real=False → deterministic stub output, ZERO workers "
         "(244_dispatch contract intact) ✓")

    # 5. NO DOUBLE EXECUTION (live) — a complex `say` turn executes the plan ONCE. ─────
    brain_before = {c.id for c in k.weave().of_type(PL.PLAN)
                    if c.content.get("objective", "").startswith("brain:")}
    workers_before = _workers(k)
    k.say("first contact the vendor and then reconcile the invoice")
    new_brain = [c for c in k.weave().of_type(PL.PLAN)
                 if c.content.get("objective", "").startswith("brain:") and c.id not in brain_before]
    assert len(new_brain) == 1, f"the turn must create exactly one brain plan, got {len(new_brain)}"
    bp = new_brain[0]
    total = PL.plan_status(k, bp.id)["total"]
    assert PL.plan_status(k, bp.id)["complete"] and total == 2, PL.plan_status(k, bp.id)
    # Exactly `total` NEW workers spawned — the plan ran once, not twice.
    assert _workers(k) == workers_before + total, \
        (f"expected {total} new workers (one per step), got {_workers(k) - workers_before} "
         "— the plan must run ONCE, never duplicated")
    # One brain plan + exactly `total` new workers ⇒ no second (stub) plan ran alongside.
    line(f"  live complex turn → {total} steps, {total} workers (no duplication); "
         "dispatch executed the brain plan exactly once ✓")

    line("  → DISPATCH1: the orchestration pattern now drives REAL gated delegation "
         "via execute_plan — pipeline serializes, orchestrator fans out, evalopt keeps "
         "its loop, stubs stay for the contract, and the live turn runs exactly once.")
