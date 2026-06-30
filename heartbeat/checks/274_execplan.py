"""EXEC1 — the cognitive layer DRIVES the live loop: a planned turn is EXECUTED.

BRAIN1 made the brain pattern-aware, but the plan it produced was inert "advice":
`say` recorded a PLAN1 decomposition on the Weft and then ignored it, collapsing
every turn onto one action. EXEC1 closes that gap. When a turn the single-action
`decide` cannot resolve carries a multi-step plan, `kernel.execute_plan` drives that
plan to completion — each ready step becomes a REAL, gated delegation (its own worker,
its own downhill-attenuated grant), waves flowing by the DAG's `depends_on` data.

This check proves:
  - LIVE WIRE — a complex multi-step utterance through `say` PLANS *and* EXECUTES: the
    brain plan ends complete, with one plan-execution root task and a real worker task
    per step (each carrying a `grant` — proof a capability was actually issued downhill,
    not simulated);
  - GATED, NOT ESCALATED — a step naming a capability Decima does NOT hold records an
    `ungranted` gap and is left undone; execution never fabricates authority;
  - TERMINATION (fail closed) — a frontier that cannot advance stops the run rather than
    spinning (the unheld-capability plan returns, incomplete, in a bounded wave count);
  - DAG SHAPE — independent steps share ONE wave (parallel frontier); a dependency chain
    serializes into one step per wave — folded from the root task's `wave_sizes`;
  - IDEMPOTENCE — re-executing a complete plan spawns no new workers and runs 0 waves.

Authority note: EXEC1 adds NO power. The plan is Decima's own decomposition and every
step rides the SAME spine (autonomy ladder, B4 governance, learned org policy,
`authorize`/Morta) as any delegation. It is a FALLBACK — it only fires when `decide`
would otherwise just talk, so explicit `delegate`/invoke commands stay untouched.

Contract: run(k, line). Fail loud.
"""
from decima import planning as PL


def _root_task(k, plan_id):
    """The single plan-execution root task cell for `plan_id` (worker_name plan:…)."""
    roots = [t for t in k.weave().of_type("task") if t.content.get("plan") == plan_id]
    assert len(roots) == 1, f"expected exactly one exec root for {plan_id}, got {len(roots)}"
    return roots[0]


