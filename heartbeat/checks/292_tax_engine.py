"""Real sales-tax engine — WRAP the provider, offline contract (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — recreating tax logic (rates, nexus, product taxability) is the liability.
TAX1 stays an advisory estimator; `tax_engine.py` asks a REAL TaxJar/Avalara-style HTTPS
provider to compute the tax on an actual transaction, over stdlib `urllib` (zero deps).
This check drives it entirely OFFLINE via an injected fake transport (the real `urllib`
transport is never called), so the oracle stays deterministic and network-free while
proving the full contract:

  - success: an injected 200 tax response → a `tax_quote` cell carrying the provider's
    tax_amount / rate_bps / provider_ref; every amount/rate on the cell is an int;
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (the fake
    transport is never called) — the API key never rides a cleartext wire;
  - fail closed: a provider 4xx / error → {"denied": ...} and NO `tax_quote` cell;
  - dispense-don't-disclose: the raw API key never appears in any event payload on the
    Weft — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import tax_engine, secrets

API_KEY = "tk_live_TAXJAR_SUPER_SECRET_KEY"
ENDPOINT = "https://api.taxjar.com/v2/taxes"


def _transport(calls, response):
    """A fake tax-provider transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL TAX ENGINE (wrapped provider, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("taxjar", API_KEY, service="taxjar")
    handle = broker.issue("taxjar", _decima(kk), "compute sales tax")

    request = {
        "amount": 10_000, "currency": "usd",                 # $100.00 in minor units
        "from": {"country": "US", "state": "CA", "zip": "94103"},
        "to": {"country": "US", "state": "CA", "zip": "90001"},
        "line_items": [{"id": "1", "quantity": 1, "unit_price": 10_000}],
    }

    # 1. SUCCESS — provider computes the tax; we record it (ints) on the Weft. ──────────
    calls = []
    ok_resp = (200, {"tax_amount": 725, "rate_bps": 725, "jurisdiction": "CA",
                     "provider_ref": "txj_abc123",
                     "breakdown": [{"jurisdiction": "CA", "rate_bps": 725, "tax_amount": 725}]})
    res = tax_engine.quote(kk, endpoint=ENDPOINT, request=request, credential_handle=handle,
                           broker=broker, agent_cell=_decima(kk), transport=_transport(calls, ok_resp))
    assert "tax_quote" in res and res["tax_amount"] == 725 and res["rate_bps"] == 725, res
    assert res["provider_ref"] == "txj_abc123", res
    assert len(calls) == 1 and calls[0]["url"] == ENDPOINT, calls
    cell = kk.weave().get(res["tax_quote"]).content
    assert cell["amount"] == 10_000 and cell["tax_amount"] == 725 and cell["rate_bps"] == 725, cell
    assert cell["provider_ref"] == "txj_abc123" and cell["jurisdiction"] == "CA", cell
    for fld in ("amount", "tax_amount", "rate_bps"):         # ints only in signed content
        assert isinstance(cell[fld], int) and not isinstance(cell[fld], bool), (fld, cell[fld])
    line("  success: injected 200 → tax_quote cell with the provider's tax_amount / "
         "rate_bps / provider_ref; amounts are ints (minor units / bps) ✓")

    # 2. HTTPS-only — a non-HTTPS endpoint is refused before any request. ──────────────
    http_calls = []
    bad = tax_engine.quote(kk, endpoint="http://api.taxjar.com/v2/taxes", request=request,
                           credential_handle=handle, broker=broker, agent_cell=_decima(kk),
                           transport=_transport(http_calls, ok_resp))
    assert "denied" in bad and "HTTPS" in bad["denied"], bad
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: a non-HTTPS endpoint is refused before the key is sent "
         "(transport never called) ✓")

    # 3. FAIL CLOSED — a provider 4xx / error → denied, NO tax_quote recorded. ─────────
    quotes_before = len(tax_engine.quotes(kk))
    err_calls = []
    declined = tax_engine.quote(kk, endpoint=ENDPOINT, request=request, credential_handle=handle,
                                broker=broker, agent_cell=_decima(kk),
                                transport=_transport(err_calls, (400, {"error": "invalid to_zip"})))
    assert "denied" in declined and "tax_engine" in declined["denied"], declined
    assert len(err_calls) == 1, "the request was made, but the 4xx must fail closed"
    assert len(tax_engine.quotes(kk)) == quotes_before, "no tax_quote cell on a provider error"
    line("  fail closed: provider 4xx → {denied} and NO tax_quote cell recorded ✓")

    # 4. DISPENSE-DON'T-DISCLOSE — the raw API key never on the Weft. ──────────────────
    payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in payloads, "the raw tax API key must never be written to the Weft"
    line("  no raw API key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → tax is wrapped, not reinvented: a real provider (over stdlib urllib, zero "
         "deps) computes the liability-bearing tax; Decima records ints on the Weft, holds "
         "the key in CRED1, refuses cleartext, and fails closed.")
