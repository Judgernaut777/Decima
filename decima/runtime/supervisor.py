"""The durable supervisor: dispatch ready steps under a lease, idempotently (DEC-044).

`run_once` folds current state, reconciles readiness, and dispatches every ready step
through a bounded lease to an injected `runner` (a deterministic effect in tests; a real
isolated worker in Phase 5). Every transition and outcome is a Weft event, so a crash
between dispatch and receipt is recoverable and a re-dispatch of an already-succeeded step
is a no-op (idempotency by receipt) — replay executes no effect.

The runner is injected precisely so the scheduler/supervisor are testable without a live
model or real worker (handoff Phase 4 acceptance: "Scheduler behavior is testable without
a live model"). The supervisor never itself executes untrusted code (invariant 2.6).

Every dispatch is ONE attempt of a logical operation (WEFT §8.5): the attempt number is
monotone over everything already on the log, so a retry after an UNKNOWN reuses the
idempotency key and appends a NEW attempt's receipt instead of overwriting the stranded
one. A resting UNKNOWN is never re-dispatched on a hunch — only a reconciler's
``retry_authorized`` receipt (or an explicit ``retry_unknown``) reopens it (§8.3). Costs the
runner reports ride the receipt as integer ``CostItem`` lines (§8.1), never floats.
"""

from __future__ import annotations

from collections.abc import Callable

from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells, scheduler, seam
from decima.runtime.cells import StepStatus, StepView

# A runner takes a step view and returns {"status": SUCCEEDED|FAILED|UNKNOWN, ...}.
Runner = Callable[[StepView], dict]

_DEFAULT_LEASE_TTL = 100  # logical-time window a lease is valid for


def sum_costs(reports: list[dict]) -> dict[str, int]:
    """Total the integer costs of a pass's dispatch reports (``{resource: int}``)."""
    total: dict[str, int] = {}
    for report in reports:
        for resource, amount in (report.get("cost") or {}).items():
            total[resource] = total.get(resource, 0) + int(amount)
    return total


def dispatch_step(
    weft: Weft,
    author: str,
    weave: Weave,
    step_id: str,
    runner: Runner,
    *,
    now: int,
    lease_ttl: int = _DEFAULT_LEASE_TTL,
    cursor: seam.Cursor | None = None,
    retry_unknown: bool = False,
) -> dict:
    """Run one attempt of a step under a fresh lease, recording a receipt and the resulting
    status. Idempotent, and honest about what "already done" means (WEFT §8.2/§8.3):

      * a FINAL receipt (SUCCEEDED/FAILED/CANCELLED/COMPENSATED) for the step's idempotency
        key → return it WITHOUT re-running the effect;
      * a resting UNKNOWN → refuse UNLESS a reconciler authorized the retry
        (``retry_authorized`` on the receipt) or the caller passes ``retry_unknown``. When
        authorized, this runs a NEW attempt under the SAME idempotency key (§8.5) so the
        provenance of every attempt stays on the log.

    `cursor` threads the caller's seam (`runtime.seam`) into the ONE read this makes after
    the effect — re-reading the step so its terminal status is asserted over the freshest
    content. None folds from genesis, exactly as before. The effect itself still runs
    against the step as it was read BEFORE the lease (`cell`), so the runner sees the same
    input either way."""
    cell = weave.get(step_id)
    if cell is None:
        raise ValueError(f"no such step {step_id}")
    content = cell.content
    idem = content.get("idempotency_key", step_id)

    prior = cells.receipt_for_idempotency_key(weave, idem)
    if prior is not None:
        prior_status = str(prior.content.get("status"))
        if prior_status in cells.ReceiptStatus.FINAL:
            return {
                "step": step_id,
                "status": prior_status,
                "idempotent_hit": True,
                "attempt": cells.receipt_attempt(prior),
                "cost": cells.receipt_cost(prior),
            }
        if prior_status in cells.ReceiptStatus.IN_FLIGHT:
            return {
                "step": step_id,
                "status": prior_status,
                "idempotent_hit": True,
                "refused": "an attempt is already in flight under this idempotency key",
            }
        if not (retry_unknown or prior.content.get("retry_authorized") is True):
            return {
                "step": step_id,
                "status": prior_status,
                "idempotent_hit": True,
                "refused": "unresolved UNKNOWN — reconcile before retrying (WEFT §8.3)",
            }

    # A NEW physical attempt, numbered above everything on the log (never a reused number).
    attempt = cells.next_attempt(weave, step_id, idem)
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

    # Bound BEFORE the try so the exception path (a crashing runner) records a well-formed
    # receipt instead of raising UnboundLocalError over the top of the real failure.
    outputs: list[str] = []
    provider_ref: str | None = None
    error: object = None
    try:
        result = runner(StepView.of(cell))
        status = str(result.get("status", StepStatus.SUCCEEDED))
        if status not in cells.ReceiptStatus.ALL:
            # An unrecognized outcome is NOT a fabricated success (§8.3): it is UNKNOWN.
            result = {**result, "unrecognized_status": status}
            status = cells.ReceiptStatus.UNKNOWN
        cost = cells.cost_from_result(result)
        outputs = [str(o) for o in (result.get("output_cell_ids") or [])]
        ref = result.get("provider_ref")
        provider_ref = None if ref is None else str(ref)
        error = result.get("error")
        diagnostics = {k: v for k, v in result.items() if k not in cells.RESERVED_RESULT_KEYS}
    except Exception as exc:  # a runner crash is a FAILED attempt, never a supervisor crash
        status = StepStatus.FAILED
        cost = []
        diagnostics = {"error": type(exc).__name__}
        # Only the exception TYPE is recorded (its message is untrusted text); `at` is
        # logical time, so the receipt stays deterministic.
        error = {"code": "executor_exception", "message": type(exc).__name__, "at": int(now)}

    cells.record_receipt(
        weft,
        author,
        step_id=step_id,
        lease_id=lease_id,
        idempotency_key=idem,
        status=status,
        attempt=attempt,
        executor=content.get("assigned_agent_id") or author,
        effect_class=(content.get("required_capability_selector") or {}).get("effect_class")
        or content.get("effect_class"),
        provider_ref=provider_ref,
        output_cell_ids=outputs,
        cost=cost,
        error=error,
        diagnostics=diagnostics,
    )
    # transition the step to the status the receipt reports (ACCEPTED/RUNNING = still in
    # flight, so the step stays RUNNING and the crash-window logic keeps applying). This
    # read MUST see the events just appended (lease, RUNNING, receipt) — that is exactly
    # the tail the seam applies, so it does.
    fresh = seam.read(weft, cursor).get(step_id)
    cells.set_status(weft, author, fresh, cells.STEP_STATUS_OF_RECEIPT.get(status, status))
    return {
        "step": step_id,
        "status": status,
        "lease": lease_id,
        "attempt": attempt,
        "cost": cells.cost_summary(cost),
    }


