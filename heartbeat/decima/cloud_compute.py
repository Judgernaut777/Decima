"""Real cloud-compute rail — wrap a REAL compute provider (AWS EC2 / GCP style),
never reimplement provisioning (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it — provisioning a compute instance SPENDS MONEY
and is irreversible the instant it starts billing (a reservation is made, a machine is
booted, the meter runs), and re-rolling a provider is itself the liability. A compute
provider is just an HTTPS API, so the real engine rides stdlib `urllib` with **zero pip
dependencies**: real engine, still pure-stdlib.

This wraps the provider behind the SAME spine PAY1 already enforces — it registers a
FINANCIAL, Morta-gated, spend-capped (estimated compute spend), idempotent effect via
`kernel.integrate_tool`. The args shape matches `payments.pay` (amount / payee /
idempotency_key / cost), so `payments.pay(k, agent, <this cap>, amount=<est cost>,
payee=<instance_type>, idempotency_key=<key>)` drives the REAL rail unchanged
(amount → est_hourly_cost → cost → the running spend cap). The receipt maps the
provider's outcome to WEFT §8 status:
  - a running / pending instance → SUCCEEDED, carrying the provider `provider_ref` (the
                                   instance / reservation id), the `count`, and the
                                   idempotency key (the machine is committed — pending
                                   still bills, so it is a real irreversible effect);
  - a quota exceeded / invalid instance type (4xx) → FAILED (nothing was provisioned,
                                   no money committed);
  - a network error / timeout    → UNKNOWN (we cannot observe whether it provisioned —
                                   never fabricated as success or failure, FOLD §11 #8).

GUARDRAILS (mirroring the Stripe / shipping rails):
  - **TEST / SANDBOX MODE ONLY** in the reference. The invariant (fail closed, BEFORE
    any request, if `test_mode`): (a) the provider key must carry the sandbox prefix
    `sandbox_` — a live key raises; AND (b) the endpoint must be a sandbox endpoint
    (its host contains `sandbox`) — a production endpoint raises. So the reference can
    never boot a real, billed instance.
  - **HTTPS-only** — refuses to send the provider key to a non-`https://` endpoint
    before any request (never leak the key in cleartext); a definite no-effect (FAILED).
  - **credentials via CRED1** — the provider key lives in the secrets broker; the
    handler calls `broker.use_secret`, which applies the key INSIDE the broker (never
    returned, never logged, never on the Weft). The raw key never appears in the
    receipt/audit.
  - **Morta-gated + idempotent** — a provision is denied until the capability is
    approved; a replay of the same idempotency_key returns the prior receipt and
    provisions nothing twice.
  - **Transport seam** — `provision` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.
  - **ints, not floats** in signed content (est hourly cost minor units, instance count).

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlencode

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
RESULT = "result"                                # the EffectReceipt cell type the kernel asserts
_TEST_PREFIX = "sandbox_"                        # TEST/SANDBOX-MODE only — a live key is refused
_SANDBOX_MARKER = "sandbox"                      # the endpoint host must name a sandbox endpoint
_OK_STATUSES = ("running", "pending")            # a committed (billed) instance — even pending bills


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `provision` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level failure
    (DNS, timeout, TLS) raises — `provision` maps that to UNKNOWN. Never used by the
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


def provision(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Provision compute via the provider, mapping the outcome to an EffectReceipt-shaped
    result. Raises `executor.ExecError` for a definite no-effect (non-sandbox key,
    non-HTTPS or non-sandbox endpoint, quota exceeded / invalid instance type / 4xx →
    FAILED) and `executor.Ambiguous` for an unobservable outcome (network/unexpected →
    UNKNOWN). On success returns the output dict spread into a SUCCEEDED receipt, carrying
    the provider `provider_ref` (the instance/reservation id) and the `count`.

    TEST/SANDBOX-MODE INVARIANT: when `test_mode`, refuse BEFORE any request unless the
    key carries the `sandbox_` prefix AND the endpoint is a sandbox endpoint (host names
    `sandbox`) — so the reference can never boot a real, billed instance. HTTPS INVARIANT:
    a non-`https://` endpoint is refused before the key is put on the wire. Ints only in
    signed content (est hourly cost minor units, instance count)."""
    transport = transport or _urllib_transport
    if test_mode and not str(secret_key).startswith(_TEST_PREFIX):
        # Refuse to boot a real, billed instance from the reference. Fail closed, no request.
        raise executor.ExecError("cloud_compute: refusing a non-sandbox key (reference is SANDBOX-MODE ONLY)")

    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the provider key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("cloud_compute: refusing to send the provider key to a non-HTTPS endpoint")
    if test_mode and _SANDBOX_MARKER not in endpoint:
        # Refuse to provision against a production endpoint. Fail closed, no request.
        raise executor.ExecError("cloud_compute: refusing to provision against a non-sandbox endpoint (SANDBOX-MODE ONLY)")

    amount = args.get("amount")                               # est hourly cost, minor units (int)
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise executor.ExecError("cloud_compute: amount (est hourly cost minor units) must be a positive integer")
    instance_type = nfc(str(args.get("payee") or args.get("instance_type") or ""))
    if not instance_type:
        raise executor.ExecError("cloud_compute: an instance_type is required")
    region = nfc(str(args.get("region", "sandbox")))
    count = args.get("count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise executor.ExecError("cloud_compute: count must be a positive integer")
    idem = nfc(str(args.get("idempotency_key") or ""))

    fields = {
        "instance_type": instance_type, "region": region, "count": count,
        "est_hourly_cost": amount, "idempotency_key": idem,
    }
    payload = urlencode(fields)
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level no-double-provision
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, payload)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"cloud_compute: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"cloud_compute: unparseable response (status {status_code})")
    provider_status = str(resp.get("status", "")).lower()
    if status_code in (200, 201) and provider_status in _OK_STATUSES:
        provider_ref = resp.get("id") or resp.get("instance_id") or resp.get("reservation_id")
        count_out = resp.get("count", count)
        if not isinstance(count_out, int) or isinstance(count_out, bool) or count_out <= 0:
            count_out = count
        return {"out": f"provisioned {count_out}x {instance_type} in {region} (~{amount}/hr)",
                "amount": amount, "instance_type": instance_type, "region": region,
                "count": int(count_out), "est_hourly_cost": amount,
                "idempotency_key": idem, "provider_ref": provider_ref,
                "provider_status": resp.get("status"), "rail": "cloud_compute"}
    if status_code and 400 <= status_code < 500:             # quota exceeded / invalid instance type
        msg = (resp.get("error", {}) or {}).get("message") or resp.get("message") \
            or resp.get("status") or f"http {status_code}"
        raise executor.ExecError(f"cloud_compute: rejected — {msg}")   # definite no-effect
    raise executor.Ambiguous(f"cloud_compute: unexpected response (status {status_code}) — outcome unknown")


