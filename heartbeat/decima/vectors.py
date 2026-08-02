"""Conformance golden vectors — the byte-for-byte target the Rust port must reproduce.

Batch D (the P6 on-ramp, VISION.md → "The single Rust port"). Phase 6 is gated on the
reference being stable and roadmap-green; when the port begins it must be proven to
reproduce the reference's *observable* bytes exactly, not merely "pass some tests." A
Rust reimplementation of the kernel is correct iff, from the SAME inputs, it emits the
SAME canonical bytes, the SAME content/event ids, the SAME Ed25519 signatures, and folds
to the SAME `state_root`. This module is the machine-checkable statement of that
contract.

It is deterministic BY CONSTRUCTION — a fixed all-zero master seed, a fixed event
script, no wall-clock, no `os.urandom`, no floats (PROFILE.md §1). `build()` re-derives
the whole vector set from the running heartbeat reference through its PUBLIC API only
(`hashing`, `crypto.Keyring`, `weft.Weft`, `weave.Weave`, `model`, `capability`), so the
committed golden file (`protocol/reference_vectors.json`) is not a hand-authored fixture
that can drift from the code — it IS the code's output, frozen. The oracle check
(`checks/510_reference_vectors.py`) rebuilds the vectors in-process and asserts they
equal the committed bytes: any change to canonical encoding, id derivation, the signing
scheme, or the fold that would silently break a port turns the oracle RED here, in the
reference, the moment it lands.

What each section pins (and why a port needs it):

  • ``canonical`` / ``blobs`` — DEC-010/DEC-030: the canonical byte encoding and the
    domain-separated content address. The cell-id and event-id spaces are DISJOINT
    (different domain tag), key order is irrelevant, NFC-equivalent strings collapse to
    one identity, big integers survive, and empty containers encode. A port that gets
    any of these wrong forks the entire id space.
  • ``principals`` — the identity derivation: a NAMED pid = blake2b(name) and a
    SELF-CERTIFYING keyed pid = blake2b(public_key), each with its Ed25519 public key.
    A port must derive the same keys from the same master seed or no warm-started Weft
    it produces would verify against one this reference wrote.
  • ``signatures`` — Ed25519 (RFC 8032) is deterministic, so signatures ARE golden: the
    same (seed, message) must yield the same 64-byte signature under any conformant
    implementation (ed25519-dalek included). Each vector is self-checked to verify, and
    the check additionally proves a one-byte tamper fails closed.
  • ``fold`` — the load-bearing one: a fixed event script exercising a TYPE_DEF, a
    versioned CONTENT cell, a capability grant, a downhill ATTENUATION of it, a typed
    EDGE, and a RETRACT — folded to a `state_root`. This is the whole kernel (append +
    verify + deterministic fold) reduced to a single comparable digest.

Run ``python3 -m decima.vectors`` (from ``heartbeat/``) to regenerate the committed file
after a DELIBERATE, reviewed protocol change; never edit the JSON by hand.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile

from decima import hashing, model
from decima.capability import attenuate, capability_content
from decima.crypto import Keyring
from decima.weave import Weave
from decima.weft import ASSERT, RETRACT, Weft

# The committed golden artifact. `parents[1]` == the heartbeat/ root, so this sits at
# heartbeat/protocol/reference_vectors.json alongside run.py/smoke.py's world.
GOLDEN_PATH = pathlib.Path(__file__).resolve().parents[1] / "protocol" / "reference_vectors.json"

# A fixed master seed → every derived key, pid, and signature below is reproducible.
MASTER_SEED = bytes(32)

# ── canonical-encoding + content-addressing edge cases ──────────────────────────
# Chosen to pin the properties a port most easily gets wrong: key-order invariance,
# NFC collapse (a combining sequence and its precomposed form are ONE identity),
# arbitrary-precision integers, empty containers, deep nesting, and non-ASCII text.
_CANON_PAYLOADS = [
    {"a": 1, "b": 2},
    {"b": 2, "a": 1},                                   # key order must not matter
    {"nested": {"x": [1, 2, 3], "y": {"z": True}}, "n": None},
    {"accent": "\u00e9", "note": "nfc"},               # é precomposed (U+00E9)
    {"accent": "e\u0301", "note": "nfc"},              # e + U+0301 → NFC-equal to the line above
    {"unicode": "café 王 \U0001f600"},
    {"empty_map": {}, "empty_list": [], "zero": 0, "neg": -5},
    {"big_int": 123456789012345678901234567890},
    {"text": "the loom is weaving"},
]

_BLOBS = [b"", b"hello, fates", bytes(range(256))]

# ── deterministic signature messages (Ed25519 is deterministic → signatures golden) ─
_SIGN_MESSAGES = [
    "",
    "the fates do not negotiate",
    "evt_content_id_stands_in_for_any_event",
    "café \U0001f600",                             # non-ASCII round-trips through UTF-8
]


def _canonical_vectors() -> list[dict]:
    out = []
    for p in _CANON_PAYLOADS:
        out.append({
            "payload": p,
            "canonical_hex": hashing.canonical(p).hex(),
            "content_id_cell": hashing.content_id(p, kind="cell"),
            "content_id_event": hashing.content_id(p, kind="event"),
        })
    return out


def _blob_vectors() -> list[dict]:
    return [{"data_hex": b.hex(), "blob_id": hashing.blob_id(b)} for b in _BLOBS]


def _principal_vectors(kr: Keyring) -> dict:
    named = []
    for name, kind in (("root", "root"), ("you", "human"), ("decima", "agent"),
                       ("nona", "reckoner")):
        p = kr.mint(name, kind)
        named.append({"name": name, "kind": kind, "pid": p.id,
                      "public_key": kr.public_key(p.id)})
    keyed = []
    for name in ("peer-a", "peer-b"):
        p = kr.mint_keyed(name, "agent")
        keyed.append({"name": name, "pid": p.id, "public_key": kr.public_key(p.id),
                      "self_certifies": Keyring.keyed_pid(kr.public_key(p.id)) == p.id})
    return {"master_seed_hex": kr.master.hex(), "named": named, "keyed": keyed}


def _signature_vectors(kr: Keyring, signer_pid: str) -> list[dict]:
    out = []
    for msg in _SIGN_MESSAGES:
        sig = kr.sign(signer_pid, msg)
        out.append({"signer_pid": signer_pid, "message": msg, "sig": sig,
                    "verifies": kr.verify(signer_pid, msg, sig)})
    return out


def _fold_vector(kr: Keyring) -> dict:
    """A fixed ASSERT/RETRACT script — no INVOKE (its nonce is random) — so the fold is
    fully reproducible. Exercises TYPE_DEF, versioned CONTENT, a capability grant, a
    downhill attenuation of it, a typed EDGE, and a RETRACT."""
    author = kr.mint("tester", "human").id
    db = os.path.join(tempfile.mkdtemp(prefix="decima-vectors-"), "weft.db")
    weft = Weft(db, kr)

    events: list[dict] = []

    def record(ev):
        events.append({"id": ev.id, "verb": ev.verb, "lamport": ev.lamport,
                       "body": ev.body})

    cid_type = model.define_type(weft, author, "note")
    record(model.assert_content(weft, author, "note:1", "note", {"text": "first", "n": 1}))
    record(model.assert_content(weft, author, "note:1", "note", {"text": "edited", "n": 2}))

    # A capability grant and its downhill attenuation — the security spine a port must
    # fold identically (MORTA §5: a child grant's invocation set ⊆ the parent's).
    parent_cap = capability_content(name="pay", effect="pay", target="*",
                                    caveats={"budget": 100}, grantee=author, granter=author)
    parent_id = hashing.content_id({"cap": "pay", "v": 1})
    record(weft.append(author, ASSERT,
                       {"cell": parent_id, "type": "capability", "content": parent_cap}))
    child_cap = attenuate(parent_cap, {"budget": 40, "requires_approval": True},
                          parent_id, grantee=author, granter=author)
    child_id = hashing.content_id({"cap": "pay", "v": 1, "att": 1})
    record(weft.append(author, ASSERT,
                       {"cell": child_id, "type": "capability", "content": child_cap}))

    record(model.assert_edge(weft, author, child_id, "attenuates", parent_id))
    record(weft.append(author, RETRACT, {"cell": "note:1", "mode": "WITHDRAW"}))

    final = Weave.fold(weft)
    return {
        "master_seed_hex": kr.master.hex(),
        "author_pid": author,
        "type_cell_id": cid_type,
        "parent_cap_id": parent_id,
        "child_cap_id": child_id,
        "events": events,
        "state_root": final.state_root(),
        "type_counts": {t: len(final.of_type(t))
                        for t in ("note", "type", "capability")},
        "event_count": weft.count(),
    }


def build() -> dict:
    """Re-derive the full golden vector set from the reference. Pure/deterministic:
    same output every call, no wall-clock, no randomness, no floats."""
    kr = Keyring(seed=MASTER_SEED)
    principals = _principal_vectors(kr)
    signer = principals["named"][0]["pid"]              # "root", the constitutional author
    return {
        "profile": "decima:v0.1 — heartbeat reference (BLAKE2b-128, sorted-key JSON UTF-8/NFC)",
        "hash": "BLAKE2b-128, domain-separated: HASH('decima:v0.1:' || kind || 0x00 || bytes)",
        "signature_scheme": "Ed25519 (RFC 8032) — deterministic, so signatures are golden",
        "canonical": _canonical_vectors(),
        "blobs": _blob_vectors(),
        "principals": principals,
        "signatures": _signature_vectors(kr, signer),
        "fold": _fold_vector(kr),
    }


def dumps(vectors: dict | None = None) -> str:
    """Canonical JSON serialization of the vectors — stable across regenerations
    (sorted keys, `ensure_ascii=False` so non-ASCII stays UTF-8, trailing newline)."""
    return json.dumps(vectors if vectors is not None else build(),
                      indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def main() -> None:
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    vectors = build()
    GOLDEN_PATH.write_text(dumps(vectors), encoding="utf-8")
    fold = vectors["fold"]
    print(f"wrote {GOLDEN_PATH}")
    print(f"  canonical: {len(vectors['canonical'])} payloads, {len(vectors['blobs'])} blobs")
    print(f"  principals: {len(vectors['principals']['named'])} named, "
          f"{len(vectors['principals']['keyed'])} keyed")
    print(f"  signatures: {len(vectors['signatures'])}")
    print(f"  fold: state_root={fold['state_root'][:24]}… over {fold['event_count']} events")


if __name__ == "__main__":
    main()
