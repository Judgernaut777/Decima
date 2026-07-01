"""Real ticketing / helpdesk rail — wrap a REAL support system, never reimplement it.

Decima's dependency policy: recreate the design in pure stdlib, but for HIGH-LIABILITY
externals WRAP THE REAL ENGINE rather than reinvent it. Filing a ticket / issue in a
real helpdesk or issue tracker (Zendesk / Jira / Freshdesk / GitHub Issues style) is an
OUTWARD effect — it creates a record a human will act on, notifies a queue, and cannot
be silently un-created — so re-rolling the desk IS the liability. A ticketing provider
is just an HTTPS API, so the real engine rides stdlib `urllib` with **zero pip deps**:
real engine, still pure-stdlib. This COMPLEMENTS `support.py` (which structures a desk
as a fold over the Weave) by actually filing the ticket in the external system.

This wraps the desk behind the SAME spine the Stripe / comms / esign rails already
enforce — it registers a COMMUNICATION, Morta-gated, budget-capped, idempotent effect
via `kernel.integrate_tool`; the receipt maps the provider's outcome to WEFT §8 status:
  - a created ticket/issue (200/201) → SUCCEEDED, carrying the provider `provider_ref`
                                       (the ticket id / issue key), status, priority, and
                                       the idempotency key;
  - an invalid project / 4xx         → FAILED (no ticket was filed);
  - a network error / timeout        → UNKNOWN (we cannot observe whether it filed —
                                       never fabricated as success or failure, FOLD §11 #8).

POLICY / GUARDRAILS (mirroring the Stripe rail):
  - **wrap the real desk** — zero pip deps; the real HTTPS provider over `urllib`.
  - **summary / description are UNTRUSTED DATA** — a ticket body is what a human (or a
    stranger) typed; it is normalized and carried as the ticket payload ONLY, never
    interpreted as an instruction, never interpolated into anything executable.
  - **HTTPS-only** — refuses to put the API key on a non-`https://` endpoint before any
    request is made (never leak the key in cleartext); a definite no-effect (FAILED).
  - **credentials via CRED1** — the API key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in a receipt / audit.
  - **Morta-gated** — filing a ticket `requires_approval`; denied until approved.
  - **idempotent** — a prior SUCCEEDED receipt for the same idempotency key returns
    without a second file (no duplicate ticket). Provider-level Idempotency-Key too.
  - **ints, not floats** in signed content (e.g. a description length is an int).
  - **transport seam** — `create_ticket` takes a `transport(url, headers, body) ->
    (status, json)`. The default is a real `urllib` POST; tests inject a fake transport,
    so the offline oracle exercises the full contract with NO network.

Pure composition (executor / secrets / manifest / kernel public APIs). No core edit.
"""
import json

from decima import executor, manifest
from decima.hashing import nfc

COMMUNICATION = "COMMUNICATION"   # effect_class — outward SUPPORT effect, not FINANCIAL
RESULT = "result"                 # the EffectReceipt cell type the kernel asserts
RAIL = "ticketing"
_OK_STATUSES = (200, 201)

