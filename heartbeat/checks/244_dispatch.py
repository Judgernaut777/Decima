"""DISPATCH1 — the pattern dispatcher: selection → EXECUTION, made LIVE.

PATTERN1 (`patterns.py`) *chooses* an agentic architecture for a task; DISPATCH1
(`dispatch.py`) *runs* the chosen one. This check proves the choice is no longer
inert — it actually executes via the matching strategy and records the run:

  - a QUALITY-critical task actually RUNS the real evaluator-optimizer loop
    (`evalopt.optimize`) — drafts, gets critiqued, improves, accepts on evidence;
  - a FIXED-stage task runs as a pipeline — the stages execute IN ORDER (composed
    through planning), each consuming the last;
  - a SIMPLE task runs single-agent — one step, run directly;
  - a DOMAIN-dispatch task ROUTES to the right per-domain handler (and the wrong
    handler is never called);
  - the chosen pattern + outcome are RECORDED on the Weft (`dispatch_run` Cell +
    `dispatched` edge to the PATTERN1 choice);
  - a MANUAL override is HONORED — the forced pattern runs, not the selector's pick.

Contract: run(k, line). Fail loud.
"""
from decima import dispatch as D
from decima import patterns as P


def run(k, line):
    line("\n== PATTERN DISPATCHER (selection → execution, live) — DISPATCH1 ==")

    # 1. QUALITY-critical → actually RUNS the real evaluator-optimizer loop. ──────
    # A writer that needs a keyword the editor demands: round 1 fails, the critique
    # teaches it, round 2 passes. Dispatch must select evaluator-optimizer AND run
    # evalopt.optimize — the output is the evidence-gated accepted candidate.
    def generate(cand, crit):
        return "brief [cited]" if crit and "missing:cite" in crit else "brief"

    def evaluate(cand):
        if "[cited]" in cand:
            return {"pass": True, "score": 95, "critique": "clears the bar"}
        return {"pass": False, "score": 40, "critique": "weak — missing:cite"}

    q = P.Task("legal brief", quality_critical=True)
    rq = D.dispatch(k, q, executors={"generate": generate, "evaluate": evaluate})
    assert rq["pattern"] == P.EVALUATOR_OPTIMIZER, rq["pattern"]
    assert rq["output"] == "brief [cited]", rq            # evidence-gated accept
    assert len(rq["steps"]) == 2, rq["steps"]             # failed r1, passed r2
    assert [s["score"] for s in rq["steps"]] == [40, 95], rq["steps"]
    assert rq["steps"][-1]["pass"] is True, rq["steps"]
    line(f"  quality-crit     → {rq['pattern']}: ran evalopt {len(rq['steps'])} rounds "
         f"(40→95, PASS) → {rq['output']!r} (evidence-gated ✓)")

    # 2. FIXED-stage → pipeline; the stages run IN ORDER, each consuming the last. ─
    order = []

    def stage(name, prev):
        order.append(name)
        return f"{name}({prev})" if prev is not None else name

    stages = ["extract", "clean", "load"]
    fx = P.Task("etl run", fixed_stages=True)
    rfx = D.dispatch(k, fx, executors={"stages": stages, "stage": stage})
    assert rfx["pattern"] == P.PIPELINE, rfx["pattern"]
    assert order == stages, order                          # executed in FIXED order
    assert [s["step"] for s in rfx["steps"]] == stages, rfx["steps"]
    # Each stage genuinely consumed the previous one (nesting proves the chain).
    assert rfx["output"] == "load(clean(extract))", rfx["output"]
    line(f"  fixed-stages     → {rfx['pattern']}: stages ran in order {order} "
         f"→ {rfx['output']!r} (each consumes the last ✓)")

    # 3. SIMPLE → single-agent: run directly, ONE step. ─────────────────────────
    rs = D.dispatch(k, P.Task("echo a date"),
                    executors={"run": lambda t: f"echoed:{t.name}"})
    assert rs["pattern"] == P.SINGLE_AGENT, rs["pattern"]
    assert len(rs["steps"]) == 1, rs["steps"]              # one loop, one step
    assert rs["output"] == "echoed:echo a date", rs["output"]
    line(f"  simple           → {rs['pattern']}: one step → {rs['output']!r}")

    # 4. DOMAIN-dispatch → router: routes to the RIGHT handler; wrong one never runs.
    called = []
    handlers = {
        "billing": lambda t: called.append("billing") or "billing-handled",
        "support": lambda t: called.append("support") or "support-handled",
    }
    rt = P.Task("a billing question", domain_dispatch=True)
    rr = D.dispatch(k, rt, executors={
        "classify": lambda t: "billing", "handlers": handlers})
    assert rr["pattern"] == P.ROUTER, rr["pattern"]
    assert rr["output"] == "billing-handled", rr["output"]
    assert called == ["billing"], called                   # ONLY the right handler ran
    assert "support" not in called, called
    line(f"  domain-dispatch  → {rr['pattern']}: routed to 'billing' handler "
         f"(support never called ✓) → {rr['output']!r}")

    # 5. The chosen pattern + outcome are RECORDED on the Weft. ──────────────────
    for res, task_name, pat in (
        (rq, "legal brief", P.EVALUATOR_OPTIMIZER),
        (rfx, "etl run", P.PIPELINE),
        (rs, "echo a date", P.SINGLE_AGENT),
        (rr, "a billing question", P.ROUTER),
    ):
        cell = k.weave().get(res["run"])
        assert cell is not None and cell.type == D.DISPATCH_RUN, cell
        assert cell.content["pattern"] == pat, cell.content
        assert cell.content["task"] == task_name, cell.content
        assert isinstance(cell.content["steps"], int), cell.content   # ints, not floats
        assert cell.content["steps"] == len(res["steps"]), cell.content
        # A `dispatched` edge links the run to the PATTERN1 choice Cell.
        edge = k.weave().edges_from(res["run"], D.DISPATCHED)
        assert edge, ("no dispatched edge", res["run"])
        choice_cell = k.weave().get(edge[0]["dst"])
        assert choice_cell is not None and choice_cell.type == P.PATTERN_CHOICE, choice_cell
        assert choice_cell.content["pattern"] == pat, choice_cell.content
    trail = D.runs_on(k)
    assert len(trail) >= 4, len(trail)
    line(f"  recorded {len(trail)} dispatch_run Cells on the Weft "
         f"(pattern + outcome + steps, dispatched→choice ✓)")

    # 6. A MANUAL override is HONORED — the forced pattern runs, not the pick. ────
    # The selector would pick evaluator-optimizer for this quality-critical task, but
    # the operator forces single-agent; dispatch must RUN single-agent.
    ov = D.dispatch(k, P.Task("urgent memo", quality_critical=True),
                    override=P.SINGLE_AGENT, who="alice", why="ship it now, skip the loop",
                    executors={"run": lambda t: f"shipped:{t.name}"})
    assert ov["pattern"] == P.SINGLE_AGENT, ov["pattern"]   # forced pattern executed
    assert ov["choice"].manual is True, ov["choice"]
    assert ov["choice"].overridden_from == P.EVALUATOR_OPTIMIZER, ov["choice"]
    assert ov["output"] == "shipped:urgent memo", ov["output"]
    ocell = k.weave().get(ov["run"])
    assert ocell.content["manual"] is True, ocell.content
    assert ocell.content["overridden_from"] == P.EVALUATOR_OPTIMIZER, ocell.content
    line(f"  manual override  → {ov['pattern']} (selector wanted "
         f"{ov['choice'].overridden_from}; honored + recorded ✓)")

    # 7. Deterministic: re-dispatch the simple task → same pattern + same output. ─
    again = D.dispatch(k, P.Task("echo a date"),
                       executors={"run": lambda t: f"echoed:{t.name}"})
    assert (again["pattern"], again["output"]) == (rs["pattern"], rs["output"]), again
    line("  deterministic: re-dispatch yields the same pattern + output ✓")

    line("  → PATTERN1's choice is now LIVE: the dispatcher selects an architecture "
         "and EXECUTES it — evalopt loop for quality, ordered pipeline for fixed "
         "stages, router to the right handler — every run + outcome on the Weft.")
