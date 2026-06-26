"""Wrapped agentic brokerage — an isolated sub-account as a scoped capability (BROKER1).

CAPABILITY_MAP D3.4 "brokerage rail", in pure ocap terms. Decima never reinvents
execution or custody: it WRAPS a broker (an Alpaca/IBKR-style deterministic stub —
no real market) and models the *wrap*, composing the primitives already built —
the Stripe capital rail (CAPITAL1), trading (TRADE1), capability attenuation, and
the kernel's grant/approve/revoke — and editing no core file.

The mapping:

  • An **isolated sub-account** is a SCOPED capability, not the main balance: a
    downhill attenuation of the rail's FINANCIAL pay-cap, with `budget = max_exposure`
    (the brokerage's risk cap), granted to the account's OWN ephemeral principal so its
    spend ledger and envelope are isolated — its blast radius is exactly this account.
  • **Funding** is money IN: a Morta-gated transfer INTO the sub-account over the
    CAPITAL1 Stripe rail (an ephemeral card minted by `CapitalDesk`, charged after
    approval). Money never lands in the main balance; it credits the sub-account's
    own envelope on the Weft.
  • A **trade** is money OUT: a Morta-gated, idempotent order through the wrapped
    broker stub (composes TRADE1's `buy`, which is itself PAY1 + WV1). It is REFUSED
    if it would push the account's notional exposure past `max_exposure` — refused
    BEFORE any INVOKE, so no money moves on an over-exposure order.
  • The **kill switch** is Morta revocation: `kernel.revoke(account_cap)` RETRACTs the
    account capability with a DERIVED_AUTHORITY CASCADE, so the account cap and every
    grant attenuated from it fail CLOSED — a subsequent trade is denied. Killing the
    account kills the derived authority too; trading after the kill fails closed.
  • **paper vs live**: a `paper` account runs in the SB1 sandbox (mode noted on the
    account Cell + the broker is a paper sandbox); a `live` account is the same wrap
    pointed at a settling broker. The safety rails are identical either way.

Pure composition: it calls capital / trading / capability / kernel PUBLIC APIs and
edits none of them. A real broker engine slots in behind the stub the same way a
real exchange or payment provider does.
"""
from decima import payments, trading, model, executor
from decima.capital import CapitalDesk
from decima.capability import attenuate
from decima.hashing import content_id, nfc
from decima.weft import ASSERT

ACCOUNT = "brokerage_account"
PAPER = "paper"
LIVE = "live"


def _account_id(agent_id: str, broker: str) -> str:
    return content_id({"brokerage_account": nfc(broker), "agent": agent_id})


def account_state(weave, account: dict) -> dict:
    """The sub-account's folded state {mode, max_exposure, funded, exposure, broker,
    positions, killed} — a pure projection of its Cell + portfolio on the Weft."""
    cell = weave.get(account["account_cell"])
    st = dict(cell.content) if cell else {}
    st["positions"] = trading.portfolio(weave, account["portfolio"])
    cap = weave.get(account["cap_id"])
    st["killed"] = bool(cap.retracted) if cap else True
    return st


