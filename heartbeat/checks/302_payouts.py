"""Real payout / ACH rail — money OUT, the most irreversible engine, wrapped.

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — recreating money movement is the liability, and a payout (money OUT to a bank
account) is the OUTBOUND twin of the Stripe charge, even harder to claw back. A payout
provider is an HTTPS API, so the real engine rides stdlib `urllib` (zero pip deps). This
check drives the rail entirely OFFLINE via an injected fake transport (the real `urllib`
transport is never called), so the oracle stays deterministic and network-free while
proving the full contract:

  - Morta-gated: unapproved → denied, and NO payout request is made before approval;
  - success: a created/paid payout → SUCCEEDED receipt carrying the provider `provider_ref`
    (the payout id), FINANCIAL class, the destination, and the idempotency key sent as the
    provider Idempotency-Key header; the spend cap is decremented via cost=amount;
  - idempotent replay: the same key returns the prior receipt and makes NO second payout;
  - insufficient balance / 4xx → FAILED (money did not leave the box);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - TEST-MODE invariant: a non-`sk_test_` (live) key is refused BEFORE any request;
  - dispense-don't-disclose: the raw provider key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud. Owns a FRESH Kernel + SecretsBroker.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import payouts, secrets, payments

TEST_KEY = "sk_test_PAYOUT123"
LIVE_KEY = "sk_live_DANGER_OUT"


def _transport(calls, response):
    """A fake payout transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _agent(kk):
    """A FRESH agent cell for each `payments.pay` — a captured cell goes stale after a
    grant re-asserts the envelope ('no grant in envelope')."""
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL PAYOUT / ACH RAIL (money OUT, wrapped engine, offline contract) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("payout", TEST_KEY, service="payouts")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("payout", decima, "send payouts")

    # 1. SUCCESS + Morta gate + provider_ref + idempotency header + spend cap. ──────────
    calls = []
    cap = payouts.install_rail(
        kk, cap=5000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="payout_ok", endpoint=payouts.PAYOUT_URL,
        transport=_transport(calls, (200, {"id": "po_test_1", "status": "paid"})))
    # Morta: no approval yet → denied, and NO payout request made.
    denied = payments.pay(kk, _agent(kk), cap, amount=2000, payee="acct_bank_1", idempotency_key="po-1")
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no payout request may be made before Morta approval"
    kk.approve(cap)
    ok = payments.pay(kk, _agent(kk), cap, amount=2000, payee="acct_bank_1", idempotency_key="po-1")
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "po_test_1" and rc["rail"] == "payout", rc
    assert rc["effect_class"] == "FINANCIAL" and rc["destination"] == "acct_bank_1", rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "po-1", calls
    assert kk.spent.get(kk.decima_agent_id, 0) == 2000, kk.spent   # cost=amount decremented the cap
    line("  success: Morta-gated (no request pre-approval) → SUCCEEDED receipt with "
         "provider_ref + destination; idempotency key sent as the provider header; spend "
         "cap decremented via cost=amount ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second payout. ──────
    before = len(calls)
    again = payments.pay(kk, _agent(kk), cap, amount=2000, payee="acct_bank_1", idempotency_key="po-1")
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert len(calls) == before, "a replay must not send a second payout"
    line("  idempotent replay: same key → prior receipt, no second payout ✓")

    # 3. INSUFFICIENT BALANCE (4xx) → FAILED (money did not move). ──────────────────────
    fcalls = []
    cap_f = payouts.install_rail(
        kk, cap=5000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="payout_insufficient",
        transport=_transport(fcalls, (402, {"error": {"message": "insufficient balance",
                                                       "code": "balance_insufficient"}})))
    kk.approve(cap_f)
    fail = payments.pay(kk, _agent(kk), cap_f, amount=500, payee="acct_bank_2", idempotency_key="po-2")
    assert fail["status"] == "FAILED", fail
    assert len(fcalls) == 1, "the request was made and definitively rejected"
    line("  insufficient balance (4xx) → FAILED receipt — a definite no-effect "
         "(money did not leave the box) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ──────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = payouts.install_rail(
        kk, cap=5000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="payout_timeout", transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = payments.pay(kk, _agent(kk), cap_t, amount=700, payee="acct_bank_3", idempotency_key="po-3")
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 5. TEST-MODE invariant — a live key is refused BEFORE any request. ────────────────
    broker.store("payout_live", LIVE_KEY, service="payouts")
    handle_live = broker.issue("payout_live", decima, "send payouts")
    lcalls = []
    cap_l = payouts.install_rail(
        kk, cap=5000, broker=broker, agent_cell=decima, credential_handle=handle_live,
        name="payout_live", transport=_transport(lcalls, (200, {"id": "po_x", "status": "paid"})),
        test_mode=True)
    kk.approve(cap_l)
    refused = payments.pay(kk, _agent(kk), cap_l, amount=100, payee="acct_bank_4", idempotency_key="po-4")
    assert refused["status"] == "FAILED", refused
    assert lcalls == [], "a live key must be refused before any payout request is made"
    line("  test-mode: a live (sk_live_) key is refused before any request — no real "
         "money can leave the reference to a bank account ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ──────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert TEST_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw provider key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw provider key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real payout engine is wrapped over stdlib urllib (zero deps): money OUT "
         "is Morta-gated, spend-capped, idempotent; receipts map paid/insufficient/timeout "
         "→ SUCCEEDED/FAILED/UNKNOWN with provider_ref; test-mode-only; key never disclosed.")
