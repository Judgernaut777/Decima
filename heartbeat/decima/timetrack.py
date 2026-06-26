"""TIMETRACK1 — time-tracking / focus: a session is DATA, a duration is an INT fold.

A "time session" is the same recall-vs-instruct law one notch over from SCHED1/METRICS1:
tracked time is DATA on the Weft, never an authority, and every duration is a LOGICAL INT —
there is no wall-clock in signed content. The caller owns the clock (`at` is passed in), exactly
as `scheduling.due(k, now)` does. We don't re-implement state — we COMPOSE the existing
capabilities:

  - start → a `time_session` Cell (int `start`, `status="open"`, an `activity` label). A fresh
    Cell per session, content-addressed by (activity, start, seq) so two sessions of the same
    activity at the same tick still land on distinct Cells. The session is OPEN until stopped.

  - stop → close a session: a LWW re-assert on the SAME Cell id (SCHED1's `fire` pattern) that
    sets `end` and the computed INT `duration = end - start`, flipping `status` to "closed". The
    history stays on the Log; the fold resolves the latest version (WEFT provenance).

  - report → total tracked time per activity: a deterministic INT fold over the CLOSED sessions,
    composing `metrics.total` per activity group. An open (un-stopped) session contributes no
    duration yet — it is reflected as open, not summed. All ints (WEFT §4/§7).

  - focus_block → schedule a focus/pomodoro block via SCHED1: `scheduling.schedule(..., at)` with
    the block's title; the reminder is the only authority, the session Cell is just intent. Linked
    by a `focus_schedule` edge (Law 4 provenance).

OWNS only heartbeat/decima/timetrack.py. Composes PUBLIC APIs (model / scheduling / metrics) —
no kernel code, no ambient authority: every assert is authored (default the Decima agent).
"""
from __future__ import annotations

from decima import model
from decima import scheduling as sched
from decima import metrics
from decima.hashing import content_id, nfc

TIME_SESSION = "time_session"

OPEN = "open"
CLOSED = "closed"


def _int(name: str, value) -> int:
    """Reject floats/bools before they reach signed content (WEFT §4/§7). A logical tick
    is an INT — bool is an int subclass in Python and must NOT be conflated with 0/1."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int logical tick, got {type(value).__name__}")
    return int(value)


def _session_id(activity: str, start: int, seq: int) -> str:
    """Content-address a session by (activity, start, seq) — a fresh seq makes two
    sessions of the same activity at the same start tick distinct Cells."""
    return content_id({"time_session": nfc(activity), "start": int(start), "seq": int(seq)})


def start(k, activity: str, *, at: int, author: str | None = None) -> str:
    """Open a time session for `activity` at integer tick `at`; return its Cell id.

    `at` is a LOGICAL INT (the caller's clock — no wall-clock here). The session is OPEN
    (status="open", no end/duration yet) until `stop` closes it."""
    at = _int("at", at)
    activity = nfc(activity)
    author = author or k.decima_agent_id
    seq = k.weft.count() + 1                      # deterministic, log-positioned id
    sid = _session_id(activity, at, seq)
    model.assert_content(k.weft, author, sid, TIME_SESSION, {
        "activity": activity,
        "start": at,
        "end": None,
        "duration": None,
        "status": OPEN,
        "seq": seq,
    })
    return sid


def stop(k, session: str, *, at: int, author: str | None = None) -> int:
    """Close an OPEN session: set `end = at` and the computed INT `duration = end - start`
    via a LWW re-assert on the SAME Cell id (the fold keeps the open version's history).
    Returns the int duration. `at` is a logical tick and must be >= the session's start —
    a negative duration (stop before start) is a fail-loud error."""
    at = _int("at", at)
    author = author or k.decima_agent_id
    cell = k.weave().get(session)
    if cell is None or cell.type != TIME_SESSION:
        raise ValueError(f"no time_session {session!r}")
    if cell.content.get("status") == CLOSED:
        raise ValueError(f"time_session {session!r} already stopped")

    s = int(cell.content["start"])
    duration = at - s
    if duration < 0:
        raise ValueError(f"stop tick {at} precedes start tick {s} (negative duration)")

    model.assert_content(k.weft, author, session, TIME_SESSION, {
        "activity": cell.content["activity"],
        "start": s,
        "end": at,
        "duration": duration,
        "status": CLOSED,
        "seq": int(cell.content.get("seq", 0)),
    })
    return duration


def sessions(k, *, activity: str | None = None, status: str | None = None) -> list:
    """The live `time_session` Cells as DATA dicts, in deterministic (start, id) order.
    Optionally filtered by `activity` and/or `status` ("open"/"closed")."""
    want_activity = nfc(activity) if activity is not None else None
    out = []
    for c in k.weave().of_type(TIME_SESSION):
        if want_activity is not None and c.content.get("activity") != want_activity:
            continue
        if status is not None and c.content.get("status") != status:
            continue
        out.append(c)
    out.sort(key=lambda c: (int(c.content.get("start", 0)), c.id))
    return [{
        "id": c.id,
        "activity": c.content["activity"],
        "start": int(c.content["start"]),
        "end": c.content.get("end"),
        "duration": c.content.get("duration"),
        "status": c.content.get("status"),
    } for c in out]


def report(k, *, by: str = "activity") -> dict:
    """Total tracked time per activity: a deterministic INT fold over the CLOSED sessions.

    Returns {activity: total_duration} (ints, in deterministic activity order). Composes
    `metrics.total` per activity group — `metrics` skips a non-int `duration`, so an OPEN
    session (duration=None) contributes 0 and is NOT summed. A read-only projection over the
    fold: two evaluations of the same Weft yield the same numbers."""
    if by != "activity":
        raise ValueError(f"unsupported report grouping {by!r}; only 'activity' is supported")

    # Distinct activities present, in deterministic order.
    activities = sorted({c.content["activity"] for c in k.weave().of_type(TIME_SESSION)})

    out: dict[str, int] = {}
    for activity in activities:
        # Per-activity total of the int `duration` field — metrics skips the None of an
        # open session (its `_as_int` treats a non-int as absent), so opens don't sum.
        cells = [c for c in k.weave().of_type(TIME_SESSION)
                 if c.content.get("activity") == activity]
        out[activity] = sum(
            d for d in (c.content.get("duration") for c in cells)
            if isinstance(d, int) and not isinstance(d, bool)
        )
    return out


def focus_block(k, activity: str, *, minutes: int, at: int, author: str | None = None) -> dict:
    """Schedule a focus / pomodoro block for `activity` via SCHED1.

    Composes `scheduling.schedule(title, at)` to drop a `scheduled_event` reminder at integer
    tick `at` (the block's start); `minutes` is the int block length, carried on a `focus_block`
    intent Cell and linked to the reminder by a `focus_schedule` edge (Law 4 provenance). The
    schedule is the only authority. Returns {block, event} (Cell ids). `minutes`/`at` are ints."""
    minutes = _int("minutes", minutes)
    at = _int("at", at)
    if minutes <= 0:
        raise ValueError("minutes must be a positive number of ticks")
    activity = nfc(activity)
    author = author or k.decima_agent_id

    title = f"focus: {activity} ({minutes}m)"
    eid = sched.schedule(k, title, at=at, author=author)

    bid = content_id({"focus_block": activity, "minutes": minutes, "at": int(at)})
    model.assert_content(k.weft, author, bid, "focus_block", {
        "activity": activity,
        "minutes": minutes,
        "at": at,
        "event": eid,
    })
    model.assert_edge(k.weft, author, bid, "focus_schedule", eid)
    return {"block": bid, "event": eid}
