"""E2E scenario F — REVOCATION on the durable stack.

An agent holds a filesystem/shell capability and begins a plan under it. A human revokes
the capability (a single RETRACT via lifecycle.revoke). From that fold onward: a pending
invocation fails closed, a fresh authorization excludes the grant, an attenuated
DESCENDANT grant is dragged closed by the DERIVED_AUTHORITY cascade, a receipt already
committed BEFORE the revocation survives untouched, and the revocation surfaces in the
activity read-model.

Load-bearing property: revocation is a fold-derived cascade, not an imperative sweep.
One RETRACT of the root capability makes every authority descending from it (the grant,
its attenuations) fail closed on the very next fold, while already-durable facts
(committed receipts) are never rewritten.
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel import lifecycle
from decima.kernel.authorization import ReasonCode, authorize_decision
from decima.kernel.capability import capability_content
from decima.kernel.crypto import Keyring
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.projections.activity import ActivityProjection
from decima.projections.engine import ProjectionDriver
from decima.runtime import cells
from decima.runtime.cells import StepStatus


def _setup() -> tuple[Weft, str, str]:
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    root = kr.mint("root", "root").id
    alice = kr.mint("alice", "agent").id
    return Weft(db, kr), root, alice


def _grant(weft, root, cap_id, principal, *, parent=None, granter=None):
    content = capability_content(
        cap_id,
        "shell",
        target="*",
        caveats={},
        grantee=principal,
        granter=granter or root,
        parent=parent,
    )
    assert_content(weft, root, cap_id, "capability", content)


def _agent(weft, root, agent_id, principal, envelope):
    assert_content(
        weft, root, agent_id, "agent", {"principal": principal, "envelope": list(envelope)}
    )


def test_revocation_fails_closed_and_cascades_while_preserving_receipts():
    weft, root, alice = _setup()

    # A shell (filesystem-effect) capability to alice, and a DESCENDANT grant attenuated
    # from it (parent=root_cap) held by a child agent.
    # alice holds cap:fs and attenuates cap:fs-child from it (she is its granter).
    _grant(weft, root, "cap:fs", alice)
    _grant(weft, root, "cap:fs-child", alice, parent="cap:fs", granter=alice)
    _agent(weft, root, "agent:alice", alice, ["cap:fs"])
    _agent(weft, root, "agent:child", alice, ["cap:fs-child"])

    # Alice begins a plan under the capability: a step with a live lease (a pending
    # invocation) and a receipt that COMMITS before any revocation.
    plan = cells.create_plan(weft, root, objective="index the tree", creator_principal=root)
    step = cells.create_step(weft, root, plan_id=plan, description="scan")
    cells.create_lease(
        weft,
        root,
        step_id=step,
        worker=alice,
        issued_frontier=0,
        expiry=100,
        attempt=1,
        idempotency_key=step,
    )
    cells.record_receipt(
        weft,
        root,
        step_id=step,
        lease_id="lease-committed",
        idempotency_key=step,
        status=StepStatus.SUCCEEDED,
    )

    weave = Weave.fold(weft)
    # Authorized on both the root grant and the descendant BEFORE revocation.
    assert authorize_decision(weave, weave.get("agent:alice"), "cap:fs", {}, alice).allowed
    assert authorize_decision(weave, weave.get("agent:child"), "cap:fs-child", {}, alice).allowed
    committed = cells.receipt_for_idempotency_key(weave, step)
    assert committed is not None

    # --- the human REVOKES the capability (one RETRACT). -----------------------------
    boundary = weft.count()
    lifecycle.revoke(weft, root, "cap:fs")
    weave2 = Weave.fold(weft)

    # (1) the pending invocation on the revoked grant fails closed.
    d_root = authorize_decision(weave2, weave2.get("agent:alice"), "cap:fs", {}, alice)
    assert not d_root.allowed
    assert d_root.reason_code == ReasonCode.REVOKED

    # (2) a NEW authorization still excludes the grant (no fresh lease can use it).
    assert not authorize_decision(weave2, weave2.get("agent:alice"), "cap:fs", {}, alice).allowed

    # (3) the DESCENDANT grant is dragged closed by the DERIVED_AUTHORITY cascade.
    assert weave2.get("cap:fs").retracted is True
    assert weave2.get("cap:fs-child").retracted is True, "descendant grant fails closed"
    assert not authorize_decision(
        weave2, weave2.get("agent:child"), "cap:fs-child", {}, alice
    ).allowed

    # (4) the receipt committed before the revocation is untouched (revocation is not
    #     a rewrite of history).
    still = cells.receipt_for_idempotency_key(weave2, step)
    assert still is not None
    assert still.id == committed.id

    # (5) the revocation surfaces in the activity read-model as a RETRACT of the cap.
    driver = ProjectionDriver(weft)
    driver.register(ActivityProjection())
    activity = driver.get("activity")
    revocations = [
        e
        for e in activity.timeline()
        if e.cell == "cap:fs" and e.verb == "RETRACT" and e.seq is not None and e.seq > boundary
    ]
    assert revocations, "the revocation must be visible in the activity projection"
