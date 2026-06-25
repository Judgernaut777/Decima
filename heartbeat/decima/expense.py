"""EXPENSE1 — user/receipt-entered spend tracking by COMPOSITION (D3.4, finance).

Distinct from BUDGET1: `budget.py` is a read-only fold over the *signed FINANCIAL
EffectReceipts* that `payments`/`trading` already wrote — money the kernel actually
moved. EXPENSE1 is the other half of a chart of accounts: spend the *owner* enters
(or a receipt they scanned) that did NOT flow through a payment rail. It moves no
money and forges no authority; it only asserts its own analytic `expense` Cells and
composes the PUBLIC `model` / `budget` / `disposition` APIs.

What it adds:
  - `capture(k, vendor, amount, category, *, trusted=True)` — assert an `expense`
    Cell (int minor units, a spend category). A TRUSTED entry is the owner recording
    their own spend. An externally-sourced receipt (`trusted=False`) is UNTRUSTED
    DATA: it is captured via `disposition.dispose` (→ remembered as DATA,
    `instruction_eligible=False`) and the resulting `expense` Cell is itself flagged
    DATA. A scanned receipt is never an instruction — it can only ever be recorded.
  - `report(k, *, by="category")` — integer totals per group, each carrying
    `provenance`: the `expense` cell ids it summed, so a number always traces to the
    entries that justify it. All arithmetic in int minor units (no floats, ever).
  - `check_budget(k)` — compose BUDGET1's caps: flag categories whose EXPENSE total
    exceeds a `budget` cap. A category at/under cap (or with no cap) is not flagged.

LAWS honored: ALL amounts are INTS in minor units; an externally-sourced receipt is
UNTRUSTED data (captured via disposition, never an instruction); no ambient authority
(asserts only its own `expense` Cells via the public `model.assert_content`, reads via
`weave`); provenance carried on every reported number and on the Weft (a `recorded_by`
edge from each expense to the intake/disposition that captured it).
"""
from decima import model, budget, disposition
from decima.hashing import content_id, nfc

EXPENSE = "expense"


def _expense_id(k, vendor: str, amount: int, category: str) -> str:
    # Content-addressed, but salted with the Weft head so two identical entries
    # (same vendor/amount/category) are distinct events, not an idempotent overwrite —
    # spending the same $5 at the same shop twice is two expenses, not one.
    return content_id({"expense": nfc(vendor), "amount": int(amount),
                       "category": nfc(category), "at": k.weft.head})


def capture(k, vendor: str, amount: int, category: str, *, trusted: bool = True,
            author: str = None) -> str:
    """Record a spend as an `expense` Cell and return its id. `amount` is int minor
    units (a receipt total in cents) — a non-int is a hard error (no float money).

    An externally-sourced receipt (`trusted=False`) is UNTRUSTED data: it is captured
    through `disposition.dispose`, which records it as memory DATA
    (`instruction_eligible=False`) — its imperative content can never select an action.
    The `expense` Cell carries `trusted`/`instruction_eligible` to match, and a
    `recorded_by` edge links it to the disposition (untrusted) or intake (trusted) that
    captured it, so the number's provenance is on the Weft."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError(f"expense amount must be int minor units, got {amount!r}")
    if amount < 0:
        raise ValueError(f"expense amount must be non-negative, got {amount}")
    author = author or k.decima_agent_id
    vendor, category = nfc(vendor), nfc(category)

    # Capture the source as an intake first (recall-vs-instruct law). An external
    # receipt routes through disposition as DATA; an owner entry is a trusted note.
    note = f"receipt: {vendor} {amount} ({category})"
    disp = disposition.dispose(k, source="receipt", text=note, trusted=bool(trusted),
                               kind=None, author=author)
    # The capturing event we tie provenance to: the disposition cell either way.
    recorded_by = disp["disposition"]

    cid = _expense_id(k, vendor, amount, category)
    model.assert_content(k.weft, author, cid, EXPENSE, {
        "vendor": vendor,
        "amount": int(amount),                 # INT minor units only
        "category": category,
        "trusted": bool(trusted),
        "instruction_eligible": bool(trusted),  # an external receipt is DATA
        "source": recorded_by,
    })
    model.assert_edge(k.weft, author, cid, "recorded_by", recorded_by)
    return cid


def expenses(k) -> list:
    """The `expense` Cells on the Weft (DATA fold, no authority)."""
    return list(k.weave().of_type(EXPENSE))


def report(k, *, by: str = "category") -> dict:
    """Integer totals over the captured `expense` Cells, grouped `by` "category" (the
    spend category) or "vendor". Returns {group: {"total": int, "count": int,
    "provenance": [expense_cell_id, …]}} — every total traces to the entries it summed.
    All arithmetic in int minor units."""
    if by not in ("category", "vendor"):
        raise ValueError(f"report: by must be 'category' or 'vendor', got {by!r}")
    groups: dict = {}
    for c in expenses(k):
        key = c.content.get(by, "uncategorized")
        g = groups.setdefault(key, {"total": 0, "count": 0, "provenance": []})
        g["total"] += int(c.content["amount"])      # INT minor units only
        g["count"] += 1
        g["provenance"].append(c.id)
    return groups


def check_budget(k) -> dict:
    """Compose BUDGET1: categories whose EXPENSE total exceeds their `budget` cap.
    Returns {category: {"spent": int, "cap": int, "over": int, "provenance": [...]}} —
    only flagged categories; a category at/under cap, or with no cap (unbudgeted, not
    overspent), is omitted. Caps come from `budget.set_budget` (the same `budget` Cells
    BUDGET1 reads), so EXPENSE1 reuses that authority-free cap store rather than
    forging its own."""
    caps = budget.budgets(k)                          # {category: cap_int}
    rep = report(k, by="category")
    flagged: dict = {}
    for category, cap in caps.items():
        g = rep.get(category)
        spent = g["total"] if g else 0
        if spent > int(cap):
            flagged[category] = {
                "spent": int(spent), "cap": int(cap), "over": int(spent) - int(cap),
                "provenance": list(g["provenance"]) if g else [],
            }
    return flagged
