"""Real e-commerce order rail — wrap a REAL order/fulfilment API (Shopify / Amazon
SP-API style), never reimplement placing an order (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it — placing an order SPENDS MONEY and creates an
irreversible effect (the platform charges the buyer, reserves inventory and dispatches
fulfilment; you cannot un-place an order, only refund/cancel after the fact), so it is a
FINANCIAL OUTWARD effect and re-rolling an order is itself the liability. An order API is
just an HTTPS endpoint, so the real engine rides stdlib `urllib` with **zero pip
dependencies**: real engine, still pure-stdlib.

This wraps the platform behind the SAME spine PAY1 already enforces — it registers a
FINANCIAL, Morta-gated, spend-capped (order value), idempotent effect via
`kernel.integrate_tool`. The args shape matches `payments.pay` (amount / payee /
idempotency_key / cost), so `payments.pay(k, agent, <this cap>, amount=<total>,
payee=<order/ship-to ref>, idempotency_key=<key>)` drives the REAL rail unchanged
(amount → total → cost → the running spend cap); the order's line items ride on
`args["lines"]` (each a {sku, qty:int, unit_price:int} in minor units). The receipt
maps the platform's outcome to WEFT §8 status:
  - a created / confirmed order → SUCCEEDED, carrying the platform `provider_ref` (the
                                  order id), the reconciled `total` (int minor units) and
                                  `item_count` (units ordered), and the idempotency key;
  - out of stock / bad sku / bad request (4xx) → FAILED (no order was placed, no charge);
  - a network error / timeout   → UNKNOWN (we cannot observe whether the order was
                                  placed — never fabricated as success or failure,
                                  FOLD §11 #8).

GUARDRAILS (mirroring the Stripe / shipping rails):
  - **TEST MODE ONLY** in the reference — `place_order` refuses any key that is not a
    `test_…` key (a live key raises BEFORE any request), so the reference can never place
    a real, money-moving order.
  - **HTTPS-only** — refuses to send the platform key to a non-`https://` endpoint before
    any request (never leak the key in cleartext); a definite no-effect (FAILED).
  - **TOTALS RECONCILE** — the order `total` (int minor units) MUST equal the sum of
    `qty * unit_price` over the line items; a mismatch is refused BEFORE any request (a
    definite no-effect), so the box can never sign a spend that does not add up.
  - **credentials via CRED1** — the platform key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Morta-gated + idempotent** — an order is denied until the capability is approved; a
    replay of the same idempotency_key returns the prior receipt and places nothing twice.
  - **Transport seam** — `place_order` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.
  - **ints, not floats** in signed content (unit prices + total in minor units, qty).

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
RESULT = "result"                                # the EffectReceipt cell type the kernel asserts
_TEST_PREFIX = "test_"                           # TEST-MODE only — a live key is refused
_OK_STATUSES = ("created", "confirmed", "paid", "open", "success")


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `place_order` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level failure
    (DNS, timeout, TLS) raises — `place_order` maps that to UNKNOWN. Never used by the
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


def _normalize_lines(raw) -> list:
    """Validate + normalize the order's line items. Each line must carry a non-empty
    `sku`, a positive integer `qty`, and a non-negative integer `unit_price` (minor
    units). Floats/bools are rejected (ints only in signed content). Raises
    `executor.ExecError` (a definite no-effect) on any bad line — before any request."""
    if not isinstance(raw, list) or not raw:
        raise executor.ExecError("ecommerce: an order requires a non-empty list of line items")
    lines = []
    for i, ln in enumerate(raw):
        if not isinstance(ln, dict):
            raise executor.ExecError(f"ecommerce: line {i} must be an object")
        sku = nfc(str(ln.get("sku") or ""))
        if not sku:
            raise executor.ExecError(f"ecommerce: line {i} requires a sku")
        qty = ln.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            raise executor.ExecError(f"ecommerce: line {i} qty must be a positive integer")
        unit_price = ln.get("unit_price")
        if not isinstance(unit_price, int) or isinstance(unit_price, bool) or unit_price < 0:
            raise executor.ExecError(f"ecommerce: line {i} unit_price must be a non-negative integer (minor units)")
        lines.append({"sku": sku, "qty": qty, "unit_price": unit_price})
    return lines


def place_order(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Place an order via the platform, mapping the outcome to an EffectReceipt-shaped
    result. Raises `executor.ExecError` for a definite no-effect (non-test key, non-HTTPS
    endpoint, unbalanced total, bad line item, out-of-stock / bad-sku / 4xx → FAILED) and
    `executor.Ambiguous` for an unobservable outcome (network/unexpected → UNKNOWN). On
    success returns the output dict spread into a SUCCEEDED receipt, carrying the platform
    `provider_ref` (the order id), the reconciled `total` and the `item_count`.

    TEST-MODE INVARIANT: a non-`test_` (live) key is refused before any request is made.
    HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on the
    wire. RECONCILE INVARIANT: `total` MUST equal sum(qty*unit_price) over the line items
    BEFORE any request — the box never signs a spend that does not add up. Ints only in
    signed content (unit prices + total in minor units, qty)."""
    transport = transport or _urllib_transport
    if test_mode and not str(secret_key).startswith(_TEST_PREFIX):
        # Refuse to place a real, money-moving order from the reference. Fail closed, no request.
        raise executor.ExecError("ecommerce: refusing a non-test key (reference is TEST-MODE ONLY)")

    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the platform key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("ecommerce: refusing to send the platform key to a non-HTTPS endpoint")

    ship_to = nfc(str(args.get("ship_to") or args.get("shipping_address") or args.get("payee") or ""))
    if not ship_to:
        raise executor.ExecError("ecommerce: a shipping address ref is required")

    # total in minor units (int). `payments.pay` sends the value as `amount`; a direct
    # invoke may send an explicit `total`. Either way it is validated as an int.
    total = args.get("total", args.get("amount"))
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        raise executor.ExecError("ecommerce: total must be a positive integer (minor units)")

    raw_lines = args.get("lines")
    if raw_lines is None:
        # payments.pay compatibility: with no explicit line items, model the order as a
        # single line for `total`, so `payments.pay(amount=<total>, payee=<ref>)` drives
        # the real rail unchanged and the total trivially reconciles.
        lines = [{"sku": nfc(str(args.get("payee") or "order")), "qty": 1, "unit_price": total}]
    else:
        lines = _normalize_lines(raw_lines)

    # RECONCILE — the signed total MUST equal the sum over the line items. Before any request.
    computed = sum(ln["qty"] * ln["unit_price"] for ln in lines)
    if computed != total:
        raise executor.ExecError(
            f"ecommerce: total {total} does not reconcile with line items (sum {computed})")
    item_count = sum(ln["qty"] for ln in lines)

    idem = nfc(str(args.get("idempotency_key") or ""))
    body = json.dumps({
        "line_items": lines, "total": total, "currency": str(args.get("currency", "usd")),
        "ship_to": ship_to, "idempotency_key": idem,
    })
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level no-double-order
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"ecommerce: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"ecommerce: unparseable response (status {status_code})")
    provider_status = str(resp.get("status", "")).lower()
    if status_code in (200, 201) and provider_status in _OK_STATUSES:
        provider_ref = resp.get("id") or resp.get("order_id") or resp.get("name")
        return {"out": f"placed order of {item_count} item(s) to {ship_to} for {total}",
                "total": int(total), "item_count": int(item_count), "ship_to": ship_to,
                "idempotency_key": idem, "provider_ref": provider_ref,
                "provider_status": resp.get("status"), "rail": "ecommerce"}
    if status_code and 400 <= status_code < 500:             # out of stock / bad sku / bad request
        msg = (resp.get("error", {}) or {}).get("message") or resp.get("message") \
            or resp.get("status") or f"http {status_code}"
        raise executor.ExecError(f"ecommerce: rejected — {msg}")   # definite no-effect
    raise executor.Ambiguous(f"ecommerce: unexpected response (status {status_code}) — outcome unknown")


