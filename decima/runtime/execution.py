"""Bounded plan-execution composition over the EXISTING runtime (planning lane).

This module composes the pieces that already exist — ``scheduler`` (readiness from the
fold), ``supervisor`` (leased, idempotent dispatch), ``budgets`` (durable pre-dispatch
gate) and ``cells`` (durable status transitions) — into ONE bounded execution pass a
command handler can drive. It adds no new authority and no new canonical store: every
decision it makes is either a pure read of the fold or a recorded status transition
through the kernel's content path, so a restarted process folds the log and continues
exactly where the last pass stopped (the fold IS the state).

Fail-closed rules this composition enforces:

  * a plan that is not ACTIVE dispatches NOTHING (pause is server-enforced, not a UI
    convention);
  * a step whose assigned agent is terminal (TERMINATED / COMPLETED / FAILED) is
    durably CANCELLED, never dispatched — a terminated agent's remaining work is
    refused, not orphaned;
  * a step with a terminally-failed dependency (FAILED / CANCELLED / unknown id) is
    durably CANCELLED, transitively — so "the remaining valid work" can complete and
    the plan reaches a terminal fold instead of stalling forever;
  * dispatch goes through ``budgets.guarded_dispatch_step``: an exhausted agent is
    BUDGET_BLOCKED durably and its step is refused BEFORE any effect runs.

The runner is injected (same seam as the supervisor): in this milestone it is a bounded
deterministic operation in trusted code. Untrusted code never runs here (invariant 7).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Weft
from decima.runtime import budgets, cells, scheduler, seam
from decima.runtime.cells import AgentStatus, PlanStatus, StepStatus

# Statuses this module never overrides on an agent: terminal ones and the durable
# budget block (only an operator raising the limit may clear that).
_FROZEN_AGENT = frozenset(AgentStatus.TERMINAL) | {budgets.BUDGET_BLOCKED}

_DEAD_STEP = frozenset({StepStatus.FAILED, StepStatus.CANCELLED})


def agents_of_plan(weave: Weave, plan_id: str) -> list[Cell]:
    """The Agent Cells minted for a plan (the planning service stamps ``plan_id``
    into the agent content when it mints them). Pure read; deterministic order."""
    out = [a for a in weave.of_type(cells.AGENT) if a.content.get("plan_id") == plan_id]
    return sorted(out, key=lambda a: (a.content.get("parent_agent_id") or "", a.id))


def _plan_steps(weave: Weave, plan_id: str) -> dict[str, Cell]:
    return {c.id: c for c in weave.of_type(cells.PLAN_STEP) if c.content.get("plan_id") == plan_id}


def cancel_unrunnable_steps(
    weft: Weft, author: str, plan_id: str, *, cursor: seam.Cursor | None = None
) -> list[str]:
    """Durably CANCEL every step that can never run: its assigned agent is terminal,
    or a dependency is FAILED/CANCELLED/missing — propagated to a fixpoint so the
    whole dead subtree fails closed in one pass. Idempotent; dispatches nothing.

    `cursor` threads the caller's seam (`runtime.seam`): the read below then advances the
    pass's existing fold instead of re-folding the whole log. None folds from genesis,
    exactly as before. The decision itself is unchanged — a pure read of a fold that
    includes every event appended so far."""
    weave = seam.read(weft, cursor)
    steps = _plan_steps(weave, plan_id)
    status = {sid: c.content.get("status") for sid, c in steps.items()}

    def _agent_terminal(cell: Cell) -> bool:
        aid = cell.content.get("assigned_agent_id")
        if not aid:
            return False
        agent = weave.get(aid)
        if agent is None:
            return True  # fail closed: an unknown agent can run nothing
        # A TERMINATEd agent's cell is retracted on the Weft; either signal bounds it.
        return bool(getattr(agent, "retracted", False)) or (
            agent.content.get("status") in AgentStatus.TERMINAL
        )

    cancelled: list[str] = []
    changed = True
    while changed:
        changed = False
        for sid, cell in steps.items():
            if status[sid] in StepStatus.TERMINAL:
                continue
            deps = cell.content.get("dependency_ids", []) or []
            dead_dep = any(d not in status or status[d] in _DEAD_STEP for d in deps)
            if dead_dep or _agent_terminal(cell):
                status[sid] = StepStatus.CANCELLED
                cancelled.append(sid)
                changed = True
    for sid in cancelled:
        cells.set_status(weft, author, weave.get(sid), StepStatus.CANCELLED)
    return cancelled


def drive_plan_once(
    weft: Weft,
    author: str,
    plan_id: str,
    runner: Callable,
    *,
    now: int,
    cost_of: Callable | None = None,
    dispatchable: Callable | None = None,
    use_seam: bool = True,
) -> dict:
    """ONE bounded execution pass: cancel dead work, reconcile readiness, then dispatch
    every ready step under a lease with the budget gate in front. Only an ACTIVE plan
    dispatches — a PAUSED/DRAFT/terminal plan returns with ``dispatched == []``. When
    every step is terminal the plan is durably transitioned to COMPLETED. Returns a
    JSON-safe report; deterministic given the same fold + runner.

    ``dispatchable`` (a pure predicate over the ready ``StepView`` and its Cell) lets the
    caller bound WHICH steps this runner may execute — a step outside the bound (e.g. a
    manually created task with no known capability) is left untouched for its own flow,
    never auto-completed here.

    ``use_seam`` routes this pass's reads through the checkpoint seam (the default: fold
    once, then advance — see `runtime.seam`). ``False`` folds from GENESIS at every read,
    the un-seamed path, kept so the equivalence is A/B-tested on the SAME code against a
    live oracle rather than a hand-copied one (tests/runtime/test_execution.py). Both must
    produce the identical report and the identical resulting log: the seam changes what a
    pass COSTS, never what it decides."""
    weave = Weave.fold(weft)
    plan = weave.get(plan_id)
    if plan is None or plan.type != cells.PLAN:
        raise ValueError(f"no such plan {plan_id}")
    report: dict = {
        "plan_id": plan_id,
        "status": plan.content.get("status"),
        "dispatched": [],
        "refused": [],
        "cancelled_steps": [],
        "complete": scheduler.plan_is_complete(weave, plan_id),
    }
    if plan.content.get("status") != PlanStatus.ACTIVE:
        return report

    # ── the checkpoint seam (P4.1 / IFB1) ────────────────────────────────────────
    # We folded ONCE above. From here the pass reads through a CURSOR over that same
    # fold: each read applies only the tail appended since the previous one. Without the
    # seam this pass folds the WHOLE log at least five times — in `cancel_unrunnable_steps`,
    # for readiness, for the ready set, once (twice on a refusal) per step inside
    # `guarded_dispatch_step`, again inside `supervisor.dispatch_step`, and once at the
    # end — each fold re-reading and re-verifying every historical signature. With the
    # seam the pass pays for the log once and for each new event once.
    #
    # The cursor is threaded INTO the helpers, because that is where the per-step folds
    # actually live. It is a within-pass seam and nothing else: no cross-call cache, no
    # second copy of the state (see `runtime.seam` for why a per-boundary
    # `Weave.checkpoint()` is the wrong instrument here), and a fresh pass folds from
    # genesis again — the fold is still the state.
    cursor = seam.Cursor(weave) if use_seam else None

    report["cancelled_steps"] = cancel_unrunnable_steps(weft, author, plan_id, cursor=cursor)
    scheduler.reconcile_readiness(weft, author, seam.read(weft, cursor), plan_id)
    ready_fold = seam.read(weft, cursor)
    ready = scheduler.ready_steps(ready_fold, plan_id)
    if dispatchable is not None:
        # The bound is evaluated HERE, against the pre-dispatch fold, for the same reason
        # the ready SET is snapshotted here: neither may shift under a pass. (The predicate
        # is contractually pure, so this is the same answer it gave when it was called just
        # before each dispatch — but now it cannot depend on a fold that has moved on.)
        ready = [s for s in ready if dispatchable(s, ready_fold.get(s.id))]
    for step in ready:
        cost = cost_of(step) if cost_of is not None else None
        out = budgets.guarded_dispatch_step(
            weft, author, step.id, runner, now=now, cost=cost, cursor=cursor
        )
        (report["dispatched"] if out.get("dispatched") else report["refused"]).append(out)

    final = seam.read(weft, cursor)
    if scheduler.plan_is_complete(final, plan_id):
        fresh = final.get(plan_id)
        if fresh is None:
            raise ValueError(f"no such plan {plan_id}")
        if fresh.content.get("status") not in PlanStatus.TERMINAL:
            cells.set_status(weft, author, fresh, PlanStatus.COMPLETED)
        report["complete"] = True
        report["status"] = PlanStatus.COMPLETED
    return report


def sync_agent_statuses(weft: Weft, author: str, plan_id: str) -> list[dict]:
    """Derive each plan agent's durable status from the fold (its steps; a parent's
    children) and record any change as a status transition. TERMINATED and
    BUDGET_BLOCKED are never overridden here — termination/blocking is authoritative.
    Returns the transitions made (JSON-safe)."""
    weave = Weave.fold(weft)
    agents = agents_of_plan(weave, plan_id)
    steps_by_agent: dict[str, list[Cell]] = {}
    for c in _plan_steps(weave, plan_id).values():
        aid = c.content.get("assigned_agent_id")
        if aid:
            steps_by_agent.setdefault(aid, []).append(c)
    receipted = {r.content.get("step_id") for r in weave.of_type(cells.RECEIPT)}

    desired: dict[str, str] = {}
    for a in agents:  # leaf agents first (parents derive from these below)
        current = a.content.get("status")
        if current in _FROZEN_AGENT:
            desired[a.id] = current
            continue
        steps = steps_by_agent.get(a.id, [])
        if not steps:
            desired[a.id] = cast(str, current)  # parent (no steps) — resolved in the next loop
            continue
        st = [s.content.get("status") for s in steps]
        if all(s in StepStatus.TERMINAL for s in st):
            good = all(s == StepStatus.SUCCEEDED for s in st)
            desired[a.id] = AgentStatus.COMPLETED if good else AgentStatus.FAILED
        elif any(s.id in receipted for s in steps) or StepStatus.RUNNING in st:
            desired[a.id] = AgentStatus.RUNNING
        else:
            desired[a.id] = cast(str, current)

    children_of: dict[str, list[str]] = {}
    for a in agents:
        parent = a.content.get("parent_agent_id")
        if parent:
            children_of.setdefault(parent, []).append(a.id)
    terminalish = frozenset(AgentStatus.TERMINAL)
    for a in agents:
        current = a.content.get("status")
        if current in _FROZEN_AGENT or steps_by_agent.get(a.id):
            continue
        kids = [desired[k] for k in children_of.get(a.id, [])]
        if not kids:
            continue
        if all(k in terminalish for k in kids):
            any_done = AgentStatus.COMPLETED in kids
            desired[a.id] = AgentStatus.COMPLETED if any_done else AgentStatus.FAILED
        elif budgets.BUDGET_BLOCKED in kids:
            desired[a.id] = AgentStatus.WAITING
        elif any(k != AgentStatus.CREATED for k in kids):
            desired[a.id] = AgentStatus.RUNNING

    changes: list[dict] = []
    for a in agents:
        want = desired.get(a.id)
        if want and want != a.content.get("status"):
            cells.set_status(weft, author, a, want)
            changes.append({"agent": a.id, "from": a.content.get("status"), "to": want})
    return changes