def run(k, line):
    line("\n== COGNITIVE LAYER DRIVES THE LOOP: a planned turn is EXECUTED — EXEC1 ==")

    # 1. LIVE WIRE — a complex multi-step utterance is PLANNED and EXECUTED. ───────────
    # Snapshot brain-plan ids first so we identify THIS turn's plan unambiguously,
    # regardless of plans other checks (sharing this kernel) may have recorded.
    plans_before = len(k.weave().of_type(PL.PLAN))
    brain_before = {c.id for c in k.weave().of_type(PL.PLAN)
                    if c.content.get("objective", "").startswith("brain:")}
    utterance = "first gather the inputs then compute the results then file the summary"
    transcript = k.say(utterance)
    # The turn engaged the plan-execution path (not a bare respond).
    assert any("complex turn →" in t for t in transcript), transcript
    assert any("steps done over" in t for t in transcript), transcript
    # Exactly one NEW brain decomposition plan was recorded by this turn.
    new_plans = [c for c in k.weave().of_type(PL.PLAN)
                 if c.content.get("objective", "").startswith("brain:")
                 and c.id not in brain_before]
    assert len(new_plans) == 1, f"the turn must record exactly one brain plan, got {len(new_plans)}"
    brain_plan = new_plans[0]
    pid = brain_plan.id
    status = PL.plan_status(k, pid)
    assert status["complete"] and status["total"] >= 2, status
    assert len(k.weave().of_type(PL.PLAN)) >= plans_before + 1
    line(f"  complex turn → planned {status['total']} steps AND executed them all "
         f"({status['done']}/{status['total']} done) ✓")

    # Each step ran as a REAL delegation: one worker task per step, each with a grant
    # (a downhill-attenuated capability was actually issued — not a simulation) and a
    # done outcome. The whole run hangs under ONE plan-execution root task.
    root = _root_task(k, pid)
    assert root.content["status"] == "done", root.content
    step_tasks = [t for t in k.weave().of_type("task")
                  if t.content.get("parent") == root.id and t.content.get("worker")]
    assert len(step_tasks) == status["total"], (len(step_tasks), status["total"])
    assert all(t.content.get("grant") for t in step_tasks), \
        "every executed step must carry a real downhill grant (gated delegation)"
    assert all(t.content["status"] == "done" for t in step_tasks), \
        [t.content["status"] for t in step_tasks]
    line(f"  {len(step_tasks)} steps each ran as a real GATED worker (own grant), "
         f"all under one plan-execution root ✓")

    # A linear chain (s0→s1→s2) serializes: one ready step per wave.
    assert root.content["waves"] == status["total"], root.content
    assert root.content["wave_sizes"] == [1] * status["total"], root.content["wave_sizes"]
    line(f"  dependency chain serialized: {root.content['waves']} waves, "
         f"widths {root.content['wave_sizes']} ✓")

    # 2. PARALLEL FRONTIER — independent steps share ONE wave. ─────────────────────────
    par = PL.plan(k, "parallel demo", [
        {"key": "a", "objective": "handle item a", "capability": "shell"},
        {"key": "b", "objective": "handle item b", "capability": "shell"},
    ])
    k.execute_plan(par["plan"])
    assert PL.plan_status(k, par["plan"])["complete"]
    par_root = _root_task(k, par["plan"])
    assert par_root.content["waves"] == 1 and par_root.content["wave_sizes"] == [2], \
        par_root.content
    line("  two INDEPENDENT steps ran in ONE wave (frontier width 2) — DAG flows by data ✓")

    # 3. GATED + TERMINATION — an unheld capability fails closed, no spin. ─────────────
    ungranted_before = sum(1 for t in k.weave().of_type("task")
                           if t.content.get("status") == "ungranted")
    ghost = PL.plan(k, "ghost demo",
                    [{"key": "g", "objective": "do the impossible", "capability": "ghostcap"}])
    out = k.execute_plan(ghost["plan"])           # must RETURN — never spin
    gstatus = PL.plan_status(k, ghost["plan"])
    assert not gstatus["complete"] and gstatus["done"] == 0, gstatus
    ghost_root = _root_task(k, ghost["plan"])
    assert ghost_root.content["status"] == "incomplete", ghost_root.content
    # One wave attempted the lone step, advanced nothing, and the run stopped.
    assert ghost_root.content["waves"] == 1, ghost_root.content
    ungranted_after = sum(1 for t in k.weave().of_type("task")
                          if t.content.get("status") == "ungranted")
    assert ungranted_after == ungranted_before + 1, "an unheld step must record an ungranted gap"
    assert any("incomplete" in t for t in out), out
    line("  unheld capability → gap recorded, plan INCOMPLETE, run terminated "
         "(fail closed, no spin) ✓")

    # 4. IDEMPOTENCE — re-executing a complete plan spawns nothing, runs 0 waves. ──────
    workers_before = sum(1 for t in k.weave().of_type("task") if t.content.get("worker"))
    k.execute_plan(par["plan"])
    workers_after = sum(1 for t in k.weave().of_type("task") if t.content.get("worker"))
    assert workers_after == workers_before, "re-executing a complete plan must spawn no worker"
    assert _root_task_waves(k, par["plan"]) == 0, "a complete plan re-runs in 0 waves"
    line("  re-executing a COMPLETE plan → 0 new workers, 0 waves (idempotent) ✓")

    line("  → the cognitive layer now DRIVES the loop: a complex turn is planned AND "
         "executed step-by-step through real, gated delegation — flowing by the DAG, "
         "failing closed, idempotent. Planning stopped being advice.")


def _root_task_waves(k, plan_id):
    """Waves recorded on the (latest) plan-execution root after a re-run."""
    roots = [t for t in k.weave().of_type("task") if t.content.get("plan") == plan_id]
    assert roots, plan_id
    return roots[-1].content["waves"]
