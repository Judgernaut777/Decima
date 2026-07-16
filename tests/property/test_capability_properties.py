"""CAPABILITY-PROPS (DEC-032/033) — property + example tests over the ocap /
authorization model in ``decima.kernel.capability``.

Three invariants of Decima's Law-2 (no ambient authority) capability layer are
exercised against the REAL kernel functions (no mocks of the logic under test):

  1. ATTENUATION MONOTONICITY / DESCENDANT ⊆ PARENT
     ``attenuate`` only ever NARROWS — a derived child grant's permitted-invocation
     set is a subset of its parent's. Numeric lease bounds (budget / expires_at /
     max_uses) may only shrink; a non-numeric parent constraint must persist.
     ``attenuation_valid`` / ``_caveats_downhill`` REJECT any widening child.

  2. PROOF BINDING
     ``verify_proof`` succeeds for the EXACT (verb, body, nonce, parents) that
     ``build_proof`` was made for, and FAILS closed when ANY of those four is
     changed (the ``invocation_bind`` is the anti-replay seam), or when the holder
     signature / holder identity is tampered.

  3. REVOCATION INVALIDATES DESCENDANTS
     After a RETRACT carrying the DERIVED_AUTHORITY cascade on a PARENT grant,
     ``authorize`` of an invocation under a DESCENDANT grant (child + grandchild)
     fails CLOSED — the cascade the fold derives (weave.py) reaches every grant
     whose authority descends from the revoked one.

Everything is built in-process from the low-level kernel like the conformance test:
a Keyring, a Weft over a temp SQLite db, capability + agent Cells asserted directly,
and the Weave folded from the Weft. Deterministic: fixed 32-byte seeds, logical time
only (no wall-clock), no unseeded randomness in recorded content.
"""

from __future__ import annotations

import os
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from decima.kernel.capability import (
    _SHRINK_ONLY,
    _caveats_downhill,
    attenuate,
    attenuation_valid,
    authorize,
    build_proof,
    capability_content,
    invocation_bind,
    verify_proof,
)
from decima.kernel.crypto import Keyring
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import RETRACT, Weft

# Keep property runs bounded + deterministic (house rule).
SETTINGS = settings(max_examples=100, deadline=None, derandomize=True)


# ── shared builders ─────────────────────────────────────────────────────────
def _fresh_weft():
    """A Weft over a throwaway temp db, keyed by a fixed 32-byte seed (determinism)."""
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    return Weft(db, kr), kr


def _assert_cap(weft, author, cap_id, content):
    assert_content(weft, author, cap_id, "capability", content)


