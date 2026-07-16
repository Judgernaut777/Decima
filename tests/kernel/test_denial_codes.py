"""Every authorization denial site produces its structured DenialCode (0.3.1).

The 0.3.0 facade recovered reason codes by substring-matching the human sentence, so a
rewording silently degraded classification to DENIED. `capability.authorize_detail` now
returns the code from the denial site itself; these tests pin each remaining site (the
lifecycle suite already covers OK / NO_SUCH_CAPABILITY / NO_ENVELOPE / APPROVAL_REQUIRED
/ SIGNER_MISMATCH / REVOKED) and the invariant that the wrapper and the vocabulary can
never drift from the primitive.
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel import capability
from decima.kernel.authorization import ReasonCode, authorize_decision
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


def _grant(weft, root, cap_id, principal, *, caveats=None, extra=None):
    content = capability_content(
        cap_id, "transform", target="*", caveats=caveats or {}, grantee=principal, granter=root
    )
    content.update(extra or {})
    assert_content(weft, root, cap_id, "capability", content)


def _agent(weft, root, agent_id, principal, envelope, *, sandbox=False):
    content = {"principal": principal, "envelope": list(envelope)}
    if sandbox:
        content["sandbox"] = True
    assert_content(weft, root, agent_id, "agent", content)


def _decide(weft, cap_id, principal, agent_id="agent:alice", **kw):
    weave = Weave.fold(weft)
    cell = weave.get(agent_id)
    assert cell is not None
    return authorize_decision(weave, cell, cap_id, {}, principal, **kw)


def test_vocabulary_is_owned_by_the_primitive():
    # ReasonCode IS the primitive's DenialCode — the facade cannot drift from it.
    assert ReasonCode is capability.DenialCode


def test_not_a_capability():
    weft, _kr, root, alice = _setup()
    assert_content(weft, root, "cell:note", "note", {"text": "not a capability"})
    _agent(weft, root, "agent:alice", alice, ["cell:note"])
    d = _decide(weft, "cell:note", alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.NOT_A_CAPABILITY


def test_quarantined():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:forged", alice, extra={"quarantined": True})
    _agent(weft, root, "agent:alice", alice, ["cap:forged"])
    d = _decide(weft, "cap:forged", alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.QUARANTINED


def test_wrong_grantee():
    weft, kr, root, alice = _setup()
    bob = kr.mint("bob", "agent").id
    _grant(weft, root, "cap:echo", bob)  # issued to bob…
    _agent(weft, root, "agent:alice", alice, ["cap:echo"])  # …but in alice's envelope
    d = _decide(weft, "cap:echo", alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.WRONG_GRANTEE


def test_delegation_invalid():
    weft, _kr, root, alice = _setup()
    # A child grant whose parent is missing → broken delegation path.
    _grant(weft, root, "cap:child", alice, extra={"parent": "cap:gone", "granter": root})
    _agent(weft, root, "agent:alice", alice, ["cap:child"])
    d = _decide(weft, "cap:child", alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.DELEGATION_INVALID


def test_budget_exceeded():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:spend", alice, caveats={"budget": 5})
    _agent(weft, root, "agent:alice", alice, ["cap:spend"])
    d = _decide(weft, "cap:spend", alice, spent=10.0)
    assert not d.allowed
    assert d.reason_code == ReasonCode.BUDGET_EXCEEDED


def test_sandbox_only():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:risky", alice, caveats={"sandbox_only": True})
    _agent(weft, root, "agent:alice", alice, ["cap:risky"])
    d = _decide(weft, "cap:risky", alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.SANDBOX_ONLY


def test_lease_expired_is_lease_failed():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:lease", alice, caveats={"expires_at": 10})
    _agent(weft, root, "agent:alice", alice, ["cap:lease"])
    d = _decide(weft, "cap:lease", alice, now=10)
    assert not d.allowed
    assert d.reason_code == ReasonCode.LEASE_FAILED


def test_lease_exhausted_is_lease_failed():
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:once", alice, caveats={"max_uses": 1})
    _agent(weft, root, "agent:alice", alice, ["cap:once"])
    d = _decide(weft, "cap:once", alice, now=1, prior_uses=1)
    assert not d.allowed
    assert d.reason_code == ReasonCode.LEASE_FAILED


def test_wrapper_matches_detail_verdict():
    # The frozen (bool, str) surface and the detail triple are the same decision.
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:echo", alice)
    _agent(weft, root, "agent:alice", alice, ["cap:echo"])
    weave = Weave.fold(weft)
    agent = weave.get("agent:alice")
    assert agent is not None
    for principal in (alice, "someone-else"):
        allowed, reason = capability.authorize(weave, agent, "cap:echo", {}, principal)
        d_allowed, d_reason, _code = capability.authorize_detail(
            weave, agent, "cap:echo", {}, principal
        )
        assert (allowed, reason) == (d_allowed, d_reason)