# Priority bands — ints, not a continuous score; a ticket is discrete.
PRIORITIES = ("low", "normal", "high", "urgent")
DEFAULT_PRIORITY = "normal"


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `create_ticket` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level
    failure (DNS, timeout, TLS) raises — `create_ticket` maps that to UNKNOWN. Never
    used by the offline oracle (tests inject a fake transport)."""
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


def create_ticket(secret_key: str, args: dict, *, transport=None) -> dict:
    """File a ticket / issue in the real helpdesk, mapping the outcome to an
    EffectReceipt-shaped result. Raises `executor.ExecError` for a definite no-effect
    (non-HTTPS endpoint, missing project/summary, bad priority, or 4xx → FAILED, no
    ticket filed) and `executor.Ambiguous` for an unobservable outcome (network/timeout
    → UNKNOWN). On success returns the output dict spread into a SUCCEEDED receipt,
    carrying `provider_ref` (the ticket id / issue key), the provider status, and the
    priority.

    `summary` / `description` are UNTRUSTED DATA: normalized and carried as the ticket
    payload only, never interpreted as instructions.

    HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on the
    wire (no request is made)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the API key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("ticketing: refusing to send the API key to a non-HTTPS endpoint")

    # project / queue — which board the ticket lands on.
    project = nfc(str(args.get("project") or args.get("queue") or ""))
    if not project:
        raise executor.ExecError("ticketing: a project/queue is required")
    summary = nfc(str(args.get("summary", "")))              # UNTRUSTED — DATA only
    if not summary:
        raise executor.ExecError("ticketing: a summary is required")
    description = nfc(str(args.get("description", "")))      # UNTRUSTED — DATA only
    priority = nfc(str(args.get("priority") or DEFAULT_PRIORITY))
    if priority not in PRIORITIES:
        raise executor.ExecError(f"ticketing: priority must be one of {list(PRIORITIES)}")
    idem = nfc(str(args.get("idempotency_key") or ""))

    payload = {
        "project": project,
        "summary": summary,                                  # UNTRUSTED DATA (the title)
        "description": description,                          # UNTRUSTED DATA (the body)
        "priority": priority,
    }
    if idem:
        payload["idempotency_key"] = idem                    # provider-level de-dupe hint
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level no-duplicate-file
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"ticketing: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"ticketing: unparseable response (status {status_code})")
    provider_ref = resp.get("id") or resp.get("key") or resp.get("ticket_id") or resp.get("issue_key")
    if status_code in _OK_STATUSES and provider_ref:
        return {"out": f"filed {priority} ticket in {project}",
                "project": project, "summary": summary,
                "description_len": int(len(description)),    # int, not float — no body on the Weft
                "priority": priority,
                "provider_ref": provider_ref,                # the ticket id / issue key
                "provider_status": str(resp.get("status", "open")),
                "idempotency_key": idem, "rail": RAIL}
    if status_code and 400 <= int(status_code) < 500:        # invalid project / bad request — no ticket
        msg = (resp.get("error", {}) or {}).get("message") if isinstance(resp.get("error"), dict) \
            else resp.get("error")
        msg = msg or resp.get("message") or f"http {status_code}"
        raise executor.ExecError(f"ticketing: rejected — {msg}")   # definite no-effect (FAILED)
    # 5xx / anything else after submission — we can't observe whether it filed.
    raise executor.Ambiguous(f"ticketing: unexpected response (status {status_code}) — outcome unknown")


def find_ticket(weave, idempotency_key: str):
    """A prior SUCCEEDED ticket receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each file a ticket; matching on the caller's key makes a replay a
    no-op — no duplicate ticket (mirrors `payments.find_payment`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == COMMUNICATION
                and rc.get("rail") == RAIL
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "ticketing", endpoint: str, transport=None) -> str:
    """Register a REAL ticketing effect and grant Decima a COMMUNICATION capability to
    run it: a hard `budget` cap (max tickets), Morta `requires_approval` (filing a ticket
    is outward, so a human/policy must approve), and a sandbox profile pinned to this rail
    (egress to the provider only). Returns the capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without filing a second ticket — then
    asks the CRED1 broker to apply the API key (`use_secret`) to the real file; the key
    never leaves the broker. `endpoint` is injected by the handler (never taken from
    caller args). A broker denial (revoked / unauthorized handle) raises ExecError → a
    FAILED receipt."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_ticket(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no duplicate file
            prev = existing.content
            return {"out": prev.get("out"), "project": prev.get("project"),
                    "summary": prev.get("summary"),
                    "description_len": prev.get("description_len"),
                    "priority": prev.get("priority"),
                    "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"),
                    "idempotency_key": idem, "rail": RAIL,
                    "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: create_ticket(key, call_args, transport=transport))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"ticketing: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": COMMUNICATION,
        "budget": int(cap),                                  # hard cap on tickets filed
        "requires_approval": True,                           # Morta gate — a ticket is outward
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)


def register_manifest(k) -> str:
    """Record a discoverable manifest for the ticketing rail (an EFFECT capability, gated
    + Morta). Registration confers NO authority — it is a description a discovery layer
    can find given a goal like 'file a support ticket'. Returns the manifest cell id."""
    m = manifest.capability_manifest(
        "ticketing",
        title="ticketing",
        description="create a support ticket or issue in a helpdesk / issue tracker",
        archetype="EFFECT",
        effect_class=COMMUNICATION,
        caveats={"requires_approval": True},
        input_schema={
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": list(PRIORITIES)},
                "idempotency_key": {"type": "string"},
            },
            "required": ["project", "summary"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "provider_ref": {"type": "string"},
                "status": {"type": "string"},
                "priority": {"type": "string"},
            },
        },
        tags=["support", "ticket", "helpdesk", "issue"],
    )
    return manifest.register(k, m)