def run_once(
    weft: Weft,
    author: str,
    plan_id: str,
    runner: Runner,
    *,
    now: int,
    use_seam: bool = True,
) -> dict:
    """One supervisor pass: reconcile readiness, then dispatch every ready step. Returns a
    report of the transitions and dispatches. Deterministic given the same fold + runner.

    The pass folds ONCE and then reads through the checkpoint seam (`runtime.seam`) instead
    of re-folding the whole log per ready step; `use_seam=False` keeps the un-seamed
    genesis-fold path, which the equivalence tests A/B against."""
    entry = Weave.fold(weft)
    cursor = seam.Cursor(entry) if use_seam else None
    scheduler.reconcile_readiness(weft, author, entry, plan_id)
    dispatched = []
    for step in scheduler.ready_steps(seam.read(weft, cursor), plan_id):
        dispatched.append(
            dispatch_step(
                weft, author, seam.read(weft, cursor), step.id, runner, now=now, cursor=cursor
            )
        )
    return {
        "plan_id": plan_id,
        "dispatched": dispatched,
        "cost": sum_costs(dispatched),
        "complete": scheduler.plan_is_complete(seam.read(weft, cursor), plan_id),
    }


def run_to_completion(
    weft: Weft,
    author: str,
    plan_id: str,
    runner: Runner,
    *,
    now: int = 0,
    max_rounds: int = 100,
    use_seam: bool = True,
) -> dict:
    """Drive a plan to completion (or until no progress / round cap). Each round advances
    the logical clock by one so leases get distinct frontiers."""
    rounds = 0
    cost: dict[str, int] = {}
    while rounds < max_rounds:
        report = run_once(weft, author, plan_id, runner, now=now + rounds, use_seam=use_seam)
        rounds += 1
        for resource, amount in report["cost"].items():
            cost[resource] = cost.get(resource, 0) + amount
        if report["complete"]:
            return {"plan_id": plan_id, "rounds": rounds, "complete": True, "cost": cost}
        if not report["dispatched"]:
            return {
                "plan_id": plan_id,
                "rounds": rounds,
                "complete": False,
                "stalled": True,
                "cost": cost,
            }
    return {
        "plan_id": plan_id,
        "rounds": rounds,
        "complete": False,
        "stalled": False,
        "cost": cost,
    }
