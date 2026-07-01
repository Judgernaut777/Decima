"""Real shipping/logistics rail — wrap a REAL postage carrier (EasyPost / Shippo style),
offline contract (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — buying a postage label SPENDS MONEY and dispatches a carrier (an irreversible
outward effect), and re-rolling a carrier is the liability. A shipping provider is an
HTTPS API, so the real engine rides stdlib `urllib` (zero pip deps). This check drives the
engine entirely OFFLINE via an injected fake transport (the real `urllib` transport is
never called), so the oracle stays deterministic and network-free while proving the full
contract:

  - rate quote (READ): an injected 200 rates response → int-cent rates, no money moves,
    NOT Morta-gated; a non-`https://` endpoint is refused BEFORE any request (fail closed);
  - buy is Morta-gated: unapproved → denied, and NO carrier request is made before approval;
  - success: a purchased label → SUCCEEDED receipt carrying the carrier `provider_ref`
    (shipment/label id), the tracking_code, and the idempotency key sent as the header;
  - idempotent: a replay of the same key returns the prior receipt (same provider_ref) and
    makes NO second buy — no double charge;
  - bad address / 4xx → FAILED (no label bought, no money moved);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - HTTPS-only invariant on the buy path: a non-`https://` endpoint is refused BEFORE any
    request (the key never rides a cleartext wire);
  - TEST-MODE only: a live (non-test) key is refused BEFORE any request — the reference can
    never buy real postage;
  - dispense-don't-disclose: the raw carrier key never appears on the Weft — CRED1 applies
    it inside the broker;
  - discovery: register_manifest → the "shipping" manifest is discoverable via
    manifest.find / registry.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import shipping, secrets, manifest

CARRIER_KEY = "shippo_test_DECIMA_secret_abc"       # a TEST-mode carrier key that must never leak
LIVE_KEY = "shippo_live_DANGER_real_money_key"      # a live key — the reference must refuse it


def _transport(calls, response):
    """A fake carrier transport: records each call and returns `response` (a (status, json)
    tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _agent(kk):
    """A FRESH decima agent cell — refetched before each invoke (its spend/lease state
    advances on the Weave, so a stale cell must never drive a later invoke)."""
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL SHIPPING RAIL (wrapped carrier, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("shippo", CARRIER_KEY, service="shippo")
    handle = broker.issue("shippo", _agent(kk), "quote rates and buy postage labels")
    RATES_HTTPS = "https://api.goshippo.com/rates"
    BUY_HTTPS = "https://api.goshippo.com/transactions"

    # 1. RATE QUOTE (READ) — int-cent rates, no money moves, not Morta-gated. ────────────
    rcalls = []
    rates_resp = (200, {"shipment_id": "shp_abc", "rates": [
        {"carrier": "USPS", "service": "Priority", "amount_cents": 785, "rate_id": "rate_1"},
        {"carrier": "UPS", "service": "Ground", "amount_cents": 1_099, "rate_id": "rate_2"},
    ]})
    quoted = shipping.quote(kk, endpoint=RATES_HTTPS,
                            request={"to_address": "addr_to", "from_address": "addr_from",
                                     "weight": 500},
                            credential_handle=handle, broker=broker, agent_cell=_agent(kk),
                            transport=_transport(rcalls, rates_resp))
    assert "denied" not in quoted, quoted
    assert quoted["provider_ref"] == "shp_abc", quoted
    assert len(quoted["rates"]) == 2, quoted
    for r in quoted["rates"]:                                  # every price is an int (cents)
        assert isinstance(r["amount_cents"], int) and not isinstance(r["amount_cents"], bool), r
    assert quoted["rates"][0]["amount_cents"] == 785, quoted
    assert quoted["rates"][0]["provider_ref"] == "rate_1", quoted
    assert len(rcalls) == 1 and rcalls[0]["url"] == RATES_HTTPS, rcalls
    line("  rate quote (READ): injected 200 → int-cent rates with carrier/service/provider_ref; "
         "no money moves ✓")

    # 1b. RATE QUOTE HTTPS-only + fail closed — a non-HTTPS endpoint is refused, no request. ─
    hrcalls = []
    bad_quote = shipping.quote(kk, endpoint="http://api.goshippo.com/rates",
                               request={"to_address": "addr_to", "weight": 500},
                               credential_handle=handle, broker=broker, agent_cell=_agent(kk),
                               transport=_transport(hrcalls, rates_resp))
    assert "denied" in bad_quote and "HTTPS" in bad_quote["denied"], bad_quote
    assert hrcalls == [], "a non-HTTPS rate endpoint must be refused before any request"
    line("  rate quote HTTPS-only: a non-https endpoint is refused before the key is sent "
         "(transport never called) ✓")

    # 2. BUY — Morta-gated → SUCCEEDED with provider_ref + tracking_code; idempotency header. ─
    calls = []
    cap = shipping.install_rail(
        kk, cap=10_000, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="ship_ok", endpoint=BUY_HTTPS,
        transport=_transport(calls, (201, {"status": "purchased", "id": "shpmt_001",
                                            "tracking_code": "9400100000000000000000"})))
    args = {"amount": 785, "payee": "addr_to", "cost": 785, "weight": 500,
            "carrier": "USPS", "service": "Priority", "idempotency_key": "ship-1"}
    # Morta: no approval yet → denied, and NO carrier request made.
    denied = kk.invoke(_agent(kk), cap, args)
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no carrier request may be made before Morta approval"
    kk.approve(cap)
    ok = kk.invoke(_agent(kk), cap, args)
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "shpmt_001" and rc["rail"] == "shipping", rc
    assert rc["effect_class"] == "FINANCIAL", rc
    assert rc["tracking_code"] == "9400100000000000000000", rc
    assert isinstance(rc["amount"], int) and not isinstance(rc["amount"], bool), rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "ship-1", calls
    assert calls[0]["url"] == BUY_HTTPS, calls
    line("  buy: Morta-gated (no call pre-approval) → SUCCEEDED receipt with carrier "
         "provider_ref + tracking_code; idempotency key sent as the header ✓")

    # 3. IDEMPOTENT REPLAY — same key returns the prior label, NO second buy (no double charge). ─
    before = len(calls)
    again = kk.invoke(_agent(kk), cap, args)
    assert again["status"] == "SUCCEEDED", again
    rc2 = kk.weave().get(again["result_cell"]).content
    assert rc2.get("idempotent_replay") is True, rc2
    assert rc2["provider_ref"] == "shpmt_001", rc2
    assert len(calls) == before, "a replay must not make a second carrier buy (no double charge)"
    line("  idempotent replay: same key → prior shipment id, no second buy — no double charge ✓")

    # 4. BAD ADDRESS / 4xx → FAILED (no label bought). ──────────────────────────────────
    fcalls = []
    cap_f = shipping.install_rail(
        kk, cap=10_000, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="ship_bad", endpoint=BUY_HTTPS,
        transport=_transport(fcalls, (422, {"error": {"message": "invalid to_address"}})))
    kk.approve(cap_f)
    bad = kk.invoke(_agent(kk), cap_f, {"amount": 785, "payee": "addr_bad", "cost": 785,
                                        "weight": 500, "idempotency_key": "ship-2"})
    assert bad["status"] == "FAILED", bad
    assert len(fcalls) == 1, "the 4xx buy was attempted once and definitively failed"
    line("  bad address (4xx) → FAILED receipt — a definite no-effect (no label bought) ✓")

    # 5. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ───────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    cap_t = shipping.install_rail(
        kk, cap=10_000, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="ship_timeout", endpoint=BUY_HTTPS, transport=_transport([], boom))
    kk.approve(cap_t)
    unk = kk.invoke(_agent(kk), cap_t, {"amount": 785, "payee": "addr_to", "cost": 785,
                                        "weight": 500, "idempotency_key": "ship-3"})
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 6. HTTPS-ONLY invariant on the buy path — a non-HTTPS endpoint is refused, no request. ─
    hcalls = []
    cap_h = shipping.install_rail(
        kk, cap=10_000, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="ship_http", endpoint="http://api.goshippo.com/transactions",
        transport=_transport(hcalls, (201, {"status": "purchased", "id": "x"})))
    kk.approve(cap_h)
    refused = kk.invoke(_agent(kk), cap_h, {"amount": 785, "payee": "addr_to", "cost": 785,
                                            "weight": 500, "idempotency_key": "ship-4"})
    assert refused["status"] == "FAILED", refused
    assert hcalls == [], "a non-HTTPS endpoint must be refused before any carrier request/key on the wire"
    line("  HTTPS-only: a non-https endpoint is refused before any request — the key never "
         "travels in cleartext ✓")

    # 7. TEST-MODE guard — a live key is refused BEFORE any request (never live money). ──
    broker.store("shippo_live", LIVE_KEY, service="shippo")
    live_handle = broker.issue("shippo_live", _agent(kk), "live carrier key (must be refused)")
    lcalls = []
    cap_l = shipping.install_rail(
        kk, cap=10_000, broker=broker, agent_cell=_agent(kk), credential_handle=live_handle,
        name="ship_live", endpoint=BUY_HTTPS,
        transport=_transport(lcalls, (201, {"status": "purchased", "id": "x"})))
    kk.approve(cap_l)
    live_refused = kk.invoke(_agent(kk), cap_l, {"amount": 785, "payee": "addr_to", "cost": 785,
                                                 "weight": 500, "idempotency_key": "ship-5"})
    assert live_refused["status"] == "FAILED", live_refused
    assert lcalls == [], "a live (non-test) key must be refused before any request (TEST-MODE only)"
    line("  TEST-MODE only: a live key is refused before any request — the reference can never "
         "buy real postage ✓")

    # 8. DISPENSE-DON'T-DISCLOSE — the raw carrier key never appears anywhere on the Weft. ─
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert CARRIER_KEY not in all_payloads, \
        "a raw carrier key must never be written to the Weft (dispense-don't-disclose)"
    assert LIVE_KEY not in all_payloads, "a raw live carrier key must never be written to the Weft"
    line("  no raw carrier key on the Weft — CRED1 applies it inside the broker ✓")

    # 9. DISCOVERABLE MANIFEST — registered and findable in the registry. ────────────────
    mid = shipping.register_manifest(kk)
    assert mid, "register_manifest must return a manifest cell id"
    m = manifest.get(kk, "shipping")
    assert m is not None and m.content["effect_class"] == "FINANCIAL", m
    assert m.content["archetype"] == "EFFECT", m.content
    assert m.content["caveats"].get("requires_approval") is True, m.content
    found = manifest.find(kk, query="postage")
    assert any(c.content["name"] == "shipping" for c in found), "shipping manifest must be discoverable"
    assert any(c.content["name"] == "shipping" for c in manifest.registry(kk)), "must be in registry"
    line("  manifest 'shipping' (EFFECT, effect_class FINANCIAL, requires_approval) discoverable "
         "in the registry ✓")

    line("  → the real shipping carrier is wrapped over stdlib urllib (zero deps): rate quotes "
         "are int-cent READs; buying a label is Morta-gated + idempotent (no double charge); "
         "receipts map purchased/rejected/timeout → SUCCEEDED/FAILED/UNKNOWN with provider_ref; "
         "HTTPS-only; TEST-MODE only; the key is never disclosed.")
