"""Real Stripe payment rail — the FIRST real external engine (dependency policy).

Decima's policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals
WRAP THE REAL ENGINE rather than reimplement it — recreating money movement is itself
the liability. Stripe is just an HTTPS API, so the real engine is reachable over stdlib
`urllib` with **zero pip dependencies**: real engine, still pure-stdlib.

This wraps Stripe behind the SAME spine PAY1 already enforces — it registers a
FINANCIAL, Morta-gated, spend-capped, idempotent effect via `kernel.integrate_tool`;
the args shape matches `payments.pay` (amount / payee / idempotency_key / cost), so
`payments.pay(k, agent, <this cap>, …)` drives the REAL rail unchanged. The receipt
maps Stripe's outcome to WEFT §8 status:
  - a confirmed charge         → SUCCEEDED, carrying the Stripe `provider_ref` (the
                                 PaymentIntent id) and the idempotency key;
  - a definite decline / 4xx   → FAILED (money did not move);
  - a network error / timeout  → UNKNOWN (we cannot observe whether it charged — never
                                 fabricated as success or failure, FOLD §11 #8).

GUARDRAILS (see the dependency-policy memory):
  - **TEST MODE ONLY** in the reference — `charge` refuses any key that is not
    `sk_test_…` (a live key raises before any request), so the reference can never move
    real money.
  - **Credentials via CRED1** — the Stripe key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Transport seam** — `charge` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlencode

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
STRIPE_URL = "https://api.stripe.com/v1/payment_intents"
_OK_STATUSES = ("succeeded", "requires_capture", "processing")


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `charge` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level failure
    (DNS, timeout, TLS) raises — `charge` maps that to UNKNOWN. Never used by the
    offline oracle (tests inject a fake transport)."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                       # 4xx/5xx carry a JSON body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": {"message": f"http {e.code}"}}


def charge(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Charge via Stripe, mapping the outcome to an EffectReceipt-shaped result. Raises
    `executor.ExecError` for a definite no-effect (bad request or decline → FAILED) and
    `executor.Ambiguous` for an unobservable outcome (network/unexpected → UNKNOWN). On
    success returns the output dict spread into a SUCCEEDED receipt.

    TEST-MODE INVARIANT: a non-`sk_test_` key is refused before any request is made."""
    transport = transport or _urllib_transport
    if test_mode and not str(secret_key).startswith("sk_test_"):
        # Refuse to move real money from the reference. Fail closed, no request.
        raise executor.ExecError("stripe: refusing a non-test key (reference is TEST-MODE ONLY)")

    amount = args.get("amount")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise executor.ExecError("stripe: amount must be a positive integer (minor units)")
    payee = nfc(str(args.get("payee", "")))
    if not payee:
        raise executor.ExecError("stripe: a payee is required")
    idem = str(args.get("idempotency_key") or "")
    currency = str(args.get("currency", "usd"))

    body = urlencode({
        "amount": amount, "currency": currency,
        "description": f"decima:{payee}",
        "confirm": "true", "payment_method": "pm_card_visa",   # Stripe test payment method
    })
    headers = {
        "Authorization": f"Bearer {secret_key}",               # applied here, never returned
        "Idempotency-Key": idem,                               # provider-level no-double-charge
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        status_code, resp = transport(STRIPE_URL, headers, body)
    except Exception as e:                                     # network/timeout — unobservable
        raise executor.Ambiguous(f"stripe: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"stripe: unparseable response (status {status_code})")
    if status_code == 200 and resp.get("status") in _OK_STATUSES:
        return {"out": f"charged {amount} {currency} to {payee}",
                "amount": amount, "payee": payee, "currency": currency,
                "idempotency_key": idem, "provider_ref": resp.get("id"),
                "provider_status": resp.get("status"), "rail": "stripe"}
    if resp.get("error") or resp.get("status") == "requires_payment_method":
        msg = (resp.get("error", {}) or {}).get("message") or resp.get("status") or f"http {status_code}"
        raise executor.ExecError(f"stripe: declined / rejected — {msg}")   # definite no-effect
    raise executor.Ambiguous(f"stripe: unexpected response (status {status_code}) — outcome unknown")


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "payment", transport=None, test_mode: bool = True) -> str:
    """Register a REAL Stripe payment effect and grant Decima a FINANCIAL capability to
    run it. Same caveats as the PAY1 stub rail (spend cap, Morta `requires_approval`,
    sandbox pinned to the rail host), so `payments.pay(k, agent, <cap>, …)` uses it
    unchanged. On each invoke the handler asks the CRED1 broker to apply the Stripe key
    (`use_secret`) — the key never leaves the broker. Returns the capability id."""
    def handler(_impl, args):
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: charge(key, args, transport=transport, test_mode=test_mode))
        if "denied" in r:                                     # handle revoked / unauthorized
            raise executor.ExecError(f"stripe: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                   # hard running spend cap
        "requires_approval": True,                            # Morta gate
        "sandbox": {"effects": [name], "network": True},      # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
