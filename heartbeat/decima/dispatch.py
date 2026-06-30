"""DISPATCH1 — the pattern dispatcher: selection → EXECUTION.

`patterns.py` (PATTERN1) *chooses* an agentic architecture for a task — a
deterministic, recorded, overridable decision. But a choice that is never acted on
is inert: the selector names a shape and stops. This module makes the choice LIVE.
`dispatch(k, task)` asks the selector (or honors an override) for a pattern, then
EXECUTES the task via the strategy that pattern prescribes, and records the run.

It is the bridge between PATTERN1 (the decision) and the existing realizations:
  - EVALOPT1 (`evalopt.optimize`) — the real writer↔editor loop, used for the
    evaluator-optimizer pattern;
  - PLAN1 (`planning.plan` / `topological_order`) — decomposition into an ordered
    DAG, used for pipeline (fixed stages, in order) and orchestrator-worker (run
    each emergent subtask).
The executors/handlers are caller-provided DETERMINISTIC stubs — exactly as
EVALOPT1's generate/evaluate and Nona's executor stand in for a real model/agent/
sandbox that wraps later. Dispatch composes the PUBLIC apis of patterns / evalopt /
planning / model; it adds no core code.

Laws this module upholds (mirroring its siblings' discipline):
  - **DETERMINISTIC dispatch.** Selection is pure (PATTERN1's policy); the executors
    are deterministic stubs. Re-running `dispatch` on the same task with the same
    stubs yields the same pattern and the same output. No randomness, no ambient
    model call.
  - **Manual override is honored.** A caller may force a pattern via `override=`;
    the forced pattern is obeyed and executed (and the selector's would-be choice is
    still recorded as `overridden_from`, the provenance PATTERN1 already carries).
  - **No ambient authority.** A dispatch names + runs an architecture over
    caller-supplied stubs; it grants nothing. `capability.authorize` still gates any
    real effect a stub might one day perform.
  - **Ints, not floats.** Any numeric content (the step count) is an int. No float
    reaches the signed log (WEFT §4/§7).
  - **The pattern used + its outcome live on the Weft.** Every dispatch writes a
    `dispatch_run` Cell (chosen pattern + reason + outcome + step count) with a
    `dispatched` edge to the choice Cell PATTERN1 recorded — so the whole
    selection→execution is auditable and time-travelable.

Public `model`/`patterns`/`evalopt`/`planning`/`hashing` API only — no core edit.
"""
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc
from decima import patterns as P
from decima import evalopt as EO
from decima import planning as PL

DISPATCH_RUN = "dispatch_run"   # the Cell type recording one selection→execution
DISPATCHED = "dispatched"       # edge: dispatch_run → dispatched → the choice Cell


# ── per-pattern execution strategies ──────────────────────────────────────────
# Each strategy is a pure function of (k, task, choice, executors) → (output,
# steps). `steps` is a list of {step, output} records — the ordered trace of what
# ran. The executors dict carries the caller's deterministic stubs; each strategy
# documents the key(s) it reads and falls back to a sensible identity stub so a
# bare dispatch still runs (and stays deterministic).

def _exec(executors, key, default):
    """Fetch a caller stub by key, or a deterministic default. Keeps dispatch
    runnable with zero executors while letting a caller wire any strategy."""
    return (executors or {}).get(key, default)


def _single_agent(k, task, choice, executors):
    """single-agent-loop → run the whole task directly, in ONE step. The simplest
    shape: one agent, one loop, no coordination. `run(task)` is the caller's stub."""
    run = _exec(executors, "run", lambda t: f"ran:{t.name}")
    out = run(task)
    return out, [{"step": task.name, "output": out}]


def _pipeline(k, task, choice, executors):
    """pipeline → run a FIXED, ordered sequence of stages, each consuming the last.
    The stage order is composed via PLAN1: we plan the stages as a linear DAG and
    execute in topological order (so the audit shows the stages, in order, on the
    Weft). `stages` is a list of stage names; `stage(name, prev)` runs one."""
    stages = _exec(executors, "stages", ["ingest", "transform", "emit"])
    stage = _exec(executors, "stage", lambda name, prev: f"{name}({prev})")
    # Compose the fixed order through planning: a linear chain of plan_steps.
    specs = []
    for i, name in enumerate(stages):
        spec = {"key": name, "objective": f"stage {name}"}
        if i > 0:
            spec["depends_on"] = [stages[i - 1]]
        specs.append(spec)
    plan = PL.plan(k, f"pipeline:{task.name}", specs)
    order = PL.topological_order(k, plan["plan"])      # the stages, in fixed order
    id_to_key = {sid: key for key, sid in plan["steps"].items()}

    steps = []
    prev = None
    for sid in order:
        name = id_to_key[sid]
        prev = stage(name, prev)
        PL.mark_done(k, sid, result=str(prev))
        steps.append({"step": name, "output": prev})
    return prev, steps


