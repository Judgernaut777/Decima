"""T2.4 — per-invocation authority RE-CHECK on ingest (WEFT §2 item 7).

`Weft.ingest` proved integrity and causality; authority was judged only at the ORIGIN, so
a peer could sign a well-formed INVOKE naming a capability it never held — or one revoked
before it acted — and the union accepted it. These tests pin the closed gap:

  * a legitimately-authorized foreign INVOKE STILL ingests (the property that must not
    regress), including when the authority it used is revoked or consumed AFTERWARD;
  * a forged one (no grant, no envelope, revoked at the frontier, a replayed proof, a
    missing proof, a proof made by someone else) is REFUSED — terminal, nothing inserted;
  * the decision is taken at the event's CAUSAL FRONTIER, never against mutable current
    state, so it is independent of the order a feed delivered events.

Everything runs in-process against the real kernel with a fixed seed and fixed nonces —
no wall-clock, no unseeded randomness — so the statuses asserted here are deterministic.
Refusals are exercised through a HOSTILE origin: `weft.append` signs whatever its author
asks for (authority lives at the invoke seam, not at the log), which is exactly the peer
a receiving weft must not trust.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from decima.kernel import acceptance
from decima.kernel import capability as C
from decima.kernel.capability import build_proof, capability_content
from decima.kernel.crypto import Keyring
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import ASSERT, INVOKE, RETRACT, Weft

SEED = bytes(32)  # fixed, deterministic


def _weft(kr):
    return Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), kr)


def _realm():
    """A keyring plus an origin weft holding a grant to alice and her agent cell.

    Returns (kr, origin, root, alice, mallory)."""
    kr = Keyring(seed=SEED)
    root = kr.mint("root", "root").id
    alice = kr.mint("alice", "agent").id
    mallory = kr.mint("mallory", "agent").id
    origin = _weft(kr)
    assert_content(
        origin,
        root,
        "cap:echo",
        "capability",
        capability_content("echo", "transform", grantee=alice, granter=root),
    )
    assert_content(
        origin, root, "agent:alice", "agent", {"principal": alice, "envelope": ["cap:echo"]}
    )
    assert_content(origin, root, "agent:mal", "agent", {"principal": mallory, "envelope": []})
    return kr, origin, root, alice, mallory


def _invoke(origin, kr, holder, cap_id, args, nonce, *, parents=None, proof_for=None):
    """Append an INVOKE the way the kernel's invoke seam does: bind a proof to
    (verb, body, nonce, parents) and carry it in the body. `proof_for` builds the proof
    for a DIFFERENT body/holder (the forgery seam)."""
    parents = parents if parents is not None else ([origin.head] if origin.head else [])
    body = {"cap": cap_id, "args": args}
    weave = Weave.fold(origin)
    pbody, pholder = proof_for if proof_for is not None else (body, holder)
    proof = build_proof(weave, kr, pholder, cap_id, INVOKE, pbody, nonce, parents)
    return origin.append(
        holder, INVOKE, {**body, "nonce": nonce, "proof": proof}, authorized=cap_id, parents=parents
    )


def _rows(weft):
    return [
        (eid, payload, author, sig)
        for (eid, payload, author, sig) in weft.db.execute(
            "SELECT id, payload, author, sig FROM events ORDER BY seq ASC"
        )
    ]


def _sync(target, origin, order=None):
    """Ingest the origin's events into `target`, returning (verb, status) per row. `order`
    optionally re-orders the wire feed by event id (delivery order is under test)."""
    rows = _rows(origin)
    if order is not None:
        by_id = {r[0]: r for r in rows}
        rows = [by_id[eid] for eid in order]
    out = []
    for row in rows:
        payload = json.loads(row[1])
        out.append((payload["verb"], target.ingest(row)))
    return out


# ── the property that must not regress: a valid foreign INVOKE still ingests ─────


def test_legitimate_foreign_invoke_is_accepted() -> None:
    kr, origin, _root, alice, _mal = _realm()
    inv = _invoke(origin, kr, alice, "cap:echo", {"cost": 0}, "aa" * 16)
    target = _weft(kr)
    assert _sync(target, origin) == [
        ("ASSERT", "ingested"),
        ("ASSERT", "ingested"),
        ("ASSERT", "ingested"),
        ("INVOKE", "ingested"),
    ]
    assert target.count() == 4
    # and it survives the full read verification, so the log stays sound
    assert inv.id in {e.id for e in target.events()}


def test_capability_less_invoke_is_unaffected() -> None:
    """An INVOKE claiming NO capability has no authority to re-check (the pre-existing
    golden vectors keep ingesting) — it never authorized anything."""
    kr = Keyring(seed=SEED)
    author = kr.mint("tester", "human").id
    origin = _weft(kr)
    origin.append(author, INVOKE, {"target": "cap:1", "op": "run"})
    target = _weft(kr)
    assert _sync(target, origin) == [("INVOKE", "ingested")]


# ── forged / revoked authority is refused, terminally ───────────────────────────


def test_invoke_without_a_grant_is_refused() -> None:
    """Mallory signs an impeccable INVOKE for a capability that is not in her envelope
    and was granted to someone else. The signature proves WHO acted, never that she MAY."""
    kr, origin, _root, _alice, mallory = _realm()
    _invoke(origin, kr, mallory, "cap:echo", {}, "bb" * 16)
    target = _weft(kr)
    statuses = _sync(target, origin)
    assert statuses[-1] == ("INVOKE", "rejected:unauthorized-invoke")
    assert target.count() == 3  # the three ASSERTs only — the INVOKE never landed
    # terminal: re-delivery does not eventually admit it
    assert _sync(target, origin)[-1] == ("INVOKE", "rejected:unauthorized-invoke")
    assert target.count() == 3


def test_invoke_on_revoked_authority_is_refused() -> None:
    """The grant was REVOKED before the invocation, so it is dead at the event's own
    causal frontier — the proof cannot verify and the effect never enters the log."""
    kr, origin, root, alice, _mal = _realm()
    origin.append(root, RETRACT, {"cell": "cap:echo", "mode": "REVOKE"})
    _invoke(origin, kr, alice, "cap:echo", {}, "cc" * 16)
    target = _weft(kr)
    statuses = _sync(target, origin)
    assert statuses[-1] == ("INVOKE", "rejected:unauthorized-invoke")
    assert target.count() == 4  # 3 ASSERTs + the RETRACT
    assert not Weave.fold(target).invocations  # no effect was recorded


def test_proof_replayed_from_another_request_is_refused() -> None:
    """A captured proof is useless against a different request: the bind commits to
    (verb, body, nonce, parents), so swapping the args breaks it."""
    kr, origin, _root, alice, _mal = _realm()
    _invoke(
        origin,
        kr,
        alice,
        "cap:echo",
        {"amount": 500},  # what is actually invoked …
        "dd" * 16,
        proof_for=({"cap": "cap:echo", "args": {"amount": 5}}, alice),  # … proof bound to 5
    )
    target = _weft(kr)
    assert _sync(target, origin)[-1] == ("INVOKE", "rejected:unauthorized-invoke")
    assert target.count() == 3


def test_proof_made_by_another_principal_is_refused() -> None:
    """Mallory signs the event but attaches ALICE's proof — the holder is not the signer,
    so possession of a proof is not possession of the key that made it."""
    kr, origin, _root, alice, mallory = _realm()
    _invoke(
        origin,
        kr,
        mallory,
        "cap:echo",
        {},
        "ee" * 16,
        proof_for=({"cap": "cap:echo", "args": {}}, alice),
    )
    target = _weft(kr)
    assert _sync(target, origin)[-1] == ("INVOKE", "rejected:proof-holder-mismatch")
    assert target.count() == 3


def test_invoke_claiming_a_capability_without_a_proof_is_refused() -> None:
    """An INVOKE that names a grant but carries NO AuthorizationProof is refused: there
    is nothing to verify, and an unproven claim of authority fails closed."""
    kr, origin, _root, alice, _mal = _realm()
    origin.append(
        alice, INVOKE, {"cap": "cap:echo", "args": {}, "nonce": "ff" * 16}, authorized="cap:echo"
    )
    target = _weft(kr)
    assert _sync(target, origin)[-1] == ("INVOKE", "rejected:missing-authorization-proof")
    assert target.count() == 3


def test_proof_for_a_different_capability_is_refused() -> None:
    """Authority cannot be laundered: the proof must name the SAME grant the event
    claims (`authorized` / body `cap`)."""
    kr, origin, root, alice, _mal = _realm()
    assert_content(
        origin,
        root,
        "cap:other",
        "capability",
        capability_content("other", "transform", grantee=alice, granter=root),
    )
    weave = Weave.fold(origin)
    parents = [origin.head]
    nonce = "1a" * 16
    body = {"cap": "cap:echo", "args": {}}
    proof = build_proof(weave, kr, alice, "cap:other", INVOKE, body, nonce, parents)
    origin.append(alice, INVOKE, {**body, "nonce": nonce, "proof": proof}, authorized="cap:echo")
    target = _weft(kr)
    assert _sync(target, origin)[-1] == ("INVOKE", "rejected:missing-authorization-proof")


# ── judged at the FRONTIER, never against mutable current state ─────────────────


def test_concurrent_revocation_does_not_refuse_a_valid_invoke_in_either_order() -> None:
    """A revoke CONCURRENT with the invocation (both descend from the same frontier) is
    not in the invocation's ancestor closure, so the invoke is accepted — and acceptance
    is identical whichever order the feed delivers the two events (determinism)."""
    for revoke_first in (True, False):
        kr, origin, root, alice, _mal = _realm()
        frontier = origin.head
        inv = _invoke(origin, kr, alice, "cap:echo", {}, "2b" * 16, parents=[frontier])
        rev = origin.append(
            root, RETRACT, {"cell": "cap:echo", "mode": "REVOKE"}, parents=[frontier]
        )
        head_ids = [rev.id, inv.id] if revoke_first else [inv.id, rev.id]
        base = [r[0] for r in _rows(origin) if r[0] not in (inv.id, rev.id)]
        target = _weft(kr)
        statuses = _sync(target, origin, order=base + head_ids)
        assert all(s == "ingested" for _v, s in statuses), (revoke_first, statuses)
        assert target.count() == 5


def test_single_use_approval_consumed_after_the_invoke_still_ingests() -> None:
    """The sharpest "never judge against current state" case: a Morta-gated cap invoked
    under a single-use INVOCATION approval, which the origin CONSUMES (retracts) right
    after the invoke. That retraction is a DESCENDANT of the INVOKE, so at the event's own
    frontier the approval is still live and the invoke ingests."""
    kr = Keyring(seed=SEED)
    root = kr.mint("root", "root").id
    human = kr.mint("you", "human").id
    alice = kr.mint("alice", "agent").id
    origin = _weft(kr)
    assert_content(
        origin,
        root,
        "cap:pay",
        "capability",
        capability_content(
            "pay", "financial", grantee=alice, granter=root, caveats={"requires_approval": True}
        ),
    )
    assert_content(
        origin, root, "agent:alice", "agent", {"principal": alice, "envelope": ["cap:pay"]}
    )
    nonce = "3c" * 16
    body = {"cap": "cap:pay", "args": {"amount": 5}}
    ob = C.op_bind(INVOKE, body, nonce)
    aid = C.approval_id("cap:pay", ob)
    origin.append(
        human,
        ASSERT,
        {
            "cell": aid,
            "type": C.APPROVAL,
            "kind": "CONTENT",
            "content": {
                "capability": "cap:pay",
                "scope": "invocation",
                "op": ob,
                "approver": human,
            },
        },
    )
    _invoke(origin, kr, alice, "cap:pay", {"amount": 5}, nonce)
    origin.append(human, RETRACT, {"cell": aid, "mode": "WITHDRAW"})  # single-use, spent
    target = _weft(kr)
    statuses = _sync(target, origin)
    assert [s for _v, s in statuses] == ["ingested"] * 5, statuses
    # A gated invocation with NO approval anywhere on the log is still refused.
    kr2 = Keyring(seed=SEED)
    root2 = kr2.mint("root", "root").id
    alice2 = kr2.mint("alice", "agent").id
    origin2 = _weft(kr2)
    assert_content(
        origin2,
        root2,
        "cap:pay",
        "capability",
        capability_content(
            "pay", "financial", grantee=alice2, granter=root2, caveats={"requires_approval": True}
        ),
    )
    assert_content(
        origin2, root2, "agent:alice", "agent", {"principal": alice2, "envelope": ["cap:pay"]}
    )
    _invoke(origin2, kr2, alice2, "cap:pay", {"amount": 5}, "4d" * 16)
    assert _sync(_weft(kr2), origin2)[-1] == ("INVOKE", "rejected:unauthorized-invoke")


def test_malformed_authority_claims_fail_closed() -> None:
    """Every shape guard on the way to the ocap spine fails CLOSED (and never raises): a
    non-dict body, a non-dict proof, a missing nonce, a non-list parents, a proof naming a
    different holder, and a frontier that is not present locally."""
    kr, origin, _root, alice, _mal = _realm()
    base = {"author": alice, "authorized": "cap:echo", "verb": INVOKE, "lamport": 4}
    good_proof = {"capability": "cap:echo", "holder": alice}
    cases = [
        ({**base, "verb": ASSERT, "body": {}, "parents": []}, (True, "ok")),
        ({**base, "body": ["not", "a", "dict"], "parents": []}, (False, acceptance.NO_PROOF)),
        ({**base, "body": {"cap": "cap:echo"}, "parents": []}, (False, acceptance.NO_PROOF)),
        (
            {**base, "body": {"cap": "cap:echo", "proof": "nope"}, "parents": []},
            (False, acceptance.NO_PROOF),
        ),
        (
            {**base, "body": {"cap": "cap:echo", "proof": good_proof}, "parents": []},
            (False, acceptance.NO_PROOF),  # no nonce
        ),
        (
            {
                **base,
                "body": {"cap": "cap:echo", "nonce": "6f" * 16, "proof": good_proof},
                "parents": "not-a-list",
            },
            (False, acceptance.NO_PROOF),
        ),
        (
            {
                **base,
                "body": {
                    "cap": "cap:echo",
                    "nonce": "6f" * 16,
                    "proof": {"capability": "cap:other", "holder": alice},
                },
                "parents": [],
            },
            (False, acceptance.NO_PROOF),  # proof names a different grant
        ),
        (
            {
                **base,
                "body": {
                    "cap": "cap:echo",
                    "nonce": "6f" * 16,
                    "proof": {"capability": "cap:echo", "holder": "someone-else"},
                },
                "parents": [],
            },
            (False, acceptance.HOLDER_MISMATCH),
        ),
        (
            {
                **base,
                "body": {"cap": "cap:echo", "nonce": "6f" * 16, "proof": good_proof},
                "parents": ["00" * 32],  # frontier absent locally → cannot be established
            },
            (False, acceptance.UNAUTHORIZED),
        ),
    ]
    for payload, expected in cases:
        assert acceptance.recheck_invoke_authority(origin, payload) == expected, payload


def test_frontier_fold_is_the_ancestor_closure_not_a_seq_prefix() -> None:
    """`Weave.fold_frontier` folds exactly the ancestor closure: a concurrent event that
    sits INSIDE the local seq prefix is excluded, and an unclosed frontier fails closed."""
    kr, origin, root, alice, _mal = _realm()
    frontier = origin.head
    rev = origin.append(root, RETRACT, {"cell": "cap:echo", "mode": "REVOKE"}, parents=[frontier])
    inv = _invoke(origin, kr, alice, "cap:echo", {}, "5e" * 16, parents=[frontier])
    assert rev.seq is not None and inv.seq is not None and rev.seq < inv.seq
    w = Weave.fold_frontier(origin, [frontier])
    cap = w.get("cap:echo")
    assert cap is not None and not cap.retracted  # the concurrent REVOKE is NOT an ancestor
    full = Weave.fold(origin).get("cap:echo")
    assert full is not None and full.retracted  # …though it IS retracted in the full fold
    with pytest.raises(ValueError):
        Weave.fold_frontier(origin, ["00" * 32])  # missing ancestor → fail closed
