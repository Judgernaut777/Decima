"""BUDGET1 — finance analytics by COMPOSITION over signed receipts + the portfolio.

`CAPABILITY_MAP` D3.4 (Finance / budgeting). A budget is not a new authority and
moves no money: it is a *read-only fold* over the FINANCIAL EffectReceipts that
`payments.pay` / `trading.buy|sell` already wrote to the Weft, plus the `portfolio`
Cell that `trading` maintains. It composes those public APIs and edits no core file.

What it adds is purely analytic state + projections:
  - `set_budget(k, category, cap)` — assert a `budget` Cell: a spend cap (int minor
    units) per spend category. LWW, content-addressed by category, so re-setting a
    cap overwrites the prior one (auditable in history).
  - `spend_report(k, *, by="category"|"period")` — total the SUCCEEDED, non-replayed
    FINANCIAL receipts, grouped by spend category (folded from the payee) or by a
    coarse period bucket (folded from the receipt event's seq). Each total carries
    `provenance`: the receipt cell ids it summed — so a number always traces to the
    signed receipts that justify it.
  - `overspend(k)` — categories whose actual spend EXCEEDS their `budget` cap (flag);
    a category at/under cap is not flagged.
  - `portfolio_pnl(k, prices, account="default")` — per-position cost basis vs a
    provided/marked price → unrealized P&L (int minor units), folded from the
    `portfolio` Cell. Provenance: the trade receipts that built each position.

LAWS honored: read-only composition (no `invoke`, no money movement — only
`weave().of_type` / `get` and `payments`/`trading` public reads); ALL amounts are
INTS in minor units (no floats ever enter a number we report); provenance is carried
on every result; no ambient authority (it reads the Weft, asserts only its own
`budget` analytic Cells via the public `model.assert_content`).
"""
from decima import payments, trading, model
from decima.hashing import content_id, nfc

BUDGET = "budget"
PERIOD_BUCKET = 8   # events per coarse "period" — a deterministic grouping seam

# A payee is written as "<category>" (a plain bill) or "exchange:<symbol>" (a trade
# fill / commission). The spend category is the part before the first ":"; trades all
# roll up under the "exchange" category. Kept deliberately simple — a real chart of
# accounts is data the same way.
def _category_of(receipt_content: dict) -> str:
    payee = nfc(str(receipt_content.get("payee", "") or "uncategorized"))
    return payee.split(":", 1)[0] if payee else "uncategorized"


def _financial_receipts(k):
    """The SUCCEEDED, non-replayed FINANCIAL EffectReceipts on the Weft — the signed
    ground truth every total folds from. (A FAILED receipt moved no money; a replay
    re-points at an existing receipt and must not be double-counted — the kernel only
    ever wrote ONE receipt per real charge, so iterating receipts dedupes itself.)"""
    out = []
    for c in k.weave().of_type(payments.RESULT):
        rc = c.content
        if (rc.get("effect_class") == payments.FINANCIAL
                and rc.get("status") == payments.executor.SUCCEEDED
                and isinstance(rc.get("amount"), int)):
            out.append(c)
    return out


def _receipt_seq(k, cell) -> int:
    """The Weft seq of the event that created a receipt cell — its position in the
    log, used as a deterministic period coordinate (no wall-clock float on a signed
    receipt). The creating ASSERT is the cell's first provenance event."""
    if not cell.provenance:
        return 0
    eid = cell.provenance[0]
    row = k.weft.db.execute("SELECT seq FROM events WHERE id=?", (eid,)).fetchone()
    return int(row[0]) if row else 0


# ── budget caps (analytic Cells on the Weft) ────────────────────────────────
def _budget_id(category: str) -> str:
    return content_id({"budget": nfc(category)})


