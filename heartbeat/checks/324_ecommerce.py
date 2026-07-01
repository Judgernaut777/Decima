"""Real e-commerce order rail — wrap a REAL order/fulfilment API (Shopify / Amazon
SP-API style), wrapped (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — placing an order SPENDS MONEY and is an irreversible effect (the platform
charges the buyer, reserves inventory, dispatches fulfilment), so recreating order
placement is itself the liability. An order API is an HTTPS endpoint, so the real engine
rides stdlib `urllib` (zero pip deps). This check drives the rail entirely OFFLINE via an
injected fake transport (the real `urllib` transport is never called), so the oracle stays
deterministic and network-free while proving the full contract:

  - Morta-gated: unapproved → denied, and NO order request is made before approval;
  - success: a created/confirmed order → SUCCEEDED receipt carrying the platform
    `provider_ref` (order id), FINANCIAL class, the reconciled `total` and `item_count`,
    with the running spend cap decremented by the order total;
  - idempotent replay: the same key returns the prior receipt and places NO second order;
  - unbalanced total (sum(lines) != total) → refused BEFORE any request (no order, no charge);
  - out of stock / bad sku (4xx) → FAILED (no order was placed, no money moved);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - TEST-MODE invariant: a non-`test_` (live) key is refused BEFORE any request;
  - `payments.pay` compatibility: the PAY1 driver places a real order unchanged;
  - dispense-don't-disclose: the raw platform key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import ecommerce, secrets, payments

TEST_KEY = "test_DECIMA123"
LIVE_KEY = "live_DANGER"
ENDPOINT = "https://orders.example-shop.test/v1/orders"


def _transport(calls, response):
    """A fake order-API transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL E-COMMERCE ORDER RAIL (wrapped engine, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("shop", TEST_KEY, service="ecommerce")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("shop", decima, "place orders")

    def order(cap, *, total, ship_to, key, lines=None):
        """Drive the rail like payments.pay (amount→total→cost→spend cap) but able to
        carry line items on args['lines']. A FRESH agent cell is fetched per call (issue/
        approve re-assert the agent, so a stale reference would miss the envelope)."""
        agent = kk.weave().get(kk.decima_agent_id)
        args = {"amount": total, "total": total, "cost": total,
                "payee": ship_to, "ship_to": ship_to, "idempotency_key": key}
        if lines is not None:
            args["lines"] = lines
        return kk.invoke(agent, cap, args)

    # 1. SUCCESS + Morta gate + provider_ref + item_count + spend cap decrement. ─────────
    calls = []
    cap = ecommerce.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="order_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (201, {"id": "ord_1", "status": "created"})))
    lines = [{"sku": "SKU-A", "qty": 2, "unit_price": 1500},
             {"sku": "SKU-B", "qty": 1, "unit_price": 2000}]            # total 5000, 3 items
    # Morta: no approval yet → denied, and NO order request made.
    denied = order(cap, total=5000, ship_to="addr-42", key="ord-1", lines=lines)
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no order request may be made before Morta approval"
    spent_before = kk.spent.get(decima.id, 0.0)
    kk.approve(cap)
    ok = order(cap, total=5000, ship_to="addr-42", key="ord-1", lines=lines)
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "ord_1" and rc["rail"] == "ecommerce" \
        and rc["effect_class"] == "FINANCIAL", rc
    assert rc["total"] == 5000 and rc["item_count"] == 3, rc
    assert kk.spent[decima.id] == spent_before + 5000, (kk.spent[decima.id], spent_before)
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "ord-1", calls
    line("  success: Morta-gated (no call pre-approval) → SUCCEEDED receipt with platform "
         "provider_ref + item_count; spend cap decremented by the order total ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second order. ────────
    before = len(calls)
    again = order(cap, total=5000, ship_to="addr-42", key="ord-1", lines=lines)
    assert again["status"] == "SUCCEEDED", again
    rc2 = kk.weave().get(again["result_cell"]).content
    assert rc2.get("idempotent_replay") is True and rc2["provider_ref"] == "ord_1", rc2
    assert len(calls) == before, "a replay must not place a second order"
    line("  idempotent replay: same key → prior receipt, no second order ✓")

    # 3. UNBALANCED TOTAL — sum(lines) != total refused BEFORE any request. ──────────────
    ucalls = []
    cap_u = ecommerce.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="order_unbalanced", endpoint=ENDPOINT,
        transport=_transport(ucalls, (201, {"id": "ord_x", "status": "created"})))
    kk.approve(cap_u)
    bad_lines = [{"sku": "SKU-A", "qty": 2, "unit_price": 1500}]        # sums to 3000
    unb = order(cap_u, total=5000, ship_to="addr-9", key="ord-u", lines=bad_lines)
    assert unb["status"] == "FAILED", unb
    assert ucalls == [], "an unbalanced total must be refused before any request"
    line("  unbalanced total (sum(lines) != total) → refused before any request — no order ✓")

    # 4. OUT OF STOCK / BAD SKU (4xx) → FAILED (no order placed, no money moved). ────────
    scalls = []
    cap_s = ecommerce.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="order_oos", endpoint=ENDPOINT,
        transport=_transport(scalls, (409, {"error": {"message": "SKU-A out of stock"}})))
    kk.approve(cap_s)
    oos = order(cap_s, total=1500, ship_to="addr-7", key="ord-s",
                lines=[{"sku": "SKU-A", "qty": 1, "unit_price": 1500}])
    assert oos["status"] == "FAILED", oos
    line("  out of stock (4xx) → FAILED receipt — a definite no-effect (no order placed) ✓")

    # 5. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ───────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = ecommerce.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="order_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = order(cap_t, total=2000, ship_to="addr-5", key="ord-t",
                lines=[{"sku": "SKU-C", "qty": 1, "unit_price": 2000}])
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 6. TEST-MODE invariant — a live key is refused BEFORE any request. ─────────────────
    broker.store("shop_live", LIVE_KEY, service="ecommerce")
    handle_live = broker.issue("shop_live", decima, "place orders")
    lcalls = []
    cap_l = ecommerce.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle_live,
        name="order_live", endpoint=ENDPOINT,
        transport=_transport(lcalls, (201, {"id": "ord_live", "status": "created"})),
        test_mode=True)
    kk.approve(cap_l)
    refused = order(cap_l, total=100, ship_to="addr-1", key="ord-l",
                    lines=[{"sku": "SKU-Z", "qty": 1, "unit_price": 100}])
    assert refused["status"] == "FAILED", refused
    assert lcalls == [], "a live key must be refused before any order request is made"
    line("  test-mode: a live (non-test_) key is refused before any request — no real "
         "order can be placed from the reference ✓")

    # 7. payments.pay COMPATIBILITY — the PAY1 driver places a real order unchanged. ─────
    pcalls = []
    cap_p = ecommerce.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="order_pay", endpoint=ENDPOINT,
        transport=_transport(pcalls, (201, {"id": "ord_pay", "status": "confirmed"})))
    kk.approve(cap_p)
    paid = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_p,
                        amount=3200, payee="addr-88", idempotency_key="ord-p")
    assert paid["status"] == "SUCCEEDED", paid
    prc = kk.weave().get(paid["result_cell"]).content
    assert prc["provider_ref"] == "ord_pay" and prc["total"] == 3200 \
        and prc["item_count"] == 1, prc
    assert len(pcalls) == 1, pcalls
    line("  payments.pay compatibility: the PAY1 driver (amount→total→spend cap) places a "
         "real order unchanged → SUCCEEDED with provider_ref ✓")

    # 8. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ───────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert TEST_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw platform key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw platform key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real e-commerce order engine is wrapped over stdlib urllib (zero deps): "
         "Morta-gated, idempotent, totals reconcile before any spend, receipts map "
         "created/rejected/timeout → SUCCEEDED/FAILED/UNKNOWN with provider_ref; "
         "test-mode-only; the key is never disclosed.")
