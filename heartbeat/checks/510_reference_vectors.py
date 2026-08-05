"""CONFORMANCE GOLDEN VECTORS (Batch D — the P6 Rust-port on-ramp).

VISION.md gates Phase 6 (the single Rust port) on the reference being stable and
roadmap-green; when the port begins, "correct" must mean *reproduces the reference's
observable bytes exactly* — not "passes some tests." `decima/vectors.py` states that
contract as a committed golden artifact (`protocol/reference_vectors.json`): canonical
encoding, content/event ids, Ed25519 signatures, principal derivation, and a fold
`state_root`, all re-derived from the running reference through its PUBLIC API.

This check is the two-way lock that makes the artifact trustworthy:

  (1) NO DRIFT — `vectors.build()` is re-run in-process and asserted BYTE-IDENTICAL to
      the committed JSON. So the committed file can never silently diverge from the
      code (a hand-edit of the JSON, or a code change that moves the bytes, turns this
      RED), and equally: any change to canonical encoding, id derivation, the signing
      scheme, or the fold that a port would have to match shows up HERE, in the
      reference oracle, the moment it lands — exactly the "stable before you port"
      guarantee Phase 6 depends on.

  (2) INTERNALLY SOUND — the vectors are not merely echoed back; every load-bearing
      property a port must satisfy is re-verified from the raw bytes:
        • canonical bytes reproduce from each payload; the cell-id and event-id spaces
          are DISJOINT (domain separation); key order is irrelevant to identity;
        • NFC-equivalent payloads (precomposed é vs. e+combining-acute) collapse to ONE
          canonical encoding and ONE content id;
        • blob ids reproduce from the raw bytes;
        • each named/keyed principal's public key reproduces, and every keyed pid
          self-certifies (pid == blake2b(public_key));
        • every signature verifies under the reference's own verifier, AND a one-byte
          tamper of the message fails closed (Ed25519 integrity);
        • the fixed event script re-folds to the committed `state_root`, with the
          committed type counts and event count; and a single edited payload byte in
          the replayed Weft is caught on read (Law 1/4 tamper-evidence).

MUTATION → RED (the properties this guards): change the canonical encoder (e.g. drop
NFC, or stop sorting keys), the domain tag that separates cell ids from event ids, the
principal-id derivation, or the fold's `state_root` composition, and (1) breaks against
the committed bytes while the specific property in (2) breaks against first principles.
Hand-edit `protocol/reference_vectors.json` and (1) breaks immediately. Verified while
drafting: mutating the fold script (e.g. dropping the RETRACT) shifts `state_root` and
flips this RED; reverted before landing.

Owns its own derivation off `decima.vectors` — never touches the shared kernel `k` the
other sections built.

Contract: run(k, line). Fail loud (assert).
"""
from decima import hashing, vectors
from decima.crypto import Keyring
from decima.weave import Weave
from decima.weft import RETRACT, Weft, WeftError