def find_instance(weave, idempotency_key: str):
    """A prior SUCCEEDED provision receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each provision; matching on the caller's key makes a replay a no-op
    (mirrors `payments.find_payment`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == FINANCIAL
                and rc.get("rail") == "cloud_compute"
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "compute", endpoint: str, transport=None,
                 test_mode: bool = True) -> str:
    """Register a REAL compute-provision effect and grant Decima a FINANCIAL capability to
    run it: a hard estimated-spend cap (`budget`), Morta `requires_approval` (provisioning
    spends money and boots a billed machine, so a human/policy must approve), and a sandbox
    profile that allows ONLY this effect with network pinned to the rail. The args shape
    matches `payments.pay`, so `payments.pay(k, agent, <cap>, amount=<est cost>,
    payee=<instance_type>, idempotency_key=<key>)` drives it unchanged (amount →
    est_hourly_cost → cost → the running spend cap). Returns the capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without a second provision — then asks
    the CRED1 broker to apply the provider key (`use_secret`) to the real provision; the
    key never leaves the broker. `endpoint` is injected by the handler (never taken from
    caller args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_instance(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-provision
            prev = existing.content
            return {"out": prev.get("out"), "amount": prev.get("amount"),
                    "instance_type": prev.get("instance_type"), "region": prev.get("region"),
                    "count": prev.get("count"), "est_hourly_cost": prev.get("est_hourly_cost"),
                    "idempotency_key": idem, "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"),
                    "rail": "cloud_compute", "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: provision(key, call_args, transport=transport,
                                                    test_mode=test_mode))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"cloud_compute: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                 # hard cap on estimated compute spend
        "requires_approval": True,                          # Morta gate — a provision spends money
        "sandbox": {"effects": [name], "network": True},    # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
