"""Model-planned agents service — OWNED BY THE PLANNING LANE (Path A).

This module is the ONLY backend file the planning lane edits (besides its own screen
``js/screens/plans.js``, its tests, and runtime glue). The shared contracts live in
``contracts.py``; the routes/commands/events are already wired:

  commands  RequestPlanProposal   → :func:`request_plan_proposal`
            AcceptPlanProposal    → :func:`accept_plan_proposal`
            StartPlanExecution    → :func:`start_plan_execution`
            ResumePlan            → :func:`resume_plan`
            CancelPlan            → :func:`cancel_plan`
            (StartPlan / PausePlan already exist in ``commands.py``;
             TerminateAgent exists and stays approval-gated.)
  readers   GET /api/v1/plans/proposals → :func:`list_plan_proposals`
            GET /api/v1/agents/runs     → :func:`list_agent_run_summaries`
  events    ``plan.* / step.* / agent.*`` via ``svc.bus.emit``

Implementation rules (the lane's obligations):
  * A model PROPOSES a plan (``contracts.PlanProposal``) — inert DATA. Deterministic
    code validates the shape (``ProposedPlanStep.from_dict``); ONLY an explicit
    AcceptPlanProposal mints durable Plan/Step Cells via ``decima.runtime.cells``
    (invariant 4: models never get authority; acceptance is the human decision).
  * Routing goes through ``decima.models.routing`` from the request's ``task_spec()``
    and the decision is RECORDED (``routing.record``); sensitive stays local-only.
  * Execution drives the EXISTING scheduler/supervisor (``decima.runtime``); budgets
    are ints; no wall-clock in recorded content (invariant 6).
  * Return ``CommandResult`` from commands, ``{"items": [...]}`` dicts from readers.
"""

from __future__ import annotations

from decima.services.api.contracts import NOT_IMPLEMENTED, CommandError


def request_plan_proposal(svc: object, args: dict) -> object:
    """Ask a model to PROPOSE a plan for an objective (no durable plan yet).

    OWNER: planning lane. Parse with ``contracts.PlanProposalRequest.from_args``,
    route via ``task_spec()``, validate the model output deterministically, record
    the proposal, emit ``plan.proposal_requested`` / ``plan.proposal_ready``."""
    raise CommandError(
        NOT_IMPLEMENTED, "RequestPlanProposal is not implemented yet (planning lane)",
        http_status=501,
    )


def accept_plan_proposal(svc: object, args: dict) -> object:
    """Turn an accepted proposal into a durable Plan + Steps (the human decision).

    OWNER: planning lane. Mint via ``runtime.cells.create_plan/create_step``, return
    a ``contracts.PlanAcceptance.as_dict()`` payload, emit ``plan.accepted``."""
    raise CommandError(
        NOT_IMPLEMENTED, "AcceptPlanProposal is not implemented yet (planning lane)",
        http_status=501,
    )


def start_plan_execution(svc: object, args: dict) -> object:
    """Start executing an accepted plan through the existing runtime.

    OWNER: planning lane. Compose with the existing StartPlan status transition
    (``svc.execute("StartPlan", ...)`` is the honest mapping for the status part),
    then drive scheduling; emit ``plan.execution_started`` / ``step.*``."""
    raise CommandError(
        NOT_IMPLEMENTED, "StartPlanExecution is not implemented yet (planning lane)",
        http_status=501,
    )


def resume_plan(svc: object, args: dict) -> object:
    """Resume a PAUSED plan (PAUSED → ACTIVE and re-drive pending steps).

    OWNER: planning lane. Emit ``plan.resumed``."""
    raise CommandError(
        NOT_IMPLEMENTED, "ResumePlan is not implemented yet (planning lane)",
        http_status=501,
    )


def cancel_plan(svc: object, args: dict) -> object:
    """Cancel a plan (terminal CANCELLED; running steps wind down fail-closed).

    OWNER: planning lane. Emit ``plan.cancelled`` / ``step.cancelled``."""
    raise CommandError(
        NOT_IMPLEMENTED, "CancelPlan is not implemented yet (planning lane)",
        http_status=501,
    )


def list_plan_proposals(app: object, query: dict) -> dict:
    """Reader: recorded plan proposals (``contracts.PlanProposal`` shapes), newest
    first — ``{"items": [...]}``.

    OWNER: planning lane."""
    raise CommandError(
        NOT_IMPLEMENTED, "plan proposals reader is not implemented yet (planning lane)",
        http_status=501,
    )


def list_agent_run_summaries(app: object, query: dict) -> dict:
    """Reader: ``contracts.AgentRunSummary`` shapes for the agents on the Weft —
    ``{"items": [...]}``.

    OWNER: planning lane."""
    raise CommandError(
        NOT_IMPLEMENTED, "agent run summaries reader is not implemented yet (planning lane)",
        http_status=501,
    )


# Reader dispatch (target name in routes.py → callable). The app consults this table;
# the planning lane replaces stub bodies above, never the table keys.
READERS = {
    "plan_proposals": list_plan_proposals,
    "agent_run_summaries": list_agent_run_summaries,
}
