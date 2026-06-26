"""LEDGER1 — accounts & a double-entry ledger by composition (CAPABILITY_MAP B1).

Proves `decima.accounts` is the accounting backbone of the money vertical: it opens
typed accounts, posts a BALANCED journal entry, REFUSES an unbalanced one (writing
nothing), folds signed integer balances, and reconciles a real payment FINANCIAL
receipt into a balanced entry that cites it for provenance. Fail-closed double-entry,
ints throughout, no new authority — pure composition over payments + the Weft.

Runs on its OWN fresh Kernel (it forges a FINANCIAL capability and moves "money", so
it stays out of the shared kernel's state). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import accounts, payments
from decima.kernel import Kernel


def run(_k, line):
    line("\n== LEDGER1 (double-entry ledger · balanced post · imbalance refused · reconcile) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- (1) open typed accounts (chart of accounts is data) -----------------
    cash = accounts.open_account(k, "cash", "asset")
    groceries = accounts.open_account(k, "groceries", "expense")
    payable = accounts.open_account(k, "accounts-payable", "liability")
    assert accounts.accounts(k)[cash]["kind"] == "asset"
    try:
        accounts.open_account(k, "bogus", "wormhole")        # unknown kind refused
        raise AssertionError("open_account accepted an unknown kind")
    except ValueError:
        pass
    line("  opened: cash(asset) groceries(expense) accounts-payable(liability); bad kind refused ✓")

    # ---- (2) post a BALANCED entry (debits == credits) -----------------------
    # buy groceries on credit: debit the expense, credit the payable (1500 == 1500).
    e1 = accounts.post(k, [
        {"account": groceries, "side": "debit", "amount": 1_500},
        {"account": payable, "side": "credit", "amount": 1_500},
    ], memo="groceries on account")
    assert k.weave().get(e1).type == accounts.JOURNAL_ENTRY
    assert k.weave().get(e1).content["amount"] == 1_500
    line("  posted balanced entry: debit groceries 1500 / credit payable 1500 (debits==credits) ✓")

    # ---- (3) an UNBALANCED entry is REFUSED — nothing written ----------------
    before = len(accounts.entries(k))
    try:
        accounts.post(k, [
            {"account": cash, "side": "debit", "amount": 1_000},
            {"account": groceries, "side": "credit", "amount": 999},   # 1000 != 999
        ], memo="should never persist")
        raise AssertionError("post accepted an UNBALANCED entry")
    except ValueError:
        pass
    after = len(accounts.entries(k))
    assert after == before, "REFUSED entry must write NOTHING (fail closed)"
    line(f"  unbalanced post (1000 debit vs 999 credit) REFUSED; entries unchanged "
         f"({before}→{after}) ✓")

    # ---- (4) balances fold correctly, signed per kind (INTS) -----------------
    # pay the payable in cash: debit payable (liability ↓), credit cash (asset ↓).
    accounts.post(k, [
        {"account": payable, "side": "debit", "amount": 1_500},
        {"account": cash, "side": "credit", "amount": 1_500},
    ], memo="pay down payable")
    bg = accounts.balance(k, groceries)      # expense, debit-normal: +1500
    bp = accounts.balance(k, payable)        # liability: +1500 then -1500 = 0
    bc = accounts.balance(k, cash)           # asset, debit-normal: -1500 (credited)
    assert (bg, bp, bc) == (1_500, 0, -1_500), (bg, bp, bc)
    assert all(isinstance(x, int) for x in (bg, bp, bc)), "balances are int minor units"
    # accounting identity: across the whole chart, debit-normal == credit-normal totals.
    tb = accounts.trial_balance(k)
    debit_side = sum(v for cid, v in tb.items()
                     if accounts.KINDS[accounts.accounts(k)[cid]["kind"]] == accounts.DEBIT)
    credit_side = sum(v for cid, v in tb.items()
                      if accounts.KINDS[accounts.accounts(k)[cid]["kind"]] == accounts.CREDIT)
    assert debit_side == credit_side, (tb, debit_side, credit_side)
    line(f"  balances (signed, int): groceries={bg} payable={bp} cash={bc}; "
         f"trial balance ties (debit {debit_side} == credit {credit_side}) ✓")

    # ---- (5) reconcile a REAL payment receipt into a balanced cited entry ----
    rail = payments.install_rail(k, cap=100_000)
    k.approve(rail)                                          # Morta gate
    pay = payments.pay(k, decima(), rail, amount=2_750, payee="utilities",
                       idempotency_key="util-1")
    assert pay["status"] == payments.executor.SUCCEEDED, pay
    receipt = pay["result_cell"]

    utilities = accounts.open_account(k, "utilities", "expense")
    e2 = accounts.reconcile(k, receipt, debit_account=utilities, credit_account=cash)
    entry = k.weave().get(e2)
    assert entry.content["source"] == receipt, entry.content
    assert entry.content["amount"] == 2_750                  # from the signed receipt
    assert accounts.balance(k, utilities) == 2_750
    # provenance EDGE: the entry RECORDS the receipt, and it resolves to a signed cell.
    edges = k.weave().edges_from(e2, accounts.RECORDS)
    assert any(ed["dst"] == receipt for ed in edges), edges
    rc = k.weave().get(receipt)
    assert rc.content["effect_class"] == payments.FINANCIAL
    assert rc.content["status"] == payments.executor.SUCCEEDED
    line(f"  reconciled receipt {receipt[:8]} → balanced entry (debit utilities 2750 / "
         f"credit cash); 'records' edge cites the signed receipt ✓")

    line("  → a fail-closed double-entry ledger: balanced posts only, signed int "
         "balances that tie, every reconciled entry traced to a signed receipt.")
