"""Extended golden vectors (port milestone 2) — generated FROM the reference.

Run from heartbeat/ (so `import decima` resolves to the real reference):

    python3 ../rust/vectors/generate.py          # writes rust/vectors/extended_vectors.json

Deterministic BY CONSTRUCTION (vectors.py discipline): the fixed all-zero
master seed, a fixed event script, no wall-clock, no os.urandom. The INVOKE
bodies carry FIXED nonces (the kernel's real path uses os.urandom, which is
why vectors.py's `_fold_vector` avoids INVOKE; here the nonce is pinned so
the bytes are reproducible). Events are appended through the SAME public API
the reference itself uses everywhere — `weft.append(..., INVOKE, {...},
authorized=cap_id)` is exactly what kernel.invoke does after authorization
(kernel.py:165), and `weft.append(..., ATTEST, {"target_cell": ..., "claim":
...})` is exactly what reckoner.py:104 / promotion.py:165 do. Authorization
and proofs are kernel-layer concerns ABOVE the fold; the fold itself
(weave._apply) only reads the INVOKE/ATTEST bodies, so these vectors pin
fold-observable behavior honestly.

What it pins (v2 criterion 4):
  (a) the exact stored `payload` TEXT bytes at INSERT time — Python's
      json.dumps(payload, sort_keys=True) with DEFAULT separators
      (", ", ": ") and ensure_ascii=True (NOT the hashing.canonical bytes
      used for ids — the stored row is a different byte shape);
  (b) warm-start equality: reopen the SAME SQLite file, recover head/lamport
      via Weft._load_head, re-fold, and the state_root must equal the first
      session's fold;
  (c) an INVOKE (through a granted + attenuated capability) and ATTEST
      script: event ids/bodies/lamports, folded invocations, the
      per-capability invoke tally, folded attestations, and the extended
      state_root.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

# The reference heartbeat is a sibling of rust/; put it on sys.path so the
# script imports the REAL reference regardless of the invoking cwd (running
# `python3 generate.py` sets sys.path[0] to this file's directory, not the
# caller's heartbeat/ checkout).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "heartbeat"))

from decima.capability import attenuate, capability_content
from decima.crypto import Keyring
from decima.weave import MERGE_MV, Weave
from decima.weft import ASSERT, ATTEST, INVOKE, Weft

from decima import hashing, model

MASTER_SEED = bytes(32)

OUT_PATH = pathlib.Path(__file__).resolve().parent / "extended_vectors.json"
OUT_PATH_V3 = pathlib.Path(__file__).resolve().parent / "extended_vectors_v3.json"


def build() -> dict:
    kr = Keyring(seed=MASTER_SEED)
    author = kr.mint("tester", "human").id
    # A second principal attests — the reference folds an attestation from ANY
    # signer (evidence, not authority), so the "wrong signer" case is pinned
    # honestly rather than silently assumed.
    attester = kr.mint("attester", "agent").id

    tmp = tempfile.mkdtemp(prefix="decima-ext-vectors-")
    db = os.path.join(tmp, "weft.db")
    weft = Weft(db, kr)

    events: list[dict] = []

    def record(ev):
        events.append(
            {
                "id": ev.id,
                "verb": ev.verb,
                "lamport": ev.lamport,
                "authorized": ev.authorized,
                "body": ev.body,
            }
        )

    cid_type = model.define_type(weft, author, "note")
    record(model.assert_content(weft, author, "note:1", "note", {"text": "milestone two", "n": 1}))
    record(model.assert_content(weft, author, "note:1", "note", {"text": "attested note", "n": 2}))

    # Capability grant + downhill attenuation (same construction as v1).
    parent_cap = capability_content(
        name="pay",
        effect="pay",
        target="*",
        caveats={"budget": 100},
        grantee=author,
        granter=author,
    )
    parent_id = hashing.content_id({"cap": "pay", "v": 1})
    record(
        weft.append(
            author, ASSERT, {"cell": parent_id, "type": "capability", "content": parent_cap}
        )
    )
    child_cap = attenuate(parent_cap, {"budget": 40}, parent_id, grantee=author, granter=author)
    child_id = hashing.content_id({"cap": "pay", "v": 1, "att": 1})
    record(
        weft.append(author, ASSERT, {"cell": child_id, "type": "capability", "content": child_cap})
    )

    # Two INVOKEs through the granted (child) capability — the kernel's append
    # shape (kernel.py:165) with a pinned nonce instead of os.urandom.
    record(
        weft.append(
            author,
            INVOKE,
            {"cap": child_id, "args": {"amount": 10, "cost": 10}, "nonce": "decima-ext-nonce-1"},
            authorized=child_id,
        )
    )
    record(
        weft.append(
            author,
            INVOKE,
            {"cap": child_id, "args": {"amount": 5, "cost": 5}, "nonce": "decima-ext-nonce-2"},
            authorized=child_id,
        )
    )

    # ATTESTs (reckoner.py:104 shape): one by the author onto the note, one by
    # the OTHER principal (attestation from a different signer folds identically
    # — the fold records evidence, it does not gate on who signed).
    record(weft.append(author, ATTEST, {"target_cell": "note:1", "claim": "verified by author"}))
    record(weft.append(attester, ATTEST, {"target_cell": "note:1", "claim": "witnessed"}))
    record(weft.append(author, ATTEST, {"target_cell": child_id, "claim": "cap review ok"}))

    # (a) The exact stored payload TEXT bytes, straight from the table.
    stored = [
        {"seq": seq, "payload": payload}
        for seq, payload in weft.db.execute("SELECT seq, payload FROM events ORDER BY seq ASC")
    ]

    first = Weave.fold(weft)
    first_root = first.state_root()

    # (b) Warm start: reopen the SAME file, recover head/lamport from the log,
    # re-fold — the projection must be identical (FOLD §11.1).
    warm = Weft(db, kr)
    warm_head, warm_lamport = warm.head, warm.lamport
    warm_root = Weave.fold(warm).state_root()
    assert warm_root == first_root, "warm-start fold must equal first fold"
    assert warm_head == weft.head and warm_lamport == weft.lamport

    attestations = {
        cid: [dict(a) for a in first.cells[cid].attestations]
        for cid in sorted(first.cells)
        if first.cells[cid].attestations
    }

    return {
        "profile": (
            "decima:v0.1 — extended vectors (SQLite persistence, warm start, INVOKE/ATTEST fold)"
        ),
        "master_seed_hex": kr.master.hex(),
        "author_pid": author,
        "attester_pid": attester,
        "type_cell_id": cid_type,
        "parent_cap_id": parent_id,
        "child_cap_id": child_id,
        "events": events,
        "stored_payloads": stored,
        "head_after": weft.head,
        "lamport_after": weft.lamport,
        "event_count": weft.count(),
        "invocations": [
            {"event": i.event, "by": i.by, "cap": i.cap, "args": i.args} for i in first.invocations
        ],
        "invoke_counts": dict(sorted(first._invoke_counts.items())),
        "attestations": attestations,
        "state_root": first_root,
        "warm_head": warm_head,
        "warm_lamport": warm_lamport,
        "warm_state_root": warm_root,
        "warm_equals_first": warm_root == first_root,
    }


def dumps(vectors: dict) -> str:
    # Same serialization discipline as vectors.py.dumps: sorted keys,
    # ensure_ascii=False, trailing newline — stable across regenerations.
    return json.dumps(vectors, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


# ── v3 (port milestone 3): adjudication, promotion, receipts, leases ─────────
#
# Same discipline as build(): the fixed all-zero seed, a fixed event script, no
# wall-clock, no os.urandom (the INVOKE nonce is pinned; the anti-grinding loop
# is a deterministic content-id search both sides re-run). The script exercises
# the four milestone-3 features through the SAME public API the reference's own
# checks use (weft.append with explicit parents for forks — checks/70_merge.py;
# adjudication/promote ATTEST bodies — checks/71_merge_advanced.py,
# checks/408_promotion_canary.py; result receipts — checks/280_receipts.py;
# lease caveats — checks/200_leases.py).


def build_v3() -> dict:
    kr = Keyring(seed=MASTER_SEED)
    root = kr.mint("root", "human").id
    tester = kr.mint("tester", "human").id
    attester = kr.mint("attester", "agent").id
    reckoner = kr.mint("reckoner", "agent").id
    impostor = kr.mint("impostor", "agent").id
    attacker = kr.mint("attacker", "agent").id

    tmp = tempfile.mkdtemp(prefix="decima-v3-vectors-")
    weft = Weft(os.path.join(tmp, "weft.db"), kr)

    events: list[dict] = []

    def record(ev):
        events.append(
            {
                "id": ev.id,
                "verb": ev.verb,
                "lamport": ev.lamport,
                "authorized": ev.authorized,
                "body": ev.body,
            }
        )
        return ev

    def fold_cell(upto_seq: int, cid: str) -> dict:
        """The folded projection of ONE cell at a log prefix (the time-travel
        read a receipt consumer performs). Pins content/heads/flags verbatim."""
        w = Weave.fold(weft, upto_seq=upto_seq)
        c = w.cells[cid]
        return {
            "content": c.content,
            "content_heads": c.content_heads,
            "in_conflict": c.in_conflict,
            "quarantined": (c.content.get("quarantined") if isinstance(c.content, dict) else None),
        }

    # (1) GENESIS: the first event of the log is parentless and root-authored —
    #     the CONSTITUTIONAL genesis (seq 1) the promotion trust anchors on.
    genesis = record(
        weft.append(
            root,
            ASSERT,
            {
                "cell": "realm:genesis",
                "type": "realm",
                "content": {"name": "decima-v3", "founded": 1},
            },
        )
    )

    # (2) ATTEST adjudication-collapse (MERGE_SEMANTICS §4). An MV type, two
    #     CONCURRENT heads (both parent the shared base — the fork pattern of
    #     checks/70_merge.py), then a resolving ATTEST (a NON-root attester:
    #     the reference gates nothing on the adjudicator, so the vector pins
    #     that honestly).
    cid_headline = model.define_type(weft, root, "headline", merge_class=MERGE_MV)
    bel = hashing.content_id({"belief": "ownership"})
    base = weft.head
    pa = record(
        weft.append(
            tester,
            ASSERT,
            {
                "cell": bel,
                "type": "headline",
                "kind": "CONTENT",
                "content": {"text": "Alice owns it"},
            },
            parents=[base],
        )
    )
    pb = record(
        weft.append(
            attester,
            ASSERT,
            {
                "cell": bel,
                "type": "headline",
                "kind": "CONTENT",
                "content": {"text": "Bob owns it"},
            },
            parents=[base],
        )
    )
    mv_before = fold_cell(pb.seq, bel)
    adj = record(
        weft.append(
            attester,
            ATTEST,
            {
                "target_cell": bel,
                "predicate": "adjudicates",
                "resolution": "select",
                "winner": pb.id,
                "evidence": [pa.id, pb.id],
                "claim": "owner resolved",
            },
        )
    )
    mv_after = fold_cell(adj.seq, bel)

    # (3) Trusted promotion (NONA_RECKONER §7). The ROOT declares the reckoner
    #     a promoter for tier "pure"; a quarantined candidate cap declares that
    #     tier. An untrusted impostor's promote-ATTEST does NOT lift — even
    #     after the impostor SELF-DECLARES a forged promoter anchor. The
    #     root-declared reckoner's promote-ATTEST lifts (quarantined cleared,
    #     sandbox_only stripped).
    promoter_cell = hashing.content_id({"promoter": reckoner, "role": "root"})
    record(
        weft.append(
            root,
            ASSERT,
            {
                "cell": promoter_cell,
                "type": "promoter",
                "content": {"principal": reckoner, "tiers": ["pure"]},
            },
        )
    )
    cap_pure = hashing.content_id({"cap": "forge-pure", "v": 1})
    pure_content = capability_content(
        name="forge-pure",
        effect="forge",
        caveats={"sandbox_only": True},
        quarantined=True,
        grantee=tester,
        granter=tester,
    )
    pure_content["declared_effect_class"] = "pure"
    pure_ev = record(
        weft.append(
            tester, ASSERT, {"cell": cap_pure, "type": "capability", "content": pure_content}
        )
    )
    promoted = {"at_grant": fold_cell(pure_ev.seq, cap_pure)["quarantined"]}
    imp1 = record(
        weft.append(impostor, ATTEST, {"target_cell": cap_pure, "promote": True, "tier": "pure"})
    )
    promoted["after_impostor"] = fold_cell(imp1.seq, cap_pure)["quarantined"]
    forged_promoter = hashing.content_id({"promoter": impostor, "role": "self"})
    record(
        weft.append(
            impostor,
            ASSERT,
            {
                "cell": forged_promoter,
                "type": "promoter",
                "content": {"principal": impostor, "tiers": ["pure", "financial"]},
            },
        )
    )
    imp2 = record(
        weft.append(impostor, ATTEST, {"target_cell": cap_pure, "promote": True, "tier": "pure"})
    )
    promoted["after_forged_self_grant"] = fold_cell(imp2.seq, cap_pure)["quarantined"]
    rec1 = record(
        weft.append(reckoner, ATTEST, {"target_cell": cap_pure, "promote": True, "tier": "pure"})
    )
    after_reckoner = fold_cell(rec1.seq, cap_pure)
    promoted["after_reckoner"] = after_reckoner["quarantined"]
    promoted["final_content"] = after_reckoner["content"]

    # (4) Anti-grinding: the attacker mints a SECOND parentless event whose id
    #     sorts BEFORE the real genesis (the id is ground offline — a
    #     deterministic content-id search, then appended once). Under an
    #     id-order anchor it would fold FIRST and hijack the root; the
    #     seq-anchored root holds, so the attacker's self-declared "financial"
    #     promoter is IGNORED and its promote-ATTEST fails closed.
    n = 0
    while True:
        body = {
            "cell": "attacker:genesis",
            "type": "realm",
            "content": {"name": "usurper", "attempt": n},
        }
        payload = {
            "parents": [],
            "author": attacker,
            "authorized": None,
            "verb": ASSERT,
            "body": body,
            "lamport": 1,
        }
        if hashing.content_id(payload, kind="event") < genesis.id:
            break
        n += 1
    grinding_attempts = n
    attacker_genesis = record(weft.append(attacker, ASSERT, body, parents=[]))
    attacker_promoter = hashing.content_id({"promoter": attacker, "role": "usurp"})
    record(
        weft.append(
            attacker,
            ASSERT,
            {
                "cell": attacker_promoter,
                "type": "promoter",
                "content": {"principal": attacker, "tiers": ["financial"]},
            },
        )
    )
    cap_fin = hashing.content_id({"cap": "wire", "v": 1})
    fin_content = capability_content(
        name="wire",
        effect="pay",
        caveats={"sandbox_only": True},
        quarantined=True,
        grantee=attacker,
        granter=attacker,
    )
    fin_content["declared_effect_class"] = "financial"
    record(
        weft.append(
            attacker, ASSERT, {"cell": cap_fin, "type": "capability", "content": fin_content}
        )
    )
    atk1 = record(
        weft.append(
            attacker, ATTEST, {"target_cell": cap_fin, "promote": True, "tier": "financial"}
        )
    )
    anti_grinding = {
        "attacker_genesis_folds_first": attacker_genesis.id < genesis.id,
        "cap_fin_quarantined_after_attack": fold_cell(atk1.seq, cap_fin)["quarantined"],
    }

    # (5) EffectReceipt projections (WEFT §8): an UNKNOWN receipt reconciled by
    #     a later definite one sharing the idempotency key; an all-UNKNOWN key
    #     folds to None.
    key = "logical-op-v3-1"
    unk_id = hashing.content_id({"unknown_attempt": key})
    record(
        model.assert_content(
            weft,
            tester,
            unk_id,
            "result",
            {
                "of": None,
                "cap": "forge-pure",
                "status": "UNKNOWN",
                "attempt": 0,
                "idempotency": key,
                "out": None,
                "error": {"code": "ambiguous", "retryable": True, "message": "timeout"},
            },
        )
    )
    def_id = hashing.content_id({"definite_attempt": key})
    record(
        model.assert_content(
            weft,
            tester,
            def_id,
            "result",
            {
                "of": None,
                "cap": "forge-pure",
                "status": "SUCCEEDED",
                "attempt": 1,
                "idempotency": key,
                "supersedes": unk_id,
                "out": {"sha": "abc"},
                "cost": 3,
            },
        )
    )
    key_unknown_only = "logical-op-v3-2"
    unk2_id = hashing.content_id({"unknown_attempt": key_unknown_only})
    record(
        model.assert_content(
            weft,
            tester,
            unk2_id,
            "result",
            {
                "of": None,
                "cap": "forge-pure",
                "status": "UNKNOWN",
                "attempt": 0,
                "idempotency": key_unknown_only,
                "out": None,
            },
        )
    )

    # (6) LEASE1: a time-locked wallet (expires_at — set deterministically two
    #     lamports above the grant, so the fixed tail pushes the frontier past
    #     it) with a downhill sub-wallet, and a single-use card (max_uses 1)
    #     spent by one INVOKE. Both lapse at the final fold: lease_expired,
    #     retracted, DERIVED_AUTHORITY cascade roots.
    expires_at = weft.lamport + 2
    wallet = capability_content(
        name="wallet",
        effect="pay",
        caveats={"expires_at": expires_at},
        grantee=tester,
        granter=tester,
    )
    wallet_id = hashing.content_id({"cap": "wallet", "v": 1})
    record(
        weft.append(tester, ASSERT, {"cell": wallet_id, "type": "capability", "content": wallet})
    )
    subwallet = attenuate(wallet, {"budget": 10}, wallet_id, grantee=tester, granter=tester)
    subwallet_id = hashing.content_id({"cap": "wallet", "v": 1, "att": 1})
    record(
        weft.append(
            tester, ASSERT, {"cell": subwallet_id, "type": "capability", "content": subwallet}
        )
    )
    # A downstream grant whose authority descends from the wallet but which
    # carries NO lease caveat of its own (a budget-only view): it fails closed
    # PURELY by the DERIVED_AUTHORITY cascade (`cascaded`), never by its own
    # lease derivation — the cascade-to-child path (checks/200_leases.py).
    view = capability_content(
        name="wallet-view",
        effect="pay",
        caveats={"budget": 5},
        parent=wallet_id,
        grantee=tester,
        granter=tester,
    )
    view_id = hashing.content_id({"cap": "wallet-view", "v": 1})
    record(weft.append(tester, ASSERT, {"cell": view_id, "type": "capability", "content": view}))
    card = capability_content(
        name="card", effect="pay", caveats={"max_uses": 1}, grantee=tester, granter=tester
    )
    card_id = hashing.content_id({"cap": "card", "v": 1})
    record(weft.append(tester, ASSERT, {"cell": card_id, "type": "capability", "content": card}))
    record(
        weft.append(
            tester,
            INVOKE,
            {"cap": card_id, "args": {"amount": 1, "cost": 1}, "nonce": "decima-v3-nonce-1"},
            authorized=card_id,
        )
    )

    final = Weave.fold(weft)
    receipts = {
        "order": [c.id for c in final.receipts_for_idempotency(key)],
        "canonical": (lambda c: c.id if c else None)(final.canonical_for_idempotency(key)),
        "unknown_only_order": [c.id for c in final.receipts_for_idempotency(key_unknown_only)],
        "unknown_only_canonical": (lambda c: c.id if c else None)(
            final.canonical_for_idempotency(key_unknown_only)
        ),
    }
    lease_outcomes = {
        cid: {
            "lease_expired": final.cells[cid].lease_expired,
            "retracted": final.cells[cid].retracted,
            "cascade_root": final.cells[cid].cascade_root,
            "cascaded": final.cells[cid].cascaded,
        }
        for cid in (wallet_id, subwallet_id, view_id, card_id)
    }

    return {
        "profile": "decima:v0.1 — v3 vectors (adjudication, promotion, receipts, leases)",
        "master_seed_hex": kr.master.hex(),
        "principals": {
            "root": root,
            "tester": tester,
            "attester": attester,
            "reckoner": reckoner,
            "impostor": impostor,
            "attacker": attacker,
        },
        "genesis_event_id": genesis.id,
        "grinding_attempts": grinding_attempts,
        "attacker_genesis_event_id": attacker_genesis.id,
        "type_headline_id": cid_headline,
        "belief_cell": bel,
        "promoter_cell_id": promoter_cell,
        "forged_promoter_cell_id": forged_promoter,
        "attacker_promoter_cell_id": attacker_promoter,
        "cap_pure_id": cap_pure,
        "cap_fin_id": cap_fin,
        "wallet_id": wallet_id,
        "subwallet_id": subwallet_id,
        "wallet_view_id": view_id,
        "card_id": card_id,
        "expires_at": expires_at,
        "receipt_key": key,
        "receipt_unknown_id": unk_id,
        "receipt_definite_id": def_id,
        "receipt_key_unknown_only": key_unknown_only,
        "receipt_unknown_only_id": unk2_id,
        "events": events,
        "mv_before": mv_before,
        "mv_after": mv_after,
        "promotion": promoted,
        "anti_grinding": anti_grinding,
        "receipts": receipts,
        "leases": {
            "frontier_lamport": final.frontier_lamport,
            "invoke_counts": dict(sorted(final._invoke_counts.items())),
            "outcomes": lease_outcomes,
        },
        "genesis_author": final._genesis_author,
        "state_root": final.state_root(),
        "head_after": weft.head,
        "lamport_after": weft.lamport,
        "event_count": weft.count(),
    }


def main() -> None:
    vectors = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(dumps(vectors), encoding="utf-8")
    print(f"wrote {OUT_PATH}")
    print(
        f"  events: {vectors['event_count']} "
        f"({sum(1 for e in vectors['events'] if e['verb'] == 'INVOKE')} INVOKE, "
        f"{sum(1 for e in vectors['events'] if e['verb'] == 'ATTEST')} ATTEST)"
    )
    print(f"  invoke_counts: {vectors['invoke_counts']}")
    print(
        f"  state_root: {vectors['state_root'][:24]}… "
        f"(warm-start equal: {vectors['warm_equals_first']})"
    )

    v3 = build_v3()
    OUT_PATH_V3.write_text(dumps(v3), encoding="utf-8")
    print(f"wrote {OUT_PATH_V3}")
    print(f"  events: {v3['event_count']} (grinding attempts: {v3['grinding_attempts']})")
    print(
        f"  mv: {len(v3['mv_before']['content_heads'])} heads → "
        f"{len(v3['mv_after']['content_heads'])} after adjudication"
    )
    print(
        f"  promotion: {v3['promotion']['at_grant']} → impostor {v3['promotion']['after_impostor']}"
        f" → forged {v3['promotion']['after_forged_self_grant']}"
        f" → reckoner {v3['promotion']['after_reckoner']}"
    )
    print(f"  anti-grinding root holds: {v3['genesis_author'] == v3['principals']['root']}")
    print(
        f"  leases: frontier {v3['leases']['frontier_lamport']} "
        f"(expires_at {v3['expires_at']}), outcomes {v3['leases']['outcomes']}"
    )
    print(f"  state_root: {v3['state_root'][:24]}…")


if __name__ == "__main__":
    main()
