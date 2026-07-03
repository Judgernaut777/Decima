"""Real ride / delivery dispatch rail — wrap a REAL dispatcher (Uber / Lyft / DoorDash
style), never reimplement dispatching a driver (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it — requesting a ride or a delivery SPENDS MONEY and
DISPATCHES A REAL-WORLD ACTION (a car rolls to a curb, a courier is sent), which is
irreversible and re-rolling a driver is itself the liability. A dispatch provider is just
an HTTPS API, so the real engine rides stdlib `urllib` with **zero pip dependencies**:
real engine, still pure-stdlib.

This wraps the dispatcher behind the SAME spine PAY1 already enforces — it registers a
FINANCIAL, Morta-gated, spend-capped (fare spend), idempotent effect via
`kernel.integrate_tool`. The args shape matches `payments.pay` (amount / payee /
idempotency_key / cost), so `payments.pay(k, agent, <this cap>, amount=<fare_estimate>,
payee=<dropoff_ref>, idempotency_key=<key>)` drives the REAL rail unchanged
(amount → fare_estimate → cost → the running spend cap; payee → the drop-off ref). The
receipt maps the dispatcher's outcome to WEFT §8 status:
  - dispatched / accepted (a driver is assigned) → SUCCEEDED, carrying the dispatcher
                                  `provider_ref` (the trip / delivery id), the `eta_min`
                                  and `fare`, and the idempotency key;
  - no drivers / invalid request (4xx) → FAILED (no ride was dispatched, no money moved);
  - a network error / timeout   → UNKNOWN (we cannot observe whether it dispatched —
                                  never fabricated as success or failure, FOLD §11 #8).

GUARDRAILS (mirroring the Stripe / shipping rails):
  - **TEST MODE ONLY** in the reference — `request_ride` refuses any key that is not
    prefixed `rt_test_` (a live key raises BEFORE any request), so the reference can never
    dispatch a real car or courier. (Ride/delivery APIs have no single documented test
    prefix; we standardise on the documented `rt_test_` — "ride token, test" — prefix.)
  - **HTTPS-only** — refuses to send the dispatcher key to a non-`https://` endpoint
    before any request (never leak the key in cleartext); a definite no-effect (FAILED).
  - **credentials via CRED1** — the dispatcher key lives in the secrets broker; the
    handler calls `broker.use_secret`, which applies the key INSIDE the broker (never
    returned, never logged, never on the Weft). The raw key never appears in the
    receipt / audit.
  - **Morta-gated + idempotent** — a request is denied until the capability is approved;
    a replay of the same idempotency_key returns the prior receipt and dispatches nothing
    twice.
  - **Transport seam** — `request_ride` takes a `transport(url, headers, body) ->
    (status, json)`. The default is a real `urllib` POST; tests inject a fake transport,
    so the offline oracle exercises the full contract with NO network.
  - **ints, not floats** in signed content (fare minor units, ETA minutes).

Pure composition (executor / secrets / manifest / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlencode

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
RESULT = "result"                                # the EffectReceipt cell type the kernel asserts
_TEST_PREFIX = "rt_test_"                        # TEST-MODE only — a live key is refused
_OK_STATUSES = ("dispatched", "accepted", "assigned", "driver_assigned", "confirmed")


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
        "ride", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def request_ride(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Request a ride / delivery dispatch, mapping the outcome to an EffectReceipt-shaped
    result. Raises `executor.ExecError` for a definite no-effect (non-test key, non-HTTPS
    endpoint, no drivers / invalid request / 4xx → FAILED) and `executor.Ambiguous` for an
    unobservable outcome (network/unexpected → UNKNOWN). On success returns the output dict
    spread into a SUCCEEDED receipt, carrying the dispatcher `provider_ref` (the trip /
    delivery id), the `eta_min` and the `fare`.

    Args (payments.pay-compatible): `amount` (fare estimate, minor units, int) doubles as
    `fare_estimate`; `payee` doubles as `dropoff_ref`. Also honored: `pickup_ref`,
    `product` (or `class`), and `idempotency_key`.

    TEST-MODE INVARIANT: a non-`rt_test_` (live) key is refused before any request is made.
    HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on the
    wire. Ints only in signed content (fare minor units, ETA minutes)."""
    transport = transport or _urllib_transport
    if test_mode and not str(secret_key).startswith(_TEST_PREFIX):
        # Refuse to dispatch a real car/courier from the reference. Fail closed, no request.
        raise executor.ExecError("ride: refusing a non-test key (reference is TEST-MODE ONLY)")

    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the dispatcher key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("ride: refusing to send the dispatcher key to a non-HTTPS endpoint")

    fare_estimate = args.get("fare_estimate", args.get("amount"))   # minor units (int)
    if not isinstance(fare_estimate, int) or isinstance(fare_estimate, bool) or fare_estimate <= 0:
        raise executor.ExecError("ride: fare_estimate (minor units) must be a positive integer")
    dropoff_ref = nfc(str(args.get("dropoff_ref") or args.get("payee") or ""))   # destination
    if not dropoff_ref:
        raise executor.ExecError("ride: a drop-off (dropoff_ref) is required")
    pickup_ref = nfc(str(args.get("pickup_ref", "")))              # origin ref (optional)
    product = nfc(str(args.get("product") or args.get("class") or "standard"))
    idem = nfc(str(args.get("idempotency_key") or ""))

    fields = {
        "pickup_ref": pickup_ref, "dropoff_ref": dropoff_ref,
        "product": product, "fare_estimate": fare_estimate,
    }
    payload = urlencode(fields)
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level no-double-dispatch
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, payload)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"ride: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"ride: unparseable response (status {status_code})")
    provider_status = str(resp.get("status", "")).lower()
    if status_code in (200, 201) and provider_status in _OK_STATUSES:
        provider_ref = resp.get("id") or resp.get("trip_id") or resp.get("delivery_id")
        eta = resp.get("eta_min", resp.get("eta_minutes", 0))
        fare = resp.get("fare", fare_estimate)
        # Ints only in signed content — coerce provider numerics, floor to int.
        eta_min = int(eta) if isinstance(eta, (int, float)) and not isinstance(eta, bool) else 0
        fare = int(fare) if isinstance(fare, (int, float)) and not isinstance(fare, bool) else fare_estimate
        return {"out": f"dispatched {product} ride to {dropoff_ref} (eta {eta_min}m, fare {fare})",
                "amount": fare, "fare": fare, "fare_estimate": fare_estimate,
                "pickup": pickup_ref, "dropoff": dropoff_ref, "payee": dropoff_ref,
                "product": product, "eta_min": eta_min,
                "idempotency_key": idem, "provider_ref": provider_ref,
                "provider_status": resp.get("status"), "rail": "ride"}
    if status_code and 400 <= status_code < 500:             # no drivers / invalid request
        msg = (resp.get("error", {}) or {}).get("message") or resp.get("message") \
            or resp.get("status") or f"http {status_code}"
        raise executor.ExecError(f"ride: rejected — {msg}")   # definite no-effect
    raise executor.Ambiguous(f"ride: unexpected response (status {status_code}) — outcome unknown")


