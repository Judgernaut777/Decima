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
from decima.weave import Weave
from decima.weft import ASSERT, ATTEST, INVOKE, Weft

from decima import hashing, model

MASTER_SEED = bytes(32)

OUT_PATH = pathlib.Path(__file__).resolve().parent / "extended_vectors.json"


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


if __name__ == "__main__":
    main()
