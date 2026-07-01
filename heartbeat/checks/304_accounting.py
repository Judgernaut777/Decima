"""Real bookkeeping engine — WRAP the books, offline contract (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — recreating a general ledger is the liability (the books are the legal system
of record). LEDGER1 stays Decima's OWN internal double-entry record; `accounting.py`
POSTS a journal entry / invoice to a REAL QuickBooks/Xero-style HTTPS provider, over
stdlib `urllib` (zero deps). This check drives it entirely OFFLINE via an injected fake
transport (the real `urllib` transport is never called), so the oracle stays deterministic
and network-free while proving the full contract:

  - success: a BALANCED entry + an injected 200 → an `accounting_entry` cell carrying the
    provider_ref / posted_amount / balanced lines; every amount on the cell is an int;
  - balance invariant: an UNBALANCED entry (debits != credits) is refused BEFORE any
    request (the fake transport is never called) and records NO cell;
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (the fake
    transport is never called) — the API key never rides a cleartext wire;
  - fail closed: a provider 4xx / error → {"denied": ...} and NO `accounting_entry` cell;
  - dispense-don't-disclose: the raw API key never appears in any event payload on the
    Weft — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import accounting, secrets

API_KEY = "qb_live_QUICKBOOKS_SUPER_SECRET_KEY"
ENDPOINT = "https://quickbooks.api.intuit.com/v3/journalentries"


def _transport(calls, response):
    """A fake bookkeeping-provider transport: records each call and returns `response`
    (a (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL ACCOUNTING ENGINE (wrapped books, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("quickbooks", API_KEY, service="quickbooks")
    handle = broker.issue("quickbooks", _decima(kk), "post journal entries")

    # A balanced journal entry: $250.00 invoice — debit A/R, credit Sales + Sales Tax. ──
    entry = {
        "lines": [
            {"account": "1100 Accounts Receivable", "debit": 25_000},
            {"account": "4000 Sales", "credit": 22_500},
            {"account": "2200 Sales Tax Payable", "credit": 2_500},
        ],
        "memo": "Invoice INV-1042",
        "reference": "INV-1042",
    }

    # 1. SUCCESS — provider records the entry; we record it (ints) on the Weft. ──────────
    calls = []
    ok_resp = (200, {"provider_ref": "qb_je_7788", "status": "posted"})
    res = accounting.post(kk, endpoint=ENDPOINT, entry=entry, credential_handle=handle,
                          broker=broker, agent_cell=_decima(kk), transport=_transport(calls, ok_resp))
    assert "accounting_entry" in res and res["provider_ref"] == "qb_je_7788", res
    assert res["posted_amount"] == 25_000, res
    assert len(calls) == 1 and calls[0]["url"] == ENDPOINT, calls
    cell = kk.weave().get(res["accounting_entry"]).content
    assert cell["provider_ref"] == "qb_je_7788" and cell["posted_amount"] == 25_000, cell
    assert cell["memo"] == "Invoice INV-1042" and cell["reference"] == "INV-1042", cell
    # the balanced lines survive onto the cell, as ints, and still balance
    d = sum(l["debit"] for l in cell["lines"] if "debit" in l)
    c = sum(l["credit"] for l in cell["lines"] if "credit" in l)
    assert d == c == 25_000, (d, c)
    assert isinstance(cell["posted_amount"], int) and not isinstance(cell["posted_amount"], bool), cell
    for l in cell["lines"]:                                   # ints only in signed content
        amt = l.get("debit", l.get("credit"))
        assert isinstance(amt, int) and not isinstance(amt, bool), l
    line("  success: balanced entry + injected 200 → accounting_entry cell with "
         "provider_ref / posted_amount / balanced lines; amounts are ints (minor units) ✓")

    # 2. BALANCE INVARIANT — an unbalanced entry is refused before any request. ─────────
    entries_before = len(accounting.entries(kk))
    bad_calls = []
    unbalanced = {
        "lines": [
            {"account": "1100 Accounts Receivable", "debit": 25_000},
            {"account": "4000 Sales", "credit": 20_000},     # 5_000 short — does not balance
        ],
        "memo": "bad", "reference": "BAD-1",
    }
    ub = accounting.post(kk, endpoint=ENDPOINT, entry=unbalanced, credential_handle=handle,
                         broker=broker, agent_cell=_decima(kk), transport=_transport(bad_calls, ok_resp))
    assert "denied" in ub and "balance" in ub["denied"], ub
    assert bad_calls == [], "an unbalanced entry must be refused before any request"
    assert len(accounting.entries(kk)) == entries_before, "no cell on an unbalanced entry"
    line("  balance invariant: an unbalanced entry (debits != credits) is refused before "
         "any request (transport never called) and records no cell ✓")

    # 3. HTTPS-only — a non-HTTPS endpoint is refused before any request. ───────────────
    http_calls = []
    nohttps = accounting.post(kk, endpoint="http://quickbooks.api.intuit.com/v3/journalentries",
                              entry=entry, credential_handle=handle, broker=broker,
                              agent_cell=_decima(kk), transport=_transport(http_calls, ok_resp))
    assert "denied" in nohttps and "HTTPS" in nohttps["denied"], nohttps
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: a non-HTTPS endpoint is refused before the key is sent "
         "(transport never called) ✓")

    # 4. FAIL CLOSED — a provider 4xx / error → denied, NO accounting_entry recorded. ───
    entries_before = len(accounting.entries(kk))
    err_calls = []
    declined = accounting.post(kk, endpoint=ENDPOINT, entry=entry, credential_handle=handle,
                               broker=broker, agent_cell=_decima(kk),
                               transport=_transport(err_calls, (400, {"error": "invalid account 4000"})))
    assert "denied" in declined and "accounting" in declined["denied"], declined
    assert len(err_calls) == 1, "the request was made, but the 4xx must fail closed"
    assert len(accounting.entries(kk)) == entries_before, "no accounting_entry on a provider error"
    line("  fail closed: provider 4xx → {denied} and NO accounting_entry cell recorded ✓")

    # 5. DISPENSE-DON'T-DISCLOSE — the raw API key never on the Weft. ───────────────────
    payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in payloads, "the raw accounting API key must never be written to the Weft"
    line("  no raw API key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the books are wrapped, not reinvented: a real provider (over stdlib urllib, "
         "zero deps) records the liability-bearing journal entry; Decima proves the "
         "double-entry invariant, records ints on the Weft, holds the key in CRED1, "
         "refuses cleartext, and fails closed.")
