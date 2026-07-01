"""Real brokerage order rail — a REAL regulated engine, wrapped (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — recreating securities order execution is the liability (it is regulated; a
mis-/double-placed order is real harm). An Alpaca-style broker is an HTTPS API, so the
real execution engine rides stdlib `urllib` (zero pip deps). This check drives the rail
entirely OFFLINE via an injected fake transport (the real `urllib` transport is never
called), so the oracle stays deterministic and network-free while proving the full
contract:

  - Morta-gated: unapproved order → denied, and NO broker request is made before approval;
  - success: a filled order → SUCCEEDED receipt carrying the broker `provider_ref`,
    FINANCIAL class, and the client_order_id sent to the broker;
  - idempotent replay: the same key returns the prior receipt and makes NO second order;
  - rejection / 4xx → FAILED (no order placed);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - PAPER-MODE invariant: a live (`AK…`) key AND a live endpoint are each refused BEFORE
    any request;
  - dispense-don't-disclose: the raw broker key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Runs on its OWN fresh Kernel + SecretsBroker (it places "orders"). Contract: run(k, line).
Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import brokerage_engine, secrets, payments

PAPER_KEY = "PK_DECIMA123"                                    # paper key (allowed)
LIVE_KEY = "AK_DANGER"                                        # live key (must be refused)
PAPER_ENDPOINT = "https://paper-api.alpaca.markets/v2/orders"
LIVE_ENDPOINT = "https://api.alpaca.markets/v2/orders"        # live venue (must be refused)


def _transport(calls, response):
    """A fake broker transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(_k, line):
    line("\n== REAL BROKERAGE ORDER RAIL (wrapped engine, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("alpaca", PAPER_KEY, service="alpaca")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("alpaca", decima, "place securities orders")

    # 1. SUCCESS + Morta gate + provider_ref + client_order_id. ─────────────────────────
    calls = []
    cap = brokerage_engine.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ord_ok", endpoint=PAPER_ENDPOINT,
        transport=_transport(calls, (200, {"id": "ord_test_1", "status": "filled",
                                            "filled_qty": "10"})))
    # Morta: no approval yet → denied, and NO broker request made.
    denied = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                          amount=10, payee="AAPL", idempotency_key="cli-1")
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no broker request may be made before Morta approval"
    kk.approve(cap)
    ok = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                      amount=10, payee="AAPL", idempotency_key="cli-1")
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "ord_test_1" and rc["rail"] == "brokerage" \
        and rc["effect_class"] == "FINANCIAL", rc
    assert rc["filled_qty"] == 10 and rc["symbol"] == "AAPL" and rc["side"] == "buy", rc
    assert len(calls) == 1 and "cli-1" in calls[0]["body"], calls
    line("  success: Morta-gated (no call pre-approval) → SUCCEEDED receipt with broker "
         "provider_ref + filled qty; client_order_id sent to the broker ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second order. ───────
    before = len(calls)
    again = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                         amount=10, payee="AAPL", idempotency_key="cli-1")
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert len(calls) == before, "a replay must not place a second broker order"
    line("  idempotent replay: same key → prior receipt, no second order ✓")

    # 3. REJECTION (4xx) → FAILED (no order placed). ────────────────────────────────────
    rcalls = []
    cap_r = brokerage_engine.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ord_reject", endpoint=PAPER_ENDPOINT,
        transport=_transport(rcalls, (403, {"code": 40310000,
                                            "message": "insufficient buying power"})))
    kk.approve(cap_r)
    rej = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_r,
                       amount=5, payee="TSLA", idempotency_key="cli-2")
    assert rej["status"] == "FAILED", rej
    line("  rejection (4xx / insufficient funds) → FAILED receipt — no order placed ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = brokerage_engine.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ord_timeout", endpoint=PAPER_ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_t,
                       amount=7, payee="NVDA", idempotency_key="cli-3")
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — we cannot know if the order placed, never fabricated ✓")

    # 5a. PAPER-MODE — a LIVE KEY is refused BEFORE any request. ────────────────────────
    broker.store("alpaca_live", LIVE_KEY, service="alpaca")
    handle_live = broker.issue("alpaca_live", decima, "place securities orders")
    lcalls = []
    cap_lk = brokerage_engine.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle_live,
        name="ord_live_key", endpoint=PAPER_ENDPOINT,
        transport=_transport(lcalls, (200, {"id": "x", "status": "filled"})), test_mode=True)
    kk.approve(cap_lk)
    ref_k = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_lk,
                         amount=1, payee="AAPL", idempotency_key="cli-4")
    assert ref_k["status"] == "FAILED", ref_k
    assert lcalls == [], "a live key must be refused before any broker request is made"

    # 5b. PAPER-MODE — a LIVE ENDPOINT is refused BEFORE any request. ───────────────────
    ecalls = []
    cap_le = brokerage_engine.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ord_live_ep", endpoint=LIVE_ENDPOINT,
        transport=_transport(ecalls, (200, {"id": "x", "status": "filled"})), test_mode=True)
    kk.approve(cap_le)
    ref_e = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_le,
                         amount=1, payee="AAPL", idempotency_key="cli-5")
    assert ref_e["status"] == "FAILED", ref_e
    assert ecalls == [], "a live endpoint must be refused before any broker request is made"
    line("  paper-mode: a live (AK_) key AND a live venue are each refused before any "
         "request — no real securities order can be routed from the reference ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ─────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert PAPER_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw broker key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw broker key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real brokerage engine is wrapped over stdlib urllib (zero deps): Morta-"
         "gated, idempotent, receipts map filled/rejected/timeout → SUCCEEDED/FAILED/"
         "UNKNOWN with provider_ref; paper-mode-only; the key is never disclosed.")
