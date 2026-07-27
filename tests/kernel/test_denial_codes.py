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

import pytest

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


def test_a_grant_that_names_no_grantee_authorizes_nobody():
    """THE ATTACK SECURITY.md carried as an open residual: `authorize_detail` refused a
    mismatched grantee only `if grantee is not None`, so a grant naming NOBODY passed the
    check for EVERY principal. No authorship rule closes it — the grant here is asserted by
    root, so every authorship check is satisfied; the defect is in the grant's CONTENT.

    Reaching it takes one more thing and the powerbox hands it over: an ordinary `agent` cell
    is deliberately not authorship-bound (a broker must be able to append a grant to the
    requesting agent's envelope), so naming a grantee-less grant in an envelope is a single
    unguarded ASSERT. Alice does exactly that below and is refused."""
    weft, _kr, root, alice = _setup()
    content = capability_content("cap:orphan", "transform", grantee=root, granter=root)
    content["grantee"] = None  # the pre-fix default, written straight onto the log
    assert_content(weft, root, "cap:orphan", "capability", content)
    _agent(weft, root, "agent:alice", alice, ["cap:orphan"])

    d = _decide(weft, "cap:orphan", alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.NO_GRANTEE
    # …and not merely for whoever happens to ask: the granter itself is refused too, which is
    # what distinguishes "this grant belongs to nobody" from "you are the wrong holder".
    _agent(weft, root, "agent:root", root, ["cap:orphan"])
    d_root = _decide(weft, "cap:orphan", root, agent_id="agent:root")
    assert not d_root.allowed
    assert d_root.reason_code == ReasonCode.NO_GRANTEE


def test_an_empty_or_non_string_grantee_is_not_coerced_into_a_holder():
    """`""` and a non-string binding are the same defect wearing a different type, and a rule
    that only screened `is None` would let `{'grantee': 0}` through to a `!=` comparison that
    no principal can satisfy — a denial, but by accident. Refused at the same site, by name."""
    weft, _kr, root, alice = _setup()
    for cap_id, bad in (("cap:blank", ""), ("cap:int", 0), ("cap:list", [])):
        content = capability_content(cap_id, "transform", grantee=root, granter=root)
        content["grantee"] = bad
        assert_content(weft, root, cap_id, "capability", content)
        _agent(weft, root, f"agent:{cap_id}", alice, [cap_id])
        d = _decide(weft, cap_id, alice, agent_id=f"agent:{cap_id}")
        assert d.reason_code == ReasonCode.NO_GRANTEE, f"grantee={bad!r}"


def test_the_mint_refuses_to_build_a_grant_that_names_nobody():
    """The paired half. The read-side refusal is what holds for a log already on disk; this is
    what stops a new one being written. `grantee` used to default to `None`, so "do not mint a
    capability without a grantee" was a sentence in SECURITY.md rather than a property of the
    code."""
    with pytest.raises(TypeError):
        capability_content("cap:x", "transform")  # type: ignore[call-arg]
    for bad in ("", None):
        with pytest.raises(ValueError, match="granted TO"):
            capability_content("cap:x", "transform", grantee=bad)  # type: ignore[arg-type]
    # The positive control: a named grantee still mints, and lands in the same field.
    assert capability_content("cap:x", "transform", grantee="principal:a")["grantee"] == (
        "principal:a"
    )


def test_a_morta_gated_grant_minted_without_its_floor_authorizes_nobody():
    """THE OTHER RESIDUAL SECURITY.md carried: `MORTA_FLOORS` was merged in by the two code
    paths that happen to issue grants and read by nothing, so the floor was a property of the
    MINTER rather than of the grant. Root is entitled to mint — every authorship check below
    passes — and root simply does not call `with_morta_floor`. Before the read-time
    re-derivation, that `shell` grant authorized arbitrary local effect with no Morta gate at
    all, and compromise of any minting authority was compromise of the realm's constitution.
    """
    weft, _kr, root, alice = _setup()
    _grant(weft, root, "cap:sh", alice, caveats={}, extra={"effect": "shell"})
    _agent(weft, root, "agent:alice", alice, ["cap:sh"])

    d = _decide(weft, "cap:sh", alice)
    assert not d.allowed
    assert d.reason_code == ReasonCode.MORTA_FLOOR_MISSING
    assert "requires_approval" in d.reason
    # …and it is not a gate an approval can talk its way past: the answer is "re-mint this
    # grant", not "approve this operation".
    d_approved = _decide(weft, "cap:sh", alice, approvals={"cap:sh"})
    assert d_approved.reason_code == ReasonCode.MORTA_FLOOR_MISSING


def test_the_same_grant_carrying_its_floor_authorizes_normally():
    """The positive control. The refusal above must be about the MISSING FLOOR and not about
    `shell` being unusable — otherwise the rule would read as "Morta-gated effects are
    banned", which is not the property and would hide a broken gate."""
    weft, _kr, root, alice = _setup()
    _grant(
        weft,
        root,
        "cap:sh",
        alice,
        caveats=capability.with_morta_floor("shell", {}),
        extra={"effect": "shell"},
    )
    _agent(weft, root, "agent:alice", alice, ["cap:sh"])

    gated = _decide(weft, "cap:sh", alice)
    assert gated.reason_code == ReasonCode.APPROVAL_REQUIRED, "the floor IS the Morta gate"
    cleared = _decide(weft, "cap:sh", alice, approvals={"cap:sh"})
    assert cleared.allowed and cleared.reason_code == ReasonCode.OK


def test_a_partial_floor_is_refused_by_the_key_it_is_missing():
    """`financial` floors TWO caveats, so carrying one of them is not carrying the floor. This
    is also the test that keeps `reversible_only` honest: it has no enforcement point anywhere
    in `decima/`, and what the re-derivation guarantees for it is PRESENCE — every live
    `financial` grant declares it — not that anything checks reversibility."""
    weft, _kr, root, alice = _setup()
    _grant(
        weft,
        root,
        "cap:pay",
        alice,
        caveats={"requires_approval": True},  # …but not `reversible_only`
        extra={"effect": "financial"},
    )
    _agent(weft, root, "agent:alice", alice, ["cap:pay"])

    d = _decide(weft, "cap:pay", alice, approvals={"cap:pay"})
    assert d.reason_code == ReasonCode.MORTA_FLOOR_MISSING
    assert "reversible_only" in d.reason and "requires_approval" not in d.reason


def test_an_ungated_effect_class_is_not_asked_for_a_floor_it_does_not_have():
    """The rule is keyed on `MORTA_FLOORS`, not on "every grant must carry caveats". A
    `transform` grant with an empty caveat set is exactly as authorized as it was before."""
    weft, _kr, root, alice = _setup()
    assert capability.morta_floor("transform") == {}
    _grant(weft, root, "cap:t", alice, caveats={})
    _agent(weft, root, "agent:alice", alice, ["cap:t"])
    d = _decide(weft, "cap:t", alice)
    assert d.allowed and d.reason_code == ReasonCode.OK


def test_an_attenuated_child_of_a_floored_grant_still_authorizes():
    """Delegation must survive the new refusal. `_caveats_downhill` already forces a parent's
    non-numeric constraints to persist, so an honestly attenuated child carries the floor and
    is judged on its own merits — the rule closes a minting hole without failing closed on the
    ordinary narrowing path."""
    weft, kr, root, alice = _setup()
    bob = kr.mint("bob", "agent").id
    parent = capability_content(
        "sh", "shell", caveats=capability.with_morta_floor("shell", {}), grantee=alice, granter=root
    )
    assert_content(weft, root, "cap:sh", "capability", parent)
    child = capability.attenuate(parent, {"budget": 3}, "cap:sh", grantee=bob, granter=alice)
    assert_content(weft, alice, "cap:sh-child", "capability", child)
    _agent(weft, root, "agent:bob", bob, ["cap:sh-child"])

    d = _decide(weft, "cap:sh-child", bob, agent_id="agent:bob", approvals={"cap:sh-child"})
    assert d.allowed and d.reason_code == ReasonCode.OK


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
