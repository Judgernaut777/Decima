"""Real CRM write rail — wrap a REAL system of record (Salesforce / HubSpot style),
never reimplement the customer database (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reinvent it. A CRM is the SYSTEM OF RECORD for customers, deals,
and revenue; writing a contact/company/deal into it is an OUTWARD, durable effect on a
book of record other teams (sales, finance, legal) trust — re-rolling that store is
itself the liability. A CRM is just an HTTPS API, so the real engine is reachable over
stdlib `urllib` with **zero pip dependencies**: real engine, still pure-stdlib.

This complements the CRM1 stub (`decima/crm.py`, a local pipeline of `deal` Cells): the
stub models pipeline STATE on the Weft; this rail WRITES to the external CRM so the
system of record is updated. It wraps the CRM behind the SAME spine the payment rail
enforces, but as a CRM (WRITE) effect rather than a FINANCIAL one. It registers a
Morta-gated, budget-capped, idempotent effect via `kernel.integrate_tool`; the receipt
maps the CRM's outcome to WEFT §8 status:
  - a created / updated record (200/201) → SUCCEEDED, carrying the CRM `provider_ref`
                                           (the record id), the create/update status,
                                           the record kind, and the idempotency key;
  - an invalid field / bad request (4xx) → FAILED (nothing was written);
  - a network error / timeout            → UNKNOWN (we cannot observe whether it wrote —
                                           never fabricated as success or failure,
                                           FOLD §11 #8).

GUARDRAILS (mirroring the Stripe / comms rails):
  - **record fields are UNTRUSTED DATA** — `kind` / `fields` / `external_id` are
    normalized and carried as the write payload only; they never become instructions and
    are never interpreted as anything but the record's data.
  - **HTTPS-only** — refuses to send the CRM key to a non-`https://` endpoint before any
    request is made (never leak the key in cleartext); a definite no-effect (FAILED).
  - **credentials via CRED1** — the CRM key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Morta-gated + idempotent** — a write is denied until the capability is approved; a
    replay of the same idempotency_key returns the prior receipt and never writes twice.
  - **Transport seam** — `upsert` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.
  - **ints, not floats** in signed content (the field count).

Pure composition (executor / secrets / kernel / manifest public APIs). No core edit.
"""
import json

from decima import executor
from decima import manifest as M
from decima.hashing import nfc

CRM = "CRM"                                       # effect_class — outward WRITE, not FINANCIAL
RESULT = "result"                                 # the EffectReceipt cell type the kernel asserts
_KINDS = ("contact", "company", "deal")
_OK_STATUSES = (200, 201)


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `upsert` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level failure
    (DNS, timeout, TLS) raises — `upsert` maps that to UNKNOWN. Never used by the offline
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
            return e.code, {"error": {"message": f"http {e.code}"}}