def _orchestrator_worker(k, task, choice, executors):
    """orchestrator-worker → DECOMPOSE the task into subtasks (emergent at runtime),
    then run each. Decomposition is composed via PLAN1; each subtask runs through
    the caller's `worker` stub. `subtasks` is a list of subtask names (the
    orchestrator's decomposition); `worker(name)` runs one."""
    subtasks = _exec(executors, "subtasks", ["part-a", "part-b"])
    worker = _exec(executors, "worker", lambda name: f"did:{name}")
    specs = [{"key": name, "objective": f"subtask {name}"} for name in subtasks]
    plan = PL.plan(k, f"orchestrate:{task.name}", specs)
    id_to_key = {sid: key for key, sid in plan["steps"].items()}

    steps = []
    for sid in PL.topological_order(k, plan["plan"]):
        name = id_to_key[sid]
        out = worker(name)
        PL.mark_done(k, sid, result=str(out))
        steps.append({"step": name, "output": out})
    # The orchestrator synthesizes the workers' outputs into one result.
    synth = _exec(executors, "synthesize",
                  lambda parts: "synthesized:" + ",".join(str(p["output"]) for p in parts))
    return synth(steps), steps


def _evaluator_optimizer(k, task, choice, executors):
    """evaluator-optimizer → run the REAL EVALOPT1 loop: a writer drafts, an editor
    judges, the critique feeds back until the bar is cleared (or max_rounds). The
    output is evidence-gated by `evalopt.optimize`. `generate`/`evaluate` are the
    caller's stubs (defaults: a one-shot pass so a bare dispatch still terminates)."""
    generate = _exec(executors, "generate", lambda cand, crit: f"draft:{task.name}")
    evaluate = _exec(executors, "evaluate",
                     lambda cand: {"pass": True, "score": 100, "critique": "ok"})
    max_rounds = int(_exec(executors, "max_rounds", 5))
    res = EO.optimize(k, task.name, generate, evaluate, max_rounds=max_rounds)
    steps = [{"step": f"round-{h['round']}", "output": h["digest"],
              "score": h["score"], "pass": h["pass"]} for h in res["history"]]
    return res["output"], steps


def _router(k, task, choice, executors):
    """router → classify the request and dispatch it to a per-DOMAIN handler. The
    caller supplies `classify(task) → domain` and a `handlers` map of domain→stub;
    the matching handler runs (clean isolation, one handler per domain). Fails loud
    if the classified domain has no handler — a router with no route is a bug."""
    handlers = _exec(executors, "handlers", {})
    classify = _exec(executors, "classify", lambda t: t.name)
    domain = classify(task)
    if domain not in handlers:
        raise ValueError(
            f"router: no handler for domain {domain!r} (have: {sorted(handlers)})")
    out = handlers[domain](task)
    return out, [{"step": f"route:{domain}", "output": out}]


def _fallback(k, task, choice, executors):
    """A documented sensible fallback for the patterns without a bespoke executor
    (supervisor, hierarchical, swarm, network-mesh). These are multi-agent shapes
    whose real realization is a later phase; until then we run the task as a single
    bounded loop — the always-safe floor PATTERN1 itself names as the starting
    point — so a dispatch of any catalog pattern still produces a deterministic,
    recorded outcome rather than raising."""
    return _single_agent(k, task, choice, executors)


# Strategy registry: pattern name → executor. Catalog patterns without a bespoke
# strategy resolve to `_fallback` (documented above).
_STRATEGIES = {
    P.SINGLE_AGENT: _single_agent,
    P.PIPELINE: _pipeline,
    P.ORCHESTRATOR_WORKER: _orchestrator_worker,
    P.EVALUATOR_OPTIMIZER: _evaluator_optimizer,
    P.ROUTER: _router,
}


def strategy_for(pattern: str):
    """The execution strategy for a pattern name — a bespoke one if defined, else the
    documented fallback. Pure lookup; never raises on a registered catalog pattern."""
    return _STRATEGIES.get(pattern, _fallback)


