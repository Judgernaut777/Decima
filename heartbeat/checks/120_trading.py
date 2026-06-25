"""TRADE1 — trading on the payments rail: a trade is a Morta-gated, wagered payment.

Proves a trade composes PAY1 + WV1 + CRED1 rather than reinventing money movement:
  - a buy is Morta-gated (denied until the rail is approved) and idempotent (a
    duplicate order does not double-fill), and updates the portfolio on a real fill;
  - the buy binds a price wager and settles a verdict on the realized return;
  - broker credentials are dispensed as a CRED1 handle (a token, never the raw key);
  - an over-cap order is refused (the spend cap bites);
  - a sell reduces the position; everything is on the Weft.

Contract: run(k, line). Fail loud.
"""
from decima import trading, payments
from decima.secrets import SecretsBroker


def run(k, line):
    line("\n== TRADING (a trade is a Morta-gated, idempotent, wagered payment) ==")
    decima0 = k.weave().get(k.decima_agent_id)   # principal/id are stable across grants

    # Rail cap sized to give 5000 headroom over whatever Decima has already spent
    # (the spend ledger is global per agent), so the math is robust across checks.
    headroom = 5000
    cap = int(k.spent.get(k.decima_agent_id, 0.0)) + headroom
    rail = payments.install_rail(k, cap=cap, name="trade.fill")

    # CRED1: the broker holds the exchange API key and issues Decima a scoped handle.
    broker = SecretsBroker(k)
    broker.store("exchange-key", "sk-live-EXCHANGE-SECRET", service="exchange")
    handle = broker.issue("exchange-key", decima0, "place trades")

    # Re-fetch AFTER both grants: the envelope now holds the rail + the handle
    # (k.invoke authorizes against the cell it is handed — a stale one lacks them).
    decima = k.weave().get(k.decima_agent_id)

    # 1. a buy is Morta-gated: denied before the rail is approved.
    b0 = trading.buy(k, decima, rail, symbol="AAPL", qty=10, price=150,
                     idempotency_key="buy-aapl-1", predicted_return=200,
                     broker=broker, handle=handle)
    line(f"  buy AAPL x10 @150 (no approval) → filled={b0['filled']} denied={b0['denied']}")
    assert not b0["filled"] and "approval" in (b0["denied"] or ""), b0

    # approve the rail (Morta) → the buy fills, updates the portfolio, binds a wager,
    # and authenticated via a dispensed credential handle (never the raw key).
    k.approve(rail)
    b1 = trading.buy(k, decima, rail, symbol="AAPL", qty=10, price=150,
                     idempotency_key="buy-aapl-1", predicted_return=200,
                     broker=broker, handle=handle)
    line(f"  approved → filled={b1['filled']} amount={b1['amount']} "
         f"pos={b1['positions'].get('AAPL')} wager={bool(b1['wager'])}")
    assert b1["filled"] and b1["positions"]["AAPL"]["qty"] == 10, b1
    assert b1["wager"], "a buy binds a price wager (WV1)"
    assert b1["credential"] and "denied" not in b1["credential"], "credential dispensed as a handle"

    # 2. idempotent: a duplicate order (same key) does NOT double-fill.
    dup = trading.buy(k, decima, rail, symbol="AAPL", qty=10, price=150,
                      idempotency_key="buy-aapl-1", predicted_return=200,
                      broker=broker, handle=handle)
    line(f"  duplicate buy (same key) → idempotent_replay={dup['idempotent_replay']} "
         f"pos still {dup['positions']['AAPL']['qty']}")
    assert dup["idempotent_replay"] and dup["positions"]["AAPL"]["qty"] == 10, dup

    # 3. settle the price wager against the realized return (a verdict, WV1).
    v = trading.settle(k, b1, observed=180)
    line(f"  verdict on the price wager: hit={v['hit']} delta={v['delta']}")
    assert v is not None and "hit" in v, v

    # 4. an over-cap order is refused (the spend cap bites).
    over = trading.buy(k, decima, rail, symbol="TSLA", qty=100, price=900,
                       idempotency_key="buy-tsla-1")
    line(f"  over-cap buy TSLA x100 @900 → filled={over['filled']} denied={over['denied']}")
    assert not over["filled"] and "budget" in (over["denied"] or ""), over

    # 5. a sell reduces the position.
    s = trading.sell(k, decima, rail, symbol="AAPL", qty=4, price=180,
                     idempotency_key="sell-aapl-1")
    line(f"  sell AAPL x4 @180 → filled={s['filled']} pos={s['positions'].get('AAPL')}")
    assert s["filled"] and s["positions"]["AAPL"]["qty"] == 6, s

    line("  → a trade is a Morta-gated, idempotent, wagered payment; positions + "
         "verdicts fold from the Weft. Real exchange engine: deferred.")
