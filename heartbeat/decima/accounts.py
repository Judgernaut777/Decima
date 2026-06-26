"""LEDGER1 — accounts & a double-entry ledger by COMPOSITION (CAPABILITY_MAP B1).

The accounting backbone of the money vertical. It is NOT a new authority and moves
no money: it is a fold over `journal_entry` Cells it asserts on the Weft via the
PUBLIC `model.assert_content` / `model.assert_edge`, and over the FINANCIAL
EffectReceipts that `payments` (and `trading`) already wrote. It edits no core file
and composes the existing public APIs.

LAWS honored:
  • Double-entry — every `journal_entry` BALANCES: sum(debits) == sum(credits),
    enforced FAIL-CLOSED. `post` REFUSES (writes nothing) when an entry is unbalanced,
    so an unbalanced fact never reaches the Log.
  • ALL amounts are INTS in minor units (no floats ever enter a number we fold).
  • Provenance on the Weft — `reconcile` records an existing payment/trade FINANCIAL
    receipt as a balanced entry and CITES it with a `records` EDGE, so a ledger entry
    always traces back to the signed receipt that justifies it.

The chart of accounts is data, the same way the type model is (Law 3): an `account`
is just a Cell carrying its `kind` (asset/liability/income/expense/equity). A balance
is a pure fold over the journal lines that touch the account, signed by the account's
"normal" side — debit-normal (asset/expense) increases on a debit; credit-normal
(liability/income/equity) increases on a credit.
"""
from decima import model, payments
from decima.hashing import content_id, nfc

ACCOUNT = "account"
JOURNAL_ENTRY = "journal_entry"
RECORDS = "records"          # entry → records → receipt (provenance edge)

DEBIT = "debit"
CREDIT = "credit"

# Account kinds and their "normal" balance side. A debit-normal account grows on a
# debit and shrinks on a credit; a credit-normal account is the mirror. This single
# table is the whole sign convention of double-entry bookkeeping.
KINDS = {
    "asset": DEBIT,
    "expense": DEBIT,
    "liability": CREDIT,
    "income": CREDIT,
    "equity": CREDIT,
}


# ── chart of accounts (account Cells on the Weft) ───────────────────────────
def _account_id(name: str) -> str:
    return content_id({"account": nfc(name)})


def open_account(k, name: str, kind: str) -> str:
    """Open an `account` Cell of `kind` ∈ asset/liability/income/expense/equity and
    return its cell id. Content-addressed by NAME (idempotent: re-opening the same
    name lands on the same cell). Refuses an unknown kind — a typo can't create an
    account with no sign convention."""
    name = nfc(name)
    kind = nfc(kind)
    if kind not in KINDS:
        raise ValueError(
            f"open_account: kind must be one of {sorted(KINDS)}, got {kind!r}")
    cid = _account_id(name)
    model.assert_content(k.weft, k.decima_agent_id, cid, ACCOUNT,
                         {"name": name, "kind": kind})
    return cid


def accounts(k) -> dict:
    """Folded chart of accounts {account_cell_id: {"name", "kind"}} from the Weft."""
    return {c.id: {"name": c.content["name"], "kind": c.content["kind"]}
            for c in k.weave().of_type(ACCOUNT)}


def _kind_of(k, account: str) -> str:
    cell = k.weave().get(account)
    if cell is None or cell.type != ACCOUNT:
        raise ValueError(f"unknown account {account!r}")
    return cell.content["kind"]


# ── posting (a balanced journal entry; fail-closed on imbalance) ────────────
def _normalize_lines(lines) -> list:
    """Validate + normalize the lines of an entry. Each line is a dict (or a tuple
    (account, side, amount)) → {"account", "side", "amount"} with an INT amount. Raises
    on a malformed line so a bad entry never reaches the balance check or the Log."""
    norm = []
    for ln in lines:
        if isinstance(ln, (tuple, list)):
            account, side, amount = ln
            ln = {"account": account, "side": side, "amount": amount}
        account = ln["account"]
        side = nfc(str(ln["side"]))
        amount = ln["amount"]
        if side not in (DEBIT, CREDIT):
            raise ValueError(f"line side must be {DEBIT!r} or {CREDIT!r}, got {side!r}")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError(
                f"line amount must be a positive int (minor units), got {amount!r}")
        norm.append({"account": account, "side": side, "amount": int(amount)})
    return norm