def _coerce_task(task):
    """Accept either a patterns.Task or a bare name string (convenience)."""
    if isinstance(task, P.Task):
        return task
    return P.Task(nfc(str(task)))


def _record(k, task, choice, output, steps, author=None) -> str:
    """Write a `dispatch_run` Cell carrying the chosen pattern, its reason, the
    step count, and a digest of the outcome — plus a `dispatched` edge to the
    PATTERN1 choice Cell. Returns the run Cell id. The whole selection→execution is
    now auditable + time-travelable. The outcome is recorded as a content DIGEST so
    a large/None output never pins into the signed body (the EVALOPT1 discipline)."""
    author = author or k.decima_agent_id
    outcome_digest = content_id({"dispatch_outcome": output})
    cid = content_id({"dispatch_run": task.name, "pattern": choice.pattern,
                      "at": k.weft.head})
    assert_content(k.weft, author, cid, DISPATCH_RUN, {
        "task": task.name,
        "pattern": choice.pattern,
        "reason": choice.reason,
        "manual": bool(choice.manual),
        "overridden_from": choice.overridden_from,
        "steps": int(len(steps)),
        "outcome_digest": outcome_digest,
    })
    return cid, outcome_digest


# ── REAL execution: run the chosen pattern through the kernel's gated delegation ──
# DISPATCH1's `real=True` mode swaps the deterministic stubs for ACTUAL workers: a
# pattern's plan is run by `kernel.execute_plan` (the EXEC1 primitive), so every step
# is a real spawned worker with a downhill-attenuated grant, gated by the same spine
# (autonomy ladder, B4 governance, learned org policy, authorize/Morta). It grants no
# new authority — `execute_plan`/`_delegate` enforce every gate; dispatch only chooses
# the shape. `kernel.execute_plan` is the kernel's PUBLIC API (this is not a core edit).

def _real_steps(k, plan_id):
    """The ordered {step, output} trace of an EXECUTED plan — each step's `result`
    was written by `execute_plan` via `planning.mark_done`. Topological order so the
    trace reads in dependency order, matching the stub strategies' `steps` shape."""
    out = []
    for sid in PL.topological_order(k, plan_id):
        c = k.weave().get(sid)
        out.append({"step": c.content.get("key") or c.content.get("objective"),
                    "output": c.content.get("result")})
    return out


def _build_real_plan(k, task, choice, executors, author):
    """Shape a PLAN1 plan from the chosen pattern, for a DIRECT real dispatch (no
    externally-supplied plan). The pattern decides the DAG: PIPELINE → a linear chain
    (each stage depends on the prior), ORCHESTRATOR_WORKER → an independent fan-out,
    ROUTER → one routed step, anything else → a single bounded step. Step objectives
    are the stage/subtask names; `capability` is the held cap each worker is granted
    (default `shell`). Returns the plan id. Composes planning's PUBLIC API only."""
    cap = _exec(executors, "capability", "shell")
    pat = choice.pattern
    if pat == P.PIPELINE:
        stages = _exec(executors, "stages", ["ingest", "transform", "emit"])
        specs = []
        for i, name in enumerate(stages):
            spec = {"key": str(name), "objective": str(name), "capability": cap}
            if i > 0:
                spec["depends_on"] = [str(stages[i - 1])]
            specs.append(spec)
    elif pat == P.ORCHESTRATOR_WORKER:
        subs = _exec(executors, "subtasks", ["part-a", "part-b"])
        specs = [{"key": str(n), "objective": str(n), "capability": cap} for n in subs]
    elif pat == P.ROUTER:
        classify = _exec(executors, "classify", lambda t: t.name)
        routes = _exec(executors, "routes", {})
        domain = classify(task)
        specs = [{"key": f"route:{domain}", "objective": str(task.name),
                  "capability": str(routes.get(domain, cap))}]
    else:  # single-agent / fallback / any pattern without a bespoke real shape
        specs = [{"key": "do", "objective": str(task.name), "capability": cap}]
    return PL.plan(k, f"real:{pat}:{task.name}", specs, author=author)["plan"]


