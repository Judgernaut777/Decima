"""Trading on the payments rail — "trade stocks" by composition, not reinvention.

`CAPABILITY_MAP` D3.4. A trade is the canonical irreversible effect with a price
prediction attached, so it composes the primitives already built rather than
minting a new authority:

  - **money movement = `payments.pay`** (PAY1): FINANCIAL effect_class, a hard
    spend cap, Morta `requires_approval`, and an **idempotency key** so a replayed
    order never double-fills;
  - **a price wager → verdict** (WV1): every order binds a prediction of its
    return (via `payments.pay`'s wager) and settles a verdict on the realized
    outcome — spending is a calibrated bet, not a hope;
  - **broker credentials = a CRED1 handle** (SecretsBroker): the exchange API key
    is *dispensed as a scoped handle / derived token*, never disclosed raw;
  - **a `portfolio` Cell** on the Weft holds positions (symbol → qty/cost), updated
    only on a real fill (not on an idempotent replay).

Pure composition: it calls `payments`/`wager`/`secrets`/`kernel` public APIs and
edits none of them, no core file. A real exchange engine slots in behind the rail
stub the same way a real payment provider does.
"""
from decima import payments, executor, model
from decima.hashing import content_id, nfc

PORTFOLIO = "portfolio"
TRADE = "trade"


# ── portfolio (positions fold from a Weft Cell) ─────────────────────────────
def _portfolio_id(account: str) -> str:
    return content_id({"portfolio": nfc(account)})


def portfolio(weave, account: str = "default") -> dict:
    """Current positions {symbol: {qty, cost}} — folded from the portfolio Cell."""
    cell = weave.get(_portfolio_id(account))
    return dict(cell.content.get("positions", {})) if cell else {}


def _apply_position(k, account: str, symbol: str, dqty: int, dcost: int) -> dict:
    """LWW re-assert of the portfolio Cell with one position delta applied. A
    position that falls to ≤0 qty is closed (removed)."""
    positions = portfolio(k.weave(), account)
    pos = dict(positions.get(symbol, {"qty": 0, "cost": 0}))
    pos["qty"] += dqty
    pos["cost"] += dcost
    if pos["qty"] <= 0:
        positions.pop(symbol, None)
    else:
        positions[symbol] = pos
    model.assert_content(k.weft, k.decima_agent_id, _portfolio_id(account), PORTFOLIO,
                         {"account": nfc(account), "positions": positions})
    return positions


# ── the order path (buy/sell share it) ─────────────────────────────────────
def _authenticate(broker, agent_cell, handle, symbol: str, side: str):
    """Dispense-don't-disclose: use the CRED1 handle to authenticate to the
    exchange, yielding a derived token — never the raw API key. None if no broker."""
    if broker is None or handle is None:
        return None
    return broker.use(agent_cell, handle, {"action": f"{side} {symbol}",
                                           "symbol": nfc(symbol)})


def _order(k, agent_cell, pay_cap, *, side: str, symbol: str, qty: int, price: int,
           amount: int, dqty: int, dcost: int, idempotency_key: str, account: str,
           predicted_return, confidence: int, broker, handle) -> dict:
    symbol, key = nfc(symbol), nfc(str(idempotency_key))
    cred = _authenticate(broker, agent_cell, handle, symbol, side)

    # The order IS a Morta-gated, spend-capped, idempotent payment that binds a
    # price wager (PAY1 + WV1). A duplicate key returns the prior receipt: no fill.
    pr = payments.pay(k, agent_cell, pay_cap, amount=int(amount),
                      payee=f"exchange:{symbol}", idempotency_key=key,
                      prediction=predicted_return, confidence=confidence)
    filled = (pr.get("status") == executor.SUCCEEDED
              and not pr.get("idempotent_replay") and "denied" not in pr)

    positions = (_apply_position(k, account, symbol, dqty, dcost) if filled
                 else portfolio(k.weave(), account))

    # Record the trade on the Weft, linked to its receipt + wager (provenance).
    tid = content_id({"trade": side, "symbol": symbol, "key": key})
    model.assert_content(k.weft, k.decima_agent_id, tid, TRADE, {
        "side": side, "symbol": symbol, "qty": int(qty), "price": int(price),
        "amount": int(amount), "account": nfc(account), "idempotency_key": key,
        "filled": filled, "status": pr.get("status"), "wager": pr.get("wager"),
    })
    if pr.get("result_cell"):
        model.assert_edge(k.weft, k.decima_agent_id, tid, "settled_by", pr["result_cell"])
    if pr.get("wager"):
        model.assert_edge(k.weft, k.decima_agent_id, tid, "wagers", pr["wager"])

    return {"side": side, "symbol": symbol, "qty": int(qty), "price": int(price),
            "amount": int(amount), "trade": tid, "payment": pr, "filled": filled,
            "denied": pr.get("denied"), "wager": pr.get("wager"),
            "idempotent_replay": bool(pr.get("idempotent_replay")),
            "positions": positions, "credential": cred}


def buy(k, agent_cell, pay_cap, *, symbol: str, qty: int, price: int,
        idempotency_key: str, account: str = "default", predicted_return=None,
        confidence: int = 900_000, broker=None, handle=None) -> dict:
    """Buy `qty` of `symbol` at `price`: pay the principal (qty·price) on the rail
    (Morta-gated, capped, idempotent), bind a price wager, and add the position on
    a real fill. An over-cap order is refused (no position change)."""
    amount = int(qty) * int(price)
    return _order(k, agent_cell, pay_cap, side="buy", symbol=symbol, qty=qty,
                  price=price, amount=amount, dqty=int(qty), dcost=amount,
                  idempotency_key=idempotency_key, account=account,
                  predicted_return=predicted_return, confidence=confidence,
                  broker=broker, handle=handle)


def sell(k, agent_cell, pay_cap, *, symbol: str, qty: int, price: int,
         idempotency_key: str, account: str = "default", predicted_return=None,
         confidence: int = 900_000, commission: int | None = None,
         broker=None, handle=None) -> dict:
    """Sell `qty` of `symbol`: submit the order on the rail (a Morta-gated
    commission payment, idempotent), bind a price wager on the realized return, and
    REDUCE the position by `qty` (cost basis reduced proportionally; the position
    closes if it hits zero). Refused if there is nothing to sell."""
    symbol = nfc(symbol)
    held = portfolio(k.weave(), account).get(symbol)
    if not held or held["qty"] <= 0:
        return {"side": "sell", "symbol": symbol, "filled": False,
                "denied": f"no position in {symbol} to sell",
                "positions": portfolio(k.weave(), account)}
    q = min(int(qty), held["qty"])
    dcost = -round(held["cost"] * q / held["qty"])          # proportional basis reduction
    fee = int(commission) if commission is not None else max(1, (q * int(price)) // 1000)
    return _order(k, agent_cell, pay_cap, side="sell", symbol=symbol, qty=q,
                  price=price, amount=fee, dqty=-q, dcost=dcost,
                  idempotency_key=idempotency_key, account=account,
                  predicted_return=predicted_return, confidence=confidence,
                  broker=broker, handle=handle)


def settle(k, trade_result: dict, observed) -> dict | None:
    """Settle the trade's bound price wager against the realized return (a verdict).
    Returns {verdict, hit, delta}, or None if the order carried no wager (WV1)."""
    return payments.settle(k, trade_result.get("payment", {}), observed)