def set_budget(k, category: str, cap: int) -> str:
    """Set a spend cap for `category` (int minor units) as a `budget` Cell. Returns
    the cell id. Re-setting overwrites (LWW) — the prior cap stays in history."""
    category = nfc(category)
    cid = _budget_id(category)
    model.assert_content(k.weft, k.decima_agent_id, cid, BUDGET,
                         {"category": category, "cap": int(cap)})
    return cid


def budgets(k) -> dict:
    """Folded caps {category: cap_int} from the `budget` Cells on the Weft."""
    return {c.content["category"]: int(c.content["cap"])
            for c in k.weave().of_type(BUDGET)}


# ── spend report (a fold over FINANCIAL receipts) ───────────────────────────
def spend_report(k, *, by: str = "category") -> dict:
    """Totals over the FINANCIAL receipts, grouped `by` "category" (folded from the
    payee) or "period" (a coarse bucket folded from the receipt event's seq). Returns
    {group: {"total": int, "count": int, "provenance": [receipt_cell_id, …]}} — every
    total traces to the signed receipts it summed. All arithmetic in int minor units."""
    if by not in ("category", "period"):
        raise ValueError(f"spend_report: by must be 'category' or 'period', got {by!r}")
    groups: dict = {}
    for cell in _financial_receipts(k):
        rc = cell.content
        if by == "category":
            key = _category_of(rc)
        else:
            key = f"p{_receipt_seq(k, cell) // PERIOD_BUCKET}"
        g = groups.setdefault(key, {"total": 0, "count": 0, "provenance": []})
        g["total"] += int(rc["amount"])         # INT minor units only
        g["count"] += 1
        g["provenance"].append(cell.id)
    return groups


def overspend(k) -> dict:
    """Categories whose actual FINANCIAL spend EXCEEDS their `budget` cap. Returns
    {category: {"spent": int, "cap": int, "over": int, "provenance": [...]}} — only the
    flagged categories; a category at/under its cap is omitted. A category with spend
    but no cap is NOT flagged (no cap = unbudgeted, not overspent)."""
    caps = budgets(k)
    report = spend_report(k, by="category")
    flagged: dict = {}
    for category, cap in caps.items():
        g = report.get(category)
        spent = g["total"] if g else 0
        if spent > int(cap):
            flagged[category] = {
                "spent": int(spent), "cap": int(cap), "over": int(spent) - int(cap),
                "provenance": list(g["provenance"]) if g else [],
            }
    return flagged


# ── portfolio P&L (a fold over the portfolio Cell + trade receipts) ─────────
def _position_provenance(k, symbol: str, account: str) -> list:
    """The FINANCIAL receipt ids of the fills that built this position — provenance
    that traces a position's cost basis back to signed receipts (via the `trade`
    Cells `trading` wrote, each `settled_by` its payment receipt)."""
    w = k.weave()
    out = []
    for t in w.of_type(trading.TRADE):
        if t.content.get("symbol") == symbol and t.content.get("account") == account \
                and t.content.get("filled"):
            for e in w.edges_from(t.id, "settled_by"):
                out.append(e["dst"])
    return out


def portfolio_pnl(k, prices: dict, account: str = "default") -> dict:
    """Per-position cost basis vs a provided/marked price → unrealized P&L. `prices`
    is {symbol: marked_price_int}. Returns {symbol: {qty, cost, mark, value, pnl,
    provenance}}; all ints in minor units (value = qty·mark, pnl = value − cost).
    A symbol with no provided mark is skipped (no fabricated price)."""
    positions = trading.portfolio(k.weave(), account)
    out: dict = {}
    for symbol, pos in positions.items():
        if symbol not in prices:
            continue
        qty, cost = int(pos["qty"]), int(pos["cost"])
        mark = int(prices[symbol])
        value = qty * mark
        out[symbol] = {
            "qty": qty, "cost": cost, "mark": mark, "value": value,
            "pnl": value - cost,                 # INT P&L in minor units
            "provenance": _position_provenance(k, symbol, account),
        }
    return out