def find_ride(weave, idempotency_key: str):
    """A prior SUCCEEDED dispatch receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each dispatch; matching on the caller's key makes a replay a no-op
    (mirrors `payments.find_payment` / `shipping.find_shipment`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == FINANCIAL
                and rc.get("rail") == "ride"
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "ride", endpoint: str, transport=None,
                 test_mode: bool = True) -> str:
    """Register a REAL ride/delivery dispatch effect and grant Decima a FINANCIAL
    capability to run it: a hard fare-spend cap (`budget`), Morta `requires_approval`
    (requesting a ride spends money and dispatches a real-world action, so a human/policy
    must approve), and a sandbox profile that allows ONLY this effect with network pinned
    to the rail. The args shape matches `payments.pay`, so `payments.pay(k, agent, <cap>,
    amount=<fare_estimate>, payee=<dropoff_ref>, idempotency_key=<key>)` drives it
    unchanged (amount → fare_estimate → cost → the running spend cap). Returns the
    capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without a second dispatch — then asks
    the CRED1 broker to apply the dispatcher key (`use_secret`) to the real request; the
    key never leaves the broker. `endpoint` is injected by the handler (never taken from
    caller args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_ride(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-dispatch
            prev = existing.content
            return {"out": prev.get("out"), "amount": prev.get("amount"),
                    "fare": prev.get("fare"), "fare_estimate": prev.get("fare_estimate"),
                    "pickup": prev.get("pickup"), "dropoff": prev.get("dropoff"),
                    "payee": prev.get("payee"), "product": prev.get("product"),
                    "eta_min": prev.get("eta_min"), "idempotency_key": idem,
                    "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"),
                    "rail": "ride", "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: request_ride(key, call_args, transport=transport,
                                                       test_mode=test_mode))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"ride: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                 # hard cap on fare spend
        "requires_approval": True,                          # Morta gate — a dispatch spends money
        "sandbox": {"effects": [name], "network": True},    # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)


def register_manifest(k) -> str:
    """Record a discoverable manifest for the ride/delivery dispatch engine (an EFFECT,
    FINANCIAL, Morta-gated capability). Registration confers NO authority (manifest.py) —
    the rail keeps its own gated `install_rail` path; this only makes the engine FINDABLE
    in `manifest.find` / discovery. Returns the manifest cell id."""
    from decima import manifest as M
    m = M.capability_manifest(
        "ride", title="ride",
        description="request a ride or delivery dispatch",
        archetype="EFFECT", effect_class=FINANCIAL,
        caveats={"requires_approval": True},
        tags=["transport", "delivery", "dispatch"])
    return M.register(k, m)
