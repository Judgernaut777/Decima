"""Real e-signature rail — wrap the legal-signature engine, never reimplement it.

Decima's dependency policy: recreate the design in pure stdlib, but for HIGH-LIABILITY
externals WRAP THE REAL ENGINE rather than reinvent it. Sending a document out for a
legally binding signature (DocuSign / Dropbox-Sign style) is an OUTWARD, high-liability
effect — recreating the signing ceremony IS the liability. An e-sign provider is just an
HTTPS API, so the real engine is reachable over stdlib `urllib` with **zero pip deps**:
real engine, still pure-stdlib.

This wraps the provider behind the SAME spine the payment rail already enforces, but as a
COMMUNICATION/LEGAL effect rather than a FINANCIAL one. It registers a Morta-gated,
budget-capped, idempotent effect via `kernel.integrate_tool`; the receipt maps the
provider's outcome to WEFT §8 status:
  - an accepted/created envelope (201/200) → SUCCEEDED, carrying the provider `provider_ref`
                                             (the envelope id), envelope status, recipient
                                             count, and the idempotency key;
  - a definite 4xx bad request             → FAILED (nothing was sent);
  - a network error / timeout              → UNKNOWN (we cannot observe whether it sent —
                                             never fabricated as success or failure,
                                             FOLD §11 #8).

POLICY / GUARDRAILS (mirroring the Stripe rail):
  - **wrap the real legal engine** — zero pip deps; the real HTTPS provider over `urllib`.
  - **HTTPS-only** — refuses to put the key / the document on a non-`https://` endpoint
    before any request is made (the e-sign analogue of Stripe's test-mode guard: a legal
    document and the API key must never travel in cleartext).
  - **credentials via CRED1** — the provider key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in a receipt/audit.
  - **Morta-gated** — sending a legal document `requires_approval`; denied until approved.
  - **idempotent** — a prior SUCCEEDED receipt for the same idempotency key returns without
    a second send (no duplicate envelope). Provider-level Idempotency-Key header too.
  - **signer input is UNTRUSTED DATA** — signer emails / subject / document ref are treated
    as data, validated, never as instructions. Ints only in signed content (recipient count).
  - **transport seam** — `send_envelope` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json

from decima import executor
from decima import manifest as M
from decima.hashing import nfc

COMMUNICATION = "COMMUNICATION"   # effect_class — LEGAL/outward, not FINANCIAL
RESULT = "result"                 # the EffectReceipt cell type the kernel asserts
_OK_STATUSES = (200, 201)
_MODES = ("test", "live")         # TEST/SANDBOX-mode guard (see `_is_sandbox_endpoint`)

# Provider envelope signing status → WEFT §8 receipt status (for the READ status check).
_STATUS_MAP = {
    "completed": executor.SUCCEEDED,   # everyone signed — the envelope is legally complete
    "signed": executor.SUCCEEDED,
    "declined": executor.FAILED,       # a signer refused — a definite negative outcome
    "voided": executor.FAILED,         # the sender voided it — a definite negative outcome
    "sent": executor.UNKNOWN,          # out for signature, not yet resolved
    "delivered": executor.UNKNOWN,
    "created": executor.UNKNOWN,
}


def _is_sandbox_endpoint(endpoint: str) -> bool:
    """True if `endpoint` is a provider SANDBOX/demo host (DocuSign uses
    `demo.docusign.net`; others use a `sandbox.`/`test.` host). The TEST/SANDBOX-mode
    guard uses this to keep a `test` run off a production host and a `live` run off a
    sandbox host, so a test can never accidentally email a real contract to real people."""
    e = endpoint.lower()
    return any(tok in e for tok in ("demo.", "demo-", "sandbox.", "sandbox-", "test.", "-test."))


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `send_envelope` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level
    failure (DNS, timeout, TLS) raises — `send_envelope` maps that to UNKNOWN. Never used
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


def _recipients(args: dict) -> list:
    """Normalize the UNTRUSTED signer input to a clean list of signer emails. Accepts a
    single email string or a list; strips blanks; normalizes each. Never interpreted as
    anything but DATA."""
    raw = args.get("recipients", args.get("signers"))
    if isinstance(raw, str):
        raw = [raw]
    return [nfc(str(r)) for r in (raw or []) if str(r).strip()]


def send_envelope(secret_key: str, args: dict, *, transport=None) -> dict:
    """Send a document out for legal signature via the provider, mapping the outcome to an
    EffectReceipt-shaped result. Raises `executor.ExecError` for a definite no-effect (a
    bad request / 4xx → FAILED, nothing sent) and `executor.Ambiguous` for an unobservable
    outcome (network/timeout → UNKNOWN). On success returns the output dict spread into a
    SUCCEEDED receipt, carrying `provider_ref` (the envelope id), the provider envelope
    status, and the recipient count (an int).

    HTTPS-ONLY INVARIANT: a non-`https://` endpoint is refused before any request is made —
    a legal document and the API key must never travel in cleartext. Signer input is treated
    as UNTRUSTED DATA (validated, never obeyed)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put a legal document / the API key on the wire in cleartext. Fail closed.
        raise executor.ExecError("esign: refusing to send a legal document to a non-HTTPS endpoint")

    # TEST/SANDBOX-mode guard — `mode` is injected by the rail (never taken from an
    # untrusted caller). When set, a `test` run MUST target a sandbox/demo endpoint and a
    # `live` run MUST NOT — a mismatch fails closed BEFORE any request, so a test can never
    # accidentally email a real contract to real signers (and vice versa). `mode is None`
    # skips the guard (unconfigured rails behave as before).
    mode = args.get("mode")
    if mode is not None:
        mode = nfc(str(mode))
        if mode not in _MODES:
            raise executor.ExecError(f"esign: mode must be one of {list(_MODES)}")
        sandbox_ep = _is_sandbox_endpoint(endpoint)
        if mode == "test" and not sandbox_ep:
            raise executor.ExecError("esign: test mode must target a sandbox/demo endpoint (fail-closed)")
        if mode == "live" and sandbox_ep:
            raise executor.ExecError("esign: live mode must not target a sandbox/demo endpoint (fail-closed)")

    recipients = _recipients(args)                            # UNTRUSTED — DATA, validated
    if not recipients:
        raise executor.ExecError("esign: at least one signer email is required")
    document = nfc(str(args.get("document", "")))            # a document reference / content hash
    if not document:
        raise executor.ExecError("esign: a document reference/hash is required")
    subject = nfc(str(args.get("subject", "")))
    idem = str(args.get("idempotency_key") or "")
    count = len(recipients)                                   # int only — signed content

    payload = {
        "document": document,
        "subject": subject,
        "recipients": recipients,
        "recipient_count": count,                            # int, never a float (§1)
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level no-duplicate-send
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"esign: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"esign: unparseable response (status {status_code})")
    env_id = resp.get("envelope_id") or resp.get("id")
    if status_code in _OK_STATUSES and env_id:
        out = {"out": f"envelope sent to {count} signer(s)",
               "provider_ref": env_id,                       # the envelope id
               "provider_status": str(resp.get("status", "sent")),   # "sent"/"delivered"
               "recipients": count,                          # int
               "document": document, "subject": subject,
               # signer-supplied names / emails / document text are DATA, never an
               # instruction — the receipt is stamped so recall can never obey them.
               "instruction_eligible": False,
               "sensitive": True,                            # a legal contract + signer PII
               "idempotency_key": idem, "rail": "esign"}
        if mode is not None:
            out["mode"] = mode
        return out
    if 400 <= int(status_code) < 500:                        # definite bad request → no send
        msg = (resp.get("error", {}) or {}).get("message") if isinstance(resp.get("error"), dict) \
            else resp.get("error")
        msg = msg or resp.get("message") or f"http {status_code}"
        raise executor.ExecError(f"esign: rejected — {msg}")  # definite no-effect (FAILED)
    # 5xx / anything else after submission — we can't observe whether it sent.
    raise executor.Ambiguous(f"esign: unexpected response (status {status_code}) — outcome unknown")


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "esign", endpoint: str, mode: str | None = None,
                 transport=None) -> str:
    """Register a REAL e-signature effect and grant Decima a COMMUNICATION capability to run
    it: a hard `budget` cap (max envelopes), Morta `requires_approval` (sending a legal
    document needs approval), and a sandbox profile pinned to this rail (egress to the
    provider only). On each invoke the handler asks the CRED1 broker to apply the provider
    key (`use_secret`) — the key never leaves the broker — and injects the configured
    `endpoint` AND `mode` (the TEST/SANDBOX-mode guard cannot be spoofed by the caller). A
    broker denial (revoked / unauthorized handle) raises ExecError → a FAILED receipt.
    `mode` (`test`/`live`, or None to disable the guard) is injected by the rail. Returns
    the capability id."""
    def handler(_impl, args):
        call_args = {**args, "endpoint": endpoint}            # endpoint injected by the rail
        if mode is not None:
            call_args["mode"] = mode                          # mode injected by the rail, not the caller
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: send_envelope(key, call_args, transport=transport))
        if "denied" in r:                                     # revoked / unauthorized handle
            raise executor.ExecError(f"esign: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": COMMUNICATION,
        "budget": int(cap),                                   # hard cap on envelopes sent
        "requires_approval": True,                            # Morta gate — a legal doc needs approval
        "sandbox": {"effects": [name], "network": True},      # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)


def find_envelope(weave, idempotency_key: str):
    """A prior SUCCEEDED envelope receipt for this idempotency key, or None. This is the
    rail-level dedupe (mirrors `payments.find_payment`): the kernel's per-INVOKE nonce
    changes every call, so two logical re-tries would each SEND; matching on the caller's
    idempotency key makes a replay a no-op — no duplicate legal envelope."""
    key = nfc(str(idempotency_key))
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == COMMUNICATION
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def send(k, agent_cell, cap_id, *, idempotency_key: str, document: str, recipients,
         subject: str = "") -> dict:
    """Send an envelope through the rail: Morta-gated, budget-capped, and idempotent.

    Flow: (idempotency) a replay of the same key returns the prior receipt and NEVER sends a
    second envelope; otherwise (invoke) the kernel authorizes + emits the EffectReceipt.
    Each envelope costs 1 against the `budget` cap (the cap is a count of envelopes).
    Returns {status, result_cell, provider_ref?, idempotent_replay, denied?}."""
    key = nfc(str(idempotency_key))

    existing = find_envelope(k.weave(), key)
    if existing is not None:                                  # (idempotency) no duplicate send
        return {"status": existing.content["status"], "result_cell": existing.id,
                "provider_ref": existing.content.get("provider_ref"),
                "idempotent_replay": True}

    res = k.invoke(agent_cell, cap_id, {                      # Morta-gated + budget-capped
        "document": nfc(str(document)),
        "recipients": recipients, "subject": nfc(str(subject)),
        "idempotency_key": key, "cost": 1,                   # one envelope = one unit of the cap
    })
    out = {"idempotent_replay": False, "status": res.get("status"),
           "result_cell": res.get("result_cell")}
    if "denied" in res:
        out["denied"] = res["denied"]
    return out


# -- READ: check an envelope's signing status (observing is always allowed) ----------
def _urllib_get_transport(url: str, headers: dict, body):
    """The real READ transport: a stdlib `urllib` GET (no pip dep). Same (status, json)
    shape as the POST transport; `body` is ignored (a status check is a GET). Never used
    by the offline oracle (tests inject a fake transport)."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": {"message": f"http {e.code}"}}


def fetch_status(secret_key: str, args: dict, *, transport=None) -> dict:
    """Check an envelope's signing status via the provider — a READ (a GET). Returns
    {provider_ref, envelope_status, receipt_status} where `receipt_status` maps the
    provider's envelope status to WEFT §8 (completed/signed → SUCCEEDED, declined/voided →
    FAILED, sent/delivered/created → UNKNOWN, i.e. not yet resolved). Raises
    `executor.ExecError` (non-HTTPS, missing id, 4xx) or `executor.Ambiguous`
    (network/unparseable). HTTPS-only: a non-`https://` endpoint is refused before the key
    touches the wire."""
    transport = transport or _urllib_get_transport
    endpoint = str(args.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        raise executor.ExecError("esign: refusing to send the e-sign key to a non-HTTPS endpoint")
    envelope_id = nfc(str(args.get("envelope_id") or ""))
    if not envelope_id:
        raise executor.ExecError("esign: an envelope_id is required to check status")
    url = f"{endpoint.rstrip('/')}/{envelope_id}"
    headers = {"Authorization": f"Bearer {secret_key}",       # applied here, never returned
               "Accept": "application/json"}
    try:
        status_code, resp = transport(url, headers, None)
    except Exception as e:
        raise executor.Ambiguous(f"esign: status transport error, outcome unknown: {e}")
    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"esign: unparseable status response (status {status_code})")
    if status_code in _OK_STATUSES:
        env_status = str(resp.get("status") or "sent")
        return {"provider_ref": envelope_id,
                "envelope_status": env_status,
                "receipt_status": _STATUS_MAP.get(nfc(env_status).lower(), executor.UNKNOWN)}
    if status_code and 400 <= int(status_code) < 500:
        raise executor.ExecError(f"esign: status check rejected — http {status_code}")
    raise executor.Ambiguous(f"esign: unexpected status response (status {status_code}) — unknown")


def check_status(k, *, endpoint: str, envelope_id: str, credential_handle: str, broker,
                 agent_cell, transport=None) -> dict:
    """Check an envelope's signing status — a READ (observing is always allowed, so NO
    Morta gate, no budget). Resolves the key via CRED1 (`broker.use_secret`, applied
    inside the broker, never disclosed) and runs `fetch_status`. Returns {provider_ref,
    envelope_status, receipt_status} on success; {"denied": reason} on a definite failure
    (non-HTTPS / bad id / 4xx / revoked handle); or {"unknown": reason,
    receipt_status: UNKNOWN} when the outcome is unobservable."""
    req = {"endpoint": endpoint, "envelope_id": envelope_id}
    try:
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: fetch_status(key, req, transport=transport))
    except executor.ExecError as e:
        return {"denied": f"esign: {e}"}                      # definite failure — fail closed
    except executor.Ambiguous as e:
        return {"unknown": f"esign: {e}", "receipt_status": executor.UNKNOWN}
    if "denied" in r:
        return {"denied": r["denied"]}                        # credential handle denied
    return r["ok"]


def register_manifest(k) -> str:
    """Record a discoverable manifest for the e-sign send rail (source="builtin"), so the
    plug-in-or-forge discovery layer can find the real e-signature engine before forging a
    new one. A manifest GRANTS NOTHING (manifest.py, Law) — the rail keeps its own gated
    install path; this only makes it findable (archetype EFFECT, requires_approval, like
    crm / banking). Returns the manifest cell id."""
    m = M.capability_manifest(
        "esign",
        description="send a document out for a legally binding electronic signature "
                    "(an e-signature envelope to signers)",
        archetype="EFFECT", effect_class=COMMUNICATION,
        caveats={"requires_approval": True},                  # a legal contract goes to signers
        source="builtin", version=1,
        tags=["esign", "esignature", "signature", "sign", "envelope", "document",
              "contract", "docusign"])
    return M.register(k, m)
