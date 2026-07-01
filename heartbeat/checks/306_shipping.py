"""Real shipping rail — wrap a REAL postage/logistics carrier (EasyPost / Shippo style),
a financial-ish OUTWARD effect, over stdlib urllib (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — buying a postage label SPENDS MONEY and creates an irreversible shipment (a
carrier is dispatched, a tracking number is minted), and re-rolling a carrier is the
liability. A shipping provider is an HTTPS API, so the real engine rides stdlib `urllib`
(zero pip deps). This check drives the rail entirely OFFLINE via an injected fake
transport (the real `urllib` transport is never called), so the oracle stays deterministic
and network-free while proving the full contract:

  - Morta-gated: unapproved → denied, and NO carrier request is made before approval;
  - success: a purchased label (injected 201) → SUCCEEDED receipt carrying the carrier
    `provider_ref` (shipment/label id) + `tracking_code`, FINANCIAL class, the idempotency
    key sent as the carrier Idempotency-Key header, and the spend cap decremented;
  - idempotent replay: the same key returns the prior receipt and makes NO second buy;
  - bad address / insufficient funds (4xx) → FAILED (no label bought, no money moved);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - TEST-MODE invariant: a non-`shippo_test_` (live) key is refused BEFORE any request;
  - dispense-don't-disclose: the raw carrier key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

The rail is args-compatible with PAY1, so `payments.pay` drives it (amount → cost → the
running postage spend cap). After `install_rail`/`broker.issue` a FRESH agent cell is
fetched for each `payments.pay` call.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import shipping, secrets, payments

TEST_KEY = "shippo_test_DECIMA123"
LIVE_KEY = "shippo_live_DANGER"
ENDPOINT = "https://api.carrier.example/v1/shipments"


def _transport(calls, response):
    """A fake carrier transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL SHIPPING RAIL (wrapped carrier, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("carrier", TEST_KEY, service="shippo")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("carrier", decima, "buy labels")

    # 1. SUCCESS + Morta gate + provider_ref + tracking_code + idempotency + spend cap. ──
    calls = []
    cap = shipping.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ship_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (201, {"id": "shp_abc123", "status": "purchased",
                                           "tracking_code": "1Z999AA10123456784"})))
    # Morta: no approval yet → denied, and NO carrier request made.
    denied = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                          amount=2000, payee="acme-warehouse", idempotency_key="shp-1")
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no carrier request may be made before Morta approval"
    kk.approve(cap)
    ok = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                      amount=2000, payee="acme-warehouse", idempotency_key="shp-1")
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "shp_abc123" and rc["rail"] == "shipping" \
        and rc["effect_class"] == "FINANCIAL", rc
    assert rc["tracking_code"] == "1Z999AA10123456784", rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "shp-1", calls
    assert kk.spent[decima.id] == 2000, kk.spent          # amount → cost → running spend cap
    line("  success: Morta-gated (no call pre-approval) → SUCCEEDED receipt with carrier "
         "provider_ref (label id) + tracking_code, FINANCIAL class; idempotency key sent as "
         "the carrier header; spend cap decremented ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second buy. ──────────
    before = len(calls)
    again = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                         amount=2000, payee="acme-warehouse", idempotency_key="shp-1")
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert len(calls) == before, "a replay must not make a second label buy"
    assert kk.spent[decima.id] == 2000, "a replay must not spend again"
    line("  idempotent replay: same key → prior receipt, no second buy, no second spend ✓")

    # 3. BAD ADDRESS / INSUFFICIENT FUNDS (4xx) → FAILED (no money moved). ───────────────
    bcalls = []
    cap_b = shipping.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ship_bad", endpoint=ENDPOINT,
        transport=_transport(bcalls, (422, {"error": {"message": "invalid destination address"}})))
    kk.approve(cap_b)
    spent_before = kk.spent[decima.id]
    bad = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_b,
                       amount=500, payee="nowhere", idempotency_key="shp-2")
    assert bad["status"] == "FAILED", bad
    assert kk.spent[decima.id] == spent_before, "a FAILED buy must not spend"
    line("  bad address (4xx) → FAILED receipt — a definite no-effect (no label, no spend) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ───────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = shipping.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ship_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_t,
                       amount=700, payee="acme-warehouse", idempotency_key="shp-3")
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 5. TEST-MODE invariant — a live key is refused BEFORE any request. ────────────────
    broker.store("carrier_live", LIVE_KEY, service="shippo")
    handle_live = broker.issue("carrier_live", decima, "buy labels")
    lcalls = []
    cap_l = shipping.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle_live,
        name="ship_live", endpoint=ENDPOINT,
        transport=_transport(lcalls, (201, {"id": "shp_x", "status": "purchased",
                                            "tracking_code": "T"})),
        test_mode=True)
    kk.approve(cap_l)
    refused = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_l,
                           amount=100, payee="X", idempotency_key="shp-4")
    assert refused["status"] == "FAILED", refused
    assert lcalls == [], "a live key must be refused before any carrier request is made"
    line("  test-mode: a live (shippo_live_) key is refused before any request — no real "
         "postage can be bought from the reference ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ──────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert TEST_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw carrier key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw carrier key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real shipping carrier is wrapped over stdlib urllib (zero deps): Morta-"
         "gated, spend-capped, idempotent, receipts map purchased/rejected/timeout → "
         "SUCCEEDED/FAILED/UNKNOWN with provider_ref + tracking_code; test-mode-only; the "
         "key is never disclosed.")
