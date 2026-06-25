"""GOALS1 — Goals & habits: a goal is a wager on yourself; a habit is a recurring nudge.

This is a COMPOSING module, not core. It binds two existing capabilities into one:

  - WV1 (wager.py): a goal is a *prediction about your own future* — set a target and you
    wager that you'll hit it. The wager is the goal's accountability spine: reaching the
    target settles its verdict (hit), and the calibration loop learns how well you keep your
    own commitments. We don't re-implement prediction — we `wager.wager` / `wager.verdict`.

  - SCHED1 (scheduling.py): a habit is a recurring `scheduled_event` — a reminder that fires
    on an interval. We don't re-implement reminders — we `scheduling.schedule(..., repeat_every)`.

What's NEW here is the small layer that ties them to user-facing intent:

  - `set_goal(k, name, target, *, confidence)` → a `goal` Cell carrying an INT `target`
    (minor units) and `progress` (starts 0), BOUND to a fresh WV1 wager via a `goal_wager`
    edge. The wager predicts the target; settling it is how a goal "completes".
  - `progress(k, goal, value)` → LWW-update the goal's `progress` (a re-assert to the same
    Cell). On reaching/clearing the target, settle the wager's verdict (observed == target →
    hit) and mark the goal `done`. Idempotent once done.
  - `habit(k, name, every, *, at)` → a recurring habit: a `habit` Cell plus a repeating
    `scheduled_event` (interval `every`, first due at `at`) via `scheduling.schedule`. Carries
    an INT `streak` (starts 0).
  - `record_done(k, habit)` → advance the streak counter by one (LWW re-assert, INT).

LAWS this lane keeps (WEFT §4/§7 + ambient authority):
  - INTS ONLY in signed content: `target`, `progress`, `streak`, `every`, `at`, `confidence`
    (millionths) are all ints — a float is rejected before it reaches the Weft.
  - PROVENANCE on the Weft: goals/habits are Cells; updates are fresh ASSERTs to the same
    Cell id (folded LWW); the goal↔wager and habit↔schedule links are typed edges.
  - NO AMBIENT AUTHORITY: every assert is authored (default the Decima agent); we only call
    the PUBLIC wager/scheduling APIs — no kernel edit, no reaching past their gates.
"""
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc
from decima import wager as wv
from decima import scheduling as sched

GOAL = "goal"
HABIT = "habit"


def _int(name: str, value) -> int:
    """Reject floats/bools before they reach signed content (WEFT §4/§7)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int (minor units / ticks), got {type(value).__name__}")
    return int(value)


def set_goal(k, name: str, target: int, *, confidence: int, author: str | None = None) -> str:
    """Set a goal with an INT `target` (minor units) and bind a WV1 wager on yourself —
    you predict you'll hit the target, at `confidence` (millionths). Returns the goal Cell id.

    The goal carries `progress` (starts 0) and `status='open'`; a `goal_wager` edge links it
    to the wager whose verdict settles when the goal completes."""
    target = _int("target", target)
    confidence = _int("confidence", confidence)
    author = author or k.decima_agent_id
    name = nfc(name)

    # A goal is a wager on yourself: predict you'll reach `target`.
    wid = wv.wager(k, f"goal: {name}", prediction=target, confidence=confidence, author=author)

    gid = content_id({"goal": name, "target": target, "at": k.weft.head})
    assert_content(k.weft, author, gid, GOAL, {
        "name": name, "target": target, "progress": 0,
        "wager": wid, "status": "open",
    })
    assert_edge(k.weft, author, gid, "goal_wager", wid)
    return gid


def progress(k, goal: str, value: int, *, author: str | None = None) -> dict:
    """Update a goal's progress to INT `value` (LWW re-assert to the same Cell). On reaching
    the target, settle the bound wager's verdict (observed == target → hit) and mark the goal
    `done`. Idempotent once done. Returns {progress, status, done, verdict} (verdict is the
    settle result the first time the target is reached, else None)."""
    value = _int("value", value)
    author = author or k.decima_agent_id
    g = k.weave().get(goal)
    if g is None or g.type != GOAL:
        raise ValueError(f"not a goal: {goal!r}")

    content = dict(g.content)
    content["progress"] = value
    settled = None
    if content["status"] == "open" and value >= content["target"]:
        # Reached the target: settle the wager. Observed == target → hit (delta 0).
        settled = wv.verdict(k, content["wager"], observed=content["target"], author=author)
        content["status"] = "done"
    assert_content(k.weft, author, goal, GOAL, content)   # new version (LWW)
    return {"progress": value, "status": content["status"],
            "done": content["status"] == "done", "verdict": settled}


def habit(k, name: str, every: int, *, at: int, author: str | None = None) -> dict:
    """Create a recurring habit: a `habit` Cell (INT `streak`, starts 0) plus a repeating
    `scheduled_event` that reminds every `every` ticks, first due at `at` — composed via
    `scheduling.schedule`. Returns {habit, event} (the two Cell ids), linked by a
    `habit_schedule` edge. `every`/`at` are ints (enforced by scheduling too)."""
    every = _int("every", every)
    at = _int("at", at)
    if every <= 0:
        raise ValueError("every must be a positive number of ticks")
    author = author or k.decima_agent_id
    name = nfc(name)

    # A habit is a recurring reminder: compose scheduling's repeat_every.
    eid = sched.schedule(k, f"habit: {name}", at=at, repeat_every=every, author=author)

    hid = content_id({"habit": name, "every": every, "at": k.weft.head})
    assert_content(k.weft, author, hid, HABIT, {
        "name": name, "every": every, "event": eid, "streak": 0,
    })
    assert_edge(k.weft, author, hid, "habit_schedule", eid)
    return {"habit": hid, "event": eid}


def record_done(k, habit: str, *, author: str | None = None) -> int:
    """Mark a habit done once: advance its INT `streak` by one (LWW re-assert to the same
    Cell). Returns the new streak."""
    author = author or k.decima_agent_id
    h = k.weave().get(habit)
    if h is None or h.type != HABIT:
        raise ValueError(f"not a habit: {habit!r}")
    content = dict(h.content)
    content["streak"] = int(content["streak"]) + 1
    assert_content(k.weft, author, habit, HABIT, content)
    return content["streak"]
