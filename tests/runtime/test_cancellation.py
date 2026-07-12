"""Cancellation that propagates (DEC-047).

Proves the load-bearing property: cancelling a root fails closed everything downstream —
plan → pending steps → active leases, and agent → child agents → leases → capability
grants — as durable RETRACT/status events, while NOT dispatching new work and NOT
pretending an already-committed effect was reversed.
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cancellation, cells, reconciliation
from decima.runtime.cells import AgentStatus, PlanStatus, StepStatus


def _setup():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(db, kr), author, db, kr


def test_cancel_plan_cascades_to_steps_and_active_leases():
    weft, author, _db, _kr = _setup()
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    a = cells.create_step(weft, author, plan_id=plan, description="A")
    b = cells.create_step(weft, author, plan_id=plan, description="B", dependency_ids=[a])

    # A is mid-flight: a live lease + RUNNING status (the crash-safe dispatch state).
    lease = cells.create_lease(
        weft,
        author,
        step_id=a,
        worker=author,
        issued_frontier=0,
        expiry=100,
        attempt=1,
        idempotency_key=a,
    )
    cells.set_status(weft, author, Weave.fold(weft).get(a), StepStatus.RUNNING)

    report = cancellation.cancel_plan(weft, author, plan)

    weave = Weave.fold(weft)
    assert weave.get(plan).content["status"] == PlanStatus.CANCELLED
    assert weave.get(a).content["status"] == StepStatus.CANCELLED
    assert weave.get(b).content["status"] == StepStatus.CANCELLED
    # The active lease was TERMINATEd (retracted → drops out of of_type).
    assert lease in report["terminated_leases"]
    assert weave.get(lease).retracted is True


def test_cancel_agent_cascades_to_children_leases_and_capabilities():
    weft, author, _db, _kr = _setup()

    # A capability granted to the parent, and a child grant attenuated from it.
    root_cap, child_cap = "cap-root", "cap-child"
    cells.assert_content(
        weft,
        author,
        root_cap,
        "capability",
        {"name": "shell", "effect": "shell", "caveats": {}, "grantee": author},
    )
    cells.assert_content(
        weft,
        author,
        child_cap,
        "capability",
        {"name": "shell", "effect": "shell", "caveats": {}, "parent": root_cap, "grantee": author},
    )

    parent = cells.create_agent(
        weft,
        author,
        objective="parent",
        principal=author,
        capability_grant_ids=[root_cap],
    )
    child = cells.create_agent(
        weft,
        author,
        objective="child",
        principal=author,
        parent_agent_id=parent,
    )
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    child_step = cells.create_step(
        weft,
        author,
        plan_id=plan,
        description="C",
        assigned_agent_id=child,
    )
    lease = cells.create_lease(
        weft,
        author,
        step_id=child_step,
        worker=child,
        issued_frontier=0,
        expiry=100,
        attempt=1,
        idempotency_key=child_step,
    )
    cells.set_status(weft, author, Weave.fold(weft).get(child_step), StepStatus.RUNNING)

    report = cancellation.cancel_agent(weft, author, parent)

    weave = Weave.fold(weft)
    assert weave.get(parent).content["status"] == AgentStatus.TERMINATED
    assert weave.get(child).content["status"] == AgentStatus.TERMINATED, "child cancelled too"
    assert weave.get(child_step).content["status"] == StepStatus.CANCELLED
    assert weave.get(lease).retracted is True, "child's active lease terminated"
    # Revoking the root grant cascades DERIVED_AUTHORITY to the child grant (fail closed).
    assert root_cap in report["revoked_capabilities"]
    assert weave.get(root_cap).retracted is True
    assert weave.get(child_cap).retracted is True, "descendant grant fails closed"


def test_cancel_does_not_reverse_a_committed_effect():
    weft, author, _db, _kr = _setup()
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    a = cells.create_step(weft, author, plan_id=plan, description="A")
    # An effect already committed: a receipt exists but the step is still RUNNING (the
    # outcome-transition crash window) — cancellation must record it, not pretend it away.
    cells.set_status(weft, author, Weave.fold(weft).get(a), StepStatus.RUNNING)
    cells.record_receipt(
        weft,
        author,
        step_id=a,
        lease_id="lease-x",
        idempotency_key=a,
        status=StepStatus.SUCCEEDED,
    )

    report = cancellation.cancel_plan(weft, author, plan)
    assert a in report["committed_effects"], "a committed effect is surfaced, not reversed"
    # The step is still moved to CANCELLED, but the receipt (the record of the effect) stays.
    assert Weave.fold(weft).get(a).content["status"] == StepStatus.CANCELLED
    assert reconciliation.receipts_for_step(Weave.fold(weft), a), "receipt is NOT erased"