def run(k, line):
    line("\n== CONFORMANCE GOLDEN VECTORS (Batch D — the Rust-port on-ramp) ==")

    built = vectors.build()
    golden = vectors.load_golden()

    # (1) NO DRIFT: the committed file IS the code's output — byte for byte.
    assert vectors.dumps(built) == vectors.dumps(golden), (
        "protocol/reference_vectors.json has drifted from decima.vectors.build() — "
        "regenerate with `python3 -m decima.vectors` (only after a DELIBERATE, reviewed "
        "protocol change) and never hand-edit the JSON")
    line(f"  committed golden matches the reference build byte-for-byte "
         f"({len(golden['canonical'])} canonical, {len(golden['signatures'])} sigs, "
         f"fold over {golden['fold']['event_count']} events)")

    # (2a) canonical bytes + domain-separated content addressing reproduce from payloads.
    for case in golden["canonical"]:
        p = case["payload"]
        assert hashing.canonical(p).hex() == case["canonical_hex"], p
        assert hashing.content_id(p, kind="cell") == case["content_id_cell"], p
        assert hashing.content_id(p, kind="event") == case["content_id_event"], p
        assert case["content_id_cell"] != case["content_id_event"], (
            "cell-id and event-id spaces must be domain-separated (disjoint)")

    # key order is irrelevant to identity (the sorted-key canonicalization).
    ab = next(c for c in golden["canonical"] if c["payload"] == {"a": 1, "b": 2})
    ba = next(c for c in golden["canonical"] if c["payload"] == {"b": 2, "a": 1})
    assert ab["content_id_cell"] == ba["content_id_cell"], "key order changed identity"

    # NFC-equivalent payloads collapse to ONE canonical encoding and ONE id.
    nfc = [c for c in golden["canonical"] if c["payload"].get("note") == "nfc"]
    assert len(nfc) == 2 and nfc[0]["payload"] != nfc[1]["payload"], (
        "expected two DISTINCT source payloads (precomposed vs combining) tagged nfc")
    assert nfc[0]["canonical_hex"] == nfc[1]["canonical_hex"], "NFC did not collapse the accent"
    assert nfc[0]["content_id_cell"] == nfc[1]["content_id_cell"], "NFC-equal payloads differ in id"
    line("  canonical: domain-separated ids · key-order invariant · NFC collapse — all reproduce")

    # (2b) blob ids reproduce from raw bytes.
    for case in golden["blobs"]:
        assert hashing.blob_id(bytes.fromhex(case["data_hex"])) == case["blob_id"]

    # (2c) principal derivation + self-certifying keyed identity.
    kr = Keyring(seed=bytes.fromhex(golden["principals"]["master_seed_hex"]))
    for entry in golden["principals"]["named"]:
        p = kr.mint(entry["name"], entry["kind"])
        assert p.id == entry["pid"], f"named pid derivation diverged for {entry['name']}"
        assert kr.public_key(p.id) == entry["public_key"], "named public key diverged"
    for entry in golden["principals"]["keyed"]:
        p = kr.mint_keyed(entry["name"], "agent")
        assert p.id == entry["pid"], f"keyed pid derivation diverged for {entry['name']}"
        assert p.id == Keyring.keyed_pid(entry["public_key"]), "keyed pid does not self-certify"
    line(f"  principals: {len(golden['principals']['named'])} named + "
         f"{len(golden['principals']['keyed'])} self-certifying keyed — keys reproduce")

    # (2d) signatures verify, and a one-byte tamper fails closed.
    for sv in golden["signatures"]:
        assert kr.verify(sv["signer_pid"], sv["message"], sv["sig"]), "golden signature does not verify"
        assert not kr.verify(sv["signer_pid"], sv["message"] + "x", sv["sig"]), (
            "a tampered message still verified — Ed25519 integrity broken")
    line(f"  signatures: {len(golden['signatures'])} Ed25519 vectors verify; "
         f"one-byte tamper fails closed")

    # (2e) the fixed event script re-folds to the committed state_root, and a single
    #      edited payload byte in the replayed Weft is caught on read.
    weft, author = _replay_fold(golden)
    assert author == golden["fold"]["author_pid"], "fold author pid diverged"
    assert weft.count() == golden["fold"]["event_count"], "fold event count diverged"
    weave = Weave.fold(weft)
    assert weave.state_root() == golden["fold"]["state_root"], "fold state_root diverged from golden"
    for t, n in golden["fold"]["type_counts"].items():
        assert len(weave.of_type(t)) == n, f"fold type '{t}' count diverged"

    seq = weft.db.execute(
        "SELECT seq FROM events WHERE payload LIKE '%edited%' LIMIT 1").fetchone()[0]
    weft.db.execute("UPDATE events SET payload = REPLACE(payload, 'edited', 'XXXXXX') WHERE seq=?", (seq,))
    weft.db.commit()
    try:
        list(weft.events())
        raise AssertionError("tampered Weft event was NOT caught on read (Law 1/4)")
    except WeftError:
        pass
    line(f"  fold: re-folds to state_root {golden['fold']['state_root'][:16]}… over "
         f"{golden['fold']['event_count']} events; a tampered byte is caught on read")
    line("  the port target is frozen and self-consistent ✓")


def _replay_fold(golden):
    """Replay the EXACT committed event script through a fresh Weft — the same
    construction decima.vectors uses, so a conformant port replays it identically."""
    import os
    import tempfile

    from decima import model
    from decima.capability import attenuate, capability_content
    from decima.weft import ASSERT

    kr = Keyring(seed=bytes.fromhex(golden["fold"]["master_seed_hex"]))
    author = kr.mint("tester", "human").id
    db = os.path.join(tempfile.mkdtemp(prefix="decima-vec-check-"), "weft.db")
    weft = Weft(db, kr)

    model.define_type(weft, author, "note")
    model.assert_content(weft, author, "note:1", "note", {"text": "first", "n": 1})
    model.assert_content(weft, author, "note:1", "note", {"text": "edited", "n": 2})
    parent_cap = capability_content(name="pay", effect="pay", target="*",
                                    caveats={"budget": 100}, grantee=author, granter=author)
    parent_id = hashing.content_id({"cap": "pay", "v": 1})
    weft.append(author, ASSERT, {"cell": parent_id, "type": "capability", "content": parent_cap})
    child_cap = attenuate(parent_cap, {"budget": 40, "requires_approval": True},
                          parent_id, grantee=author, granter=author)
    child_id = hashing.content_id({"cap": "pay", "v": 1, "att": 1})
    weft.append(author, ASSERT, {"cell": child_id, "type": "capability", "content": child_cap})
    model.assert_edge(weft, author, child_id, "attenuates", parent_id)
    weft.append(author, RETRACT, {"cell": "note:1", "mode": "WITHDRAW"})
    return weft, author
