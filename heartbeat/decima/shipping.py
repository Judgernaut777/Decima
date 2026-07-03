"""Real shipping rail — wrap a REAL postage/logistics carrier (EasyPost / Shippo style),
never reimplement buying postage (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it — buying a postage label SPENDS MONEY and creates
an irreversible shipment (a carrier is dispatched, a tracking number is minted), so it is
a financial-ish OUTWARD effect, and re-rolling a carrier is itself the liability. A
shipping provider is just an HTTPS API, so the real engine rides stdlib `urllib` with
**zero pip dependencies**: real engine, still pure-stdlib.

This wraps the carrier behind the SAME spine PAY1 already enforces — it registers a
FINANCIAL, Morta-gated, spend-capped (postage spend), idempotent effect via
`kernel.integrate_tool`. The args shape matches `payments.pay` (amount / payee /
idempotency_key / cost), so `payments.pay(k, agent, <this cap>, amount=<postage>,
payee=<to-address ref>, idempotency_key=<key>)` drives the REAL rail unchanged
(amount → cost → the running spend cap). The receipt maps the carrier's outcome to
WEFT §8 status:
  - a purchased / created label → SUCCEEDED, carrying the carrier `provider_ref` (the
                                  shipment / label id), the `tracking_code`, and the
                                  idempotency key;
  - a bad address / insufficient funds (4xx) → FAILED (no label was bought, no money moved);
  - a network error / timeout   → UNKNOWN (we cannot observe whether it bought — never
                                  fabricated as success or failure, FOLD §11 #8).

GUARDRAILS (mirroring the Stripe / comms rails):
  - **TEST MODE ONLY** in the reference — `buy_label` refuses any key that is not
    `shippo_test_…` (a live key raises BEFORE any request), so the reference can never buy
    real postage. (Shippo's real test keys carry the `shippo_test_` prefix; EasyPost uses
    an `EZTK…` test key — we standardise on the documented `shippo_test_` prefix here.)
  - **HTTPS-only** — refuses to send the carrier key to a non-`https://` endpoint before
    any request (never leak the key in cleartext); a definite no-effect (FAILED).
  - **credentials via CRED1** — the carrier key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Morta-gated + idempotent** — a buy is denied until the capability is approved; a
    replay of the same idempotency_key returns the prior receipt and buys nothing twice.
  - **Transport seam** — `buy_label` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.
  - **ints, not floats** in signed content (postage minor units, parcel weight grams).

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlencode

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
RESULT = "result"                                # the EffectReceipt cell type the kernel asserts
_TEST_PREFIX = "shippo_test_"                    # TEST-MODE only — a live key is refused
_OK_STATUSES = ("purchased", "created", "bought", "success")


class ShippingError(Exception):
    """A rate-quote (READ) failure — a non-HTTPS endpoint, an unreachable/timed-out
    endpoint, or a provider 4xx/error body. The READ path fails closed (returns NO
    rates); it never fabricates a quote."""


def _require_int(name: str, v):
    """Guard that a price the engine will surface / carry is an int minor unit (never a
    float/bool). A carrier that returns a float rate is a contract violation — fail
    closed rather than let a float represent money."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise ShippingError(f"{name} must be an int (minor units / cents), got {v!r}")
    return int(v)


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
        "shipping", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def quote_rates(secret_key: str, args: dict, *, transport=None) -> dict:
    """Fetch shipping rates for a parcel from the REAL carrier — a READ (no money moves, no
    label bought), so it is NOT Morta-gated. POSTs the shipment spec over the transport and
    returns the carrier's rates normalized to:
        {rates: [{carrier, service, amount_cents:int, provider_ref}], provider_ref}
    Every rate price is an int in minor units (cents) — no floats ever represent money.

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the carrier key touches the
    wire. Raises `ShippingError` on a non-HTTPS endpoint, an unreachable endpoint, or a
    definite provider error (4xx / error body) — the caller fails closed (no rates)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the carrier key on the wire in cleartext. Refuse first.
        raise ShippingError("refusing to send the carrier key to a non-HTTPS endpoint")

    payload = json.dumps({
        "to_address": nfc(str(args.get("to_address") or args.get("payee") or "")),
        "from_address": nfc(str(args.get("from_address", ""))),
        "weight": args.get("weight", 0),
        "carrier": nfc(str(args.get("carrier", ""))),
    }, sort_keys=True, separators=(",", ":"))
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, payload)
    except Exception as e:                                    # network/timeout — unreachable
        raise ShippingError(f"rate endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise ShippingError(f"unparseable rate response (status {status_code})")
    if status_code == 200 and isinstance(resp.get("rates"), list):
        rates = []
        for r in resp["rates"]:
            if not isinstance(r, dict):
                raise ShippingError("carrier returned a non-dict rate")
            rates.append({
                "carrier": nfc(str(r.get("carrier", ""))),
                "service": nfc(str(r.get("service", ""))),
                # the money-bearing datum — a real rate — carried as int cents.
                "amount_cents": _require_int("amount_cents", r.get("amount_cents")),
                "provider_ref": r.get("rate_id") or r.get("id"),
            })
        return {"rates": rates,
                "provider_ref": resp.get("shipment_id") or resp.get("id")}
    err = (resp.get("error", {}) or {}).get("message") if isinstance(resp.get("error"), dict) \
        else resp.get("error")
    err = err or resp.get("message") or f"http {status_code}"
    raise ShippingError(f"carrier rejected the rate request: {err}")     # definite error


def quote(k, *, endpoint: str, request: dict, credential_handle: str, broker,
          agent_cell, transport=None) -> dict:
    """Quote shipping rates for a parcel — a READ that resolves the carrier key via CRED1
    (`broker.use_secret`, applied INSIDE the broker, never disclosed) and runs `quote_rates`
    against the HTTPS `endpoint`. Returns {rates: [...], provider_ref} on success, or
    {"denied": reason} on a denied credential or any engine error (non-HTTPS, unreachable,
    provider 4xx). A quote moves no money, so it is not Morta-gated and records no cell."""
    req = {**request, "endpoint": endpoint}
    try:
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: quote_rates(key, req, transport=transport))
    except ShippingError as e:
        return {"denied": f"shipping: {e}"}                   # fail closed — no rates
    if "denied" in r:
        return {"denied": r["denied"]}                        # credential handle denied
    return r["ok"]


def buy_label(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Buy a postage label via the carrier, mapping the outcome to an EffectReceipt-shaped
    result. Raises `executor.ExecError` for a definite no-effect (non-test key, non-HTTPS
    endpoint, bad address / insufficient funds / 4xx → FAILED) and `executor.Ambiguous`
    for an unobservable outcome (network/unexpected → UNKNOWN). On success returns the
    output dict spread into a SUCCEEDED receipt, carrying the carrier `provider_ref` (the
    shipment/label id) and the `tracking_code`.

    TEST-MODE INVARIANT: a non-`shippo_test_` (live) key is refused before any request is
    made. HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on
    the wire. Ints only in signed content (postage minor units, parcel weight grams)."""
    transport = transport or _urllib_transport
    if test_mode and not str(secret_key).startswith(_TEST_PREFIX):
        # Refuse to buy real postage from the reference. Fail closed, no request.
        raise executor.ExecError("shipping: refusing a non-test key (reference is TEST-MODE ONLY)")

    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the carrier key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("shipping: refusing to send the carrier key to a non-HTTPS endpoint")

    amount = args.get("amount")                               # postage in minor units (int)
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise executor.ExecError("shipping: amount (postage minor units) must be a positive integer")
    to_address = nfc(str(args.get("payee") or args.get("to_address") or ""))   # destination ref
    if not to_address:
        raise executor.ExecError("shipping: a destination (to-address ref) is required")
    from_address = nfc(str(args.get("from_address", "")))    # origin ref (optional)
    weight = args.get("weight", 0)                            # parcel weight in grams (int)
    if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
        raise executor.ExecError("shipping: parcel weight must be a non-negative integer (grams)")
    carrier = nfc(str(args.get("carrier", "USPS")))
    service = nfc(str(args.get("service", "Priority")))
    idem = nfc(str(args.get("idempotency_key") or ""))

    fields = {
        "amount": amount, "to_address": to_address, "from_address": from_address,
        "weight": weight, "carrier": carrier, "service": service,
    }
    payload = urlencode(fields)
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level no-double-buy
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, payload)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"shipping: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"shipping: unparseable response (status {status_code})")
    provider_status = str(resp.get("status", "")).lower()
    if status_code in (200, 201) and provider_status in _OK_STATUSES:
        provider_ref = resp.get("id") or resp.get("shipment_id") or resp.get("object_id")
        tracking_code = resp.get("tracking_code") or resp.get("tracking_number")
        return {"out": f"bought {carrier} {service} label to {to_address} for {amount}",
                "amount": amount, "to": to_address, "from": from_address,
                "weight": int(weight), "carrier": carrier, "service": service,
                "idempotency_key": idem, "provider_ref": provider_ref,
                "tracking_code": tracking_code, "provider_status": resp.get("status"),
                "rail": "shipping"}
    if status_code and 400 <= status_code < 500:             # bad address / insufficient funds
        msg = (resp.get("error", {}) or {}).get("message") or resp.get("message") \
            or resp.get("status") or f"http {status_code}"
        raise executor.ExecError(f"shipping: rejected — {msg}")   # definite no-effect
    raise executor.Ambiguous(f"shipping: unexpected response (status {status_code}) — outcome unknown")


