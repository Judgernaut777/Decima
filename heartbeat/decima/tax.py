"""TAX1 — progressive tax ESTIMATION over the ledger by COMPOSITION (B1 finance).

The money vertical's advisory tier. TAX1 is NOT a new authority and moves no money:
it is a deterministic fold over the spend the owner already recorded — the `expense`
Cells EXPENSE1 captured and the `journal_entry` lines LEDGER1 posted — that computes
an *estimate* of tax owed. It files nothing and has NO outward effect: it only asserts
its own analytic `tax_estimate` Cells (and `tax_brackets` cap-style Cells) via the
PUBLIC `model.assert_content` and reads via `weave`. It composes the public APIs of
`accounts`, `expense`, `budget`, and `model`; it edits no core file.

What it adds:
  - `set_brackets(k, *, brackets)` — a progressive schedule as `tax_brackets`: a list
    of (int threshold in minor units, int rate in BASIS POINTS). LWW, singular.
  - `deductible_categories(k)` / `categorize(k, category)` — the chart of which spend
    categories reduce taxable income (data, the same way the chart of accounts is).
  - `estimate(k, *, year, income, deductions=None)` — taxable income = income minus
    the deductible spend pulled from EXPENSE1/LEDGER1 (plus any explicit `deductions`),
    floored at zero; apply the progressive brackets with INTEGER arithmetic; return tax
    owed (int), effective rate (int bps), taxable income, and a `tax_estimate` Cell that
    CITES every entry it summed for provenance.
  - `quarterly(k, year)` — the annual estimate split into 4 INTEGER quarterly payments
    whose remainder is handed deterministically to the EARLY quarters, so the four parts
    sum back EXACTLY to the annual total (no rounding loss).

LAWS honored: ALL money is INTS in minor units; ALL rates are INTS in basis points
(1% == 100 bps) — never a float enters a number we fold; deterministic (same ledger →
same estimate); provenance carried on every result AND on the Weft (a `summed` edge
from each estimate to the expense entries it folded); this is an ESTIMATE/advisory
capability — it never invokes an effect and moves no money.
"""
from decima import model, expense, accounts, budget          # noqa: F401 (compose)
from decima.hashing import content_id, nfc

TAX_BRACKETS = "tax_brackets"
TAX_ESTIMATE = "tax_estimate"
DEDUCTIBLE = "tax_deductible"        # a category flagged deductible (analytic Cell)
SUMMED = "summed"                    # estimate → summed → expense entry (provenance)

BPS = 10_000                         # basis-point denominator (100% == 10000 bps)

# The default chart of deductible spend categories. Data, not code — `categorize`
# extends it on the Weft. Deliberately conservative + simple (this is an estimate).
DEFAULT_DEDUCTIBLE = ("charity", "medical", "business", "mortgage_interest")


# ── progressive bracket schedule (one analytic Cell on the Weft) ────────────
def _brackets_id() -> str:
    return content_id({"tax_brackets": "schedule"})


def _normalize_brackets(brackets) -> list:
    """Validate + normalize a progressive schedule into a sorted list of
    {"threshold": int, "rate": int}. A bracket applies to the income ABOVE its
    threshold (and below the next threshold). Every threshold and rate is an INT —
    a float rate is a hard error (rates are basis points, never floats). The first
    threshold is normally 0. Raises on a malformed schedule so a bad schedule never
    reaches the Log or the arithmetic."""
    norm = []
    for b in brackets:
        if isinstance(b, (tuple, list)):
            threshold, rate = b
            b = {"threshold": threshold, "rate": rate}
        threshold, rate = b["threshold"], b["rate"]
        for nm, v in (("threshold", threshold), ("rate", rate)):
            if not isinstance(v, int) or isinstance(v, bool):
                raise TypeError(
                    f"bracket {nm} must be an int (minor units / basis points), got {v!r}")
        if threshold < 0:
            raise ValueError(f"bracket threshold must be >= 0, got {threshold}")
        if not (0 <= rate <= BPS):
            raise ValueError(f"bracket rate must be 0..{BPS} bps, got {rate}")
        norm.append({"threshold": int(threshold), "rate": int(rate)})
    if not norm:
        raise ValueError("a tax schedule needs >=1 bracket")
    norm.sort(key=lambda x: x["threshold"])
    # thresholds must be strictly increasing (a duplicate threshold is ambiguous).
    for a, c in zip(norm, norm[1:]):
        if a["threshold"] == c["threshold"]:
            raise ValueError(f"duplicate bracket threshold {a['threshold']}")
    return norm


