"""The explicit checkpoint SEAM the runtime's hot path reads through (P4.1 / IFB1).

Folding from genesis is O(all events) — and not cheaply: `Weft.events` recomputes the
content id and verifies the SIGNATURE of every event on the way (Law 1/4 enforced on
read). A bounded execution pass needs the fold many times — cancel, reconcile, readiness,
once or twice per dispatched step, final — so an un-seamed pass costs O(steps x whole log)
in signature verification, and that cost grows forever as the log grows. That is the
genesis-fold scaling problem this module closes.

The seam is a CURSOR: the pass folds ONCE, then every later read ADVANCES that same
in-memory fold with just the events appended since (`Weave.advance`). A pass therefore
pays for the whole log once and for each new event once, instead of re-reading history
per step.

Why a cursor and not a `Weave.checkpoint()` per boundary: a checkpoint deep-copies the
entire fold substrate and `fold_incremental` deep-copies it back, which is O(state) per
fold — measured at roughly TWICE the cost of the genesis fold it was meant to replace on
a multi-thousand-event log. The checkpoint form earns that cost when the base crossed a
trust boundary (a snapshot, another process), because it can then verify the reassembled
base against a trusted `state_root` and does so by default. Inside one pass there is no
such boundary: the cursor extends a fold THIS process performed, from the log itself,
through the verifying reader. Nothing unverified enters and no second copy of the state
exists to drift (Law 5) — the fold is still the state, just not recomputed from scratch.

Equality is PROVEN, not assumed (FOLD §11.1): the reducers are pure functions of the
applied event set and the (lamport, event_id) total order, never of arrival order.
tests/runtime/test_execution.py pins it two ways — every advanced read's `state_root`
must equal a genesis fold's at that frontier, and twin Wefts driven seamed vs un-seamed
must produce identical reports and identical `state_root`s.

`cursor=None` everywhere means "no seam threaded — fold from genesis", so every existing
caller keeps byte-identical behavior and the un-seamed path stays available as the live
oracle the equivalence tests A/B against. A cursor never outlives its pass: a fresh pass
folds from genesis and opens a new one.
"""

from __future__ import annotations

from decima.kernel.weave import Weave
from decima.kernel.weft import Weft


class Cursor:
    """A live, advancing fold — the seam made an object so the sharing is EXPLICIT.

    It deliberately wraps ONE mutable Weave: every holder of the cursor reads the same
    fold, which is exactly what makes a pass's reads cost O(new events). A caller that
    needs a stable snapshot of some state across writes must copy what it needs out
    (the runtime hoists such reads before the writes instead)."""

    __slots__ = ("weave",)

    def __init__(self, weave: Weave) -> None:
        self.weave = weave

    @classmethod
    def at(cls, weft: Weft) -> Cursor:
        """Open a cursor at the log's current frontier (one genesis fold, once)."""
        return cls(Weave.fold(weft))

    def read(self, weft: Weft) -> Weave:
        """The fold, current as of NOW: applies only the tail appended since the last
        read. Equal to a genesis fold at this frontier (FOLD §11.1)."""
        return self.weave.advance(weft)


def read(weft: Weft, cursor: Cursor | None) -> Weave:
    """The seam's one rule: a read either advances the caller's cursor (tail only) or
    folds from genesis when no cursor was threaded. The answer is the same either way —
    only the cost differs."""
    return Weave.fold(weft) if cursor is None else cursor.read(weft)