def _execute_real(k, task, choice, executors, plan, author):
    """Run the pattern FOR REAL via `kernel.execute_plan`. If `plan` is supplied (the
    live `say` path's brain decomposition), execute THAT plan — one plan, so there is
    no duplicate of the work the brain already decomposed. Otherwise build the plan
    from the chosen pattern. Returns (output, steps, lines, plan_id)."""
    author = author or k.decima_agent_id
    plan_id = plan if plan is not None else _build_real_plan(k, task, choice, executors, author)
    # execute_plan derives its own principal/author from the Decima agent cell.
    lines = k.execute_plan(plan_id, label="dispatch")
    st = PL.plan_status(k, plan_id)
    return f"executed:{st['done']}/{st['total']}", _real_steps(k, plan_id), lines, plan_id


def dispatch(k, task, *, override=None, executors=None, who="", why="", author=None,
             real=False, plan=None):
    """Select an architecture for `task` (or honor `override`), EXECUTE the task via
    that pattern's strategy, and RECORD the run on the Weft.

    `task` is a `patterns.Task` (or a bare name string). `override`, if given, names
    a catalog pattern to force — honored regardless of what the selector would pick;
    the selector's would-be choice is still recorded as `overridden_from` (and a
    manual override must name `who`). `executors` is a dict of caller-provided
    deterministic stubs keyed per strategy (see each `_*` strategy for its keys);
    omitted ⇒ deterministic identity defaults so a bare dispatch still runs.

    `real` (DISPATCH1): when True, the chosen pattern runs through the kernel's gated
    delegation (`kernel.execute_plan`) — REAL workers with downhill grants, not stubs.
    `plan`, if given (the live `say` path's brain decomposition), is the plan executed,
    so the work the brain already decomposed runs ONCE — no duplicate. `real=False`
    (default) is the unchanged deterministic-stub path. `EVALUATOR_OPTIMIZER` is already
    a real loop (`evalopt.optimize`), so it runs via its strategy in either mode.

    Returns {pattern, output, steps, choice, reason, run, real, executed, lines}:
      - pattern — the pattern actually executed (the override, if any).
      - output  — the strategy's result (e.g. evalopt's evidence-gated candidate, or
                  `executed:done/total` for a real run).
      - steps   — the ordered trace of what ran ([{step, output, ...}]).
      - choice  — the patterns.Choice (carries manual/overridden_from/who/why).
      - reason  — why this pattern (selector reason, or the override's).
      - run     — the `dispatch_run` Cell id recorded on the Weft.
      - real    — whether this run used real delegation.
      - executed — the plan id executed for real (None in stub mode / evalopt).
      - lines   — the execution transcript (real mode) for the caller to surface.

    Deterministic; the pattern + outcome are on the Weft. In stub mode no authority is
    exercised; in real mode every effect is gated by `execute_plan`/`authorize`."""
    task = _coerce_task(task)
    sel = P.make_selector()

    # 1. Choose the pattern — selector, or the honored manual override (PATTERN1
    #    records the choice Cell either way, with full provenance).
    if override is not None:
        if not who or not str(who).strip():
            who = "dispatch"   # a forced pattern still records WHO; default the caller
        choice, choice_cid = sel.override(
            k, task, override, who=who, why=why or "dispatch override", author=author)
    else:
        choice, choice_cid = sel.select_k(k, task, author=author)

    # 2. EXECUTE. Real mode runs the chosen pattern through gated delegation; the
    #    evaluator-optimizer is already a real loop, so it keeps its strategy. Stub
    #    mode (default) runs the deterministic strategy — the unchanged contract path.
    executed_plan, lines = None, []
    if real and choice.pattern != P.EVALUATOR_OPTIMIZER:
        output, steps, lines, executed_plan = _execute_real(
            k, task, choice, executors, plan, author)
    else:
        output, steps = strategy_for(choice.pattern)(k, task, choice, executors)

    # 3. RECORD the run + outcome, and link it to the choice Cell.
    run_cid, _digest = _record(k, task, choice, output, steps, author=author)
    assert_edge(k.weft, author or k.decima_agent_id, run_cid, DISPATCHED, choice_cid)

    return {
        "pattern": choice.pattern,
        "output": output,
        "steps": steps,
        "choice": choice,
        "reason": choice.reason,
        "run": run_cid,
        "real": bool(real and choice.pattern != P.EVALUATOR_OPTIMIZER),
        "executed": executed_plan,
        "lines": lines,
    }


def runs_on(k, task_name=None) -> list:
    """Fold the recorded `dispatch_run` Cells (optionally for one task), in
    appearance order. A pure read over the Weave — the audit trail of every
    selection→execution."""
    return [c for c in k.weave().of_type(DISPATCH_RUN)
            if not c.retracted and (task_name is None or c.content.get("task") == task_name)]