def set_brackets(k, *, brackets) -> str:
    """Set the progressive tax schedule as a `tax_brackets` Cell and return its id.
    `brackets` is an iterable of (threshold_int, rate_bps_int) (or dicts with those
    keys): each bracket taxes the income ABOVE its threshold up to the next threshold,
    at its int basis-point rate. LWW / singular (content-addressed by a fixed key), so
    re-setting overwrites the prior schedule (auditable in history)."""
    norm = _normalize_brackets(brackets)
    cid = _brackets_id()
    model.assert_content(k.weft, k.decima_agent_id, cid, TAX_BRACKETS,
                         {"brackets": norm})
    return cid


def brackets(k) -> list:
    """The folded progressive schedule [{"threshold", "rate"}], sorted ascending.
    Empty list when none has been set."""
    cell = k.weave().get(_brackets_id())
    return list(cell.content["brackets"]) if cell is not None else []


# ── deductible categories (the chart of what reduces taxable income) ─────────
def _deductible_id(category: str) -> str:
    return content_id({"tax_deductible": nfc(category)})


def categorize(k, category: str, *, deductible: bool = True) -> str:
    """Flag a spend `category` as deductible (or, with deductible=False, NOT). Asserts
    a `tax_deductible` analytic Cell and returns its id. LWW per category — re-flagging
    overwrites. The chart of deductible categories is data the same way the chart of
    accounts is."""
    category = nfc(category)
    cid = _deductible_id(category)
    model.assert_content(k.weft, k.decima_agent_id, cid, DEDUCTIBLE,
                         {"category": category, "deductible": bool(deductible)})
    return cid


def deductible_categories(k) -> set:
    """The set of spend categories that reduce taxable income: the DEFAULT chart unioned
    with any `tax_deductible` Cells flagged True, minus any explicitly flagged False.
    Folded from the Weft (overrides win), so it is deterministic and auditable."""
    cats = set(DEFAULT_DEDUCTIBLE)
    for c in k.weave().of_type(DEDUCTIBLE):
        cat = c.content["category"]
        if c.content.get("deductible", True):
            cats.add(cat)
        else:
            cats.discard(cat)
    return cats


# ── deductible spend pulled from EXPENSE1 / LEDGER1 ─────────────────────────
def _deductible_spend(k) -> dict:
    """Fold the deductible spend out of the recorded ledger. Returns
    {"total": int, "provenance": [expense_cell_id, …]} — the int sum of every
    `expense` Cell whose category is deductible, plus the entries that justify it.
    Pure fold over EXPENSE1's report; ints throughout (no float ever enters the sum)."""
    deductible = deductible_categories(k)
    rep = expense.report(k, by="category")          # composes EXPENSE1
    total = 0
    provenance: list = []
    for category, g in rep.items():
        if category in deductible:
            total += int(g["total"])                # INT minor units only
            provenance.extend(g["provenance"])
    return {"total": int(total), "provenance": provenance}


# ── progressive tax math (integer arithmetic, basis points) ─────────────────
def _progressive_tax(taxable: int, schedule: list) -> int:
    """The progressive tax on an INT `taxable` income under `schedule` (sorted
    ascending). Each bracket taxes the slice of income between its threshold and the
    next bracket's threshold at its bps rate; the top bracket taxes everything above
    its threshold. ALL integer arithmetic: a slice's tax is (slice * rate_bps) // BPS
    — floor division keeps the result an int (an estimate rounds toward zero, no float).
    """
    if taxable <= 0 or not schedule:
        return 0
    tax = 0
    for i, b in enumerate(schedule):
        lo = b["threshold"]
        if taxable <= lo:
            break                                   # nothing reaches this bracket
        hi = schedule[i + 1]["threshold"] if i + 1 < len(schedule) else None
        top = taxable if hi is None else min(taxable, hi)
        slice_amt = top - lo                        # the income taxed in THIS bracket
        if slice_amt > 0:
            tax += (slice_amt * b["rate"]) // BPS   # INT: floor, no float
    return tax


