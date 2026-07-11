"""The durable supervisor: dispatch ready steps under a lease, idempotently (DEC-044).

`run_once` folds current state, reconciles readiness, and dispatches every ready step
through a bounded lease to an injected `runner` (a deterministic effect in tests; a real
isolated worker in Phase 5). Every transition and outcome is a Weft event, so a crash
between dispatch and receipt is recoverable and a re-dispatch of an already-succeeded step
is a no-op (idempotency by receipt) — replay executes no effect.

The runner is injected precisely so the scheduler/supervisor are testable without a live
model or real worker (handoff Phase 4 acceptance: "Scheduler behavior is testable without
a live model"). The supervisor never itself executes untrusted code (invariant 2.6).
"""

from __future__ import annotations

from collections.abc import Callable

from decima.kernel.weave import Weave
from decima.runtime import cells, scheduler
from decima.runtime.cells import StepStatus, StepView

# A runner takes a step view and returns {"status": SUCCEEDED|FAILED|UNKNOWN, ...}.
Runner = Callable[[StepView], dict]

_DEFAULT_LEASE_TTL = 100  # logical-time window a lease is valid for


def dispatch_step(
    weft: object,
    author: str,
    weave: object,
    step_id: str,
    runner: Runner,
    *,
    now: int,
    lease_ttl: int = _DEFAULT_LEASE_TTL,
) -> dict:
    """Run one attempt of a step under a fresh lease, recording a receipt and the terminal
    status. Idempotent: if a terminal receipt already exists for the step's idempotency
    key, return it WITHOUT re-running the effect."""
    cell = weave.get(step_id)
    if cell is None:
        raise ValueError(f"no such step {step_id}")
    content = cell.content
    idem = content.get("idempotency_key", step_id)

    prior = cells.receipt_for_idempotency_key(weave, idem)
    if prior is not None:
        return {"step": step_id, "status": prior.content["status"], "idempotent_hit": True}

    attempt = int(content.get("attempt", 0)) + 1
    lease_id = cells.create_lease(
        weft,
        author,
        step_id=step_id,
        worker=content.get("assigned_agent_id") or author,
        capability_ids=list((content.get("required_capability_selector") or {}).get("grants", [])),
        issued_frontier=now,
        expiry=now + lease_ttl,
        attempt=attempt,
        idempotency_key=idem,
    )
    # mark RUNNING (durably) before invoking the effect — a crash here is recoverable.
    running = dict(content)
    running["status"] = StepStatus.RUNNING
    running["attempt"] = attempt
    cells.assert_content(weft, author, step_id, cells.PLAN_STEP, running)

    try:
        result = runner(StepView.of(cell))
        status = result.get("status", StepStatus.SUCCEEDED)
        diagnostics = {k: v for k, v in result.items() if k != "status"}
    except Exception as exc:  # a runner crash is a FAILED attempt, never a supervisor crash
        status = StepStatus.FAILED
        diagnostics = {"error": type(exc).__name__}

    cells.record_receipt(
        weft,
        author,
        step_id=step_id,
        lease_id=lease_id,
        idempotency_key=idem,
        status=status,
        diagnostics=diagnostics,
    )
    # transition the step to its terminal (or UNKNOWN) status from the receipt.
    fresh = Weave.fold(weft).get(step_id)
    cells.set_status(weft, author, fresh, status)
    return {"step": step_id, "status": status, "lease": lease_id, "attempt": attempt}


def run_once(
    weft: object,
    author: str,
    plan_id: str,
    runner: Runner,
    *,
    now: int,
) -> dict:
    """One supervisor pass: reconcile readiness, then dispatch every ready step. Returns a
    report of the transitions and dispatches. Deterministic given the same fold + runner."""
    scheduler.reconcile_readiness(weft, author, Weave.fold(weft), plan_id)
    weave = Weave.fold(weft)
    dispatched = []
    for step in scheduler.ready_steps(weave, plan_id):
        dispatched.append(dispatch_step(weft, author, Weave.fold(weft), step.id, runner, now=now))
    return {
        "plan_id": plan_id,
        "dispatched": dispatched,
        "complete": scheduler.plan_is_complete(Weave.fold(weft), plan_id),
    }


def run_to_completion(
    weft: object,
    author: str,
    plan_id: str,
    runner: Runner,
    *,
    now: int = 0,
    max_rounds: int = 100,
) -> dict:
    """Drive a plan to completion (or until no progress / round cap). Each round advances
    the logical clock by one so leases get distinct frontiers."""
    rounds = 0
    while rounds < max_rounds:
        report = run_once(weft, author, plan_id, runner, now=now + rounds)
        rounds += 1
        if report["complete"]:
            return {"plan_id": plan_id, "rounds": rounds, "complete": True}
        if not report["dispatched"]:
            return {"plan_id": plan_id, "rounds": rounds, "complete": False, "stalled": True}
    return {"plan_id": plan_id, "rounds": rounds, "complete": False, "stalled": False}
