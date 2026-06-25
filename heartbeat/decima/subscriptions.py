"""SUBS1 — Subscriptions tracker by COMPOSITION over scheduling + budget (D3.4).

A subscription is recurring spend: a named charge of an integer `amount` (minor
units) that renews every `every` logical ticks. This module forges no new authority
and moves no money — it only asserts its own analytic `subscription` Cells and
composes the PUBLIC `scheduling` and `budget` APIs:

  - `add_subscription(k, name, amount, every, *, category=None, next_at)` — assert a
    `subscription` Cell (int amount, int interval) and schedule its NEXT renewal as a
    *recurring* reminder via `scheduling.schedule(repeat_every=every)`. The reminder is
    the renewal clock (deterministic logical ticks, no wall-clock); the Cell is the
    analytic record. A `renews_via` edge links the subscription to its reminder, so the
    schedule provenance lives on the Weft.
  - `due_renewals(k, now)` — subscriptions whose next renewal is at/before `now`,
    folded from `scheduling.due(now)` over the linked reminders (a clock-parameterized
    projection — the caller owns `now`).
  - `monthly_cost(k)` — total recurring cost (int minor units) and the breakdown by
    category, folded from the `subscription` Cells. All arithmetic in ints.
  - `check_budget(k)` — compose BUDGET1's caps: flag categories whose recurring
    subscription cost exceeds a `budget` cap. A category at/under cap (or uncapped) is
    not flagged.

LAWS honored: ALL amounts/intervals are INTS in minor units / logical ticks (no
floats, ever); the renewal clock is `scheduling`'s explicit-tick reminder (no
wall-clock in signed content); no ambient authority (asserts only its own
`subscription` Cells + a `renews_via` edge via the public `model`/`scheduling` APIs,
reads via `weave`); provenance carried on every reported number and on the Weft.
"""
from decima import model, scheduling, budget
from decima.hashing import content_id, nfc

SUBSCRIPTION = "subscription"


def _subscription_id(name: str) -> str:
    """Content-address a subscription by its NAME — re-adding the same subscription
    (a changed amount/interval) lands LWW on the same Cell, with history on the Log."""
    return content_id({"subscription": nfc(name)})


def add_subscription(k, name: str, amount: int, every: int, *,
                     category: str = None, next_at: int, author: str = None) -> str:
    """Track a recurring charge as a `subscription` Cell and return its id.

    `amount` is int minor units (the recurring charge in cents) and `every` is an int
    interval in logical ticks — a non-int (or float) is a hard error so no float ever
    reaches signed content. `next_at` is the integer tick of the NEXT renewal; the
    renewal is scheduled via `scheduling.schedule(repeat_every=every)`, so it reschedules
    itself one interval out each time it fires. A `renews_via` edge links the Cell to
    that reminder (schedule provenance on the Weft)."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError(f"subscription amount must be int minor units, got {amount!r}")
    if amount < 0:
        raise ValueError(f"subscription amount must be non-negative, got {amount}")
    if not isinstance(every, int) or isinstance(every, bool):
        raise TypeError(f"every must be an int interval (ticks), got {every!r}")
    if every <= 0:
        raise ValueError("every must be a positive number of ticks")
    author = author or k.decima_agent_id
    name = nfc(name)
    category = nfc(category) if category is not None else "uncategorized"

    # The renewal clock: a recurring scheduled_event that reschedules itself every
    # `every` ticks (scheduling.schedule rejects float at/repeat_every for us).
    reminder = scheduling.schedule(k, f"Renew {name}", at=next_at,
                                   repeat_every=every, author=author)

    sid = _subscription_id(name)
    model.assert_content(k.weft, author, sid, SUBSCRIPTION, {
        "name": name, "amount": int(amount), "every": int(every),
        "category": category, "next_at": int(next_at), "reminder": reminder,
    })
    # Provenance on the Weft: the subscription renews via its reminder.
    model.assert_edge(k.weft, author, sid, "renews_via", reminder)
    return sid


def subscriptions(k) -> list:
    """The `subscription` Cells on the Weft, in (name, id) order."""
    out = list(k.weave().of_type(SUBSCRIPTION))
    out.sort(key=lambda c: (c.content.get("name", ""), c.id))
    return out


def due_renewals(k, now: int) -> list:
    """The subscriptions whose next renewal is at/before `now`. A clock-parameterized
    projection: it folds `scheduling.due(now)` (the reminders at <= now that haven't
    fired) back to the `subscription` Cells they renew. `now` is supplied by the caller
    (no wall-clock here). Returned in (name, id) order."""
    if not isinstance(now, int) or isinstance(now, bool):
        raise TypeError(f"now must be an int logical tick, got {type(now).__name__}")
    due_ids = {c.id for c in scheduling.due(k, now)}
    out = [s for s in subscriptions(k) if s.content.get("reminder") in due_ids]
    out.sort(key=lambda c: (c.content.get("name", ""), c.id))
    return out


def monthly_cost(k) -> dict:
    """Total recurring cost over the `subscription` Cells, and the breakdown by
    category. Returns {"total": int, "by_category": {category: {"total": int,
    "count": int, "provenance": [subscription_cell_id, …]}}} — every total traces to
    the subscriptions it summed. All arithmetic in int minor units (no floats)."""
    by_category: dict = {}
    total = 0
    for s in subscriptions(k):
        amount = int(s.content["amount"])        # INT minor units only
        category = s.content.get("category", "uncategorized")
        total += amount
        g = by_category.setdefault(category, {"total": 0, "count": 0, "provenance": []})
        g["total"] += amount
        g["count"] += 1
        g["provenance"].append(s.id)
    return {"total": total, "by_category": by_category}


def check_budget(k) -> dict:
    """Compose BUDGET1: categories whose RECURRING subscription cost exceeds their
    `budget` cap. Returns {category: {"spent": int, "cap": int, "over": int,
    "provenance": [...]}} — only flagged categories; a category at/under cap, or with no
    cap (unbudgeted, not overspent), is omitted. Caps come from `budget.set_budget` (the
    same `budget` Cells BUDGET1 reads), so SUBS1 reuses that authority-free cap store
    rather than forging its own."""
    caps = budget.budgets(k)                          # {category: cap_int}
    cost = monthly_cost(k)["by_category"]
    flagged: dict = {}
    for category, cap in caps.items():
        g = cost.get(category)
        spent = g["total"] if g else 0
        if spent > int(cap):
            flagged[category] = {
                "spent": int(spent), "cap": int(cap), "over": int(spent) - int(cap),
                "provenance": list(g["provenance"]) if g else [],
            }
    return flagged
