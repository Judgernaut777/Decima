"""Real Stripe rail — the first REAL external engine, wrapped (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — recreating money movement is the liability. Stripe is an HTTPS API, so the
real engine rides stdlib `urllib` (zero pip deps). This check drives the rail entirely
OFFLINE via an injected fake transport (the real `urllib` transport is never called), so
the oracle stays deterministic and network-free while proving the full contract:

  - Morta-gated: unapproved → denied, and NO Stripe request is made before approval;
  - success: a confirmed charge → SUCCEEDED receipt carrying the Stripe `provider_ref`,
    FINANCIAL class, and the idempotency key sent as the Stripe Idempotency-Key header;
  - idempotent replay: the same key returns the prior receipt and makes NO second call;
  - decline / 4xx → FAILED (money did not move);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - TEST-MODE invariant: a non-`sk_test_` (live) key is refused BEFORE any request;
  - dispense-don't-disclose: the raw Stripe key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import stripe_rail, secrets, payments

TEST_KEY = "sk_test_DECIMA123"
LIVE_KEY = "sk_live_DANGER"


def _transport(calls, response):
    """A fake Stripe transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL STRIPE RAIL (wrapped engine, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("stripe", TEST_KEY, service="stripe")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("stripe", decima, "charge cards")

    # 1. SUCCESS + Morta gate + provider_ref + idempotency header. ─────────────────────
    calls = []
    cap = stripe_rail.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="pay_ok", transport=_transport(calls, (200, {"id": "pi_test_1", "status": "succeeded"})))
    # Morta: no approval yet → denied, and NO Stripe request made.
    denied = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap, amount=2000, payee="Acme", idempotency_key="ord-1")
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no Stripe request may be made before Morta approval"
    kk.approve(cap)
    ok = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap, amount=2000, payee="Acme", idempotency_key="ord-1")
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "pi_test_1" and rc["rail"] == "stripe" and rc["effect_class"] == "FINANCIAL", rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "ord-1", calls
    line("  success: Morta-gated (no call pre-approval) → SUCCEEDED receipt with Stripe "
         "provider_ref; idempotency key sent as the Stripe header ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second call. ───────
    before = len(calls)
    again = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap, amount=2000, payee="Acme", idempotency_key="ord-1")
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert len(calls) == before, "a replay must not make a second Stripe charge"
    line("  idempotent replay: same key → prior receipt, no second charge ✓")

    # 3. DECLINE → FAILED (money did not move). ────────────────────────────────────────
    dcalls = []
    cap_d = stripe_rail.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="pay_decline",
        transport=_transport(dcalls, (402, {"error": {"message": "card declined", "code": "card_declined"}})))
    kk.approve(cap_d)
    dec = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_d, amount=500, payee="Bob", idempotency_key="ord-2")
    assert dec["status"] == "FAILED", dec
    line("  decline (4xx) → FAILED receipt — a definite no-effect (money did not move) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = stripe_rail.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="pay_timeout", transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_t, amount=700, payee="Carol", idempotency_key="ord-3")
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 5. TEST-MODE invariant — a live key is refused BEFORE any request. ──────────────
    broker.store("stripe_live", LIVE_KEY, service="stripe")
    handle_live = broker.issue("stripe_live", decima, "charge cards")
    lcalls = []
    cap_l = stripe_rail.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle_live,
        name="pay_live", transport=_transport(lcalls, (200, {"id": "pi_x", "status": "succeeded"})),
        test_mode=True)
    kk.approve(cap_l)
    refused = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_l, amount=100, payee="X", idempotency_key="ord-4")
    assert refused["status"] == "FAILED", refused
    assert lcalls == [], "a live key must be refused before any Stripe request is made"
    line("  test-mode: a live (sk_live_) key is refused before any request — no real "
         "money can move from the reference ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ─────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert TEST_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw Stripe key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw Stripe key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real Stripe engine is wrapped over stdlib urllib (zero deps): Morta-"
         "gated, idempotent, receipts map succeeded/declined/timeout → SUCCEEDED/FAILED/"
         "UNKNOWN with provider_ref; test-mode-only; the key is never disclosed.")
