"""Real brokerage order rail — wrap the regulated execution engine (dependency policy).

Decima's policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals
WRAP THE REAL ENGINE rather than reimplement it — recreating securities order execution
is itself the liability (it is a regulated function; a mis-placed or double-placed order
is real financial harm). An Alpaca-style broker is just an HTTPS API, so the real
execution engine is reachable over stdlib `urllib` with **zero pip dependencies**: the
real, regulated engine, still pure-stdlib.

This wraps the broker behind the SAME spine PAY1 enforces — it registers a FINANCIAL,
Morta-gated, spend-capped, idempotent effect via `kernel.integrate_tool`; the args shape
matches `payments.pay` (amount / payee / idempotency_key / cost), so
`payments.pay(k, agent, <this cap>, amount=<notional/shares>, payee=<symbol>,
idempotency_key=<client_order_id>)` drives the REAL rail unchanged. The receipt maps the
broker's outcome to WEFT §8 status:
  - a filled / accepted order   → SUCCEEDED, carrying the broker `provider_ref` (the
                                  broker order id), its status, and the filled qty;
  - a definite rejection / 4xx  → FAILED (insufficient funds, bad symbol, bad request —
                                  no order was placed);
  - a network error / timeout   → UNKNOWN (we cannot observe whether the order placed —
                                  never fabricated as filled or rejected, FOLD §11 #8).

GUARDRAILS (see the dependency-policy memory; mirrors stripe_rail.py):
  - **PAPER MODE ONLY** in the reference — `place` refuses to touch a live venue before
    any request: (a) the endpoint host MUST be the paper-trading host
    (`paper-api.alpaca.markets`), and (b) the API key MUST be a paper key (`PK…`; a live
    `AK…` key is refused). Either check fails closed BEFORE a request, so the reference
    can never route a real securities order. HTTPS-only, always.
  - **Credentials via CRED1** — the broker key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Transport seam** — `place` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.

Amounts / quantities are INTS (WEFT §1 — no floats in signed content). The `amount` from
`payments.pay` doubles as the share `qty` (an explicit `args["qty"]` overrides it) and
drives the running notional spend cap.

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlparse

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
PAPER_HOST = "paper-api.alpaca.markets"   # the paper-trading venue — the only host allowed
PAPER_KEY_PREFIX = "PK"                   # Alpaca paper key ids start "PK"; live start "AK"
# Broker order states that mean the order reached the book (a real, non-fabricated effect).
_OK_STATUSES = ("filled", "partially_filled", "accepted", "new", "pending_new")


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `place` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level failure
    (DNS, timeout, TLS) raises — `place` maps that to UNKNOWN. Never used by the offline
    oracle (tests inject a fake transport)."""
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
            return e.code, {"message": f"http {e.code}"}


def place(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Place a securities order via the broker, mapping the outcome to an
    EffectReceipt-shaped result. Raises `executor.ExecError` for a definite no-effect (bad
    request or rejection → FAILED) and `executor.Ambiguous` for an unobservable outcome
    (network/unexpected → UNKNOWN). On success returns the output dict spread into a
    SUCCEEDED receipt, carrying the broker `provider_ref` (order id) and filled qty.

    PAPER-MODE INVARIANT: a non-paper endpoint host or a live (non-`PK`) key is refused
    BEFORE any request is made. HTTPS-only. The order endpoint arrives on `args["endpoint"]`
    (the rail's `install_rail` injects it)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):                   # HTTPS-only, always
        raise executor.ExecError("brokerage: HTTPS-only orders endpoint required")
    if test_mode:
        host = urlparse(endpoint).hostname or ""
        if host != PAPER_HOST:                               # refuse a live/unknown venue, no request
            raise executor.ExecError(
                f"brokerage: refusing a non-paper endpoint {host!r} (reference is PAPER-MODE ONLY)")
        if not str(secret_key).startswith(PAPER_KEY_PREFIX): # refuse a live key, no request
            raise executor.ExecError(
                "brokerage: refusing a non-paper (non-'PK') key (reference is PAPER-MODE ONLY)")

    # `amount` doubles as share qty; an explicit qty overrides. INT, positive.
    qty = args.get("qty", args.get("amount"))
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        raise executor.ExecError("brokerage: qty must be a positive integer (shares)")
    symbol = nfc(str(args.get("symbol") or args.get("payee") or "")).upper()
    if not symbol:
        raise executor.ExecError("brokerage: a symbol is required")
    side = str(args.get("side", "buy")).lower()
    if side not in ("buy", "sell"):
        raise executor.ExecError(f"brokerage: side must be 'buy' or 'sell', not {side!r}")
    order_type = str(args.get("type", "market")).lower()
    tif = str(args.get("time_in_force", "day")).lower()
    client_order_id = str(args.get("idempotency_key") or "")

    body = json.dumps({
        "symbol": symbol, "qty": str(qty), "side": side, "type": order_type,
        "time_in_force": tif,
        "client_order_id": client_order_id,                  # broker-level no-double-order
    })
    headers = {
        "APCA-API-KEY-ID": secret_key,                       # applied here, never returned
        "APCA-API-SECRET-KEY": secret_key,                   # paired secret (held in the broker)
        "Content-Type": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"brokerage: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"brokerage: unparseable response (status {status_code})")
    order_status = resp.get("status")
    if status_code in (200, 201) and order_status in _OK_STATUSES:
        try:
            filled_qty = int(resp.get("filled_qty"))
        except (TypeError, ValueError):
            filled_qty = qty if order_status == "filled" else 0
        return {"out": f"{side} {qty} {symbol} — {order_status}",
                "amount": qty, "payee": symbol, "symbol": symbol, "qty": qty,
                "side": side, "order_type": order_type,
                "idempotency_key": client_order_id,
                "provider_ref": resp.get("id"), "provider_status": order_status,
                "filled_qty": filled_qty, "rail": "brokerage"}
    if (isinstance(status_code, int) and 400 <= status_code < 500) \
            or order_status == "rejected" or resp.get("error"):
        msg = (resp.get("message") or resp.get("error") or order_status
               or f"http {status_code}")
        raise executor.ExecError(f"brokerage: order rejected — {msg}")   # definite no-effect
    raise executor.Ambiguous(
        f"brokerage: unexpected response (status {status_code}) — outcome unknown")


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "brokerage", endpoint: str, transport=None,
                 test_mode: bool = True) -> str:
    """Register a REAL brokerage order effect and grant Decima a FINANCIAL capability to
    run it. Same caveats as the PAY1 stub rail (notional spend cap, Morta
    `requires_approval`, sandbox pinned to the rail host), so `payments.pay(k, agent,
    <cap>, amount=<shares>, payee=<symbol>, idempotency_key=<client_order_id>)` uses it
    unchanged. On each invoke the handler asks the CRED1 broker to apply the broker key
    (`use_secret`) — the key never leaves the broker — and injects the (paper) `endpoint`.
    Returns the capability id."""
    def handler(_impl, args):
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: place(key, {**args, "endpoint": endpoint},
                              transport=transport, test_mode=test_mode))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"brokerage: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                  # hard running notional cap
        "requires_approval": True,                           # Morta gate
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