def _assert_agent(weft, author, agent_id, principal, envelope):
    assert_content(
        weft, author, agent_id, "agent", {"principal": principal, "envelope": list(envelope)}
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. ATTENUATION MONOTONICITY — a child grant is never wider than its parent
# ─────────────────────────────────────────────────────────────────────────────
# A parent's caveats: numeric lease bounds + an optional boolean constraint.
_parent_caveats = st.fixed_dictionaries(
    {
        "budget": st.integers(min_value=0, max_value=10_000),
        "expires_at": st.integers(min_value=1, max_value=10_000),
        "max_uses": st.integers(min_value=1, max_value=1_000),
    },
    optional={"requires_approval": st.just(True), "sandbox_only": st.just(True)},
)
# What the delegator *proposes* to tighten by. Any int here can only shrink a bound
# (attenuate takes the min); a new boolean key can only add a constraint.
_stricter = st.fixed_dictionaries(
    {},
    optional={
        "budget": st.integers(min_value=0, max_value=20_000),
        "expires_at": st.integers(min_value=1, max_value=20_000),
        "max_uses": st.integers(min_value=1, max_value=2_000),
        "requires_approval": st.just(True),
    },
)


@SETTINGS
@given(caveats=_parent_caveats, stricter=_stricter)
def test_attenuate_is_always_downhill(caveats, stricter):
    """attenuate() ALWAYS produces a child ⊆ parent: attenuation_valid accepts it,
    every numeric bound only shrinks, and every parent constraint persists."""
    parent = capability_content(
        "pay", "financial", target="*", caveats=caveats, grantee="A", granter="root"
    )
    child = attenuate(parent, stricter, parent_id="cap:parent", grantee="B", granter="A")

    ok, why = attenuation_valid(child, parent)
    assert ok, f"attenuate() produced a NON-downhill child: {why} / {child['caveats']}"
    assert _caveats_downhill(child, parent), "child caveats must be ⊆ parent's"

    pcav, ccav = parent["caveats"], child["caveats"]
    for k in _SHRINK_ONLY:  # numeric bounds may only shrink
        if k in pcav:
            assert k in ccav, f"child dropped the numeric bound {k} (would widen)"
            assert int(ccav[k]) <= int(pcav[k]), (
                f"attenuate WIDENED {k}: child {ccav[k]} > parent {pcav[k]}"
            )
    for k, v in pcav.items():  # non-numeric constraints must persist
        if k in _SHRINK_ONLY:
            continue
        if v:
            assert ccav.get(k), f"child dropped parent constraint {k} (would widen)"


@SETTINGS
@given(
    pbudget=st.integers(min_value=0, max_value=10_000),
    widen=st.integers(min_value=1, max_value=10_000),
)
def test_widening_a_bound_is_rejected(pbudget, widen):
    """A hand-built child that RAISES a numeric bound above the parent is rejected
    by BOTH attenuation_valid and _caveats_downhill (fail closed on widening)."""
    parent = capability_content(
        "pay", "financial", caveats={"budget": pbudget}, grantee="A", granter="root"
    )
    wider = capability_content(
        "pay",
        "financial",
        caveats={"budget": pbudget + widen},
        grantee="B",
        granter="A",
        parent="cap:parent",
    )
    ok, why = attenuation_valid(wider, parent)
    assert not ok, f"a wider budget child must be rejected, got ok (why={why})"
    assert "downhill" in why or "widened" in why
    assert not _caveats_downhill(wider, parent)


def test_dropping_a_constraint_or_changing_effect_is_rejected():
    """A child that drops a parent constraint, widens the target, or changes the
    effect is NOT a valid attenuation (structural narrowing proof, MORTA §5)."""
    parent = capability_content(
        "pay",
        "financial",
        target="acct:42",
        caveats={"requires_approval": True},
        grantee="A",
        granter="root",
    )

    # (a) drops requires_approval — widens authority.
    dropped = capability_content(
        "pay",
        "financial",
        target="acct:42",
        caveats={},
        grantee="B",
        granter="A",
        parent="cap:parent",
    )
    ok, _ = attenuation_valid(dropped, parent)
    assert not ok, "dropping requires_approval must be rejected"

    # (b) widens the target selector (exact acct:42 → * everything).
    widened = capability_content(
        "pay",
        "financial",
        target="*",
        caveats={"requires_approval": True},
        grantee="B",
        granter="A",
        parent="cap:parent",
    )
    ok, why = attenuation_valid(widened, parent)
    assert not ok and "target" in why, f"widening the target must be rejected: {why}"

    # (c) changes the effect class entirely — not a specialization.
    changed = capability_content(
        "pay",
        "shell",
        target="acct:42",
        caveats={"requires_approval": True},
        grantee="B",
        granter="A",
        parent="cap:parent",
    )
    ok, why = attenuation_valid(changed, parent)
    assert not ok and "effect" in why, f"changing the effect must be rejected: {why}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. PROOF BINDING — a proof authorizes exactly ONE request, nothing else
# ─────────────────────────────────────────────────────────────────────────────
def _proof_fixture():
    """A folded Weave with a root grant held by an agent, plus everything a proof
    needs. Returns (weave, keyring, agent_cell, holder_pid, cap_id)."""
    weft, kr = _fresh_weft()
    root = kr.mint("root", "human").id
    holder = kr.mint("holder-agent", "agent").id
    cap_id = "cap:root-grant"
    _assert_cap(
        weft,
        root,
        cap_id,
        capability_content("echo", "echo", caveats={}, grantee=holder, granter=root),
    )
    _assert_agent(weft, root, "agent:holder", holder, [cap_id])
    weave = Weave.fold(weft)
    return weave, kr, weave.get("agent:holder"), holder, cap_id


# The canonical request the proof is minted for.
_VERB = "INVOKE"
_NONCE = "nonce-canonical"
_PARENTS = ["evt:a", "evt:b"]


def _body(cap_id, args=None):
    return {"cap": cap_id, "args": args or {}}


def test_proof_verifies_for_the_exact_request():
    """A freshly built proof verifies for the EXACT (verb, body, nonce, parents)
    it was minted for — real Ed25519 possession proof + full ocap check."""
    weave, kr, agent_cell, holder, cap_id = _proof_fixture()
    body = _body(cap_id)
    proof = build_proof(weave, kr, holder, cap_id, _VERB, body, _NONCE, _PARENTS)
    ok, why = verify_proof(weave, kr, agent_cell, proof, _VERB, body, _NONCE, _PARENTS)
    assert ok, f"a proof must verify for its own request: {why}"


# A field to mutate, paired with a strategy for a value that must DIFFER from the
# canonical one. Every one of these four feeds `invocation_bind`.
_mutations = st.one_of(
    st.tuples(st.just("verb"), st.sampled_from(["ASSERT", "RETRACT", "ATTEST", "PAY", "invoke"])),
    st.tuples(st.just("nonce"), st.text(min_size=1, max_size=12).filter(lambda s: s != _NONCE)),
    st.tuples(
        st.just("parents"),
        st.lists(
            st.sampled_from(["evt:a", "evt:b", "evt:c", "evt:z"]), min_size=0, max_size=3
        ).filter(lambda p: p != _PARENTS),
    ),
    st.tuples(
        st.just("args"),
        st.dictionaries(
            st.sampled_from(["cost", "to", "text"]), st.integers(0, 9), min_size=1, max_size=2
        ),
    ),
)


@SETTINGS
@given(mutation=_mutations)
def test_proof_fails_when_any_bound_field_changes(mutation):
    """Present a proof minted for the canonical request against a request differing
    in ONE of {verb, nonce, parents, args}. verify_proof must fail closed — the
    invocation bind no longer matches (anti-replay)."""
    weave, kr, agent_cell, holder, cap_id = _proof_fixture()
    body = _body(cap_id)
    proof = build_proof(weave, kr, holder, cap_id, _VERB, body, _NONCE, _PARENTS)

    field, value = mutation
    verb, nonce, parents, mbody = _VERB, _NONCE, list(_PARENTS), _body(cap_id)
    if field == "verb":
        verb = value
    elif field == "nonce":
        nonce = value
    elif field == "parents":
        parents = value
    elif field == "args":
        mbody = _body(cap_id, value)

    # Sanity: the mutated request really does produce a different bind than the proof.
    assert invocation_bind(verb, mbody, nonce, parents) != proof["invocation_bind"]

    ok, why = verify_proof(weave, kr, agent_cell, proof, verb, mbody, nonce, parents)
    assert not ok, f"a proof must NOT verify a changed request (field={field}): {why}"


def test_proof_fails_on_tampered_signature_or_holder():
    """The possession side of the bind: a swapped-out signature, or a proof
    claiming a different holder, fails closed."""
    weave, kr, agent_cell, holder, cap_id = _proof_fixture()
    body = _body(cap_id)
    proof = build_proof(weave, kr, holder, cap_id, _VERB, body, _NONCE, _PARENTS)

    # (a) tampered signature — sign the bind under a DIFFERENT key (an impostor).
    forged = dict(proof)
    impostor = kr.mint("impostor", "agent").id
    forged["holder_sig"] = kr.sign(impostor, proof["invocation_bind"])
    ok, why = verify_proof(weave, kr, agent_cell, forged, _VERB, body, _NONCE, _PARENTS)
    assert not ok and "signature" in why, f"a forged signature must fail: {why}"

    # (b) wrong holder — not the acting agent.
    swapped = dict(proof, holder=impostor)
    ok, why = verify_proof(weave, kr, agent_cell, swapped, _VERB, body, _NONCE, _PARENTS)
    assert not ok, f"a proof for a different holder must fail: {why}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. REVOCATION INVALIDATES DESCENDANTS — the DERIVED_AUTHORITY cascade
# ─────────────────────────────────────────────────────────────────────────────
def _delegation_chain():
    """Build parent → child → grandchild grants + their holder agent cells on a Weft.
    Returns (weft, root, ids…, principals…) so a test can revoke then re-fold."""
    weft, kr = _fresh_weft()
    root = kr.mint("root", "human").id
    a = kr.mint("agent-A", "agent").id
    b = kr.mint("agent-B", "agent").id
    c = kr.mint("agent-C", "agent").id

    parent = capability_content("echo", "echo", caveats={}, grantee=a, granter=root)
    child = attenuate(parent, {}, parent_id="cap:parent", grantee=b, granter=a)
    grand = attenuate(child, {}, parent_id="cap:child", grantee=c, granter=b)

    _assert_cap(weft, root, "cap:parent", parent)
    _assert_cap(weft, a, "cap:child", child)
    _assert_cap(weft, b, "cap:grand", grand)

    _assert_agent(weft, root, "agent:A", a, ["cap:parent"])
    _assert_agent(weft, root, "agent:B", b, ["cap:child"])
    _assert_agent(weft, root, "agent:C", c, ["cap:grand"])
    return weft, root, a, b, c


def test_revocation_cascades_to_every_descendant_grant():
    """Revoke a PARENT grant with the DERIVED_AUTHORITY cascade; authorize() under
    the child AND grandchild grants both fail CLOSED, while the parent holder does
    too — the cascade the fold derives reaches every descendant of the revoked grant."""
    weft, root, a, b, c = _delegation_chain()

    # BEFORE revocation: every holder authorizes through its own grant.
    w0 = Weave.fold(weft)
    agentA0, agentB0, agentC0 = w0.get("agent:A"), w0.get("agent:B"), w0.get("agent:C")
    assert agentA0 is not None and agentB0 is not None and agentC0 is not None
    okA, _ = authorize(w0, agentA0, "cap:parent", {}, a)
    okB, _ = authorize(w0, agentB0, "cap:child", {}, b)
    okC, _ = authorize(w0, agentC0, "cap:grand", {}, c)
    assert okA and okB and okC, "the whole chain must authorize before revocation"

    # RETRACT the PARENT grant with the capability-revocation cascade (FOLD §10.2).
    weft.append(
        root, RETRACT, {"cell": "cap:parent", "mode": "REVOKE", "cascade": "DERIVED_AUTHORITY"}
    )
    w1 = Weave.fold(weft)

    # The cascade materializes in the fold: parent is a cascade root; child and
    # grandchild are marked retracted BY the cascade (not by their own RETRACT).
    capParent1, capChild1, capGrand1 = (
        w1.get("cap:parent"),
        w1.get("cap:child"),
        w1.get("cap:grand"),
    )
    assert capParent1 is not None and capChild1 is not None and capGrand1 is not None
    assert capParent1.retracted and capParent1.cascade_root
    assert capChild1.retracted and capChild1.cascaded, (
        "the child grant must fail closed via the cascade"
    )
    assert capGrand1.retracted and capGrand1.cascaded, (
        "the grandchild grant must fail closed via the cascade (transitive)"
    )

    # The cascaded grants drop out of every projection.
    live = {cap.id for cap in w1.of_type("capability")}
    assert "cap:parent" not in live and "cap:child" not in live and "cap:grand" not in live

    # AFTER revocation: authorize() fails CLOSED for the parent holder AND every
    # descendant holder — this is the load-bearing invariant of this lane.
    agentA1, agentB1, agentC1 = w1.get("agent:A"), w1.get("agent:B"), w1.get("agent:C")
    assert agentA1 is not None and agentB1 is not None and agentC1 is not None
    postA, whyA = authorize(w1, agentA1, "cap:parent", {}, a)
    postB, whyB = authorize(w1, agentB1, "cap:child", {}, b)
    postC, whyC = authorize(w1, agentC1, "cap:grand", {}, c)
    assert not postA, f"parent holder must fail closed after revoke: {whyA}"
    assert not postB, f"child holder must fail closed via cascade: {whyB}"
    assert not postC, f"grandchild holder must fail closed via cascade: {whyC}"

    # Time-travel: at the pre-revoke frontier the chain still authorizes — the
    # cascade is strictly AFTER its frontier, never a rewrite of history.
    past = Weave.fold(weft, upto_seq=weft.count() - 1)
    agentB_past = past.get("agent:B")
    assert agentB_past is not None
    okB_past, _ = authorize(past, agentB_past, "cap:child", {}, b)
    assert okB_past, "at the pre-revoke frontier the child grant must still authorize"


def test_direct_child_revocation_also_fails_the_grandchild():
    """Revoking the MIDDLE grant cascades to the grandchild only — the parent, above
    the revocation, stays live (the cascade flows strictly DOWNHILL)."""
    weft, root, a, b, c = _delegation_chain()
    weft.append(
        root, RETRACT, {"cell": "cap:child", "mode": "REVOKE", "cascade": "DERIVED_AUTHORITY"}
    )
    w = Weave.fold(weft)
    agentA, agentB, agentC = w.get("agent:A"), w.get("agent:B"), w.get("agent:C")
    assert agentA is not None and agentB is not None and agentC is not None

    okA, _ = authorize(w, agentA, "cap:parent", {}, a)
    assert okA, "the parent (above the revocation) must remain authorizable"

    okB, _ = authorize(w, agentB, "cap:child", {}, b)
    okC, _ = authorize(w, agentC, "cap:grand", {}, c)
    assert not okB, "the revoked middle grant must fail closed"
    assert not okC, "the grandchild must fail closed via the cascade"
