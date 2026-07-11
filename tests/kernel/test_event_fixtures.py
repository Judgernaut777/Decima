"""DEC-030 — golden event vectors + the Weft acceptance gate (ingest).

Deterministic golden coverage of the four verbs round-tripping through
append -> events(), of content-id stability across awkward payload shapes
(unicode/NFC, empty collections, deep nesting, large integers), and of the
`weft.ingest` acceptance gate returning the EXACT status string for every
branch its docstring enumerates.

Everything is built in-process against the REAL extracted kernel
(`decima.kernel`) with a fixed seed and no wall-clock / unseeded randomness,
so the ids and statuses asserted here are stable golden vectors.
"""

from __future__ import annotations

import json
import os
import tempfile
import unicodedata

from decima.kernel.crypto import Keyring
from decima.kernel.hashing import content_id, nfc_deep
from decima.kernel.weft import ASSERT, ATTEST, INVOKE, RETRACT, VERBS, Weft

SEED = bytes(32)  # fixed, deterministic


def _fresh_weft() -> tuple[Weft, str, Keyring]:
    kr = Keyring(seed=SEED)
    author = kr.mint("tester", "human").id
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    return Weft(db, kr), author, kr


def _build_row(
    kr: Keyring,
    author: str,
    verb: str,
    body: dict,
    parents: list,
    lamport: int,
    authorized=None,
    wire_author: str | None = None,
):
    """Forge ONE wire record `(id, payload_text, author, sig)` exactly the way
    an honest peer's weft would, so we can hand it to `ingest` and then corrupt
    a single dimension to exercise each acceptance branch. `wire_author` lets
    the envelope author diverge from the payload author (author-mismatch)."""
    payload = {
        "parents": parents,
        "author": author,
        "authorized": authorized,
        "verb": verb,
        "body": nfc_deep(body),
        "lamport": lamport,
    }
    eid = content_id(payload, kind="event")
    sig = kr.sign(author, eid)
    payload_text = json.dumps(payload, sort_keys=True)
    return (eid, payload_text, wire_author or author, sig), payload, eid


# ── the four verbs round-trip append -> events() with matching ids ──────────────


def test_four_verbs_roundtrip_with_matching_ids() -> None:
    weft, author, _kr = _fresh_weft()
    appended = [
        weft.append(author, ASSERT, {"cell": "note:1", "text": "hello"}),
        weft.append(author, RETRACT, {"cell": "note:1", "mode": "WITHDRAW"}),
        weft.append(author, INVOKE, {"target": "cap:1", "op": "run"}),
        weft.append(author, ATTEST, {"subject": "note:1", "kind": "witness"}),
    ]
    assert [e.verb for e in appended] == list(VERBS)

    # events() re-reads from the DB, recomputing id + verifying signature on each.
    read = list(weft.events())
    assert [e.id for e in read] == [e.id for e in appended]
    assert [e.verb for e in read] == list(VERBS)
    # Each read event's id RECOMPUTES from its own payload (Law 4: id = content+cause).
    for e in read:
        assert content_id(e.hashed_payload(), kind="event") == e.id


# ── content-id stability across awkward payload shapes ──────────────────────────


def test_unicode_nfc_yields_identical_id_for_decomposed_and_precomposed() -> None:
    # "café" precomposed (é = U+00E9) vs decomposed (e + U+0301 combining acute).
    precomposed = "café"
    decomposed = "café"
    assert precomposed != decomposed  # distinct code-point strings
    assert unicodedata.normalize("NFC", decomposed) == precomposed

    w1, a1, _ = _fresh_weft()
    w2, a2, _ = _fresh_weft()
    assert a1 == a2  # same seed -> same principal id
    e1 = w1.append(a1, ASSERT, {"cell": "c", "text": precomposed})
    e2 = w2.append(a2, ASSERT, {"cell": "c", "text": decomposed})
    # NFC normalization on the way in makes the two byte-different inputs one identity.
    assert e1.id == e2.id
    # ...and the STORED body is the NFC (precomposed) form, not the raw decomposed input.
    assert e2.body["text"] == precomposed


def test_empty_nested_and_large_int_bodies_have_stable_ids() -> None:
    body = {
        "empty_map": {},
        "empty_list": [],
        "nested": {"a": {"b": {"c": [1, 2, {"d": "deep"}]}}},
        "big": 123456789012345678901234567890123456789,
        "neg_big": -98765432109876543210,
        "unicode_key_é": "v",
    }
    # Same content on two independent fresh wefts -> byte-identical id (determinism).
    w1, a1, _ = _fresh_weft()
    w2, a2, _ = _fresh_weft()
    e1 = w1.append(a1, ASSERT, body)
    e2 = w2.append(a2, ASSERT, body)
    assert e1.id == e2.id
    # Recomputing over the stored payload reproduces the id (no float drift, big ints ok).
    assert content_id(e1.hashed_payload(), kind="event") == e1.id
    # The large integers survived storage + fold-read exactly (no truncation to float).
    got = list(w1.events())[-1]
    assert got.body["big"] == 123456789012345678901234567890123456789
    assert got.body["neg_big"] == -98765432109876543210


