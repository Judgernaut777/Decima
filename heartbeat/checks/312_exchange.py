"""Real crypto-exchange order rail — a REAL trading engine, wrapped (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — placing a crypto trade is an irreversible money movement (a mis-/double-placed
order is real, unrecoverable harm), so recreating exchange order execution is the
liability. A Coinbase/Kraken-style exchange is an HTTPS API, so the real matching engine
rides stdlib `urllib` (zero pip deps). This is DISTINCT from the securities brokerage
(checks/296) — that trades regulated securities; this trades crypto — but it composes the
SAME Morta-gated financial spine. This check drives the rail entirely OFFLINE via an
injected fake transport (the real `urllib` transport is never called), so the oracle stays
deterministic and network-free while proving the full contract:

  - Morta-gated: unapproved order → denied, and NO exchange request is made before approval;
  - success: a filled order → SUCCEEDED receipt carrying the exchange `provider_ref`,
    FINANCIAL class, the filled size, and the client_order_id sent to the exchange;
  - idempotent replay: the same key returns the prior receipt and makes NO second order;
  - rejection / 4xx → FAILED (no order placed);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - SANDBOX-MODE invariant: a live key AND a live endpoint are each refused BEFORE any
    request;
  - dispense-don't-disclose: the raw exchange key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Runs on its OWN fresh Kernel + SecretsBroker (it places "orders"). Contract: run(k, line).
Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import exchange, secrets, payments

SANDBOX_KEY = "sandbox_DECIMA123"                             # sandbox key (allowed)
LIVE_KEY = "live_DANGER"                                      # live key (must be refused)
SANDBOX_ENDPOINT = "https://api-public.sandbox.exchange.coinbase.com/orders"
LIVE_ENDPOINT = "https://api.exchange.coinbase.com/orders"    # live venue (must be refused)


def _transport(calls, response):
    """A fake exchange transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(_k, line):
    line("\n== REAL CRYPTO-EXCHANGE ORDER RAIL (wrapped engine, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("coinbase", SANDBOX_KEY, service="coinbase")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("coinbase", decima, "place crypto orders")

    # 1. SUCCESS + Morta gate + provider_ref + client_order_id. ─────────────────────────
    calls = []
    cap = exchange.install_rail(
        kk, cap=100_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="xch_ok", endpoint=SANDBOX_ENDPOINT,
        transport=_transport(calls, (200, {"id": "xch_test_1", "status": "filled",
                                           "filled_size": "50000"})))
    # Morta: no approval yet → denied, and NO exchange request made.
    denied = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                          amount=50000, payee="BTC-USD", idempotency_key="cli-1")
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no exchange request may be made before Morta approval"
    kk.approve(cap)
    ok = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                      amount=50000, payee="BTC-USD", idempotency_key="cli-1")
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "xch_test_1" and rc["rail"] == "exchange" \
        and rc["effect_class"] == "FINANCIAL", rc
    assert rc["filled_size"] == 50000 and rc["product"] == "BTC-USD" and rc["side"] == "buy", rc
    assert len(calls) == 1 and "cli-1" in calls[0]["body"], calls
    line("  success: Morta-gated (no call pre-approval) → SUCCEEDED receipt with exchange "
         "provider_ref + filled size; client_order_id sent to the exchange ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second order. ───────
    before = len(calls)
    again = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                         amount=50000, payee="BTC-USD", idempotency_key="cli-1")
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert len(calls) == before, "a replay must not place a second exchange order"
    line("  idempotent replay: same key → prior receipt, no second order ✓")

    # 3. REJECTION (4xx) → FAILED (no order placed). ────────────────────────────────────
    rcalls = []
    cap_r = exchange.install_rail(
        kk, cap=100_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="xch_reject", endpoint=SANDBOX_ENDPOINT,
        transport=_transport(rcalls, (400, {"message": "Insufficient funds"})))
    kk.approve(cap_r)
    rej = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_r,
                       amount=5000, payee="ETH-USD", idempotency_key="cli-2")
    assert rej["status"] == "FAILED", rej
    line("  rejection (4xx / insufficient funds) → FAILED receipt — no order placed ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = exchange.install_rail(
        kk, cap=100_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="xch_timeout", endpoint=SANDBOX_ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_t,
                       amount=7000, payee="SOL-USD", idempotency_key="cli-3")
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — we cannot know if the order placed, never fabricated ✓")

    # 5a. SANDBOX-MODE — a LIVE KEY is refused BEFORE any request. ──────────────────────
    broker.store("coinbase_live", LIVE_KEY, service="coinbase")
    handle_live = broker.issue("coinbase_live", decima, "place crypto orders")
    lcalls = []
    cap_lk = exchange.install_rail(
        kk, cap=100_000_000, broker=broker, agent_cell=decima, credential_handle=handle_live,
        name="xch_live_key", endpoint=SANDBOX_ENDPOINT,
        transport=_transport(lcalls, (200, {"id": "x", "status": "filled"})), test_mode=True)
    kk.approve(cap_lk)
    ref_k = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_lk,
                         amount=1, payee="BTC-USD", idempotency_key="cli-4")
    assert ref_k["status"] == "FAILED", ref_k
    assert lcalls == [], "a live key must be refused before any exchange request is made"

    # 5b. SANDBOX-MODE — a LIVE ENDPOINT is refused BEFORE any request. ─────────────────
    ecalls = []
    cap_le = exchange.install_rail(
        kk, cap=100_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="xch_live_ep", endpoint=LIVE_ENDPOINT,
        transport=_transport(ecalls, (200, {"id": "x", "status": "filled"})), test_mode=True)
    kk.approve(cap_le)
    ref_e = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_le,
                         amount=1, payee="BTC-USD", idempotency_key="cli-5")
    assert ref_e["status"] == "FAILED", ref_e
    assert ecalls == [], "a live endpoint must be refused before any exchange request is made"
    line("  sandbox-mode: a live key AND a live venue are each refused before any "
         "request — no real crypto trade can be routed from the reference ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ─────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert SANDBOX_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw exchange key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw exchange key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real crypto exchange engine is wrapped over stdlib urllib (zero deps): "
         "Morta-gated, idempotent, receipts map filled/rejected/timeout → SUCCEEDED/FAILED/"
         "UNKNOWN with provider_ref; sandbox-mode-only; the key is never disclosed.")
