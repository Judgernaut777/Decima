"""Real payout / ACH bank-transfer rail — money OUT, the most irreversible effect.

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it — recreating money movement is itself the
liability. A payout provider (Stripe Payouts / an ACH bank transfer) is just an HTTPS
API, so the real engine is reachable over stdlib `urllib` with **zero pip dependencies**:
real engine, still pure-stdlib.

A payout is the OUTBOUND twin of the Stripe charge rail: a charge is money IN, a payout
is money OUT to a bank account. Money leaving the box to a destination account is even
harder to claw back than a charge, so it composes the SAME spine — it registers a
FINANCIAL, Morta-gated, spend-capped, idempotent effect via `kernel.integrate_tool`; the
args shape matches `payments.pay` (amount / payee→destination / idempotency_key / cost),
so `payments.pay(k, agent, <this cap>, …)` drives the REAL rail unchanged. The receipt
maps the provider's outcome to WEFT §8 status:
  - a created / paid / pending payout → SUCCEEDED, carrying the provider `provider_ref`
                                        (the payout id), the destination, and the key;
  - a definite 4xx (insufficient      → FAILED (money did not leave the box);
    balance / invalid destination)
  - a network error / timeout         → UNKNOWN (we cannot observe whether the money
                                        moved — never fabricated as success or failure,
                                        FOLD §11 #8).

GUARDRAILS (see the dependency-policy memory):
  - **TEST MODE ONLY** in the reference — `payout` refuses any key that is not
    `sk_test_…` (a live key raises before any request), so money can NEVER leave the
    reference to a real bank account.
  - **Credentials via CRED1** — the provider key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Transport seam** — `payout` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.
  - **HTTPS-only** and **ints only** in signed content (amount is minor units).

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlencode

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
PAYOUT_URL = "https://api.stripe.com/v1/payouts"
_OK_STATUSES = ("paid", "pending", "in_transit", "created")


def _urllib_transport(url: str, headers: dict, body: str):
    """(Phase 2 · GO LIVE) FAIL-CLOSED default — the bare stdlib socket default is
    GONE: the armed wire guard (decima/wire.py) refuses ungated egress anyway, so
    `transport=None` on the live path now refuses HERE, first, with the sanctioned
    path named. Build the wire-gated transport via
    `live_wire.gated_transport(k, agent_cell, cap_id)`
    (a granted, Morta-approved egress capability) and inject it as `transport=`.
    Injected fake transports (the offline oracle, every test-mode path) never
    resolve to this default and are unaffected."""
    from decima import live_wire
    raise live_wire.NoGatedTransport(
        "payouts", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def payout(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Send a payout (money OUT) via the provider, mapping the outcome to an
    EffectReceipt-shaped result. Raises `executor.ExecError` for a definite no-effect
    (bad request / insufficient balance / invalid destination → FAILED, money did not
    move) and `executor.Ambiguous` for an unobservable outcome (network/unexpected →
    UNKNOWN). On success returns the output dict spread into a SUCCEEDED receipt.

    TEST-MODE INVARIANT: a non-`sk_test_` key is refused before any request is made —
    money can never leave the reference to a real bank account."""
    transport = transport or _urllib_transport
    if test_mode and not str(secret_key).startswith("sk_test_"):
        # Refuse to move real money out of the reference. Fail closed, no request.
        raise executor.ExecError("payout: refusing a non-test key (reference is TEST-MODE ONLY)")

    amount = args.get("amount")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise executor.ExecError("payout: amount must be a positive integer (minor units)")
    destination = nfc(str(args.get("payee", "") or args.get("destination", "")))
    if not destination:
        raise executor.ExecError("payout: a destination account is required")
    idem = str(args.get("idempotency_key") or "")
    currency = str(args.get("currency", "usd"))

    body = urlencode({
        "amount": amount, "currency": currency,
        "destination": destination,                            # the bank account / connected acct ref
        "description": f"decima:payout:{destination}",
        "method": "standard",
    })
    headers = {
        "Authorization": f"Bearer {secret_key}",               # applied here, never returned
        "Idempotency-Key": idem,                               # provider-level no-double-payout
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        status_code, resp = transport(PAYOUT_URL, headers, body)
    except Exception as e:                                     # network/timeout — unobservable
        raise executor.Ambiguous(f"payout: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"payout: unparseable response (status {status_code})")
    if status_code == 200 and resp.get("status") in _OK_STATUSES:
        return {"out": f"paid out {amount} {currency} to {destination}",
                "amount": amount, "payee": destination, "destination": destination,
                "currency": currency, "idempotency_key": idem,
                "provider_ref": resp.get("id"), "provider_status": resp.get("status"),
                "rail": "payout"}
    if resp.get("error"):                                     # definite no-effect (insufficient / invalid)
        msg = (resp.get("error", {}) or {}).get("message") or f"http {status_code}"
        raise executor.ExecError(f"payout: rejected — {msg}")
    raise executor.Ambiguous(f"payout: unexpected response (status {status_code}) — outcome unknown")


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "payout", endpoint: str = PAYOUT_URL,
                 transport=None, test_mode: bool = True) -> str:
    """Register a REAL payout effect (money OUT) and grant Decima a FINANCIAL capability
    to run it. Same caveats as the PAY1 stub rail (hard running spend cap, Morta
    `requires_approval`, sandbox pinned to the rail host), so `payments.pay(k, agent,
    <cap>, amount=…, payee=<destination>, idempotency_key=…)` drives it unchanged
    (amount→cost drives the running spend cap; payee→destination). On each invoke the
    handler asks the CRED1 broker to apply the provider key (`use_secret`) — the key never
    leaves the broker. Returns the capability id."""
    def handler(_impl, args):
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: payout(key, args, transport=transport, test_mode=test_mode))
        if "denied" in r:                                     # handle revoked / unauthorized
            raise executor.ExecError(f"payout: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                   # hard running spend cap
        "requires_approval": True,                            # Morta gate
        "sandbox": {"effects": [name], "network": True, "endpoint": endpoint},  # egress pinned to the rail
    }
    return k.integrate_tool(name, handler, caveats=caveats)
