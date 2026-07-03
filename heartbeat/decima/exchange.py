"""Real crypto-exchange order rail — wrap the real trading engine (dependency policy).

Decima's policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals
WRAP THE REAL ENGINE rather than reimplement it — placing a crypto trade is an
irreversible money movement (a mis-/double-placed order is real, unrecoverable harm), so
recreating exchange order execution is itself the liability. A Coinbase/Kraken-style
exchange is just an HTTPS API, so the real matching engine is reachable over stdlib
`urllib` with **zero pip dependencies**: the real engine, still pure-stdlib.

This is DISTINCT from the securities brokerage (`brokerage_engine.py`) — that rail trades
regulated securities; this one trades crypto — but it composes the SAME Morta-gated
financial spine. It registers a FINANCIAL, Morta-gated, spend-capped, idempotent effect
via `kernel.integrate_tool`; the args shape matches `payments.pay` (amount / payee /
idempotency_key / cost), so `payments.pay(k, agent, <this cap>, amount=<size>,
payee=<pair>, idempotency_key=<client_order_id>)` drives the REAL rail unchanged. The
receipt maps the exchange's outcome to WEFT §8 status:
  - a filled / accepted order   → SUCCEEDED, carrying the exchange `provider_ref` (the
                                  exchange order id), its status, and the filled size;
  - a definite rejection / 4xx  → FAILED (insufficient funds, bad pair, bad request —
                                  no order was placed);
  - a network error / timeout   → UNKNOWN (we cannot observe whether the order placed —
                                  never fabricated as filled or rejected, FOLD §11 #8).

GUARDRAILS (see the dependency-policy memory; mirrors brokerage_engine.py):
  - **SANDBOX / TEST MODE ONLY** in the reference — `place_order` refuses to touch a live
    venue before any request: (a) the endpoint host MUST be the exchange sandbox host
    (`api-public.sandbox.exchange.coinbase.com`), and (b) the API key MUST be a sandbox
    key (`sandbox_…`; a live key is refused). Either check fails closed BEFORE a request,
    so the reference can never route a real crypto trade. HTTPS-only, always.
  - **Credentials via CRED1** — the exchange key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Transport seam** — `place_order` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.

Sizes are INTS in the smallest unit (e.g. satoshis) — WEFT §1 forbids floats in signed
content. The `amount` from `payments.pay` doubles as the order `size` (an explicit
`args["size"]` overrides it) and drives the running notional spend cap.

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlparse

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
SANDBOX_HOST = "api-public.sandbox.exchange.coinbase.com"  # the sandbox venue — the only host allowed
SANDBOX_KEY_PREFIX = "sandbox_"                            # sandbox key ids start "sandbox_"; live do not
# Exchange order states that mean the order reached the book (a real, non-fabricated effect).
_OK_STATUSES = ("filled", "done", "settled", "open", "pending", "accepted", "partially_filled")


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
        "exchange", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def place_order(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Place a crypto order on the exchange, mapping the outcome to an EffectReceipt-shaped
    result. Raises `executor.ExecError` for a definite no-effect (bad request or rejection
    → FAILED) and `executor.Ambiguous` for an unobservable outcome (network/unexpected →
    UNKNOWN). On success returns the output dict spread into a SUCCEEDED receipt, carrying
    the exchange `provider_ref` (order id) and filled size.

    SANDBOX-MODE INVARIANT: a non-sandbox endpoint host or a live (non-`sandbox_`) key is
    refused BEFORE any request is made. HTTPS-only. The order endpoint arrives on
    `args["endpoint"]` (the rail's `install_rail` injects it)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):                   # HTTPS-only, always
        raise executor.ExecError("exchange: HTTPS-only orders endpoint required")
    if test_mode:
        host = urlparse(endpoint).hostname or ""
        if host != SANDBOX_HOST:                             # refuse a live/unknown venue, no request
            raise executor.ExecError(
                f"exchange: refusing a non-sandbox endpoint {host!r} (reference is SANDBOX-MODE ONLY)")
        if not str(secret_key).startswith(SANDBOX_KEY_PREFIX):  # refuse a live key, no request
            raise executor.ExecError(
                "exchange: refusing a non-sandbox (non-'sandbox_') key (reference is SANDBOX-MODE ONLY)")

    # `amount` doubles as order size (smallest unit); an explicit size overrides. INT, positive.
    size = args.get("size", args.get("amount"))
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise executor.ExecError("exchange: size must be a positive integer (smallest unit)")
    product = nfc(str(args.get("product") or args.get("payee") or "")).upper()
    if not product:
        raise executor.ExecError("exchange: a product/pair is required")
    side = str(args.get("side", "buy")).lower()
    if side not in ("buy", "sell"):
        raise executor.ExecError(f"exchange: side must be 'buy' or 'sell', not {side!r}")
    order_type = str(args.get("type", "market")).lower()
    client_order_id = str(args.get("idempotency_key") or "")

    body = json.dumps({
        "product_id": product, "side": side, "type": order_type,
        "size": str(size),                                   # smallest-unit size, as a string field
        "client_order_id": client_order_id,                  # exchange-level no-double-order
    })
    headers = {
        "CB-ACCESS-KEY": secret_key,                         # applied here, never returned
        "Content-Type": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"exchange: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"exchange: unparseable response (status {status_code})")
    order_status = resp.get("status")
    if status_code in (200, 201) and order_status in _OK_STATUSES:
        try:
            filled_size = int(resp.get("filled_size"))
        except (TypeError, ValueError):
            filled_size = size if order_status in ("filled", "done", "settled") else 0
        return {"out": f"{side} {size} {product} — {order_status}",
                "amount": size, "payee": product, "product": product, "size": size,
                "side": side, "order_type": order_type,
                "idempotency_key": client_order_id,
                "provider_ref": resp.get("id") or resp.get("order_id"),
                "provider_status": order_status,
                "filled_size": filled_size, "rail": "exchange"}
    if (isinstance(status_code, int) and 400 <= status_code < 500) \
            or order_status == "rejected" or resp.get("error"):
        msg = (resp.get("message") or resp.get("error") or order_status
               or f"http {status_code}")
        raise executor.ExecError(f"exchange: order rejected — {msg}")   # definite no-effect
    raise executor.Ambiguous(
        f"exchange: unexpected response (status {status_code}) — outcome unknown")


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "exchange", endpoint: str, transport=None,
                 test_mode: bool = True) -> str:
    """Register a REAL crypto-exchange order effect and grant Decima a FINANCIAL capability
    to run it. Same caveats as the PAY1 stub rail (notional spend cap, Morta
    `requires_approval`, sandbox pinned to the rail host), so `payments.pay(k, agent,
    <cap>, amount=<size>, payee=<pair>, idempotency_key=<client_order_id>)` uses it
    unchanged. On each invoke the handler asks the CRED1 broker to apply the exchange key
    (`use_secret`) — the key never leaves the broker — and injects the (sandbox) `endpoint`.
    Returns the capability id."""
    def handler(_impl, args):
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: place_order(key, {**args, "endpoint": endpoint},
                                    transport=transport, test_mode=test_mode))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"exchange: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                  # hard running notional cap
        "requires_approval": True,                           # Morta gate
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
