"""Cancellation that PROPAGATES (DEC-047).

Cancellation is not a single status flip — it is a cascade that fails closed everything
downstream of the cancelled thing, recorded durably on the Weft. Two roots:

  * :func:`cancel_plan` — the plan is transitioned to CANCELLED and every non-terminal
    step under it is cancelled; each cancelled step's ACTIVE leases are TERMINATEd
    (``lifecycle.terminate`` → a LEASE_TREE cascade that fails closed the authority
    descending from the lease).
  * :func:`cancel_agent` — the agent is TERMINATED, and cancellation recurses into its
    child agents first, then cancels the agent's own steps + leases and REVOKEs its
    capability grants (``lifecycle.revoke`` → a DERIVED_AUTHORITY cascade that fails closed
    every descendant grant). Any invocation the agent already issued that has no receipt
    is reported as a pending invocation whose OUTCOME is now unknowable.

Two honesty rules the code obeys and records:
  1. Cancelling NEVER dispatches new work — this module only asserts RETRACT/status
     events; it never calls the supervisor's dispatch path.
  2. An external effect already committed (a step that already has a receipt, an INVOKE
     already written) is NOT reversed. Cancellation stops FUTURE authority; it cannot
     un-ring a bell. Such effects are surfaced in ``committed_effects`` /
     ``pending_invocations`` rather than silently pretended away.

Authority-bearing teardown goes through ``decima.kernel.lifecycle`` (RETRACT semantics);
plan/step/agent lifecycle goes through status transitions on their Cells.
"""

from __future__ import annotations

from decima.kernel import lifecycle
from decima.kernel.weave import Weave
from decima.runtime import cells
from decima.runtime.cells import AgentStatus, PlanStatus, StepStatus


def _steps_of_plan(weave: object, plan_id: str) -> list[object]:
    return [c for c in weave.of_type(cells.PLAN_STEP) if c.content.get("plan_id") == plan_id]


def _steps_of_agent(weave: object, agent_id: str) -> list[object]:
    return [
        c for c in weave.of_type(cells.PLAN_STEP) if c.content.get("assigned_agent_id") == agent_id
    ]


def _active_leases_for_step(weave: object, step_id: str) -> list[object]:
    """Live (not-yet-retracted) leases minted for a step. A terminated lease is retracted
    and drops out of ``of_type``, so calling twice is idempotent."""
    return [c for c in weave.of_type(cells.LEASE) if c.content.get("step_id") == step_id]


def _has_receipt(weave: object, step_id: str) -> bool:
    """True iff a step already produced a receipt — i.e. an effect was dispatched and its
    outcome recorded. Such a committed effect is NOT reversed by cancellation."""
    return any(r.content.get("step_id") == step_id for r in weave.of_type(cells.RECEIPT))


def _cancel_step(weft: object, author: str, weave: object, step: object, report: dict) -> None:
    """Fail closed one step: TERMINATE its active leases, honestly record whether an
    effect already committed, then transition the step to CANCELLED. A terminal step is
    left untouched (its outcome stands) and only noted."""
    status = step.content.get("status")
    if status in StepStatus.TERMINAL:
        report["already_terminal"].append(step.id)
        return
    for lease in _active_leases_for_step(weave, step.id):
        lifecycle.terminate(weft, author, lease.id)
        report["terminated_leases"].append(lease.id)
    if _has_receipt(weave, step.id):
        # An effect was already dispatched for this step; its external result is not undone.
        report["committed_effects"].append(step.id)
    cells.set_status(weft, author, step, StepStatus.CANCELLED)
    report["cancelled_steps"].append(step.id)