def find_shipment(weave, idempotency_key: str):
    """A prior SUCCEEDED label receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each buy; matching on the caller's key makes a replay a no-op
    (mirrors `payments.find_payment`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == FINANCIAL
                and rc.get("rail") == "shipping"
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "shipping", endpoint: str, transport=None,
                 test_mode: bool = True) -> str:
    """Register a REAL carrier label-buy effect and grant Decima a FINANCIAL capability to
    run it: a hard postage-spend cap (`budget`), Morta `requires_approval` (buying a label
    spends money and dispatches a carrier, so a human/policy must approve), and a sandbox
    profile that allows ONLY this effect with network pinned to the rail. The args shape
    matches `payments.pay`, so `payments.pay(k, agent, <cap>, amount=<postage>,
    payee=<to-address ref>, idempotency_key=<key>)` drives it unchanged (amount → cost →
    the running spend cap). Returns the capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without a second buy — then asks the
    CRED1 broker to apply the carrier key (`use_secret`) to the real buy; the key never
    leaves the broker. `endpoint` is injected by the handler (never taken from caller
    args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_shipment(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-buy
            prev = existing.content
            return {"out": prev.get("out"), "amount": prev.get("amount"),
                    "to": prev.get("to"), "from": prev.get("from"),
                    "weight": prev.get("weight"), "carrier": prev.get("carrier"),
                    "service": prev.get("service"), "idempotency_key": idem,
                    "provider_ref": prev.get("provider_ref"),
                    "tracking_code": prev.get("tracking_code"),
                    "provider_status": prev.get("provider_status"),
                    "rail": "shipping", "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: buy_label(key, call_args, transport=transport,
                                                    test_mode=test_mode))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"shipping: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                 # hard cap on postage spend
        "requires_approval": True,                          # Morta gate — a buy spends money
        "sandbox": {"effects": [name], "network": True},    # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)


def register_manifest(k) -> str:
    """Record a discoverable manifest for the shipping/postage engine (an EFFECT,
    FINANCIAL, Morta-gated capability — buying a label spends money). Registration confers
    NO authority (manifest.py, Law) — the rail keeps its own gated `install_rail` path;
    this only makes the engine FINDABLE in `manifest.find` / `registry` for a real logistics
    goal before forging a new capability. Returns the manifest cell id."""
    from decima import manifest as M
    m = M.capability_manifest(
        "shipping", title="shipping",
        description="quote shipping rates and buy a postage label (carrier / logistics)",
        archetype="EFFECT", effect_class=FINANCIAL,
        caveats={"requires_approval": True},                # buying a label spends money
        source="builtin", version=1,
        tags=["shipping", "logistics", "postage", "carrier"])
    return M.register(k, m)
