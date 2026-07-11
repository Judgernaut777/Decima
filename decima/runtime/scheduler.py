"""The scheduler's decision core: which steps are ready, from the fold (DEC-043).

The scheduler is a PURE projection over the Weave: given the current fold, it computes
which plan steps can run (all dependencies SUCCEEDED, not itself terminal or running) and
which are blocked. It records readiness as durable status transitions — it never holds the
decision only in memory. Dispatch under a lease, budgets, and crash recovery build on this
core (subsequent DEC-044..048); this module is the deterministic "what's runnable now".
"""

from __future__ import annotations

from decima.runtime.cells import StepStatus, StepView, set_status, steps_of_plan


def _deps_satisfied(step: StepView, by_id: dict[str, StepView]) -> bool:
    """True iff every dependency of `step` exists and has SUCCEEDED."""
    for dep in step.dependency_ids:
        d = by_id.get(dep)
        if d is None or d.status != StepStatus.SUCCEEDED:
            return False
    return True


def ready_steps(weave: object, plan_id: str) -> list[StepView]:
    """Steps whose dependencies are all SUCCEEDED and that are not already terminal,
    running, or waiting on approval — i.e. the steps a dispatcher may launch now.
    Deterministic order: by step id."""
    steps = steps_of_plan(weave, plan_id)
    by_id = {s.id: s for s in steps}
    runnable = {StepStatus.PENDING, StepStatus.BLOCKED, StepStatus.READY}
    out = [s for s in steps if s.status in runnable and _deps_satisfied(s, by_id)]
    return sorted(out, key=lambda s: s.id)


def blocked_steps(weave: object, plan_id: str) -> list[StepView]:
    """Steps not yet runnable because a dependency has not SUCCEEDED (and is not itself
    failed/cancelled — those make the step permanently unrunnable, surfaced separately)."""
    steps = steps_of_plan(weave, plan_id)
    by_id = {s.id: s for s in steps}
    runnable = {StepStatus.PENDING, StepStatus.BLOCKED, StepStatus.READY}
    return sorted(
        [s for s in steps if s.status in runnable and not _deps_satisfied(s, by_id)],
        key=lambda s: s.id,
    )


def plan_is_complete(weave: object, plan_id: str) -> bool:
    """True iff every step of the plan is in a terminal status."""
    steps = steps_of_plan(weave, plan_id)
    return bool(steps) and all(s.status in StepStatus.TERMINAL for s in steps)


def reconcile_readiness(weft: object, author: str, weave: object, plan_id: str) -> dict:
    """Durably transition each PENDING/BLOCKED step to READY (deps met) or BLOCKED (deps
    outstanding), so the persisted status reflects the fold. Returns the transitions made.
    Idempotent: a step already in the correct status is not re-asserted."""
    steps = steps_of_plan(weave, plan_id)
    by_id = {s.id: s for s in steps}
    transitions: list[dict] = []
    for s in steps:
        if s.status not in (StepStatus.PENDING, StepStatus.BLOCKED):
            continue
        target = StepStatus.READY if _deps_satisfied(s, by_id) else StepStatus.BLOCKED
        if s.status != target:
            set_status(weft, author, weave.get(s.id), target)
            transitions.append({"step": s.id, "from": s.status, "to": target})
    return {"plan_id": plan_id, "transitions": transitions}
