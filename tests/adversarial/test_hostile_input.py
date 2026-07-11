"""DEC-034 — ADVERSARIAL: hostile input FAILS CLOSED, the kernel never crashes.

Every test here drives the EXTRACTED kernel (decima.kernel) with input a hostile
peer or a corrupt disk could produce, and proves the kernel's fail-closed contract:

  - `weft.ingest(row)` (the cross-peer acceptance gate, WEFT §2) NEVER raises and
    NEVER silently accepts a malformed/forged/inconsistent event. It returns a
    DEFINED status string; a terminal `"rejected:<reason>"` inserts NOTHING.
  - `weft.events()` / `Weave.fold()` (the read path, Laws 1 & 4) raise a specific
    `WeftError` when the stored log has been truncated/tampered — never a silent
    accept and never an interpreter-level crash.
  - Content addressing (Law 4) canonicalizes Unicode: a decomposed (NFD) and a
    composed (NFC) spelling of the same text MUST land on the SAME id.

No wall-clock, no unseeded randomness in recorded content: a fixed 32-byte seed
derives every key deterministically, so these ids/roots are reproducible.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.hashing import content_id, nfc_deep
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import ASSERT, Weft, WeftError

# ── deterministic in-process fixture (no wall-clock, no unseeded random) ─────────

SEED = bytes(32)  # fixed seed → reproducible keys and ids


def _fresh_weft() -> tuple[Weft, Keyring, str]:
    """A fresh, empty Weft on a private temp db, plus its keyring and one author."""
    kr = Keyring(seed=SEED)
    author = kr.mint("peer", "human").id
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    return Weft(db, kr), kr, author


def _mkrow(
    kr, author, verb, body, parents, lamport, authorized=None, *, sign_msg=None, eid_override=None
):
    """Build a wire record `(id, payload_text, author, sig)` exactly as a networked
    sync transport would deliver it — the shape `weft.ingest` consumes. Every field
    is under test control so a single one can be made hostile in isolation:

      - `parents` is passed THROUGH (not auto-sorted) so a non-canonical frontier
        can be forged;
      - `sign_msg` lets us sign the WRONG bytes (a forged signature that verifies
        against nothing);
      - `eid_override` lets us claim an id the payload does not recompute to.
    """
    payload = {
        "parents": parents,
        "author": author,
        "authorized": authorized,
        "verb": verb,
        "body": body,
        "lamport": lamport,
    }
    # The honest id is the content-address the acceptance gate will recompute.
    eid = eid_override if eid_override is not None else content_id(payload, kind="event")
    sig = kr.sign(author, sign_msg if sign_msg is not None else eid)
    return (eid, json.dumps(payload, sort_keys=True), author, sig)


def _genesis_present(weft, kr, author):
    """Ingest ONE honest genesis event so parent-referencing children have a real
    causal anchor to descend from. Returns the genesis id. Also our positive control
    that the harness CAN produce an acceptable event (so rejections are meaningful)."""
    row = _mkrow(
        kr,
        author,
        ASSERT,
        {"cell": "root", "type": "note", "kind": "CONTENT", "content": {"text": "genesis"}},
        parents=[],
        lamport=1,
    )
    status = weft.ingest(row)
    assert status == "ingested", status
    assert weft.count() == 1
    return row[0]


def _assert_terminal_reject(weft, row, expect_status):
    """Ingest a hostile row and assert it fails CLOSED: a specific terminal status,
    NEVER raising, and NOTHING inserted."""
    before = weft.count()
    try:
        status = weft.ingest(row)  # must not raise — never crash the process
    except Exception as exc:  # pragma: no cover - a raise here is the bug
        raise AssertionError(
            f"ingest CRASHED on hostile input instead of failing closed: {exc!r}"
        ) from exc
    assert isinstance(status, str)
    assert status == expect_status, f"expected {expect_status!r}, got {status!r}"
    assert weft.count() == before, "a TERMINAL rejection must insert nothing"
    return status


# ── 1. malformed JSON payload ────────────────────────────────────────────────────


def test_malformed_json_payload_rejected():
    weft, kr, author = _fresh_weft()
    # Not JSON at all.
    _assert_terminal_reject(
        weft, ("someid", "{ this is not json", author, "00"), "rejected:malformed-payload"
    )
    # Valid JSON but not an object (a bare list / scalar is not an event payload).
    _assert_terminal_reject(
        weft, ("someid", "[1, 2, 3]", author, "00"), "rejected:malformed-payload"
    )
    _assert_terminal_reject(weft, ("someid", "42", author, "00"), "rejected:malformed-payload")
    assert weft.count() == 0


# ── 2. missing required fields ───────────────────────────────────────────────────


def test_missing_required_field_rejected():
    weft, kr, author = _fresh_weft()
    incomplete = {  # drops the mandatory "lamport" field
        "parents": [],
        "author": author,
        "authorized": None,
        "verb": ASSERT,
        "body": {"cell": "x", "type": "note", "kind": "CONTENT", "content": {}},
    }
    _assert_terminal_reject(
        weft, ("id", json.dumps(incomplete), author, "00"), "rejected:missing-fields"
    )


# ── 3. cyclic / self parent reference ────────────────────────────────────────────


def test_self_parent_reference_rejected():
    """An event that lists its OWN id as a parent is uncomputable (its id depends on
    its parents) — no honest producer can mint the fixpoint, so a forged self-cycle
    can never recompute to the id it claims and fails closed at the integrity gate.
    A dangling causal edge NEVER enters the append-only DAG."""
    weft, kr, author = _fresh_weft()
    claimed = "self0000cafef00d"  # the id the event pretends to be
    payload = {
        "parents": [claimed],
        "author": author,
        "authorized": None,
        "verb": ASSERT,
        "body": {"cell": "x", "type": "note", "kind": "CONTENT", "content": {}},
        "lamport": 1,
    }
    row = (claimed, json.dumps(payload, sort_keys=True), author, kr.sign(author, claimed))
    _assert_terminal_reject(weft, row, "rejected:id-mismatch")


# ── 4. impossible lamport (not 1 + max(parents)) ─────────────────────────────────


def test_impossible_lamport_rejected():
    weft, kr, author = _fresh_weft()
    g = _genesis_present(weft, kr, author)  # lamport 1
    # A correctly-signed, correctly-addressed child whose ONLY defect is a forged
    # clock: lamport 9 where 1 + max(parent lamports) = 2. The honesty check catches
    # a frontier-jumping clock even though signature + id are valid.
    row = _mkrow(
        kr,
        author,
        ASSERT,
        {"cell": "y", "type": "note", "kind": "CONTENT", "content": {}},
        parents=[g],
        lamport=9,
    )
    _assert_terminal_reject(weft, row, "rejected:bad-lamport")


# ── 5. non-canonical parents (unsorted frontier) ─────────────────────────────────


def test_non_canonical_parents_rejected():
    """WEFT §2: a frontier is a canonically SORTED id list. A payload whose parents
    are out of order is rejected before anything else is trusted (a peer normalizes
    its own frontier; a non-canonical one is a broken/forged producer)."""
    weft, kr, author = _fresh_weft()
    hi, lo = "ffffffffffffffff", "0000000000000000"
    assert [hi, lo] != sorted([hi, lo])  # genuinely out of canonical order
    row = _mkrow(
        kr,
        author,
        ASSERT,
        {"cell": "z", "type": "note", "kind": "CONTENT", "content": {}},
        parents=[hi, lo],
        lamport=2,
    )  # unsorted on purpose
    _assert_terminal_reject(weft, row, "rejected:parents-not-canonical")


# ── 6. forged signature ──────────────────────────────────────────────────────────


def test_forged_signature_rejected():
    """The id proves integrity; the signature proves AUTHORSHIP. A correctly-formed,
    correctly-addressed event carrying a signature over the WRONG bytes verifies
    against nothing — possession of a public id buys no authority (Law 2)."""
    weft, kr, author = _fresh_weft()
    g = _genesis_present(weft, kr, author)
    row = _mkrow(
        kr,
        author,
        ASSERT,
        {"cell": "y", "type": "note", "kind": "CONTENT", "content": {}},
        parents=[g],
        lamport=2,
        sign_msg="a-signature-over-completely-different-bytes",
    )
    _assert_terminal_reject(weft, row, "rejected:bad-signature")


# ── 7. payload whose id does not recompute ───────────────────────────────────────


def test_id_does_not_recompute_rejected():
    weft, kr, author = _fresh_weft()
    # A well-formed genesis payload, but the wire record claims an id the canonical
    # bytes do not hash to — a single edited byte changes the id (Law 4).
    row = _mkrow(
        kr,
        author,
        ASSERT,
        {"cell": "root", "type": "note", "kind": "CONTENT", "content": {"text": "genesis"}},
        parents=[],
        lamport=1,
        eid_override="deadbeefdeadbeef",
    )
    _assert_terminal_reject(weft, row, "rejected:id-mismatch")


def test_bad_verb_and_author_mismatch_rejected():
    """Two more §2 gates, each fail-closed and each inserting nothing."""
    weft, kr, author = _fresh_weft()
    # Unknown verb — outside the four-verb instruction set.
    bad_verb = {
        "parents": [],
        "author": author,
        "authorized": None,
        "verb": "DELETE",
        "body": {},
        "lamport": 1,
    }
    _assert_terminal_reject(
        weft, ("id", json.dumps(bad_verb), author, kr.sign(author, "id")), "rejected:bad-verb"
    )
    # Wire author disagrees with the payload author (a relabel attack).
    other = kr.mint("other", "agent").id
    payload = {
        "parents": [],
        "author": author,
        "authorized": None,
        "verb": ASSERT,
        "body": {"cell": "x", "type": "note", "kind": "CONTENT", "content": {}},
        "lamport": 1,
    }
    eid = content_id(payload, kind="event")
    _assert_terminal_reject(
        weft,
        (eid, json.dumps(payload, sort_keys=True), other, kr.sign(other, eid)),
        "rejected:author-mismatch",
    )


# ── 8. Unicode normalization ambiguity → SAME id (NFC canonicalization) ──────────


def test_unicode_nfc_ambiguity_canonicalizes_to_same_id():
    """Law 4 + WEFT §1: text is UTF-8/NFC. A COMPOSED (NFC) and a DECOMPOSED (NFD)
    spelling of the same string are DIFFERENT code-point sequences, but must address
    to the SAME event id — otherwise an attacker could smuggle two 'different' cells
    that render identically. Exercised through the real append path in two fresh
    wefts: identical genesis content ⇒ identical id AND byte-identical stored bytes."""
    composed = "café"  # U+00E9  é
    decomposed = "café"  # U+0065 U+0301  e + combining acute
    assert composed != decomposed  # genuinely distinct code points (not trivial)
    assert len(composed) != len(decomposed)

    wa, ka, aa = _fresh_weft()
    wb, kb, ab = _fresh_weft()
    ev_a = assert_content(wa, aa, "cell:x", "note", {"text": composed})
    ev_b = assert_content(wb, ab, "cell:x", "note", {"text": decomposed})

    # The ambiguity collapses: same content-address for both spellings.
    assert ev_a.id == ev_b.id, "NFD vs NFC content produced DIFFERENT event ids"
    # ...and the STORED, folded body is canonical NFC on both sides (nfc_deep pinned).
    assert ev_a.body == ev_b.body
    assert ev_a.body["content"]["text"] == nfc_deep(decomposed) == composed
    # Sanity that the fold itself agrees on one converged state.
    assert Weave.fold(wa).state_root() == Weave.fold(wb).state_root()


# ── 9. log truncation / tamper on the READ path → WeftError (never silent) ───────


def test_stored_byte_tamper_raises_on_read():
    """Laws 1 & 4 on read: edit ONE stored payload byte and `events()` recomputes the
    id, finds the mismatch, and raises WeftError — the tamper is DETECTED, never
    silently folded. The kernel raises a defined exception, it does not crash."""
    weft, kr, author = _fresh_weft()
    assert_content(weft, author, "note:1", "note", {"text": "keepme"})
    assert_content(weft, author, "note:2", "note", {"text": "secret"})
    # A clean read verifies every event.
    assert len(list(weft.events())) == 2

    # Corrupt a stored payload byte in place (a disk/db tamper).
    seq = weft.db.execute(
        "SELECT seq FROM events WHERE payload LIKE '%secret%' LIMIT 1"
    ).fetchone()[0]
    weft.db.execute(
        "UPDATE events SET payload = REPLACE(payload, 'secret', 'FORGED') WHERE seq=?", (seq,)
    )
    weft.db.commit()

    with pytest.raises(WeftError):
        list(weft.events())  # id no longer recomputes → tamper detected
    with pytest.raises(WeftError):
        Weave.fold(weft)  # the fold reads through events(), same guard


def test_signature_tamper_raises_on_read():
    """A tampered SIGNATURE (id intact, authorship broken) is caught on read too:
    the recomputed id matches but the signature no longer verifies → WeftError."""
    weft, kr, author = _fresh_weft()
    ev = assert_content(weft, author, "note:1", "note", {"text": "hi"})
    # Flip the signature to a valid-length but wrong signature (sign other bytes).
    forged = kr.sign(author, "not the event id")
    weft.db.execute("UPDATE events SET sig=? WHERE id=?", (forged, ev.id))
    weft.db.commit()
    with pytest.raises(WeftError):
        list(weft.events())
