"""LEARN1 — spaced-repetition flashcards by composition over scheduling (SCHED1).

Logical ticks (ints), no wall-clock: `due_cards(k, deck, now)` takes the clock.
This check proves:
  - add cards → fresh cards land in box 1, due at their `at` tick (ints);
  - due_cards(now) returns ONLY the cards due at/before now (future-due excluded);
  - a CORRECT review PROMOTES the box and pushes `due` further out (reschedules
    via scheduling.schedule — the next review is a scheduled_event on the Weft);
  - a WRONG review RESETS the box to 1 and reschedules due soon;
  - deck_stats counts cards by box;
  - every box/due/count is an int; all state is on the Weft.

Contract: run(k, line). Fail loud.
"""
from decima import learn, scheduling


def run(k, line):
    line("\n== LEARNING / FLASHCARDS (add → due(now) → review → deck_stats) — LEARN1 ==")
    w = lambda: k.weave()
    ids = lambda cells: {c.id for c in cells}

    # 1. Add three cards to deck "es": two due by tick 0, one due in the future.
    a = learn.add_card(k, "es", "hola", "hello", at=0)
    b = learn.add_card(k, "es", "gato", "cat", at=0)
    later = learn.add_card(k, "es", "perro", "dog", at=50)
    ca = w().get(a).content
    assert ca["box"] == 1 and ca["due"] == 0 and ca["reviews"] == 0, ca
    assert isinstance(ca["box"], int) and isinstance(ca["due"], int), "box/due must be ints"
    # Composition: adding a card scheduled its first review as a scheduled_event.
    assert any(c.content["title"] == "review:es:hola"
               for c in w().of_type(scheduling.SCHEDULED_EVENT)), "first review must be scheduled"
    line("  added 3 cards to deck 'es' (box=1, due ints; first review scheduled on Weft) ✓")

    # 2. due_cards(now=0): only due <= 0 — the future card (due=50) is excluded.
    d = learn.due_cards(k, "es", now=0)
    assert ids(d) == {a, b}, ids(d)
    assert later not in ids(d), "a card due at=50 must NOT be due at now=0"
    line(f"  due_cards(now=0) → {len(d)} cards (due<=0); future due=50 excluded ✓")

    # 3. A CORRECT review PROMOTES the box and pushes due FURTHER OUT.
    before = w().get(a).content
    res = learn.review(k, a, grade=1, now=0)
    after = w().get(a).content
    assert after["box"] == before["box"] + 1, "a correct review must promote the box"
    assert after["due"] > before["due"], "a correct review must push due further out"
    assert after["due"] == 0 + learn.interval(after["box"]), "due = now + interval(new box)"
    assert isinstance(after["due"], int) and isinstance(after["box"], int)
    assert after["reviews"] == 1
    # The promoted card is no longer due at now=0 (rescheduled out).
    assert a not in ids(learn.due_cards(k, "es", now=0)), "promoted card pushed past now=0"
    line(f"  correct review: box {before['box']}→{after['box']}, "
         f"due {before['due']}→{after['due']} (further out, off due-list) ✓")

    # 4. Promote again, then a WRONG review RESETS the box to 1 (and due soon).
    learn.review(k, a, grade=1, now=10)          # box 2 → 3
    promoted = w().get(a).content
    assert promoted["box"] == 3, promoted
    res_wrong = learn.review(k, a, grade=0, now=20)
    reset = w().get(a).content
    assert reset["box"] == 1, "a wrong review must reset the box to 1"
    assert reset["due"] == 20 + learn.interval(1), "wrong → due = now + interval(1) (soon)"
    assert reset["reviews"] == 3, reset
    line(f"  wrong review: box {promoted['box']}→1 (reset), due={reset['due']} (soon again) ✓")

    # 5. deck_stats counts cards by box (all ints).
    stats = learn.deck_stats(k, "es")
    assert stats["total"] == 3, stats
    assert sum(stats["box"].values()) == 3, stats
    assert all(isinstance(b, int) and isinstance(n, int) for b, n in stats["box"].items())
    # a reset to box 1; b untouched (box 1); later untouched (box 1) → all in box 1.
    assert stats["box"].get(1) == 3, stats
    line(f"  deck_stats → total={stats['total']}, by box {stats['box']} (all ints) ✓")

    # 6. Fail-loud: reviewing an unknown card raises.
    try:
        learn.review(k, "nope-not-a-card", grade=1, now=0)
        raise AssertionError("reviewing an unknown card must raise")
    except ValueError:
        pass
    line("  reviewing an unknown card raises (fail-loud) ✓")
    line("  → flashcards are data on the Weft; due_cards(now) is a clock-parameterized "
         "projection; review promotes/resets the Leitner box and reschedules via SCHED1.")
