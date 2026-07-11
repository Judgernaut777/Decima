"""Stage 1b facades: authorization decisions + lifecycle over the extracted kernel.

Proves the typed AuthorizationDecision facade classifies real capability.authorize
outcomes into stable reason codes, and that lifecycle.revoke drives a real
DERIVED_AUTHORITY cascade that makes a descendant invocation fail closed — all against
the genuine kernel primitives (no mocks of the logic under test).
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel import lifecycle
from decima.kernel.authorization import AuthorizationDecision, ReasonCode, authorize_decision
from decima.kernel.capability import capability_content
from decima.kernel.crypto import Keyring
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft


def _setup():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    root = kr.mint("root", "root").id
    alice = kr.mint("alice", "agent").id
    weft = Weft(db, kr)
    return weft, kr, root, alice


def _grant(weft, root, cap_id, principal, *, effect="transform", caveats=None):
    content = capability_content(
        cap_id, effect, target="*", caveats=caveats or {}, grantee=principal, granter=root
    )
    assert_content(weft, root, cap_id, "capability", content)


def _agent(weft, root, agent_id, principal, envelope):
    assert_content(
        weft, root, agent_id, "agent", {"principal": principal, "envelope": list(envelope)}
    )


def test_allow_yields_ok_decision():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:echo", alice)
    _agent(weft, root, "agent:alice", alice, ["cap:echo"])
    weave = Weave.fold(weft)
    d = authorize_decision(weave, weave.get("agent:alice"), "cap:echo", {}, alice)
    assert isinstance(d, AuthorizationDecision)
    assert d.allowed and bool(d) is True
    assert d.reason_code == ReasonCode.OK
    assert d.matched_grant_id == "cap:echo"
    assert d.required_approval is False


def test_missing_grant_is_no_such_capability():
    weft, _kr, root, alice = _setup()
    _agent(weft, root, "agent:alice", alice, [])
    weave = Weave.fold(weft)
    d = authorize_decision(weave, weave.get("agent:alice"), "cap:ghost", {}, alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.NO_SUCH_CAPABILITY


def test_not_in_envelope_is_no_envelope():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:echo", alice)
    _agent(weft, root, "agent:alice", alice, [])  # cap exists but NOT in envelope
    weave = Weave.fold(weft)
    d = authorize_decision(weave, weave.get("agent:alice"), "cap:echo", {}, alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.NO_ENVELOPE


def test_requires_approval_is_flagged():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:pay", alice, effect="financial", caveats={"requires_approval": True})
    _agent(weft, root, "agent:alice", alice, ["cap:pay"])
    weave = Weave.fold(weft)
    d = authorize_decision(weave, weave.get("agent:alice"), "cap:pay", {}, alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.APPROVAL_REQUIRED
    assert d.required_approval is True
    # ...and it clears once the approval is supplied.
    d2 = authorize_decision(
        weave, weave.get("agent:alice"), "cap:pay", {}, alice, approvals={"cap:pay"}
    )
    assert d2.allowed and d2.reason_code == ReasonCode.OK


def test_signer_mismatch():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:echo", alice)
    _agent(weft, root, "agent:alice", alice, ["cap:echo"])
    weave = Weave.fold(weft)
    d = authorize_decision(weave, weave.get("agent:alice"), "cap:echo", {}, "someone-else")
    assert not d.allowed
    assert d.reason_code == ReasonCode.SIGNER_MISMATCH


def test_revoke_makes_invocation_fail_closed():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:echo", alice)
    _agent(weft, root, "agent:alice", alice, ["cap:echo"])
    # authorized before revocation
    weave = Weave.fold(weft)
    assert authorize_decision(weave, weave.get("agent:alice"), "cap:echo", {}, alice).allowed
    # lifecycle.revoke appends a real RETRACT; re-fold; now fails closed
    lifecycle.revoke(weft, root, "cap:echo")
    weave2 = Weave.fold(weft)
    d = authorize_decision(weave2, weave2.get("agent:alice"), "cap:echo", {}, alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.REVOKED
