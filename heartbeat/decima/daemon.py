"""DAEMON1 — the durable run-loop: the heartbeat resumes across a restart.

REACTOR1 gave Decima the single deterministic pass (`tick(k, now)`) and JOBS1/RESUME made
the WORK durable — a job is a Cell, its transitions fold back on a fresh Kernel, and an
interrupted job is reconciled to its true receipt outcome. But the RUN-LOOP itself had no
durable memory of HOW FAR it has beaten: `reactor.run_until` ticks an in-memory sequence
and defaults its start to `k.weft.lamport`, so after a crash a naive restart either
RE-SCANS from an arbitrary start or SKIPS every beat between the last processed frontier
and now. DAEMON1 closes that gap: the loop's PROGRESS is itself a Cell on the Weft — a
`loop_checkpoint` recording the highest logical frontier the loop has FULLY ticked — so a
fresh process resumes exactly where the last one stopped: no beat re-fired, no beat
skipped.

  - `checkpoint(k)` — fold the cursor: the highest fully-ticked frontier, or -1 (NEVER)
    if the loop has never run. A pure read of the Weave; the cursor is a FOLD, not a
    variable, so a reconstructed Kernel over the same db sees the same progress.
  - `advance(k, upto)` — drive the loop from `checkpoint(k)+1` THROUGH `upto` (inclusive),
    one `reactor.tick(k, f)` per frontier in order, then record ONE new checkpoint at
    `upto`. The load-bearing guard: no frontier `<= checkpoint(k)` is ever re-ticked —
    idempotence across restart lives HERE, at the loop level, not in the caller's memory.
  - `resume(k, upto)` — the restart entrypoint: advance from the DURABLE checkpoint (the
    value a fresh Kernel folds from the same db), proving a restart CONTINUES the
    heartbeat rather than restarting it.

The laws DAEMON1 keeps:

  - EVERYTHING ON THE WEFT. The cursor is a `loop_checkpoint` Cell asserted via
    `model.assert_content` (LWW re-asserts on ONE well-known cell id), authored by the
    Decima principal. History stays on the Log; progress is never an in-memory variable.

  - DETERMINISM / INTS-NOT-FLOATS. Every frontier is a LOGICAL int the caller supplies —
    no wall-clock anywhere. A float/bool `upto` is rejected at the door (TypeError) before
    anything is ticked or signed; the recorded content is int-only ({frontier, beats}).

  - FAIL CLOSED / MONOTONE. The cursor NEVER moves backward: an `upto` below the folded
    checkpoint is refused loud (ValueError), and an `upto` equal to it is a genuine no-op
    (nothing ticked, no new Cell). Progress is append-only, like everything else.

  - NO AMBIENT AUTHORITY / NO ESCALATION. The daemon mints NOTHING: it drives
    `reactor.tick`, which fires each lane through its own gates (dispositions, pre-fixed
    job leases, crash recovery). A loop-driver confers no authority; its only new state
    is the cursor.

  - IDEMPOTENCE AT THE LOOP LEVEL. `tick` was already idempotent per-frontier; DAEMON1
    makes the SWEEP idempotent: re-advancing to an already-checkpointed frontier ticks
    nothing, fires nothing, moves no cursor — across a process boundary.

Public APIs only (reactor.tick, model.assert_content, hashing.content_id, k.weave /
k.weft / k.decima.id) — no core edit, no edit to reactor/jobs/scheduling/resume.
"""
from __future__ import annotations

from decima.model import assert_content
from decima.hashing import content_id
from decima import reactor

LOOP_CHECKPOINT = "loop_checkpoint"

#: The sentinel `checkpoint` returns when the loop has never run (no cursor folded).
NEVER = -1

# ONE well-known, content-addressed cursor Cell for THE run-loop. Every checkpoint is an
# LWW re-assert on this same id, so the fold always yields exactly the latest progress
# (and the full history of every past checkpoint stays on the Log).
_CURSOR = content_id({"loop_checkpoint": "run-loop"})


def _int_frontier(name: str, v) -> int:
    """Reject floats/bools at the door — a frontier is a LOGICAL int the caller supplies
    (DETERMINISM / ints-not-floats); nothing else may steer the loop or reach signed
    content."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise TypeError(f"{name} must be an int logical frontier, got {type(v).__name__}")
    return int(v)


def cursor_id() -> str:
    """The well-known `loop_checkpoint` Cell id (content-addressed, stable across
    processes) — exposed so checks/tools can inspect the cursor's provenance."""
    return _CURSOR