def estimate(k, *, year: int, income: int, deductions: int = None) -> dict:
    """Estimate the tax owed for `year` on an INT `income` (minor units), and assert a
    `tax_estimate` Cell that cites the entries it summed.

    Taxable income = income − (deductible spend folded from EXPENSE1/LEDGER1 + any
    explicit `deductions`), FLOORED at zero (an estimate is never negative). The
    progressive `set_brackets` schedule is applied with integer arithmetic to yield the
    int tax owed and the effective rate in basis points (tax * BPS // income, 0 when
    income is 0). The returned dict carries the taxable income, the deductible total,
    the tax, the effective rate, and `provenance` (every expense cell id summed); a
    `summed` EDGE ties the estimate Cell to each of those entries on the Weft.

    This is ADVISORY: it asserts an analytic Cell and invokes no effect — no money moves
    and nothing is filed."""
    if not isinstance(year, int) or isinstance(year, bool):
        raise TypeError(f"year must be an int, got {year!r}")
    if not isinstance(income, int) or isinstance(income, bool):
        raise TypeError(f"income must be int minor units, got {income!r}")
    if income < 0:
        raise ValueError(f"income must be non-negative, got {income}")
    if deductions is not None and (not isinstance(deductions, int)
                                   or isinstance(deductions, bool)):
        raise TypeError(f"deductions must be int minor units, got {deductions!r}")

    schedule = brackets(k)
    if not schedule:
        raise ValueError("estimate: no tax schedule set (call set_brackets first)")

    spend = _deductible_spend(k)
    explicit = int(deductions) if deductions is not None else 0
    if explicit < 0:
        raise ValueError(f"deductions must be non-negative, got {explicit}")
    deductible_total = int(spend["total"]) + explicit
    taxable = income - deductible_total
    if taxable < 0:                                 # an estimate floors at zero
        taxable = 0

    tax = _progressive_tax(taxable, schedule)
    effective_rate = (tax * BPS) // income if income > 0 else 0   # INT bps

    content = {
        "year": int(year),
        "income": int(income),
        "deductible": int(deductible_total),
        "taxable": int(taxable),
        "tax": int(tax),
        "effective_rate": int(effective_rate),     # basis points
        "provenance": list(spend["provenance"]),
    }
    # Content-addressed by the estimate's full body (so re-estimating identical inputs
    # is idempotent and an estimate keeps one identity on the Log).
    cid = content_id({"tax_estimate": content})
    model.assert_content(k.weft, k.decima_agent_id, cid, TAX_ESTIMATE, content)
    for eid in spend["provenance"]:                 # provenance EDGES on the Weft
        model.assert_edge(k.weft, k.decima_agent_id, cid, SUMMED, eid)

    return {"estimate": cid, **content}


def estimates(k) -> list:
    """All folded `tax_estimate` Cells on the Weft."""
    return list(k.weave().of_type(TAX_ESTIMATE))


# ── quarterly split (deterministic, sums back to the annual total) ──────────
def quarterly(k, year: int, *, income: int = None, deductions: int = None) -> dict:
    """Split the annual estimate for `year` into 4 INTEGER quarterly payments. Returns
    {"year", "annual": int, "quarters": [q1, q2, q3, q4], "estimate": cell_id}.

    The split is deterministic: each quarter is annual // 4, and the remainder
    (annual % 4) is handed one minor unit at a time to the EARLIEST quarters — so the
    four payments ALWAYS sum back EXACTLY to the annual total (no rounding loss). With
    `income`/`deductions` given it computes a fresh estimate; otherwise it reuses the
    most recent `tax_estimate` for `year`, raising if none exists."""
    if income is not None:
        est = estimate(k, year=year, income=income, deductions=deductions)
    else:
        prior = [c for c in estimates(k) if c.content.get("year") == year]
        if not prior:
            raise ValueError(
                f"quarterly: no estimate for year {year} (pass income, or estimate first)")
        cell = prior[-1]                            # most recent for the year
        est = {"estimate": cell.id, **cell.content}

    annual = int(est["tax"])
    base, rem = divmod(annual, 4)                   # INT: floor + remainder
    quarters = [base + (1 if i < rem else 0) for i in range(4)]   # remainder to early Qs
    assert sum(quarters) == annual, (quarters, annual)            # sums back exactly
    return {
        "year": int(year),
        "annual": annual,
        "quarters": quarters,
        "estimate": est["estimate"],
    }
