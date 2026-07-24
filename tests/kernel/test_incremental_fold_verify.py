"""IFB1 / R11 — incremental fold-from-base verifies its checkpoint BY DEFAULT.

Folding onto a checkpointed base must not blindly trust that base: a cache you cannot
verify is a second source of truth, and Law 5 forbids it. `checkpoint()` embeds the
frozen `state_root`; `fold_incremental` re-derives the reassembled base's root and
rejects a mismatch UNLESS verification is explicitly disabled. These tests pin that the
default is closed — a tampered base is rejected with no extra arguments — and that the
conscious `verify=False` opt-out still works.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from decima.kernel import model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft

_SEED = bytes(32)  # fixed, deterministic


def _build_weft() -> tuple[Weft, str]:
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
    return weft, author


def test_checkpoint_embeds_frozen_root() -> None:
    """checkpoint() carries the frozen state_root as a self-verifying commitment."""
    weft, _author = _build_weft()
    mid = Weave.fold(weft, 3)
    ckpt = mid.checkpoint()
    assert ckpt["state_root"] == mid.state_root()


def test_incremental_verifies_by_default() -> None:
    """A genuine checkpoint base folds to the full genesis root with NO verify args."""
    weft, _author = _build_weft()
    full_root = Weave.fold(weft).state_root()
    ckpt = Weave.fold(weft, 3).checkpoint()
    inc = Weave.fold_incremental(weft, ckpt)  # verification is ON by default
    assert inc.state_root() == full_root
    assert inc.last_seq == weft.count()


def test_tampered_base_rejected_by_default() -> None:
    """Corrupting a checkpoint cell (without updating the embedded root) is caught with
    NO extra arguments — the default is fail-closed, not opt-in."""
    weft, _author = _build_weft()
    ckpt = Weave.fold(weft, 3).checkpoint()
    ckpt["cells"]["note:1"].content["text"] = "TAMPERED"  # forge the base state
    with pytest.raises(ValueError, match="state_root"):
        Weave.fold_incremental(weft, ckpt)


def test_tampered_base_accepted_when_verification_disabled() -> None:
    """`verify=False` is the conscious opt-out: the same tampered base folds without a
    rejection (the caller has taken responsibility for the base's provenance)."""
    weft, _author = _build_weft()
    ckpt = Weave.fold(weft, 3).checkpoint()
    ckpt["cells"]["note:1"].content["text"] = "TAMPERED"
    inc = Weave.fold_incremental(weft, ckpt, verify=False)  # explicit opt-out
    note = inc.get("note:1")
    assert note is not None and note.content["text"] == "TAMPERED"  # forged base survived


def test_explicit_verify_root_takes_precedence() -> None:
    """An explicit trusted root (a signed snapshot's) overrides the embedded one: a
    matching root passes, a bogus one is rejected even though the embedded root is fine."""
    weft, _author = _build_weft()
    trusted = Weave.fold(weft, 3).state_root()
    ckpt = Weave.fold(weft, 3).checkpoint()
    Weave.fold_incremental(weft, ckpt, verify_root=trusted)  # matching → accepted
    with pytest.raises(ValueError, match="state_root"):
        Weave.fold_incremental(weft, ckpt, verify_root="deadbeef" * 4)


def test_checkpoint_without_embedded_root_is_rejected() -> None:
    """A checkpoint carrying no trusted root cannot be verified — fail closed rather
    than silently trust it; verify=False remains the escape hatch."""
    weft, _author = _build_weft()
    ckpt = Weave.fold(weft, 3).checkpoint()
    del ckpt["state_root"]
    with pytest.raises(ValueError, match="no trusted state_root"):
        Weave.fold_incremental(weft, ckpt)
    inc = Weave.fold_incremental(weft, ckpt, verify=False)  # opt-out still works
    assert inc.last_seq == weft.count()
