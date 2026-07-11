"""E2E scenario D — APPROVAL GATING on the durable stack.

A Morta-gated (requires_approval) capability cannot fire on the agent's say-so. The
deterministic authorizer returns APPROVAL_REQUIRED; a human DENY lands a durable decision
and the effect never runs; a human APPROVE lands a durable capability-scoped approval that
clears the exact gate; and because the approval is SINGLE-USE, once consumed it fails
closed again on reuse. Every disposition is folded from the Weft and surfaced by the
approvals read-model.

Load-bearing property: authority to run a gated effect is DATA on the Weft (a live
capability-scoped approval cell), never a property of the requesting agent. authorize
returns OK only while that approval is live; deny records refusal without any effect; and
a consumed (retracted) approval reverts the gate to APPROVAL_REQUIRED — a human's single
approval enacts exactly one effect and can never be replayed.
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel import lifecycle
from decima.kernel.authorization import ReasonCode, authorize_decision
from decima.kernel.capability import (
    APPROVAL,
    approval_id,
    capability_approvals,
    capability_content,
)
from decima.kernel.crypto import Keyring
from decima.kernel.inbox import DECISION, ITEM
from decima.kernel.model import assert_content, assert_edge
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.projections.approvals import (
    APPROVED,
    CONSUMED,
    DENIED,
    PENDING,
    ApprovalsProjection,
)
from decima.projections.engine import ProjectionDriver


def _setup() -> tuple[Weft, str, str, str, Keyring]:
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    root = kr.mint("root", "root").id
    alice = kr.mint("alice", "agent").id
    return Weft(db, kr), root, alice, db, kr


def _gated_cap(weft, root, alice, cap_id="cap:pay"):
    content = capability_content(
        cap_id, "financial", target="*", caveats={"requires_approval": True},
        grantee=alice, granter=root,
    )
    assert_content(weft, root, cap_id, "capability", content)
    assert_content(
        weft, root, "agent:alice", "agent",
        {"principal": alice, "envelope": [cap_id]},
    )
    return cap_id


def _enqueue(weft, root, cap_id, item_id, *, decided=None):
    """Land a pending inbox item (and, optionally, its human decision) on the Weft."""
    assert_content(weft, root, item_id, ITEM, {
        "capability": cap_id, "description": f"run {cap_id}",
        "instruction_eligible": False,
    })
    if decided is not None:
        did = f"inbox_decision:{item_id}"
        assert_content(weft, root, did, DECISION, {
            "item": item_id, "decision": decided,
            "approver": root, "ran": decided == "approved",
        })
        assert_edge(weft, root, did, "decides", item_id)


def test_gate_denies_then_single_use_approval_clears_then_reuse_fails():
    weft, root, alice, db, kr = _setup()
    cap = _gated_cap(weft, root, alice)
    weave = Weave.fold(weft)
    agent = weave.get("agent:alice")

    # 1) The gated effect is refused by the deterministic authorizer: APPROVAL_REQUIRED.
    d = authorize_decision(weave, agent, cap, {}, alice, approvals=capability_approvals(weave))
    assert not d.allowed
    assert d.reason_code == ReasonCode.APPROVAL_REQUIRED
    assert d.required_approval is True

    # 2) DENY: a human declines. The decision is durable; the effect never runs.
    _enqueue(weft, root, cap, "inbox_item:pay-1", decided="denied")
    weave = Weave.fold(weft)
    # authorize still refuses (no live approval) — a denial confers no authority.
    assert not authorize_decision(
        weave, agent, cap, {}, alice, approvals=capability_approvals(weave)
    ).allowed
    # The approvals read-model buckets it as DENIED.
    driver = ProjectionDriver(weft)
    driver.register(ApprovalsProjection())
    approvals = driver.get("approvals")
    denied = {a.item for a in approvals.by_state(DENIED)}
    assert "inbox_item:pay-1" in denied
    # Durable across a restart: a fresh Weft over the same db still shows the denial and
    # still has no live approval.
    weft2 = Weft(db, kr)
    assert not capability_approvals(Weave.fold(weft2))

    # 3) APPROVE ONCE: a human lands a live, capability-scoped approval on the Weft. That
    #    is the authority; now the exact gate clears.
    appr_id = approval_id(cap)  # capability-scoped (ob=None)
    assert_content(weft, root, appr_id, APPROVAL, {"capability": cap, "scope": "capability"})
    _enqueue(weft, root, cap, "inbox_item:pay-2", decided="approved")
    weave = Weave.fold(weft)
    assert cap in capability_approvals(weave)
    ok = authorize_decision(weave, agent, cap, {}, alice, approvals=capability_approvals(weave))
    assert ok.allowed and ok.reason_code == ReasonCode.OK, "the live approval clears the gate"
    # The read-model shows the approved item consumed (its effect ran).
    driver2 = ProjectionDriver(weft)
    driver2.register(ApprovalsProjection())
    states = {a.item: a.state for a in driver2.get("approvals").approvals()}
    assert states["inbox_item:pay-2"] == CONSUMED
    assert states["inbox_item:pay-1"] == DENIED

    # 4) SINGLE-USE: consuming (retracting) the approval reverts the gate. Reuse fails
    #    closed — one human approval enacts exactly one effect, never a replay.
    lifecycle.revoke(weft, root, appr_id)
    weave = Weave.fold(weft)
    assert cap not in capability_approvals(weave), "a consumed approval is no longer live"
    d2 = authorize_decision(
        weave, agent, cap, {}, alice, approvals=capability_approvals(weave)
    )
    assert not d2.allowed
    assert d2.reason_code == ReasonCode.APPROVAL_REQUIRED, "reuse fails closed at the gate"


def test_pending_item_is_not_authority():
    """Merely ENQUEUING a request grants nothing: a pending item authorizes no effect."""
    weft, root, alice, _db, _kr = _setup()
    cap = _gated_cap(weft, root, alice)
    _enqueue(weft, root, cap, "inbox_item:pay-p")  # pending, no decision
    weave = Weave.fold(weft)
    assert not authorize_decision(
        weave, weave.get("agent:alice"), cap, {}, alice,
        approvals=capability_approvals(weave),
    ).allowed
    driver = ProjectionDriver(weft)
    driver.register(ApprovalsProjection())
    assert {a.item for a in driver.get("approvals").by_state(PENDING)} == {"inbox_item:pay-p"}
    assert not driver.get("approvals").by_state(APPROVED)