def post(k, lines, *, memo: str, source: str | None = None) -> str:
    """Post a balanced `journal_entry` with ≥2 lines and return its cell id.

    Each line is {"account", "side": debit|credit, "amount": int} (or a tuple
    (account, side, amount)). FAIL-CLOSED double-entry: if sum(debits) != sum(credits)
    the entry is REFUSED and NOTHING is written (no cell, no event). `memo` is a human
    note; `source`, when given, is a FINANCIAL receipt cell id this entry records — it
    is carried in content AND as a `records` EDGE for provenance.
    """
    norm = _normalize_lines(lines)
    if len(norm) < 2:
        raise ValueError("a journal entry needs ≥2 lines (double-entry)")

    debits = sum(ln["amount"] for ln in norm if ln["side"] == DEBIT)
    credits = sum(ln["amount"] for ln in norm if ln["side"] == CREDIT)
    if debits != credits:                      # ← the law: REFUSE, write nothing
        raise ValueError(
            f"unbalanced journal entry refused: debits {debits} != credits {credits}")

    # Every referenced account must exist (a balanced entry against a phantom account
    # is still meaningless) — validates before any write.
    for ln in norm:
        _kind_of(k, ln["account"])

    content = {"lines": norm, "memo": nfc(str(memo)), "amount": int(debits)}
    if source is not None:
        content["source"] = source
    # Content-addressed by the entry's full body (lines+memo+source) so re-posting an
    # identical entry is idempotent and an entry keeps one identity on the Log.
    cid = content_id({"journal_entry": content})
    model.assert_content(k.weft, k.decima_agent_id, cid, JOURNAL_ENTRY, content)
    if source is not None:                      # provenance: entry → records → receipt
        model.assert_edge(k.weft, k.decima_agent_id, cid, RECORDS, source)
    return cid


def entries(k) -> list:
    """All folded `journal_entry` Cells on the Weft."""
    return list(k.weave().of_type(JOURNAL_ENTRY))


# ── balances (a signed fold over the journal lines) ─────────────────────────
def balance(k, account: str) -> int:
    """The folded INT balance of `account`, signed per its kind. Debit-normal accounts
    (asset/expense) increase on a debit and decrease on a credit; credit-normal accounts
    (liability/income/equity) are the mirror. Pure fold over every posted entry's lines
    — no float ever enters the sum."""
    normal = KINDS[_kind_of(k, account)]
    total = 0
    for e in entries(k):
        for ln in e.content["lines"]:
            if ln["account"] != account:
                continue
            amt = int(ln["amount"])
            total += amt if ln["side"] == normal else -amt
    return total


def trial_balance(k) -> dict:
    """{account_cell_id: signed_int_balance} over the whole chart of accounts. The
    accounting identity holds: because every entry balances, the sum of debit-normal
    balances equals the sum of credit-normal balances (debits == credits in aggregate)."""
    return {cid: balance(k, cid) for cid in accounts(k)}


# ── reconciliation (record a signed FINANCIAL receipt as a balanced entry) ──
def reconcile(k, receipt, *, debit_account: str, credit_account: str,
              memo: str | None = None) -> str:
    """Post a balanced `journal_entry` that records an EXISTING payment/trade FINANCIAL
    receipt, citing it for provenance. `receipt` is a receipt cell id or Cell. The
    receipt's INT `amount` is debited to `debit_account` and credited to
    `credit_account` (e.g. an expense debit vs a cash credit), so the entry balances by
    construction and `source` + a `records` edge tie it to the signed receipt.

    Refuses (writes nothing) unless `receipt` is a SUCCEEDED FINANCIAL receipt carrying
    an int amount — a ledger entry must trace to real money that actually moved."""
    cell = k.weave().get(receipt) if isinstance(receipt, str) else receipt
    if cell is None or cell.type != payments.RESULT:
        raise ValueError(f"reconcile: not a receipt cell: {receipt!r}")
    rc = cell.content
    if (rc.get("effect_class") != payments.FINANCIAL
            or rc.get("status") != payments.executor.SUCCEEDED
            or not isinstance(rc.get("amount"), int)):
        raise ValueError(
            "reconcile: receipt must be a SUCCEEDED FINANCIAL receipt with an int amount")
    amount = int(rc["amount"])
    note = memo if memo is not None else f"reconcile {cell.id[:8]} ({rc.get('payee','')})"
    return post(k, [
        {"account": debit_account, "side": DEBIT, "amount": amount},
        {"account": credit_account, "side": CREDIT, "amount": amount},
    ], memo=note, source=cell.id)
