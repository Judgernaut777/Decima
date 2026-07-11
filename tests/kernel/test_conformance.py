"""Kernel extraction conformance: decima.kernel reproduces the reference exactly.

The fixtures in protocol/fixtures/ were generated from the reference implementation
(heartbeat/decima) by tools/kernel/gen_fixtures.py. These tests run the identical
operations through the EXTRACTED decima.kernel package and assert byte-for-byte /
id-for-id equality — proving the extraction preserved semantics (handoff Phase 2 goal:
"without changing the semantics of the current implementation").

No network, no provider, no web server, no live model — pure kernel.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile

import pytest

from decima.kernel import hashing, model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import RETRACT, Weft, WeftError

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "protocol" / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ── DEC-010 / DEC-030: canonical encoding + content addressing ──────────────────


def test_canonical_bytes_match_reference() -> None:
    fx = _load("canonical.json")
    for case in fx["payloads"]:
        payload = case["payload"]
        assert hashing.canonical(payload).hex() == case["canonical_hex"], payload
        assert hashing.content_id(payload, kind="cell") == case["content_id_cell"]
        assert hashing.content_id(payload, kind="event") == case["content_id_event"]


def test_key_order_is_irrelevant_to_identity() -> None:
    # {a:1,b:2} and {b:2,a:1} must hash identically — canonical sorts keys.
    assert hashing.content_id({"a": 1, "b": 2}) == hashing.content_id({"b": 2, "a": 1})


def test_blob_ids_match_reference() -> None:
    fx = _load("canonical.json")
    for case in fx["blobs"]:
        data = bytes.fromhex(case["data_hex"])
        assert hashing.blob_id(data) == case["blob_id"]


# ── DEC-012/013/014: the append-only Weft + deterministic fold ──────────────────


def _replay_reference_script() -> tuple[Weft, str, str]:
    """Replay the exact fixture event script through the extracted kernel."""
    fx = _load("fold.json")
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes.fromhex(fx["master_seed_hex"]))
    author = kr.mint("tester", "human").id
    assert author == fx["author_pid"], "principal id derivation diverged from reference"
    weft = Weft(db, kr)

    cid_type = model.define_type(weft, author, "note")
    model.assert_content(weft, author, "note:1", "note", {"text": "first", "n": 1})
    model.assert_content(weft, author, "note:1", "note", {"text": "edited", "n": 2})
    model.assert_content(weft, author, "note:2", "note", {"text": "second", "n": 3})
    model.assert_edge(weft, author, "note:1", "links_to", "note:2")
    weft.append(author, RETRACT, {"cell": "note:2", "mode": "WITHDRAW"})
    return weft, author, cid_type


def test_event_ids_match_reference() -> None:
    fx = _load("fold.json")
    weft, _author, _cid = _replay_reference_script()
    # The Weft verifies every event's id + signature on read; collect them in order.
    got = [ev.id for ev in weft.events()]
    expected_ids = [e["id"] for e in fx["events"]]
    # events() yields ALL events incl. the TYPE_DEF; compare the CONTENT/EDGE/RETRACT tail.
    assert expected_ids == [i for i in got if i in set(expected_ids)]


def test_fold_state_root_matches_reference() -> None:
    fx = _load("fold.json")
    weft, _author, cid_type = _replay_reference_script()
    assert cid_type == fx["type_cell_id"]
    assert weft.count() == fx["event_count"]
    weave = Weave.fold(weft)
    assert weave.state_root() == fx["state_root"], "fold diverged from the reference"
    for t, n in fx["type_counts"].items():
        assert len(weave.of_type(t)) == n, f"type {t} count diverged"


def test_tamper_is_detected_on_read() -> None:
    """Law 1/4: a single edited payload byte fails the id check on fold."""
    weft, _author, _cid = _replay_reference_script()
    seq = weft.db.execute(
        "SELECT seq FROM events WHERE payload LIKE '%second%' LIMIT 1"
    ).fetchone()[0]
    weft.db.execute(
        "UPDATE events SET payload = REPLACE(payload, 'second', 'XXXXXX') WHERE seq=?",
        (seq,),
    )
    weft.db.commit()
    with pytest.raises(WeftError):
        list(weft.events())