# ── the ingest() acceptance gate: exact status for every branch ─────────────────


def test_ingest_well_formed_then_duplicate() -> None:
    _src, author, kr = _fresh_weft()
    target, _a, _kr = _fresh_weft()  # fresh, empty; SAME seed so kr verifies `author`
    row, _payload, _eid = _build_row(
        kr, author, ASSERT, {"cell": "n:1", "text": "peer"}, parents=[], lamport=1
    )
    assert target.ingest(row) == "ingested"
    assert target.ingest(row) == "duplicate"  # idempotent no-op the second time
    assert target.count() == 1


def test_ingest_tampered_payload_is_id_mismatch() -> None:
    _src, author, kr = _fresh_weft()
    target, _a, _kr = _fresh_weft()
    row, _payload, eid = _build_row(
        kr, author, ASSERT, {"cell": "n:1", "text": "honest"}, parents=[], lamport=1
    )
    # Flip one byte of the STORED body without recomputing the id -> id no longer binds.
    eid, payload_text, wire_author, sig = row
    tampered_text = payload_text.replace("honest", "forged")
    assert tampered_text != payload_text
    tampered = (eid, tampered_text, wire_author, sig)
    assert target.ingest(tampered) == "rejected:id-mismatch"
    assert target.count() == 0


def test_ingest_missing_parent_is_orphan() -> None:
    _src, author, kr = _fresh_weft()
    target, _a, _kr = _fresh_weft()
    # A causally-valid child whose single parent is NOT present in the target log.
    ghost_parent = "00" * 16
    row, _payload, _eid = _build_row(
        kr, author, ASSERT, {"cell": "n:2"}, parents=[ghost_parent], lamport=99
    )
    assert target.ingest(row) == "orphan"  # deferred, retryable, NEVER inserted
    assert target.count() == 0


def test_ingest_noncanonical_parents_is_rejected() -> None:
    _src, author, kr = _fresh_weft()
    target, _a, _kr = _fresh_weft()
    # parents present but NOT sorted (ff.. before 00..) -> violates WEFT §2.
    unsorted = ["ff" * 16, "00" * 16]
    assert unsorted != sorted(unsorted)
    row, _payload, _eid = _build_row(
        kr, author, ASSERT, {"cell": "n:3"}, parents=unsorted, lamport=1
    )
    assert target.ingest(row) == "rejected:parents-not-canonical"
    assert target.count() == 0


def test_ingest_author_mismatch_is_rejected() -> None:
    _src, author, kr = _fresh_weft()
    target, _a, _kr = _fresh_weft()
    other = kr.mint("someone-else", "human").id
    assert other != author
    # Envelope author (wire) diverges from the payload author.
    row, _payload, _eid = _build_row(
        kr, author, ASSERT, {"cell": "n:4"}, parents=[], lamport=1, wire_author=other
    )
    assert target.ingest(row) == "rejected:author-mismatch"
    assert target.count() == 0


def test_ingest_bad_verb_is_rejected() -> None:
    _src, author, kr = _fresh_weft()
    target, _a, _kr = _fresh_weft()
    row, _payload, _eid = _build_row(
        kr, author, "FROBNICATE", {"cell": "n:5"}, parents=[], lamport=1
    )
    assert "FROBNICATE" not in VERBS
    assert target.ingest(row) == "rejected:bad-verb"
    assert target.count() == 0


def test_ingest_forged_signature_is_rejected() -> None:
    _src, author, kr = _fresh_weft()
    target, _a, _kr = _fresh_weft()
    # A perfectly-formed genesis event whose signature is a valid-hex but WRONG sig.
    row, _payload, eid = _build_row(kr, author, ASSERT, {"cell": "n:6"}, parents=[], lamport=1)
    forged = (row[0], row[1], row[2], "00" * 64)
    assert target.ingest(forged) == "rejected:bad-signature"
    assert target.count() == 0


def test_ingest_forged_event_never_verifies_through_events() -> None:
    """Belt-and-suspenders: an accepted (ingested) foreign event still passes
    the full events() read-verification, proving ingest only admits genuinely
    valid events onto the append-only log."""
    _src, author, kr = _fresh_weft()
    target, _a, _kr = _fresh_weft()
    row, _payload, _eid = _build_row(kr, author, ATTEST, {"subject": "x"}, parents=[], lamport=1)
    assert target.ingest(row) == "ingested"
    read = list(target.events())  # would raise WeftError if id/sig did not hold
    assert len(read) == 1 and read[0].verb == ATTEST