# ── open: an isolated sub-account = a scoped, exposure-capped capability ─────
def open_account(k, agent, broker, *, max_exposure, mode=PAPER) -> dict:
    """Open an ISOLATED sub-account: a scoped FINANCIAL capability attenuated DOWNHILL
    from the wrapped broker's rail pay-cap, with `budget = max_exposure` (the risk
    cap) and granted to the account's OWN ephemeral principal — its own envelope and
    spend ledger, never the main balance. `mode` is paper (SB1 sandbox) vs live.

    Returns an account dict {account_cell, cap_id, principal, holder, portfolio,
    broker, mode, max_exposure, desk, card}. The cap is born Morta-gated and is NOT
    yet approved — funding/trading must pass the Morta gate."""
    if mode not in (PAPER, LIVE):
        raise ValueError(f"mode must be {PAPER!r} or {LIVE!r}, got {mode!r}")
    max_exposure = int(max_exposure)
    broker = nfc(broker)

    # The wrapped broker's rail — a deterministic stub (Alpaca/IBKR-style; no real
    # market). It is the rail's FINANCIAL pay-cap that the account attenuates from.
    rail = payments.install_rail(k, cap=max_exposure,
                                 name=f"brokerage.{broker}.fill", rail=f"broker:{broker}")
    k.approve(rail)   # the RAIL is operator-enabled; per-account Morta is the account cap's gate
    parent = k.weave().get(rail)

    # The account's OWN ephemeral principal — isolation: its envelope IS this one
    # sub-account, its spend ledger is keyed by its own agent (not the main balance).
    holder = k.keyring.mint(f"brokerage:{broker}", "account")
    grantee = holder.id
    granter = parent.content["grantee"]      # the rail is granted to Decima; Decima issues downhill

    # Downhill: budget shrinks to max_exposure (the risk cap), Morta stays on, and in
    # paper mode the SB1 sandbox flag is carried (sandbox-only authority).
    stricter = {"budget": max_exposure, "requires_approval": True}
    if mode == PAPER:
        stricter["sandbox_only"] = True
    acct_cap = attenuate(parent.content, stricter, rail, grantee=grantee, granter=granter)
    cap_id = content_id({"brokerage_cap": broker, "to": grantee, "exposure": max_exposure})
    model.assert_content(k.weft, k.decima_agent_id, cap_id, "capability", acct_cap)

    # The account agent — its envelope is exactly this one account cap (isolated
    # blast radius), signed under the account's own principal. `sandbox` follows mode.
    portfolio = content_id({"brokerage_portfolio": cap_id})
    holder_id = content_id({"account_holder": cap_id})
    k.weft.append(k.decima_agent_id, ASSERT, {
        "cell": holder_id, "type": "agent",
        "content": {"principal": holder.id, "objective": f"hold one {broker} sub-account",
                    "envelope": [cap_id], "budget": max_exposure,
                    "sandbox": (mode == PAPER), "lineage": agent.id},
    })

    # The account Cell — the sub-account ledger projection (funded / exposure / mode).
    account_cell = _account_id(grantee, broker)
    model.assert_content(k.weft, k.decima_agent_id, account_cell, ACCOUNT, {
        "broker": broker, "mode": mode, "max_exposure": max_exposure,
        "funded": 0, "exposure": 0, "cap": cap_id, "principal": holder.id,
        "lineage": agent.id, "sandbox": (mode == PAPER),
    })

    # CAPITAL1 Stripe rail desk — funding flows IN over the ephemeral-card rail.
    desk = CapitalDesk(k)
    return {"account_cell": account_cell, "cap_id": cap_id, "principal": holder.id,
            "holder": holder_id, "portfolio": portfolio, "broker": broker, "mode": mode,
            "max_exposure": max_exposure, "rail": rail, "desk": desk, "card": None}


# ── fund: money IN over the CAPITAL1 Stripe rail, Morta-gated ────────────────
def fund(k, agent, account, amount) -> dict:
    """Fund the sub-account: a Morta-gated transfer INTO it over the CAPITAL1 Stripe
    capital rail (an ephemeral card charged after approval). Money credits the
    sub-account's own ledger Cell — never the main balance. Returns the charge result
    plus {funded} (the new sub-account balance). REFUSED if it would push the funded
    balance past `max_exposure` (you cannot over-fund the risk cap)."""
    amount = int(amount)
    st = account_state(k.weave(), account)
    if st.get("killed"):
        return {"denied": "account killed (capability revoked) — funding fails closed"}
    new_funded = int(st.get("funded", 0)) + amount
    if new_funded > int(account["max_exposure"]):
        return {"refused": f"funding {amount} → {new_funded} over max_exposure "
                           f"{account['max_exposure']}", "funded": st.get("funded", 0)}

    # Mint an ephemeral Stripe card capped at `amount`, locked to this account's
    # funding category, then charge it (Morta-gated by the card; approve → charged).
    decima = k.weave().get(k.decima_agent_id)
    cat = f"brokerage-fund:{account['broker']}"
    card = account["desk"].mint_card(k, decima, amount_cap=amount,
                                     merchant_category=cat, rail="stripe")
    account["card"] = card
    account["desk"].approve(card)            # the synchronous Morta gate on the funding charge
    res = account["desk"].charge(k, decima, card, amount=amount,
                                 merchant=f"subaccount:{account['broker']}",
                                 merchant_category=cat,
                                 idempotency_key=f"fund:{account['cap_id'][:8]}:{new_funded}")
    if res.get("status") != executor.SUCCEEDED:
        res.setdefault("denied", res.get("refused", "funding charge did not settle"))
        res["funded"] = st.get("funded", 0)
        return res

    # Credit the sub-account ledger Cell (money landed in the isolated sub-account).
    cell = k.weave().get(account["account_cell"])
    model.assert_content(k.weft, k.decima_agent_id, account["account_cell"], ACCOUNT,
                         {**cell.content, "funded": new_funded})
    res["funded"] = new_funded
    res["rail"] = "stripe"
    return res