def cancel_plan(weft: object, author: str, plan_id: str) -> dict:
    """Cancel a plan and cascade to its pending steps → active leases. The plan is
    transitioned to CANCELLED; every non-terminal step is cancelled and its live leases are
    TERMINATEd. Already-terminal steps and already-committed effects are reported, not
    reversed. Does NOT dispatch new work. Idempotent: re-cancelling a cancelled plan is a
    no-op (its steps are already terminal / its leases already retracted)."""
    weave = Weave.fold(weft)
    plan = weave.get(plan_id)
    if plan is None or plan.type != cells.PLAN:
        raise ValueError(f"no such plan {plan_id}")
    report: dict = {
        "plan_id": plan_id,
        "cancelled_steps": [],
        "terminated_leases": [],
        "committed_effects": [],
        "already_terminal": [],
    }
    if plan.content.get("status") not in PlanStatus.TERMINAL:
        cells.set_status(weft, author, plan, PlanStatus.CANCELLED)
        report["plan_cancelled"] = True
    else:
        report["plan_cancelled"] = False
    for step in _steps_of_plan(weave, plan_id):
        _cancel_step(weft, author, weave, step, report)
    return report


def revoke_capability(weft: object, author: str, cap_id: str) -> object:
    """Revoke a capability grant, cascading to every descendant grant. Thin composition of
    ``lifecycle.revoke`` (RETRACT of the capability cell): the fold derives the
    DERIVED_AUTHORITY cascade, so every grant attenuated from this one fails closed on the
    next fold. Returns the RETRACT event."""
    return lifecycle.revoke(weft, author, cap_id)


def cancel_agent(weft: object, author: str, agent_id: str) -> dict:
    """Cancel an agent and cascade: child agents → the agent's steps → their leases →
    the agent's capability grants (revoked, failing closed descendant grants) → its
    pending invocations (surfaced as unknown-outcome, never reversed).

    Child agents are cancelled FIRST (depth-first) so a parent's teardown fully contains
    its subtree. The agent is transitioned to TERMINATED. An INVOKE the agent already
    wrote with a receipt is a committed effect and is left intact; one WITHOUT a receipt is
    reported in ``pending_invocations`` because its outcome is now unknowable. Does NOT
    dispatch new work."""
    weave = Weave.fold(weft)
    agent = weave.get(agent_id)
    if agent is None or agent.type != cells.AGENT:
        raise ValueError(f"no such agent {agent_id}")
    report: dict = {
        "agent_id": agent_id,
        "terminated_agents": [],
        "cancelled_steps": [],
        "terminated_leases": [],
        "revoked_capabilities": [],
        "committed_effects": [],
        "already_terminal": [],
        "pending_invocations": [],
        "children": [],
    }

    # 1. Recurse into child agents first (depth-first containment).
    for child in weave.of_type(cells.AGENT):
        if child.content.get("parent_agent_id") == agent_id:
            child_report = cancel_agent(weft, author, child.id)
            report["children"].append(child_report)

    # 2. Terminate the agent itself.
    if agent.content.get("status") not in AgentStatus.TERMINAL:
        cells.set_status(weft, author, agent, AgentStatus.TERMINATED)
        report["terminated_agents"].append(agent_id)

    # 3. Cancel the agent's own steps + their leases.
    for step in _steps_of_agent(weave, agent_id):
        _cancel_step(weft, author, weave, step, report)

    # 4. Revoke the agent's capability grants → descendant grants fail closed at the fold.
    for cap_id in agent.content.get("capability_grant_ids", []) or []:
        cap = weave.get(cap_id)
        if cap is not None and not cap.retracted:
            revoke_capability(weft, author, cap_id)
            report["revoked_capabilities"].append(cap_id)

    # 5. Surface pending invocations by the agent's principal that have no receipt: their
    #    external effect may or may not have landed — cancellation cannot know or reverse it.
    principal = agent.content.get("principal")
    receipted_leases = {r.content.get("lease_id") for r in weave.of_type(cells.RECEIPT)}
    for inv in weave.invocations:
        if inv.by == principal and inv.event not in receipted_leases:
            report["pending_invocations"].append(inv.event)

    return report
