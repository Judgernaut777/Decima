"""SCHED1 — Scheduling / reminders: a due event can fire a disposition.

A reminder is just data on the Weft. `schedule` asserts a `scheduled_event` Cell —
a title, an integer logical time `at`, and an optional `repeat_every` interval (also
an int). `due(k, now)` is a pure projection over the fold: the events whose `at <= now`
that haven't fired yet. `fire(k, event_id, now)` marks the event fired (last-writer-wins
on the same Cell id) and ROUTES its action through `disposition.dispose` — so a due
reminder becomes a first-class disposition (archive/remember/task/invoke/policy), Decima's
decision, never the reminder's instruction. A repeating event reschedules itself to
`at + repeat_every` (fired=False again at the new time).

LAWS this lane keeps:
  - DETERMINISM: no wall-clock anywhere in signed content. Every event carries an
    explicit integer `at`; `due(k, now)` takes `now` as a parameter — the caller owns
    the clock. Logical ticks (ints), never floats (WEFT §4/§7).
  - PROVENANCE: state lives on the Weft; `fired`/reschedule are new ASSERTs to the same
    Cell, folded LWW — the history stays on the Log.
  - NO AMBIENT AUTHORITY: every assert is authored (default the Decima agent); routing a
    fired action still goes through `dispose`, which is itself authorize/Morta-gated for
    anything beyond remember/archive.

Public APIs only (model / disposition / weave / weft) — no core edit.
"""
from __future__ import annotations

from decima.model import assert_content
from decima.hashing import content_id, nfc
from decima import disposition as disp

SCHEDULED_EVENT = "scheduled_event"


def _event_id(title: str, at: int) -> str:
    """Content-address a reminder by its (title, at) — re-scheduling the same title at
    the same tick is idempotent; a reschedule to at+interval lands on a fresh id."""
    return content_id({"scheduled_event": nfc(title), "at": int(at)})


def schedule(k, title: str, at: int, *, repeat_every: int | None = None,
             author: str | None = None) -> str:
    """Assert a `scheduled_event` Cell and return its id.

    `at` is an integer logical tick (the time the event becomes due). `repeat_every`,
    if given, is a positive integer interval: when the event fires it reschedules to
    `at + repeat_every`. All times are ints — a float `at`/`repeat_every` is rejected so
    no float ever reaches signed content."""
    if not isinstance(at, int) or isinstance(at, bool):
        raise TypeError(f"at must be an int logical tick, got {type(at).__name__}")
    if repeat_every is not None:
        if not isinstance(repeat_every, int) or isinstance(repeat_every, bool):
            raise TypeError("repeat_every must be an int interval")
        if repeat_every <= 0:
            raise ValueError("repeat_every must be a positive number of ticks")
    author = author or k.decima_agent_id
    title = nfc(title)
    eid = _event_id(title, at)
    content = {"title": title, "at": int(at), "fired": False}
    if repeat_every is not None:
        content["repeat_every"] = int(repeat_every)
    assert_content(k.weft, author, eid, SCHEDULED_EVENT, content)
    return eid


def due(k, now: int) -> list:
    """The scheduled-event Cells with `at <= now` that have NOT yet fired, in
    (at, id) order. A pure projection over the current fold — `now` is supplied by the
    caller (no wall-clock here). Future events (at > now) and already-fired events are
    excluded."""
    if not isinstance(now, int) or isinstance(now, bool):
        raise TypeError(f"now must be an int logical tick, got {type(now).__name__}")
    out = [c for c in k.weave().of_type(SCHEDULED_EVENT)
           if not c.content.get("fired", False) and int(c.content["at"]) <= now]
    out.sort(key=lambda c: (int(c.content["at"]), c.id))
    return out


def fire(k, event_id: str, now: int, *, author: str | None = None,
         source: str = "scheduler", trusted: bool = True, kind: str = "request",
         **dispose_kwargs) -> dict:
    """Fire a due event: mark it fired (LWW re-assert on the same Cell) and route its
    action through `disposition.dispose`, then reschedule if it repeats.

    Returns {event, fired_at, disposition, rescheduled} where `disposition` is the dict
    `dispose` returned (the routed action — Decima's decision) and `rescheduled` is the
    new event id for a repeating event (else None).

    A scheduler-fired reminder is a TRUSTED intake by default (the owner set it), routed
    as a `request` → a task; pass `trusted`/`kind`/`source` to route it differently, or
    `target=`/`agent_cell=` straight through to `dispose`. Firing an already-fired or
    unknown event is a fail-loud error."""
    if not isinstance(now, int) or isinstance(now, bool):
        raise TypeError(f"now must be an int logical tick, got {type(now).__name__}")
    author = author or k.decima_agent_id
    cell = k.weave().get(event_id)
    if cell is None or cell.type != SCHEDULED_EVENT:
        raise ValueError(f"no scheduled_event {event_id!r}")
    if cell.content.get("fired", False):
        raise ValueError(f"scheduled_event {event_id!r} already fired")

    title = cell.content["title"]
    at = int(cell.content["at"])
    repeat_every = cell.content.get("repeat_every")

    # 1. Route the action via disposition — a due event can fire a disposition.
    routed = disp.dispose(k, source, title, trusted=trusted, kind=kind,
                          author=author, **dispose_kwargs)

    # 2. Mark fired — a fresh CONTENT assert on the SAME cell id; the fold resolves it
    #    LWW, so `due` no longer returns it. `fired_at`/`disposition` keep provenance.
    fired_content = {"title": title, "at": at, "fired": True,
                     "fired_at": int(now), "disposition": routed["disposition"]}
    if repeat_every is not None:
        fired_content["repeat_every"] = int(repeat_every)
    assert_content(k.weft, author, event_id, SCHEDULED_EVENT, fired_content)

    # 3. Reschedule a repeating event to at + interval (a new, not-yet-fired Cell).
    rescheduled = None
    if repeat_every is not None:
        rescheduled = schedule(k, title, at + int(repeat_every),
                               repeat_every=int(repeat_every), author=author)

    return {"event": event_id, "fired_at": int(now),
            "disposition": routed, "rescheduled": rescheduled}
