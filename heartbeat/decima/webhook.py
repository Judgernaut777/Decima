"""WEBHOOK1 — synchronous real-time approval callbacks (the Stripe-style gate).

CAPABILITY_MAP D3.4: a purchase mints an ephemeral, single-use virtual card with a
hard `spending_limit` + merchant lock, and **the real-time authorization webhook —
the 2-second `approve:true/false` — is the synchronous Morta gate at the instant
money moves**. This module is that gate.

An inbound authorization request (Stripe POSTs one when a card is swiped) is
**UNTRUSTED DATA**, exactly like a disposition intake (DISP1) or a recalled claim:
the decision is DECIMA's, computed from the request-as-DATA against a deterministic
policy — *never* the request's own instruction. The request cannot approve itself.

Two laws, both fail-closed:
  - **policy**: a deterministic predicate (spend ceiling, merchant allowlist, and a
    B4 `governance_check` against the merchant) — if it denies, `approve=false`.
  - **deadline**: the decision is bounded by a DEADLINE modeled deterministically as
    an int budget of *ticks* (NOT wall-clock — WEFT §4/§7: ints, time-travelable).
    Each evaluation step costs ticks; an inbound that forces more work than the
    deadline allows (`request["cost"]`, an adversary's slow request) elapses the
    budget → `approve=false`. If policy denies OR the deadline elapses → fail closed.

Every decision is recorded as an `auth_decision` Cell grounded (`decided_from`) in
the recorded inbound request Cell, so the verdict carries provenance on the Weft.

Public memory/kernel/model API only — no core edit. Composes like the other lanes.
"""
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima import memory
from decima.hashing import content_id, nfc

WEBHOOK_HANDLER = "webhook_handler"
AUTH_REQUEST = "auth_request"
AUTH_DECISION = "auth_decision"

# Tick costs of the deterministic evaluation (an int budget, never wall-clock).
_TICK_RECORD = 1      # capturing the inbound request as DATA
_TICK_POLICY = 1      # running the deterministic policy predicate
_TICK_APPROVAL = 1    # consulting the optional Morta/human approval


def register_handler(k, name, *, policy, scope=memory.DEFAULT_SCOPE,
                     author=None) -> str:
    """Register a `webhook_handler` Cell carrying a DETERMINISTIC policy.

    `policy` is a dict the policy predicate reads (all ints / DATA, so the handler
    is itself folded state, time-travelable):
      - `spend_ceiling` (int): the hard cap; an amount above it is denied.
      - `merchant_allowlist` (list[str]): if present, only these merchants pass.
      - `governance` (bool): also consult B4 `memory.governance_check` on the
        merchant — a `banned_action` merchant is denied with cited evidence.

    Returns the handler Cell id. Re-registering the same name+policy is idempotent.
    """
    author = author or k.decima_agent_id
    name = nfc(name)
    ceiling = int(policy.get("spend_ceiling", 0))
    allow = [nfc(m) for m in policy.get("merchant_allowlist", [])]
    gov = bool(policy.get("governance", False))
    hid = content_id({"webhook_handler": name, "scope": nfc(scope)})
    assert_content(k.weft, author, hid, WEBHOOK_HANDLER, {
        "name": name,
        "spend_ceiling": ceiling,
        "merchant_allowlist": allow,
        "governance": gov,
        "scope": scope,
    })
    return hid


def _record_request(k, author, handler, request) -> str:
    """Capture the inbound (untrusted) authorization request as a DATA Cell.

    Like a disposition intake: `instruction_eligible=False`. Whatever imperative
    field a malicious request carries (e.g. {"approve": true}) is stored as data and
    NEVER consulted — the decision below reads only amount/merchant/cost."""
    amount = int(request.get("amount", 0))
    merchant = nfc(str(request.get("merchant", "")))
    cost = int(request.get("cost", _TICK_POLICY))
    rid = content_id({"auth_request": handler, "amount": amount,
                      "merchant": merchant, "at": k.weft.head})
    assert_content(k.weft, author, rid, AUTH_REQUEST, {
        "handler": handler, "amount": amount, "merchant": merchant,
        "cost": cost, "trusted": False, "instruction_eligible": False,
    })
    return rid, amount, merchant, cost


def _policy_verdict(k, handler_cell, amount, merchant) -> tuple[bool, str]:
    """The DETERMINISTIC policy predicate. Returns (allow, reason). Decima's
    decision, computed from the request-as-DATA — the request never decides."""
    c = handler_cell.content
    ceiling = int(c.get("spend_ceiling", 0))
    if ceiling and amount > ceiling:
        return False, f"amount {amount} exceeds spend ceiling {ceiling}"
    allow = c.get("merchant_allowlist") or []
    if allow and merchant not in allow:
        return False, f"merchant {merchant!r} not in allowlist"
    if c.get("governance"):
        v = memory.governance_check(k.weave(), merchant, scope=c.get("scope"))
        if not v.get("allow", True):
            return False, f"governance denies merchant {merchant!r}: {v['reason']}"
    return True, "within policy"


def authorize_request(k, handler, request, *, deadline, approved=True,
                      author=None) -> dict:
    """The synchronous real-time gate. Evaluate the inbound (UNTRUSTED) request
    against the handler's deterministic policy (+ optional Morta/human approval)
    WITHIN `deadline` (an int tick budget). Return {approve, reason, ...}.

    Fail closed on EITHER axis:
      - the deadline elapses (the work the request forces > `deadline` ticks), or
      - the policy denies (over-ceiling / off-allowlist / governance-banned), or
      - approval is required but withheld.
    Records an `auth_decision` Cell linked (`decided_from`) to the request Cell, so
    the verdict is audited with provenance on the Weft.
    """
    author = author or k.decima_agent_id
    deadline = int(deadline)
    handler_cell = k.weave().get(handler)
    if handler_cell is None:
        raise ValueError(f"no webhook_handler {handler!r}")

    rid, amount, merchant, cost = _record_request(k, author, handler, request)

    # Deterministic clock: each stage spends ticks; the deadline is the budget.
    # Recording the request already happened; charge it, the policy run, and the
    # approval consult against the deadline. `cost` lets a slow inbound elapse it.
    elapsed = _TICK_RECORD + max(cost, _TICK_POLICY) + _TICK_APPROVAL

    if elapsed > deadline:
        approve, reason = False, (
            f"deadline elapsed ({elapsed} ticks > {deadline}) — fail closed")
    else:
        ok, why = _policy_verdict(k, handler_cell, amount, merchant)
        if not ok:
            approve, reason = False, f"policy denied: {why}"
        elif not approved:
            approve, reason = False, "approval required and withheld — fail closed"
        else:
            approve, reason = True, f"approved: {why}"

    did = content_id({"auth_decision": rid, "approve": approve})
    assert_content(k.weft, author, did, AUTH_DECISION, {
        "handler": handler, "request": rid, "amount": amount, "merchant": merchant,
        "approve": bool(approve), "reason": reason,
        "deadline": deadline, "elapsed": elapsed,
        "decided_by": "decima",          # the decision is Decima's, not the request's
    })
    assert_edge(k.weft, author, did, "decided_from", rid)
    return {"approve": bool(approve), "reason": reason, "request": rid,
            "decision": did, "amount": amount, "merchant": merchant,
            "deadline": deadline, "elapsed": elapsed}