def find_order(weave, idempotency_key: str):
    """A prior SUCCEEDED order receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each place an order; matching on the caller's key makes a replay a
    no-op (mirrors `payments.find_payment` / `shipping.find_shipment`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == FINANCIAL
                and rc.get("rail") == "ecommerce"
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "ecommerce", endpoint: str, transport=None,
                 test_mode: bool = True) -> str:
    """Register a REAL e-commerce order effect and grant Decima a FINANCIAL capability to
    run it: a hard order-value cap (`budget`), Morta `requires_approval` (placing an order
    spends money and dispatches fulfilment, so a human/policy must approve), and a sandbox
    profile that allows ONLY this effect with network pinned to the rail. The args shape
    matches `payments.pay`, so `payments.pay(k, agent, <cap>, amount=<total>,
    payee=<order/ship-to ref>, idempotency_key=<key>)` drives it unchanged (amount →
    total → cost → the running spend cap); line items ride on `args["lines"]`. Returns the
    capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without a second order — then asks the
    CRED1 broker to apply the platform key (`use_secret`) to the real order; the key never
    leaves the broker. `endpoint` is injected by the handler (never taken from caller
    args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_order(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-order
            prev = existing.content
            return {"out": prev.get("out"), "total": prev.get("total"),
                    "item_count": prev.get("item_count"), "ship_to": prev.get("ship_to"),
                    "idempotency_key": idem, "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"),
                    "rail": "ecommerce", "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: place_order(key, call_args, transport=transport,
                                                      test_mode=test_mode))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"ecommerce: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                 # hard cap on order value spend
        "requires_approval": True,                          # Morta gate — an order spends money
        "sandbox": {"effects": [name], "network": True},    # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
