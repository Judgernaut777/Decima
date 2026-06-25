"""LEARN1 — spaced-repetition flashcards by COMPOSITION over scheduling (SCHED1).

`CAPABILITY_MAP` Part B (Personal-OS domains — notes/knowledge): learning is
review *scheduled over time*. A flashcard is data on the Weft; *when* it next comes
up for review is exactly a scheduled event — so this module composes the scheduling
PUBLIC API (`scheduling.schedule`) rather than re-inventing a clock or a queue.

A `flashcard` Cell carries:
  - `deck`, `front`, `back`   — the card (content-addressed by (deck, front), so
    re-adding the same prompt is idempotent / LWW-updates the answer);
  - `box`  — the Leitner box (int ≥ 1); a higher box = a longer interval;
  - `due`  — the integer logical tick at/after which the card is due for review;
  - `reviews` — how many times it's been reviewed (int provenance counter).

The model:
  - `add_card(k, deck, front, back, at=0)` → a fresh card in box 1, due at `at`,
    and a `scheduled_event` (via `scheduling.schedule`) for that first review.
  - `review(k, card, grade, now)` → a Leitner / SM2-lite step: a CORRECT grade
    PROMOTES the box (box+1, capped) and reschedules due = now + interval(box) —
    further out; a WRONG grade RESETS the box to 1 and reschedules due = now +
    interval(1) — soon again. The reschedule goes through `scheduling.schedule`.
  - `due_cards(k, deck, now)` → the deck's cards with `due <= now` (a clock-
    parameterized projection — `now` is supplied by the caller, no wall-clock).
  - `deck_stats(k, deck)` → counts of cards by box.

LAWS honored:
  - INTS, never floats: box numbers, the per-box intervals, `due` ticks and the
    review counter are all ints; `interval(box)` returns an int; a non-int `now`/
    `at`/`grade` is rejected so no float reaches signed content (mirrors SCHED1).
  - DETERMINISM / no ambient clock: `due_cards(k, deck, now)` takes `now`; the
    intervals are a fixed deterministic table — the same review at the same tick
    always reschedules to the same due tick.
  - PROVENANCE on the Weft: every box/due change is a fresh CONTENT assert on the
    SAME card cell (folded LWW), and each review schedules a `scheduled_event`
    (SCHED1) — the review history stays on the Log; the module authors only its own
    `flashcard` Cells via the public `model.assert_content`, no core edit.
"""
from __future__ import annotations

from decima import scheduling, model
from decima.hashing import content_id, nfc

FLASHCARD = "flashcard"

# Leitner box → interval in integer logical ticks. Box 1 is "review soon"; each
# higher box pushes the next review deterministically further out. The last entry
# is the cap — a card that keeps being answered correctly stays at the top box.
# Deliberately a fixed table (no float math): SM2-lite without a fractional ease.
_INTERVALS = (1, 2, 4, 8, 16)
MAX_BOX = len(_INTERVALS)


def interval(box: int) -> int:
    """The integer review interval (ticks) for a Leitner `box` (1-based). A box at or
    above the cap uses the top interval. Fail-loud on a non-positive / non-int box."""
    if not isinstance(box, int) or isinstance(box, bool):
        raise TypeError(f"box must be an int, got {type(box).__name__}")
    if box < 1:
        raise ValueError(f"box must be >= 1, got {box}")
    return int(_INTERVALS[min(box, MAX_BOX) - 1])


def _card_id(deck: str, front: str) -> str:
    """Content-address a card by (deck, front) — re-adding the same prompt in the
    same deck is idempotent (LWW-updates the answer / resets box), never a duplicate."""
    return content_id({"flashcard": nfc(deck), "front": nfc(front)})


