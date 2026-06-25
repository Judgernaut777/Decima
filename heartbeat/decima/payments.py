"""Payments rail — financial transactions as the canonical irreversible effect (PAY1).

Paying for compute, trading, ads: money leaving the box is the most irreversible
thing Decima can do, so it composes every safety primitive already in the kernel
rather than inventing a new authority:

  • FINANCIAL effect_class + a hard **spend cap** (the `budget` caveat — `authorize`
    refuses a spend that would exceed it, and the cap is a RUNNING total, not
    per-transaction);
  • **Morta** (`requires_approval`) — a payment is denied until a human/policy
    approves the capability;
  • a **sandbox profile** — only the payment effect may run under this capability,
    network pinned to the rail (the durable form pins egress to the rail host);
  • an **idempotency key** at the RAIL layer — the kernel's per-INVOKE nonce can't
    tell two *logical* calls apart, so a replayed request with the same key returns
    the prior receipt and never double-spends;
  • a full **EffectReceipt** on the Weft (status/effect_class/idempotency — audit);
  • a **WV1 wager → verdict** binding: predict the outcome, get it approved, act,
    then measure — so spending is a calibrated bet, not a hope.

Pure composition: registers its effect via the public `executor.register`
(through `kernel.integrate_tool`) and uses `wager` + Morta + the SB1 sandbox — it
edits none of them, and no core file.
"""
from decima import executor, wager
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
PAYMENT_EFFECT = "payment"
RESULT = "result"          # the EffectReceipt cell type the kernel asserts


def _payment_handler(rail: str, args: dict) -> dict:
    """The rail itself — a deterministic stub standing in for Stripe / an exchange /
    a compute biller. A real handler calls the provider over the network-to-rail-only
    sandbox; here it confirms the charge deterministically. Echoes the idempotency key
    and amount into its output so the receipt carries them for audit + dedupe.

    A bad request (non-positive amount, missing payee) raises ExecError → a FAILED
    receipt: a definite no-effect, money never moved."""
    amount = args.get("amount")
    payee = nfc(str(args.get("payee", "")))
    if not isinstance(amount, int) or amount <= 0:
        raise executor.ExecError("payment amount must be a positive integer")
    if not payee:
        raise executor.ExecError("payment requires a payee")
    return {"out": f"charged {amount} to {payee}", "amount": amount, "payee": payee,
            "idempotency_key": args.get("idempotency_key"), "rail": rail}


def install_rail(k, *, cap: int, name: str = PAYMENT_EFFECT, rail: str = "stub-rail") -> str:
    """Register the payment effect and forge a FINANCIAL capability granted to Decima:
    a hard spend cap, Morta `requires_approval`, and a sandbox profile that allows
    only this effect with network to the rail. Returns the capability id."""
    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                 # hard spend cap — authorize enforces it
        "requires_approval": True,          # Morta gate
        # SB1 sandbox: only this effect may run under the cap; network on (to the
        # rail). The durable form pins egress to the rail host (landlock/egress filter).
        "sandbox": {"effects": [name], "network": True},
    }
    return k.integrate_tool(name, lambda _impl, args: _payment_handler(rail, args),
                            caveats=caveats)


def find_payment(weave, idempotency_key: str):
    """A prior SUCCEEDED payment receipt for this idempotency key, or None. This is
    the rail-level dedupe: the kernel's per-INVOKE nonce changes every call, so two
    logical re-tries would each spend; matching on the caller's key makes a replay a
    no-op."""
    key = nfc(str(idempotency_key))
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == FINANCIAL
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def pay(k, agent_cell, cap_id, *, amount: int, payee: str, idempotency_key: str,
        prediction=None, confidence: int = 900_000) -> dict:
    """Run a Morta-gated, spend-capped, idempotent payment — optionally a calibrated
    bet (WV1). Returns {status, result_cell, denied?, wager, idempotent_replay, amount}.

    Flow: (idempotency) a replay of the same key returns the prior receipt, no spend;
    (wager) optionally record a prediction bound to the cap; (invoke) Morta-gated +
    budget-capped, with `cost=amount` driving the running spend cap; the kernel emits
    the EffectReceipt. Settle the wager later with `settle()`."""
    key = nfc(str(idempotency_key))

    existing = find_payment(k.weave(), key)
    if existing is not None:                                   # (idempotency) no double-spend
        return {"status": existing.content["status"], "result_cell": existing.id,
                "amount": existing.content.get("amount"), "wager": None,
                "idempotent_replay": True}

    wid = None
    if prediction is not None:                                # (wager) predict before acting
        wid = wager.wager(k, f"pay {amount} to {payee}", prediction, confidence,
                          significant=True, action_cap=cap_id)

    res = k.invoke(agent_cell, cap_id, {                      # Morta-gated + budget-capped
        "amount": int(amount), "payee": nfc(str(payee)),
        "cost": int(amount), "idempotency_key": key,
    })
    out = {"wager": wid, "idempotent_replay": False, "amount": int(amount),
           "status": res.get("status"), "result_cell": res.get("result_cell")}
    if "denied" in res:
        out["denied"] = res["denied"]
    return out


def settle(k, pay_result: dict, observed) -> dict | None:
    """Resolve the payment's bound wager against the observed outcome (a verdict).
    Returns the verdict result, or None if the payment carried no wager."""
    if not pay_result.get("wager"):
        return None
    return wager.verdict(k, pay_result["wager"], observed)
