"""Generate kernel conformance fixtures FROM THE REFERENCE implementation.

Run with the reference package on the path (cwd = heartbeat), so `import decima`
resolves to heartbeat/decima. The fixtures it emits are the oracle the extracted
`decima.kernel` must reproduce byte-for-byte (see tests/kernel/test_conformance.py).

    cd heartbeat && python3 ../tools/kernel/gen_fixtures.py

Deterministic: a fixed master seed, a fixed event script, no wall-clock, no random.
"""

import json
import os
import pathlib
import tempfile

from decima.crypto import Keyring
from decima.weave import Weave
from decima.weft import RETRACT, Weft

from decima import hashing, model

OUT = pathlib.Path(__file__).resolve().parents[2] / "protocol" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)

# ── 1. Canonical encoding + content addressing (DEC-010 / DEC-030) ──────────────
CANON_PAYLOADS = [
    {"a": 1, "b": 2},
    {"b": 2, "a": 1},  # key order must not matter
    {"nested": {"x": [1, 2, 3], "y": {"z": True}}, "n": None},
    {"unicode": "café é 王 \U0001f600", "combining": "é"},  # NFC-normalized
    {"empty_map": {}, "empty_list": [], "zero": 0, "neg": -5},
    {"big_int": 123456789012345678901234567890},
    {"text": "the loom is weaving"},
]

canon = []
for p in CANON_PAYLOADS:
    canon.append(
        {
            "payload": p,
            "canonical_hex": hashing.canonical(p).hex(),
            "content_id_cell": hashing.content_id(p, kind="cell"),
            "content_id_event": hashing.content_id(p, kind="event"),
        }
    )

blobs = [
    {"data_hex": b"".hex(), "blob_id": hashing.blob_id(b"")},
    {"data_hex": b"hello, fates".hex(), "blob_id": hashing.blob_id(b"hello, fates")},
    {"data_hex": bytes(range(256)).hex(), "blob_id": hashing.blob_id(bytes(range(256)))},
]

(OUT / "canonical.json").write_text(
    json.dumps({"payloads": canon, "blobs": blobs}, indent=2, ensure_ascii=False)
)

# ── 2. Full fold parity: append a fixed event script, record ids + state_root ───
db = os.path.join(tempfile.mkdtemp(), "weft.db")
kr = Keyring(seed=bytes(32))  # fixed all-zero master → reproducible keys
author = kr.mint("tester", "human").id
weft = Weft(db, kr)

events = []


def record(ev):
    events.append({"id": ev.id, "verb": ev.verb, "lamport": ev.lamport, "body": ev.body})


# A deterministic script exercising TYPE_DEF, CONTENT, EDGE, and RETRACT.
cid_type = model.define_type(weft, author, "note")
record(model.assert_content(weft, author, "note:1", "note", {"text": "first", "n": 1}))
record(model.assert_content(weft, author, "note:1", "note", {"text": "edited", "n": 2}))
record(model.assert_content(weft, author, "note:2", "note", {"text": "second", "n": 3}))
record(model.assert_edge(weft, author, "note:1", "links_to", "note:2"))
record(weft.append(author, RETRACT, {"cell": "note:2", "mode": "WITHDRAW"}))

final = Weave.fold(weft)
fold_fixture = {
    "master_seed_hex": bytes(32).hex(),
    "author_pid": author,
    "type_cell_id": cid_type,
    "events": events,
    "state_root": final.state_root(),
    "type_counts": {t: len(final.of_type(t)) for t in ("note", "type")},
    "event_count": weft.count(),
}
(OUT / "fold.json").write_text(json.dumps(fold_fixture, indent=2, ensure_ascii=False))

print(f"wrote {OUT / 'canonical.json'} ({len(canon)} payloads, {len(blobs)} blobs)")
print(f"wrote {OUT / 'fold.json'} (state_root={final.state_root()[:16]}…, {weft.count()} events)")
