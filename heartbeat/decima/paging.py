"""Real incident/paging rail — wrap a REAL pager (PagerDuty / Opsgenie style), never
reimplement on-call escalation (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it — waking a human on-call is an irreversible
OUTWARD effect, and re-rolling an escalation platform is itself the liability. A paging
provider is just an HTTPS events API, so the real engine rides stdlib `urllib` with
**zero pip dependencies**: real engine, still pure-stdlib.

This wraps the pager behind the SAME spine the other outbound rails enforce — it
registers a COMMUNICATION, Morta-gated, budget-capped, idempotent effect via
`kernel.integrate_tool`. The receipt maps the provider's outcome to WEFT §8 status:
  - an accepted / triggered / created incident → SUCCEEDED, carrying the provider
                                                 `provider_ref` (the incident/dedup id)
                                                 and the idempotency key;
  - a bad service / routing key (4xx)          → FAILED (no incident was opened);
  - a network error / timeout                  → UNKNOWN (we cannot observe whether the
                                                 page fired — never fabricated as success
                                                 or failure, FOLD §11 #8).

GUARDRAILS (mirroring the comms / Stripe rails):
  - **alert text is UNTRUSTED DATA** — `summary` / `service` are normalized and carried
    as the incident payload only; they never become instructions and are never
    interpolated into anything executable. Only their length (an int) reaches the Weft.
  - **HTTPS-only** — refuses to send the routing key to a non-`https://` endpoint before
    any request (never leak the key in cleartext); a definite no-effect (FAILED).
  - **credentials via CRED1** — the routing/integration key lives in the secrets broker;
    the handler calls `broker.use_secret`, which applies the key INSIDE the broker (never
    returned, never logged, never on the Weft). The raw key never appears in the
    receipt/audit — dispense, don't disclose.
  - **Morta-gated + idempotent** — a page is denied until the capability is approved; a
    replay of the same idempotency_key (the provider dedup_key) returns the prior receipt
    and opens no second incident.
  - **Transport seam** — `trigger` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.
  - **ints, not floats** in signed content.

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json

from decima import executor
from decima.hashing import nfc

COMMUNICATION = "COMMUNICATION"
RESULT = "result"                                # the EffectReceipt cell type the kernel asserts
_SEVERITIES = ("critical", "high", "low")        # the allowed incident severities
_OK_STATUSES = ("success", "triggered", "created")


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
        "paging", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def trigger(secret_key: str, args: dict, *, transport=None) -> dict:
    """Trigger an incident on the pager, mapping the outcome to an EffectReceipt-shaped
    result. Raises `executor.ExecError` for a definite no-effect (non-HTTPS endpoint, bad
    request, bad service/routing key / 4xx → FAILED) and `executor.Ambiguous` for an
    unobservable outcome (network/unexpected → UNKNOWN). On success returns the output
    dict spread into a SUCCEEDED receipt, carrying the provider `provider_ref` (the
    incident / dedup id).

    `service` / `summary` are UNTRUSTED DATA: normalized and carried as the incident
    payload only, never interpreted as instructions; only `summary_len` (an int) reaches
    the Weft.

    HTTPS INVARIANT: a non-`https://` endpoint is refused before the routing key is put
    on the wire (no request is made)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the routing key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("paging: refusing to send the routing key to a non-HTTPS endpoint")

    severity = str(args.get("severity", ""))
    if severity not in _SEVERITIES:
        raise executor.ExecError(f"paging: severity must be one of {list(_SEVERITIES)}")
    service = nfc(str(args.get("service", "")))               # UNTRUSTED service ref (data only)
    if not service:
        raise executor.ExecError("paging: a service reference is required")
    summary = nfc(str(args.get("summary", "")))              # UNTRUSTED alert text (data only)
    if not summary:
        raise executor.ExecError("paging: a summary is required")
    dedup = nfc(str(args.get("dedup_key") or args.get("idempotency_key") or ""))

    # PagerDuty Events API v2 shape: routing_key + event_action + dedup_key + payload.
    # The routing key rides in the JSON body over HTTPS — on the wire, never on the Weft.
    fields = {
        "routing_key": secret_key,                          # applied here, never returned
        "event_action": "trigger",
        "payload": {"summary": summary, "severity": severity, "source": service},
    }
    if dedup:
        fields["dedup_key"] = dedup                          # provider-level de-dupe key
    body = json.dumps(fields)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"paging: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"paging: unparseable response (status {status_code})")
    provider_status = resp.get("status")
    if status_code in (200, 201, 202) and provider_status in _OK_STATUSES:
        provider_ref = resp.get("dedup_key") or resp.get("incident_id") or resp.get("id") or dedup
        return {"out": f"paged {severity} incident on {service}",
                "service": service, "severity": severity,
                "summary_len": int(len(summary)),            # int, not float — no alert text on the Weft
                "idempotency_key": dedup, "provider_ref": provider_ref,
                "provider_status": provider_status, "rail": "paging"}
    if status_code and 400 <= status_code < 500:             # bad service / routing key — definite no-effect
        msg = (resp.get("error", {}) or {}).get("message") or resp.get("message") \
            or provider_status or f"http {status_code}"
        raise executor.ExecError(f"paging: rejected — {msg}")
    raise executor.Ambiguous(f"paging: unexpected response (status {status_code}) — outcome unknown")


def find_page(weave, idempotency_key: str):
    """A prior SUCCEEDED incident receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each page; matching on the caller's dedup key makes a replay a no-op
    (mirrors `comms.find_message`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == COMMUNICATION
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "paging", endpoint: str, transport=None) -> str:
    """Register a REAL pager incident effect and grant Decima a COMMUNICATION capability
    to run it: a hard page cap (`budget`), Morta `requires_approval` (a page wakes a human,
    so a human/policy must approve), and a sandbox profile that allows ONLY this effect
    with network pinned to the rail. Returns the capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` (dedup key) returns without a second page —
    then asks the CRED1 broker to apply the routing key (`use_secret`) to the real
    trigger; the key never leaves the broker. `endpoint` is injected by the handler
    (never taken from caller args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or args.get("dedup_key") or ""))
        existing = find_page(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-page
            prev = existing.content
            return {"out": prev.get("out"), "service": prev.get("service"),
                    "severity": prev.get("severity"), "summary_len": prev.get("summary_len"),
                    "idempotency_key": idem, "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"), "rail": "paging",
                    "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: trigger(key, call_args, transport=transport))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"paging: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": COMMUNICATION,
        "budget": int(cap),                                  # hard cap on pages
        "requires_approval": True,                           # Morta gate — a page wakes a human
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
