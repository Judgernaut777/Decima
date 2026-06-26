"""BROKER1 — wrapped agentic brokerage: an isolated sub-account as a scoped capability.

Proves the D3.4 brokerage rail composes CAPITAL1 + TRADE1 + capability + kernel
rather than reinventing execution or custody (the broker is a deterministic stub):
  - open an ISOLATED sub-account = a scoped FINANCIAL capability with budget =
    max_exposure (its own envelope/principal, never the main balance), paper mode;
  - FUND it via the Stripe capital rail (money IN, Morta-gated through an ephemeral
    card — denied until approved → charged), crediting the sub-account's own ledger;
  - a WITHIN-exposure trade is Morta-gated (denied → approve → filled, on the Weft),
    updating positions + notional exposure;
  - an OVER-exposure trade is REFUSED before any money moves;
  - the KILL SWITCH = Morta revocation (CASCADE) → a subsequent trade fails CLOSED
    at the kernel (capability RETRACTed), not just a soft pre-check;
  - paper vs live is noted on the account.

Runs on its OWN fresh Kernel (it moves "money"). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import executor, payments, trading, brokerage
from decima.kernel import Kernel


def run(_k, line):
    line("\n== WRAPPED BROKERAGE (isolated sub-account = scoped cap · Stripe-funded · Morta · kill switch) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)     # isolated
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- (1) open an ISOLATED sub-account: scoped + exposure-capped ----------
    acct = brokerage.open_account(k, decima(), "alpaca", max_exposure=10_000, mode="paper")
    cap = k.weave().get(acct["cap_id"])
    assert cap.content["caveats"]["budget"] == 10_000                        # exposure cap
    assert cap.content["caveats"]["effect_class"] == payments.FINANCIAL      # FINANCIAL contract
    assert cap.content["caveats"]["requires_approval"] is True               # Morta gate
    assert cap.content["parent"] is not None                                 # downhill of the rail cap
    holder = k.weave().get(acct["holder"])
    assert holder.content["principal"] == acct["principal"]                  # OWN principal
    assert holder.content["envelope"] == [acct["cap_id"]]                    # envelope = ONE account (isolated)
    assert acct["principal"] != decima().content["principal"]               # NOT the main balance
    assert acct["mode"] == brokerage.PAPER                                   # paper (sandbox)
    line(f"  open(alpaca,paper): account {acct['cap_id'][:8]} — exposure_cap=10000, "
         f"own principal {acct['principal'][:8]}, isolated envelope ✓")

    # ---- (2) FUND it via the Stripe capital rail (money IN, Morta-gated) ------
    f = brokerage.fund(k, decima(), acct, 5_000)
    assert f.get("status") == executor.SUCCEEDED and f["rail"] == "stripe", f
    assert f["funded"] == 5_000                                              # credited the sub-account ledger
    st = brokerage.account_state(k.weave(), acct)
    assert st["funded"] == 5_000                                             # on the Weft
    line(f"  fund(5000): Morta-gated transfer IN over the Stripe rail (card charged) "
         f"→ sub-account funded={f['funded']} ✓")

    # ---- (3a) a within-exposure trade is DENIED until the Morta gate is met ---
    t0 = brokerage.trade(k, decima(), acct, "AAPL", 10, "buy", price=150)
    assert not t0["filled"] and "approval" in (t0.get("denied") or "").lower(), t0
    line(f"  pre-approval: buy AAPL x10 @150 DENIED — {t0['denied']}")

    # ---- (3b) approve → within-exposure trade FILLS, on the Weft -------------
    k.approve(acct["cap_id"])
    t1 = brokerage.trade(k, decima(), acct, "AAPL", 10, "buy", price=150, predicted_return=200)
    assert t1["filled"] and t1["positions"]["AAPL"]["qty"] == 10, t1
    assert t1["exposure"] == 1_500, t1                                       # notional folded
    assert t1["wager"], "a trade binds a price wager (WV1)"
    pos = trading.portfolio(k.weave(), acct["portfolio"])
    assert pos["AAPL"]["qty"] == 10                                          # positions on the Weft
    line(f"  approved: buy AAPL x10 @150 → FILLED, pos={pos['AAPL']['qty']}, "
         f"exposure={t1['exposure']}/10000, wager bound ✓")

    # ---- (4) an OVER-exposure trade is REFUSED (no money moves) --------------
    over = brokerage.trade(k, decima(), acct, "TSLA", 100, "buy", price=900)
    assert not over["filled"] and "over max_exposure" in (over.get("refused") or ""), over
    assert payments.find_payment(k.weave(), f"trade:{acct['cap_id'][:8]}:buy:tsla:100:900") is None
    line(f"  over-exposure: buy TSLA x100 @900 (notional 90000) REFUSED — no money moved ✓")

    # ---- (5) the KILL SWITCH: Morta revocation (CASCADE) → fails CLOSED ------
    killed = brokerage.kill(k, acct)
    assert killed["killed"] and k.weave().get(acct["cap_id"]).retracted    # cap RETRACTed (Morta)
    # the module's own pre-check refuses...
    t2 = brokerage.trade(k, decima(), acct, "AAPL", 1, "buy", price=150)
    assert not t2["filled"] and "killed" in (t2.get("denied") or "").lower(), t2
    # ...and the KERNEL itself fails closed even bypassing the pre-check (the real
    # kill switch: a revoked capability cannot authorize an INVOKE — CASCADE).
    raw = trading.buy(k, k.weave().get(acct["holder"]), acct["cap_id"], symbol="AAPL",
                      qty=1, price=150, idempotency_key="post-kill-raw",
                      account=acct["portfolio"])
    assert not raw["filled"] and "revok" in (raw.get("denied") or "").lower(), raw
    assert trading.portfolio(k.weave(), acct["portfolio"])["AAPL"]["qty"] == 10  # unchanged
    line(f"  kill switch: revoke(account) → subsequent trade FAILS CLOSED "
         f"(kernel: {raw['denied']}) ✓")

    line("  → an isolated sub-account is a scoped, exposure-capped capability; money in "
         "(Stripe) + trades out are Morta-gated; kill = revocation. Real broker: deferred.")