def _clean_fields(raw) -> dict:
    """Normalize the UNTRUSTED `fields` mapping to a clean dict of string keys → nfc'd
    string values. Non-dict input yields an empty dict. Every value is treated as DATA
    (normalized, never obeyed) — this is the record's payload, nothing more."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for kk, vv in raw.items():
        out[nfc(str(kk))] = nfc(str(vv))
    return out


def upsert(secret_key: str, args: dict, *, transport=None) -> dict:
    """Create or update a CRM record via the provider, mapping the outcome to an
    EffectReceipt-shaped result. Raises `executor.ExecError` for a definite no-effect
    (non-HTTPS endpoint, unknown kind, invalid field / 4xx → FAILED, nothing written) and
    `executor.Ambiguous` for an unobservable outcome (network/unexpected → UNKNOWN). On
    success returns the output dict spread into a SUCCEEDED receipt, carrying the CRM
    `provider_ref` (the record id), the provider create/update status, and the kind.

    `kind` / `fields` / `external_id` are UNTRUSTED DATA: normalized and carried as the
    write payload only, never interpreted as instructions.

    HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on the
    wire (no request is made)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the CRM key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("crm: refusing to send the CRM key to a non-HTTPS endpoint")

    kind = nfc(str(args.get("kind", "")))                     # UNTRUSTED — validated as DATA
    if kind not in _KINDS:
        raise executor.ExecError(f"crm: kind must be one of {list(_KINDS)}")
    fields = _clean_fields(args.get("fields"))               # UNTRUSTED record data
    if not fields:
        raise executor.ExecError("crm: at least one record field is required")
    external_id = nfc(str(args.get("external_id") or ""))    # caller's stable external key
    idem = nfc(str(args.get("idempotency_key") or ""))
    field_count = len(fields)                                 # int only — signed content

    payload = {"kind": kind, "fields": fields}
    if external_id:
        payload["external_id"] = external_id                 # provider-side upsert key
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level no-duplicate-write
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"crm: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"crm: unparseable response (status {status_code})")
    record_id = resp.get("id") or resp.get("record_id")
    if status_code in _OK_STATUSES and record_id:
        # created (201) vs updated (200) — the provider says which; default by status code.
        provider_status = str(resp.get("status") or ("created" if status_code == 201 else "updated"))
        return {"out": f"{provider_status} {kind} {record_id}",
                "kind": kind, "provider_ref": record_id,     # the CRM record id
                "provider_status": provider_status,          # created / updated (NOT "status": WEFT §8)
                "field_count": int(field_count),             # int, never a float (§1)
                "external_id": external_id,
                "idempotency_key": idem, "rail": "crm"}
    if status_code and 400 <= int(status_code) < 500:        # invalid field — definite no-effect
        err = resp.get("error")
        msg = (err.get("message") if isinstance(err, dict) else err) \
            or resp.get("message") or f"http {status_code}"
        raise executor.ExecError(f"crm: rejected — {msg}")   # definite no-effect (FAILED)
    # 5xx / anything else after submission — we can't observe whether it wrote.
    raise executor.Ambiguous(f"crm: unexpected response (status {status_code}) — outcome unknown")


def find_record(weave, idempotency_key: str):
    """A prior SUCCEEDED CRM-write receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each write; matching on the caller's key makes a replay a no-op — no
    duplicate record (mirrors `comms.find_message` / `payments.find_payment`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == CRM
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "crm", endpoint: str, transport=None) -> str:
    """Register a REAL CRM-write effect and grant Decima a CRM capability to run it: a hard
    write cap (`budget`), Morta `requires_approval` (a write lands on the system of record,
    so a human/policy must approve), and a sandbox profile that allows ONLY this effect with
    network pinned to the rail. Returns the capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without a second write — then asks the
    CRED1 broker to apply the CRM key (`use_secret`) to the real upsert; the key never
    leaves the broker. `endpoint` is injected by the handler (never taken from caller args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_record(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-write
            prev = existing.content
            return {"out": prev.get("out"), "kind": prev.get("kind"),
                    "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"),
                    "field_count": prev.get("field_count"),
                    "external_id": prev.get("external_id"),
                    "idempotency_key": idem, "rail": "crm",
                    "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: upsert(key, call_args, transport=transport))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"crm: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": CRM,
        "budget": int(cap),                                  # hard cap on records written
        "requires_approval": True,                           # Morta gate — a write hits the record
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)


def register_manifest(k) -> str:
    """Record a discoverable manifest for the CRM write rail (source="builtin"), so the
    plug-in-or-forge discovery layer can find the real CRM engine before forging a new
    one. A manifest GRANTS NOTHING (manifest.py, Law) — the rail keeps its own gated
    install path; this only makes it findable. Returns the manifest cell id."""
    m = M.capability_manifest(
        "crm",
        description="create or update a CRM contact, company, or deal",
        archetype="EFFECT", effect_class=CRM,
        caveats={"requires_approval": True},                 # a write hits the system of record
        source="builtin", version=1,
        tags=["crm", "sales", "contacts"])
    return M.register(k, m)
