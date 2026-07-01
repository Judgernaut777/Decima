"""Real bank account-aggregation engine — WRAP the provider, offline contract.

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — reaching into someone's real financial life (their account balances /
transactions) is the liability. `banking.py` asks a REAL Plaid/Finicity-style HTTPS
aggregation provider over stdlib `urllib` (zero deps). This check drives it entirely
OFFLINE via an injected fake transport (the real `urllib` transport is never called), so
the oracle stays deterministic and network-free while proving the full contract:

  - success: an injected 200 accounts response → a `bank_snapshot` cell carrying the
    provider's accounts and provider_ref; every balance on the cell is an int (cents),
    no floats anywhere in the signed content; the cell is sensitive DATA
    (instruction_eligible=False);
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (the fake
    transport is never called) — the access token never rides a cleartext wire;
  - fail closed: a provider 4xx / error → {"denied": ...} and NO `bank_snapshot` cell;
  - dispense-don't-disclose: the raw access token / API key never appears in any event
    payload on the Weft — CRED1 applies it inside the broker;
  - discovery: register_manifest → the "banking" manifest is discoverable via
    manifest.find / registry.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import banking, secrets, manifest

API_KEY = "access-production-PLAID_SUPER_SECRET_ACCESS_TOKEN"
ENDPOINT = "https://production.plaid.com/accounts/balance/get"


def _transport(calls, response):
    """A fake aggregation-provider transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL BANKING ENGINE (wrapped aggregator, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("plaid", API_KEY, service="plaid")
    handle = broker.issue("plaid", _decima(kk), "aggregate bank balances")

    request = {"access_token": "access-item-abc", "item_id": "item_123"}

    # 1. SUCCESS — provider returns balances; we record them (int cents) on the Weft. ────
    calls = []
    ok_resp = (200, {"accounts": [
        {"account_ref": "acc_checking", "name": "Everyday Checking", "type": "depository",
         "balance_cents": 128_355, "currency": "usd"},
        {"account_ref": "acc_savings", "name": "High-Yield Savings", "type": "depository",
         "balance_cents": 1_050_000, "currency": "usd"},
    ], "provider_ref": "req_xyz789"})
    res = banking.snapshot(kk, endpoint=ENDPOINT, request=request, credential_handle=handle,
                           broker=broker, agent_cell=_decima(kk), transport=_transport(calls, ok_resp))
    assert "bank_snapshot" in res, res
    assert res["total_cents"] == 128_355 + 1_050_000, res
    assert res["provider_ref"] == "req_xyz789", res
    assert len(calls) == 1 and calls[0]["url"] == ENDPOINT, calls

    cell = kk.weave().get(res["bank_snapshot"]).content
    assert cell["provider_ref"] == "req_xyz789", cell
    assert cell["instruction_eligible"] is False, "account data must never be an instruction"
    assert cell["sensitive"] is True, cell
    # ints only in signed content — total and every per-account balance.
    assert isinstance(cell["total_cents"], int) and not isinstance(cell["total_cents"], bool), cell
    assert len(cell["accounts"]) == 2, cell
    for a in cell["accounts"]:
        assert isinstance(a["balance_cents"], int) and not isinstance(a["balance_cents"], bool), a
    assert cell["accounts"][0]["balance_cents"] == 128_355, cell
    line("  success: injected 200 → bank_snapshot cell with the provider's accounts / "
         "provider_ref; balances are ints (cents); sensitive DATA, instruction_eligible=False ✓")

    # 2. HTTPS-only — a non-HTTPS endpoint is refused before any request. ────────────────
    http_calls = []
    bad = banking.snapshot(kk, endpoint="http://production.plaid.com/accounts/balance/get",
                           request=request, credential_handle=handle, broker=broker,
                           agent_cell=_decima(kk), transport=_transport(http_calls, ok_resp))
    assert "denied" in bad and "HTTPS" in bad["denied"], bad
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: a non-HTTPS endpoint is refused before the access token is sent "
         "(transport never called) ✓")

    # 3. FAIL CLOSED — a provider 4xx / error → denied, NO bank_snapshot recorded. ───────
    before = len(banking.snapshots(kk))
    err_calls = []
    declined = banking.snapshot(kk, endpoint=ENDPOINT, request=request, credential_handle=handle,
                                broker=broker, agent_cell=_decima(kk),
                                transport=_transport(err_calls, (400, {"error": "ITEM_LOGIN_REQUIRED"})))
    assert "denied" in declined and "banking" in declined["denied"], declined
    assert len(err_calls) == 1, "the request was made, but the 4xx must fail closed"
    assert len(banking.snapshots(kk)) == before, "no bank_snapshot cell on a provider error"
    line("  fail closed: provider 4xx → {denied} and NO bank_snapshot cell recorded ✓")

    # 4. DISPENSE-DON'T-DISCLOSE — the raw access token / API key never on the Weft. ─────
    payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in payloads, "the raw bank access token must never be written to the Weft"
    line("  no raw access token on the Weft — CRED1 applies it inside the broker ✓")

    # 5. DISCOVERY — register the manifest; it is findable in the registry. ──────────────
    mid = banking.register_manifest(kk)
    assert mid, "register_manifest must return a manifest cell id"
    hits = manifest.find(kk, query="bank")
    assert any(c.content["name"] == "banking" for c in hits), "banking manifest must be discoverable"
    m = manifest.get(kk, "banking").content
    assert m["archetype"] == "COMPUTE" and m["effect_class"] == "FINANCIAL_DATA", m
    assert "plaid" in m["tags"], m
    assert any(c.content["name"] == "banking" for c in manifest.registry(kk)), "must be in registry"
    line("  discovery: register_manifest → 'banking' (COMPUTE / FINANCIAL_DATA) findable via "
         "manifest.find / registry ✓")

    line("  → banking is wrapped, not reinvented: a real aggregator (over stdlib urllib, zero "
         "deps) returns real balances; Decima records ints on the Weft as sensitive DATA, holds "
         "the token in CRED1, refuses cleartext, and fails closed.")
