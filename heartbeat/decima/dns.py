"""Real DNS / domains rail — wrap a REAL provider (Route53 / Namecheap style), never
reimplement DNS control (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it. Applying a DNS record — or registering a domain —
is an irreversible OUTWARD infrastructure effect: a live record change can take a service
down (or hijack a name) the instant it propagates, and a registration spends money.
Re-rolling a registrar/zone controller IS the liability. A DNS/domains provider is just
an HTTPS API, so the real engine rides stdlib `urllib` with **zero pip dependencies**:
real engine, still pure-stdlib.

This wraps the provider behind the SAME spine the payment rail already enforces, but as
an INFRA effect (a documented outward-effect class) rather than a FINANCIAL one. It
registers a Morta-gated, budget-capped, idempotent effect via `kernel.integrate_tool`;
the receipt maps the provider's outcome to WEFT §8 status:
  - an applied / pending change (200/201/202) → SUCCEEDED, carrying the provider
                                                `provider_ref` (the change id), the change
                                                status, the record name, ttl (int), and
                                                the idempotency key;
  - an invalid zone / record (definite 4xx)   → FAILED (no record changed);
  - a network error / timeout                 → UNKNOWN (we cannot observe whether the
                                                change applied — never fabricated as
                                                success or failure, FOLD §11 #8).

POLICY / GUARDRAILS (mirroring the Stripe / comms rails):
  - **wrap the real DNS engine** — zero pip deps; the real HTTPS provider over `urllib`.
  - **HTTPS-only** — refuses to put the provider key on a non-`https://` endpoint before
    any request is made (never leak the key in cleartext); a definite no-effect (FAILED).
  - **credentials via CRED1** — the provider key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in a receipt/audit.
  - **Morta-gated + idempotent** — a live DNS change `requires_approval`; denied until
    approved. A replay of the same idempotency_key returns the prior SUCCEEDED receipt and
    applies nothing twice (provider-level Idempotency-Key header too).
  - **record VALUES are UNTRUSTED DATA** — zone / record name / value / type are normalized
    and carried as the change payload only; they never become instructions.
  - **ints, not floats** in signed content (ttl is an int).
  - **transport seam** — `apply_record` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlencode

from decima import executor
from decima.hashing import nfc

INFRA = "INFRA"                                  # effect_class — outward infrastructure effect
RESULT = "result"                               # the EffectReceipt cell type the kernel asserts
_RECORD_TYPES = ("A", "AAAA", "CNAME", "TXT", "MX")
_OK_STATUSES = ("applied", "pending", "insync", "created")
_OK_CODES = (200, 201, 202)


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `apply_record` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level
    failure (DNS, timeout, TLS) raises — `apply_record` maps that to UNKNOWN. Never used
    by the offline oracle (tests inject a fake transport)."""
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


def apply_record(secret_key: str, args: dict, *, transport=None) -> dict:
    """Apply a DNS record change via the provider, mapping the outcome to an
    EffectReceipt-shaped result. Raises `executor.ExecError` for a definite no-effect
    (non-HTTPS endpoint, bad request, invalid zone/record / 4xx → FAILED) and
    `executor.Ambiguous` for an unobservable outcome (network/unexpected → UNKNOWN). On
    success returns the output dict spread into a SUCCEEDED receipt, carrying the provider
    `provider_ref` (the change id).

    `zone` / `name` / `value` / `type` are UNTRUSTED DATA: normalized and carried as the
    change payload only, never interpreted as instructions.

    HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on the
    wire (no request is made). TTL is an int, never a float."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the provider key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("dns: refusing to send the provider key to a non-HTTPS endpoint")

    rtype = str(args.get("type", "")).upper()
    if rtype not in _RECORD_TYPES:
        raise executor.ExecError(f"dns: record type must be one of {list(_RECORD_TYPES)}")
    zone = nfc(str(args.get("zone", "")))                     # UNTRUSTED zone (data only)
    if not zone:
        raise executor.ExecError("dns: a zone is required")
    name = nfc(str(args.get("name", "")))                     # UNTRUSTED record name (data only)
    if not name:
        raise executor.ExecError("dns: a record name is required")
    value = nfc(str(args.get("value", "")))                   # UNTRUSTED record value (data only)
    if not value:
        raise executor.ExecError("dns: a record value is required")
    ttl = args.get("ttl", 300)
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0:
        raise executor.ExecError("dns: ttl must be a positive integer (seconds)")
    idem = nfc(str(args.get("idempotency_key") or ""))

    fields = {"zone": zone, "name": name, "type": rtype, "value": value, "ttl": ttl}
    if idem:
        fields["idempotency_key"] = idem                     # provider-level de-dupe hint
    payload = urlencode(fields)
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if idem:
        headers["Idempotency-Key"] = idem                    # provider-level no-double-change
    try:
        status_code, resp = transport(endpoint, headers, payload)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"dns: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"dns: unparseable response (status {status_code})")
    provider_status = resp.get("status")
    if status_code in _OK_CODES and provider_status in _OK_STATUSES:
        provider_ref = resp.get("change_id") or resp.get("id") or resp.get("changeId")
        return {"out": f"applied {rtype} {name} in {zone}",
                "zone": zone, "name": name, "type": rtype,
                "ttl": int(ttl),                             # int, not float — value stays off the Weft
                "idempotency_key": idem, "provider_ref": provider_ref,
                "provider_status": provider_status, "rail": "dns"}
    if status_code and 400 <= status_code < 500:             # invalid zone / record — definite no-effect
        msg = (resp.get("error", {}) or {}).get("message") or resp.get("message") \
            or provider_status or f"http {status_code}"
        raise executor.ExecError(f"dns: rejected — {msg}")
    raise executor.Ambiguous(f"dns: unexpected response (status {status_code}) — outcome unknown")


def find_change(weave, idempotency_key: str):
    """A prior SUCCEEDED DNS-change receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each apply; matching on the caller's key makes a replay a no-op
    (mirrors `comms.find_message`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == INFRA
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "dns", endpoint: str, transport=None) -> str:
    """Register a REAL DNS-provider change effect and grant Decima an INFRA capability to
    run it: a hard change cap (`budget`), Morta `requires_approval` (a live DNS change is
    an irreversible outward effect, so a human/policy must approve), and a sandbox profile
    that allows ONLY this effect with network pinned to the rail. Returns the capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without a second apply — then asks the
    CRED1 broker to apply the provider key (`use_secret`) to the real change; the key never
    leaves the broker. `endpoint` is injected by the handler (never taken from caller
    args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_change(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-apply
            prev = existing.content
            return {"out": prev.get("out"), "zone": prev.get("zone"),
                    "name": prev.get("name"), "type": prev.get("type"),
                    "ttl": prev.get("ttl"), "idempotency_key": idem,
                    "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"), "rail": "dns",
                    "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: apply_record(key, call_args, transport=transport))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"dns: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": INFRA,
        "budget": int(cap),                                  # hard cap on changes
        "requires_approval": True,                           # Morta gate — a live DNS change goes outward
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
