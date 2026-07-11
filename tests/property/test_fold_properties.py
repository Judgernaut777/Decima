"""FOLD-PROPS (DEC-033/035) — Hypothesis property tests over the deterministic fold.

The Weave is a fold of the Weft (Law 5). These properties pin the four things a
deterministic, content-addressed fold must guarantee, over bounded random event
scripts applied to a real in-process Weft (decima.kernel):

  1. DETERMINISM         — folding the same Weft twice yields the identical
                           state_root (FOLD §6: state_root is a pure digest of
                           folded state, independent of *how* it was replayed).
  2. REBUILD ≡ INCREMENTAL — a full genesis Weave.fold equals folding up to head,
                           and (FOLD §11.1 / IFB1) an incremental fold resuming
                           from a mid-history checkpoint equals the genesis fold.
  3. DUPLICATE IDEMPOTENCE — unioning an already-present event via ingest() returns
                           'duplicate' and does NOT change the state_root (FOLD §2:
                           idempotent by Event ID). A second Weft rebuilt purely by
                           ingesting the first's events reproduces its state_root.
  4. RETRACTION STABILITY — a WITHDRAW tombstones its cell out of of_type() and
                           re-folding is stable (the cascade is a pure derived pass).

Strategies are deliberately small (max_examples <= 50, derandomized) and use fixed
seeds / no wall-clock, so every recorded content is deterministic.
"""

from __future__ import annotations

import os
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from decima.kernel.crypto import Keyring
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import RETRACT, Weft

# All three types default to LWW (no TYPE_DEF asserted), so an ASSERT upserts a
# register-cell's content — the simplest reducer, enough to exercise the fold.
CELL_IDS = ["c0", "c1", "c2", "c3"]
TYPE_NAMES = ["note", "thing", "doc"]

_content = st.fixed_dictionaries(
    {
        "text": st.text(alphabet="abcdeαβγ", max_size=6),
        "n": st.integers(min_value=-50, max_value=50),
    }
)
_assert_op = st.tuples(
    st.just("assert"), st.sampled_from(CELL_IDS), st.sampled_from(TYPE_NAMES), _content
)
_retract_op = st.tuples(st.just("retract"), st.sampled_from(CELL_IDS))
_op = st.one_of(_assert_op, _retract_op)
SCRIPT = st.lists(_op, min_size=1, max_size=12)

SETTINGS = settings(max_examples=50, deadline=None, derandomize=True)


def _new_weft(keyring: Keyring):
    """A fresh, isolated Weft backed by a temp SQLite db, sharing `keyring` so a
    second Weft can verify the first's foreign events (same master seed)."""
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    return Weft(db, keyring)


def _apply_script(weft: Weft, author: str, script) -> None:
    for op in script:
        if op[0] == "assert":
            _, cell, typ, content = op
            assert_content(weft, author, cell, typ, content)
        else:  # retract (WITHDRAW) — harmless if the cell was never asserted
            _, cell = op
            weft.append(author, RETRACT, {"cell": cell, "mode": "WITHDRAW"})


def _build(script):
    """Fixed-seed keyring + author, a fresh Weft, the script applied. Deterministic."""
    kr = Keyring(seed=bytes(32))
    author = kr.mint("tester", "human").id
    weft = _new_weft(kr)
    _apply_script(weft, author, script)
    return weft, kr, author


# ── 1. DETERMINISM ──────────────────────────────────────────────────────────────
@given(SCRIPT)
@SETTINGS
def test_fold_is_deterministic(script) -> None:
    """Two folds of the SAME Weft produce the identical state_root — the fold is a
    pure function of the event set, not of replay order or object identity."""
    weft, _kr, _author = _build(script)
    r1 = Weave.fold(weft).state_root()
    r2 = Weave.fold(weft).state_root()
    assert r1 == r2, "two folds of the same Weft diverged"