def add_card(k, deck: str, front: str, back: str, at: int = 0,
             *, author: str | None = None) -> str:
    """Add a `flashcard` to `deck` in box 1, due at tick `at` (default 0). Schedules
    the first review as a `scheduled_event` (SCHED1) and returns the card cell id.

    All times are ints: a float/bool `at` is rejected so no float reaches signed
    content (mirrors `scheduling.schedule`)."""
    if not isinstance(at, int) or isinstance(at, bool):
        raise TypeError(f"at must be an int logical tick, got {type(at).__name__}")
    author = author or k.decima_agent_id
    deck, front, back = nfc(deck), nfc(front), nfc(back)
    cid = _card_id(deck, front)
    model.assert_content(k.weft, author, cid, FLASHCARD, {
        "deck": deck, "front": front, "back": back,
        "box": 1, "due": int(at), "reviews": 0,
    })
    # Compose scheduling: the first review is a scheduled event on the Weft.
    scheduling.schedule(k, f"review:{deck}:{front}", at=int(at), author=author)
    return cid


def review(k, card: str, grade: int, now: int, *, author: str | None = None) -> dict:
    """Record a review of `card` at tick `now` with an integer `grade`:
      - a CORRECT grade (>= 1) PROMOTES the box (box+1, capped at MAX_BOX) and
        reschedules due = now + interval(new_box) — further out;
      - a WRONG grade (<= 0) RESETS the box to 1 and reschedules due = now +
        interval(1) — soon again.
    The new due is a fresh CONTENT assert on the SAME card cell (LWW) and the next
    review is (re)scheduled via `scheduling.schedule`. Returns the new card state
    {card, box, due, reviews}. Fail-loud on a non-int grade/now or an unknown card."""
    if not isinstance(grade, int) or isinstance(grade, bool):
        raise TypeError(f"grade must be an int, got {type(grade).__name__}")
    if not isinstance(now, int) or isinstance(now, bool):
        raise TypeError(f"now must be an int logical tick, got {type(now).__name__}")
    author = author or k.decima_agent_id
    cell = k.weave().get(card)
    if cell is None or cell.type != FLASHCARD:
        raise ValueError(f"no flashcard {card!r}")
    c = cell.content
    correct = grade >= 1
    box = min(int(c["box"]) + 1, MAX_BOX) if correct else 1
    due = int(now) + interval(box)            # INT tick — deterministic, further out
    reviews = int(c.get("reviews", 0)) + 1
    model.assert_content(k.weft, author, card, FLASHCARD, {
        "deck": c["deck"], "front": c["front"], "back": c["back"],
        "box": box, "due": due, "reviews": reviews,
    })
    # Compose scheduling: reschedule the next review as a scheduled event.
    scheduling.schedule(k, f"review:{c['deck']}:{c['front']}", at=due, author=author)
    return {"card": card, "box": box, "due": due, "reviews": reviews}


def due_cards(k, deck: str, now: int) -> list:
    """The `deck`'s flashcards with `due <= now`, in (due, id) order. A pure clock-
    parameterized projection over the fold — `now` is supplied by the caller, no
    wall-clock. Cards due in the future (due > now) are excluded."""
    if not isinstance(now, int) or isinstance(now, bool):
        raise TypeError(f"now must be an int logical tick, got {type(now).__name__}")
    deck = nfc(deck)
    out = [c for c in k.weave().of_type(FLASHCARD)
           if c.content.get("deck") == deck and int(c.content["due"]) <= now]
    out.sort(key=lambda c: (int(c.content["due"]), c.id))
    return out


def deck_stats(k, deck: str) -> dict:
    """Counts of the `deck`'s cards by Leitner box: {"box": {box_int: count_int},
    "total": int}. A pure projection over the fold; all counts are ints."""
    deck = nfc(deck)
    by_box: dict = {}
    total = 0
    for c in k.weave().of_type(FLASHCARD):
        if c.content.get("deck") != deck:
            continue
        box = int(c.content["box"])
        by_box[box] = by_box.get(box, 0) + 1
        total += 1
    return {"box": by_box, "total": total}