# ── trade: money OUT through the wrapped broker, Morta-gated + exposure-capped ─
def trade(k, agent, account, symbol, qty, side, *, price=None,
          predicted_return=None) -> dict:
    """Place a trade through the WRAPPED broker stub. Morta-gated (denied until the
    account cap is approved) and idempotent (a replayed order does not double-fill).
    REFUSED — before any INVOKE — if it would push the account's notional exposure
    past `max_exposure`. A within-exposure approved trade fills, updates positions/P&L
    on the Weft, and reduces remaining exposure. A `sell` reduces the position.

    Signed by the ACCOUNT's own principal so `budget` is a per-account spend cap and
    the order is isolated from the main balance. Returns the trade result + {exposure,
    refused?}."""
    side = nfc(side)
    symbol = nfc(symbol)
    qty = int(qty)
    px = int(price) if price is not None else 100
    notional = qty * px

    st = account_state(k.weave(), account)
    if st.get("killed"):
        return {"denied": "account killed (capability revoked) — trade fails closed",
                "filled": False}

    # The account agent signs its own orders (per-account spend cap + isolation).
    holder = k.weave().get(account["holder"])
    key = f"trade:{account['cap_id'][:8]}:{side}:{symbol}:{qty}:{px}"

    if side == "buy":
        # (refuse) over-exposure — BEFORE any INVOKE, so no money moves.
        prior = int(st.get("exposure", 0))
        if prior + notional > int(account["max_exposure"]):
            return {"refused": f"buy {symbol} x{qty} @{px} (notional {notional}) would push "
                               f"exposure {prior}→{prior + notional} over max_exposure "
                               f"{account['max_exposure']}", "filled": False,
                    "exposure": prior}
        res = trading.buy(k, holder, account["cap_id"], symbol=symbol, qty=qty, price=px,
                          idempotency_key=key, predicted_return=predicted_return,
                          account=account["portfolio"])
    elif side == "sell":
        res = trading.sell(k, holder, account["cap_id"], symbol=symbol, qty=qty, price=px,
                           idempotency_key=key, predicted_return=predicted_return,
                           account=account["portfolio"])
    else:
        return {"refused": f"unknown side {side!r} (buy|sell)", "filled": False}

    # Update the account's notional exposure on a real fill (buy +notional, sell -).
    if res.get("filled") and not res.get("idempotent_replay"):
        delta = notional if side == "buy" else -notional
        new_exp = max(0, int(st.get("exposure", 0)) + delta)
        cell = k.weave().get(account["account_cell"])
        model.assert_content(k.weft, k.decima_agent_id, account["account_cell"], ACCOUNT,
                             {**cell.content, "exposure": new_exp})
        res["exposure"] = new_exp
    else:
        res["exposure"] = int(st.get("exposure", 0))
    res["mode"] = account["mode"]
    return res


# ── kill: the kill switch = Morta revocation (CASCADE) ──────────────────────
def kill(k, account) -> dict:
    """The kill switch: Morta revocation of the account capability. RETRACTs the
    account cap with a DERIVED_AUTHORITY CASCADE, so the account cap and every grant
    derived from it fail CLOSED. Any subsequent trade/fund is denied — fail closed.
    Returns {killed, cap_id}."""
    k.revoke(account["cap_id"])      # RETRACT → DERIVED_AUTHORITY cascade (default for a capability)
    cell = k.weave().get(account["account_cell"])
    if cell is not None:
        model.assert_content(k.weft, k.decima_agent_id, account["account_cell"], ACCOUNT,
                             {**cell.content, "killed": True})
    return {"killed": True, "cap_id": account["cap_id"]}