def checkpoint(k) -> int:
    """The highest logical frontier the run-loop has FULLY ticked, folded from the Weft —
    or NEVER (-1) if the loop has never run. A pure read: the cursor is a fold over the
    Log, not a process variable, so a fresh Kernel over the same db folds the SAME value
    (this is what makes the loop's progress durable across a restart)."""
    cell = k.weave().get(_CURSOR)
    if cell is None or cell.type != LOOP_CHECKPOINT:
        return NEVER
    return int(cell.content["frontier"])


def beats(k) -> int:
    """Cumulative count of frontiers the loop has fully ticked (folded from the cursor;
    0 if the loop has never run). Pure read; always an int."""
    cell = k.weave().get(_CURSOR)
    if cell is None or cell.type != LOOP_CHECKPOINT:
        return 0
    return int(cell.content.get("beats", 0))


def advance(k, upto: int, *, author: str | None = None) -> dict:
    """Drive the run-loop from `checkpoint(k)+1` THROUGH `upto` (inclusive): one
    `reactor.tick(k, f)` per frontier, in order, then record ONE new durable
    `loop_checkpoint` at `upto`.

    The load-bearing guard: NO frontier `<= checkpoint(k)` is ever re-ticked. The sweep
    starts strictly after the folded cursor, so a crash+restart CONTINUES the heartbeat —
    already-processed beats are not re-fired (idempotence across restart, at the loop
    level) and the beats between the checkpoint and `upto` are all ticked (no skip).

    Fail closed: a float/bool `upto` is a TypeError (nothing ticked, nothing signed); an
    `upto` below the current checkpoint is a ValueError — the cursor NEVER moves backward.
    `upto == checkpoint(k)` is a genuine no-op: ticks nothing, fires nothing, asserts no
    new Cell.

    Returns ``{"from": <checkpoint before>, "to": <checkpoint after>,
    "ticked": [frontiers ticked, in order], "fired": <total across all ticks>,
    "quiet": <True iff nothing fired>}``.
    """
    upto = _int_frontier("upto", upto)
    cp = checkpoint(k)
    if upto < cp:
        raise ValueError(
            f"refusing to advance to {upto}: the loop is already checkpointed at {cp} "
            "(the cursor never moves backward)")
    if upto == cp:
        # Already fully ticked through `upto` — a genuine no-op: nothing ticked, nothing
        # fired, no new checkpoint Cell (re-advancing is safe to call blindly on restart).
        return {"from": cp, "to": cp, "ticked": [], "fired": 0, "quiet": True}

    prior_beats = beats(k)

    # THE LOAD-BEARING GUARD: start strictly AFTER the folded checkpoint — no frontier
    # <= checkpoint(k) is ever re-ticked, so a restart resumes instead of re-scanning.
    frontiers = list(range(cp + 1, upto + 1))

    fired = 0
    for f in frontiers:
        fired += reactor.tick(k, f, author=author)["fired"]

    # ONE new durable checkpoint at `upto` — an LWW re-assert on the well-known cursor
    # Cell, int-only content, authored by the Decima principal. The loop's progress is
    # now ON the Weft: the next process (or the next call) folds it back and continues.
    assert_content(k.weft, author or k.decima.id, _CURSOR, LOOP_CHECKPOINT,
                   {"frontier": upto, "beats": prior_beats + len(frontiers)})

    return {"from": cp, "to": upto, "ticked": frontiers, "fired": fired,
            "quiet": fired == 0}


def resume(k, upto: int, *, author: str | None = None) -> dict:
    """The restart entrypoint: continue the heartbeat from the DURABLE checkpoint — the
    value a fresh Kernel folds from the same db — through `upto`.

    This is `advance` by construction (the cursor is a fold, so there is no other place
    progress could live), named and returned with `resumed_from` to make the restart
    semantics explicit: a new process does not re-beat frontiers the old process fully
    ticked, and does not skip the beats in between."""
    resumed_from = checkpoint(k)
    out = advance(k, upto, author=author)
    out["resumed_from"] = resumed_from
    return out