# ── 2. REBUILD ≡ INCREMENTAL ─────────────────────────────────────────────────────
@given(SCRIPT)
@SETTINGS
def test_rebuild_equals_incremental(script) -> None:
    """A full genesis fold equals folding up to head, and an incremental fold that
    resumes from a mid-history checkpoint (applying only the tail) equals the
    genesis fold — the same state_root and the same last_seq (FOLD §11.1 / IFB1)."""
    weft, _kr, _author = _build(script)
    head = weft.count()
    full_root = Weave.fold(weft).state_root()

    # Folding explicitly up to head is the genesis fold.
    upto_root = Weave.fold(weft, head).state_root()
    assert upto_root == full_root, "fold(weft, head) != fold(weft)"

    # Incremental: checkpoint at a mid frontier F, then apply only events seq > F.
    if head >= 2:
        F = max(1, head // 2)
        base_ckpt = Weave.fold(weft, F).checkpoint()
        inc = Weave.fold_incremental(weft, base_ckpt)
        assert inc.state_root() == full_root, "incremental fold != genesis fold"
        assert inc.last_seq == head, (inc.last_seq, head)


# ── 3. DUPLICATE-DELIVERY IDEMPOTENCE (via cross-Weft event union) ───────────────
@given(SCRIPT)
@SETTINGS
def test_ingest_union_rebuilds_then_duplicate_is_a_noop(script) -> None:
    """A second Weft rebuilt purely by INGESTing the first's events (in seq order,
    so every parent lands first) reproduces the first's fold state_root. Re-ingesting
    the very same rows then returns 'duplicate' for every event and does NOT change
    the state_root — idempotent by Event ID (FOLD §2 / WEFT ingest)."""
    kr = Keyring(seed=bytes(32))
    author = kr.mint("tester", "human").id
    weft_a = _new_weft(kr)
    _apply_script(weft_a, author, script)

    # Wire rows the ingest gate consumes: (id, payload_text, author, sig), seq order.
    rows = [
        (eid, payload, auth, sig)
        for (_seq, eid, payload, auth, sig) in weft_a.db.execute(
            "SELECT seq, id, payload, author, sig FROM events ORDER BY seq ASC"
        )
    ]

    weft_b = _new_weft(kr)  # same keyring → the foreign signatures verify
    for row in rows:
        assert weft_b.ingest(row) == "ingested", "a valid foreign event was refused"

    root_a = Weave.fold(weft_a).state_root()
    root_b = Weave.fold(weft_b).state_root()
    assert root_b == root_a, "rebuild-by-union diverged from the original fold"

    # DUPLICATE DELIVERY: re-ingest every row → 'duplicate', state_root unchanged.
    before = Weave.fold(weft_b).state_root()
    for row in rows:
        assert weft_b.ingest(row) == "duplicate", "re-delivered event was not a duplicate"
    after = Weave.fold(weft_b).state_root()
    assert after == before, "duplicate delivery changed the state_root"


# ── 4. RETRACTION STABILITY ──────────────────────────────────────────────────────
@given(SCRIPT)
@SETTINGS
def test_withdraw_tombstones_out_of_of_type_and_is_stable(script) -> None:
    """A WITHDRAW tombstones its cell: it leaves of_type() and re-folding is stable.

    We append a KNOWN live cell after the random script (so a live target always
    exists), then WITHDRAW it. Post-retraction it must be absent from of_type() for
    its type, and two successive folds must give the identical state_root (the
    retraction/cascade projection is a pure derived pass — order-independent)."""
    weft, _kr, author = _build(script)

    target = "live-target"
    assert_content(weft, author, target, "note", {"text": "here", "n": 7})
    live_before = {c.id for c in Weave.fold(weft).of_type("note")}
    assert target in live_before, "the seeded cell must be live before withdrawal"

    weft.append(author, RETRACT, {"cell": target, "mode": "WITHDRAW"})
    w = Weave.fold(weft)
    live_after = {c.id for c in w.of_type("note")}
    assert target not in live_after, "a WITHDRAWn cell must drop out of of_type()"
    tcell = w.get(target)
    assert tcell is not None and tcell.retracted, "the tombstone must remain, retracted"

    r1 = Weave.fold(weft).state_root()
    r2 = Weave.fold(weft).state_root()
    assert r1 == r2, "re-folding after a WITHDRAW is not stable"
