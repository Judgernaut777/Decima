"""DEC-020 — signed local checkpoints over the snapshot frontier.

Exercises the REAL extracted kernel: builds a Weft in-process (fixed seed, no clock,
no unseeded randomness in recorded content), folds it, and proves the checkpoint
module's local integrity evidence:

  - a fresh checkpoint verifies (True, "ok");
  - a checkpoint whose committed state_root is altered FAILS verification;
  - a checkpoint whose frontier (head/event_count) is altered FAILS verification;
  - a checkpoint signed by (or re-signed under) the WRONG key FAILS verification;
  - the checkpoint records the EXACT event count and the protocol version string.
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel import model
from decima.kernel.checkpoints import make_checkpoint, verify_checkpoint
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft

_SEED = bytes(32)  # fixed, deterministic
_PROTO = "weft/0.1"


def _build_weft() -> tuple[Weft, str, Keyring]:
    """A small, deterministic Weft: a type + three content asserts + one edge."""
    kr = Keyring(seed=_SEED)
    author = kr.mint("author", "human").id
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    weft = Weft(db, kr)
    model.define_type(weft, author, "note")
    model.assert_content(weft, author, "note:1", "note", {"text": "first", "n": 1})
    model.assert_content(weft, author, "note:1", "note", {"text": "edited", "n": 2})
    model.assert_content(weft, author, "note:2", "note", {"text": "second", "n": 3})
    model.assert_edge(weft, author, "note:1", "links_to", "note:2")
    return weft, author, kr


def test_fresh_checkpoint_verifies() -> None:
    weft, author, kr = _build_weft()
    weave = Weave.fold(weft)
    ckpt = make_checkpoint(weft, weave, kr, author, protocol_version=_PROTO)

    ok, reason = verify_checkpoint(ckpt, Weave.fold(weft), kr)
    assert ok is True, reason
    assert reason == "ok"


def test_records_exact_event_count_and_protocol() -> None:
    weft, author, kr = _build_weft()
    weave = Weave.fold(weft)
    ckpt = make_checkpoint(weft, weave, kr, author, protocol_version=_PROTO)

    # 5 events were appended: type + 3 content + 1 edge.
    assert weft.count() == 5
    assert ckpt["event_count"] == 5
    assert ckpt["event_count"] == weft.count()
    assert ckpt["protocol_version"] == _PROTO
    assert ckpt["head"] == weft.head
    assert ckpt["frontier"] == [weft.head]
    assert ckpt["state_root"] == weave.state_root()
    assert ckpt["signer"] == author


def test_altered_state_root_fails() -> None:
    weft, author, kr = _build_weft()
    weave = Weave.fold(weft)
    ckpt = make_checkpoint(weft, weave, kr, author, protocol_version=_PROTO)

    tampered = dict(ckpt)
    tampered["state_root"] = "deadbeef" * 4  # swap in a bogus committed root
    ok, reason = verify_checkpoint(tampered, Weave.fold(weft), kr)
    assert ok is False
    assert "state_root" in reason


def test_altered_frontier_fails() -> None:
    weft, author, kr = _build_weft()
    weave = Weave.fold(weft)
    ckpt = make_checkpoint(weft, weave, kr, author, protocol_version=_PROTO)

    # Lie about the frontier size while leaving the (correct) state_root intact, so the
    # ONLY thing that can catch it is the signature over the frontier bytes.
    tampered = dict(ckpt)
    tampered["event_count"] = ckpt["event_count"] - 1
    ok, reason = verify_checkpoint(tampered, Weave.fold(weft), kr)
    assert ok is False
    assert "signature" in reason


def test_altered_protocol_version_fails() -> None:
    weft, author, kr = _build_weft()
    weave = Weave.fold(weft)
    ckpt = make_checkpoint(weft, weave, kr, author, protocol_version=_PROTO)

    tampered = dict(ckpt)
    tampered["protocol_version"] = "weft/9.9"  # signed field → sig must break
    ok, reason = verify_checkpoint(tampered, Weave.fold(weft), kr)
    assert ok is False
    assert "signature" in reason


def test_wrong_key_signature_fails() -> None:
    weft, author, kr = _build_weft()
    other = kr.mint("intruder", "human").id
    weave = Weave.fold(weft)
    ckpt = make_checkpoint(weft, weave, kr, author, protocol_version=_PROTO)

    # Re-sign the exact same unsigned bytes with a DIFFERENT principal's key, but keep
    # the claimed signer = author. Verifying under author's public key must reject it.
    from decima.kernel.checkpoints import _checkpoint_id

    forged = dict(ckpt)
    forged["signature"] = kr.sign(other, _checkpoint_id(ckpt))
    ok, reason = verify_checkpoint(forged, Weave.fold(weft), kr)
    assert ok is False
    assert "signature" in reason

    # And a checkpoint genuinely SIGNED by `other` but CLAIMING to be `other` also
    # fails when verified against `author`'s expectation via a swapped signer field is
    # caught by the id/signature binding: signer is part of the signed bytes.
    mis = make_checkpoint(weft, weave, kr, other, protocol_version=_PROTO)
    mis2 = dict(mis)
    mis2["signer"] = author  # claim author; sig is other's
    ok2, reason2 = verify_checkpoint(mis2, Weave.fold(weft), kr)
    assert ok2 is False
