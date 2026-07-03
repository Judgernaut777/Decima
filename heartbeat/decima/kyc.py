"""Real KYC / identity-verification rail — wrap the compliant engine, never rebuild it.

Dependency policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals
WRAP THE REAL ENGINE rather than reimplement it. Identity verification (KYC/AML) is the
textbook case — recreating a document/liveness/identity check is itself the compliance
liability (a home-grown "verifier" that says VERIFIED is worthless and dangerous). A real
provider (Persona / Onfido-style) is just an HTTPS API, so the real, compliant engine is
reachable over stdlib `urllib` with **zero pip dependencies**: real engine, pure stdlib.

`verify` POSTs an applicant's verification request to the provider's real inquiry
endpoint and maps the provider's response to a status in {VERIFIED, REJECTED, PENDING}
plus the provider's inquiry id (`provider_ref`) and any reasons. `run_check` composes
that with CRED1 and records the outcome on the Weft.

POLICY / GUARDRAILS:
  - **wrap the real engine** — the verification decision is ALWAYS the provider's word;
    Decima never invents an identity determination (recreating KYC is a liability).
  - **VERIFIED is never fabricated** — it is only ever what the provider returned. When
    the outcome is unobservable (network error / timeout / server error) the result is
    PENDING, never a manufactured VERIFIED.
  - **key via CRED1** — the provider API key lives in the secrets broker and is applied
    INSIDE the broker (`use_secret`); it is never returned, never logged, never on the
    Weft.
  - **HTTPS-only** — refuses to send the key to a non-`https://` endpoint (never leak the
    key in cleartext), before any request is made.
  - **untrusted applicant input** — all applicant document/claim fields are treated as
    UNTRUSTED DATA: they are sent to the provider and the recorded cell is marked
    non-instruction data; PII is stored minimally (a reference id + the status).
  - **transport seam** — `verify` takes a `transport(url, headers, body) -> (status,
    json)`; the default is a real `urllib` POST, tests inject a fake so the offline
    oracle exercises the full contract with NO network.

Zero pip deps. Composes public secrets / model / kernel APIs only. No core edit.
"""
import json

from decima.model import assert_content
from decima.hashing import content_id, nfc

KYC_RESULT = "kyc_result"
VERIFIED = "VERIFIED"
REJECTED = "REJECTED"
PENDING = "PENDING"

# The provider's raw status vocabulary → Decima's three-valued status. Anything the
# provider does not clearly approve or clearly decline stays PENDING (never fabricated).
_VERIFIED = ("approved", "completed", "passed", "verified", "clear")
_REJECTED = ("declined", "failed", "rejected", "denied")


class KYCError(Exception):
    """A definite client-side / bad-request failure (e.g. a non-HTTPS endpoint or a
    provider 4xx). Fail loud — this is not an unobservable outcome."""


class KYCUnreachable(Exception):
    """The provider was unreachable (network / timeout / 5xx). The outcome is
    UNOBSERVABLE — the caller maps this to PENDING, never to VERIFIED."""


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
        "kyc", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def _map_status(raw) -> str:
    s = nfc(str(raw or "")).lower()
    if s in _VERIFIED:
        return VERIFIED
    if s in _REJECTED:
        return REJECTED
    return PENDING                                             # created / processing / unknown


def verify(secret_key: str, applicant: dict, *, transport=None) -> dict:
    """Submit an applicant's verification request to the provider's real inquiry endpoint
    and map the response. `applicant` carries the provider `endpoint`, a `reference_id`,
    and document/claim fields — ALL of which are treated as UNTRUSTED DATA. Returns
    {status, provider_ref, reasons}; `status` is one of {VERIFIED, REJECTED, PENDING} and
    is ALWAYS the provider's determination (never fabricated here).

    Raises `KYCError` on a non-HTTPS endpoint or a provider 4xx (bad request), and
    `KYCUnreachable` on a network error / timeout / 5xx (outcome unobservable)."""
    transport = transport or _urllib_transport
    endpoint = str(applicant.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the provider API key on the wire in cleartext. Fail before sending.
        raise KYCError("refusing to send the KYC API key to a non-HTTPS endpoint")

    reference_id = nfc(str(applicant.get("reference_id", "")))
    if not reference_id:
        raise KYCError("kyc: a reference_id is required")
    # Every applicant field except the endpoint is forwarded as an untrusted claim.
    claims = {k: applicant[k] for k in applicant if k != "endpoint"}
    body = json.dumps({"reference_id": reference_id, "claims": claims}, sort_keys=True)
    headers = {
        "Authorization": f"Bearer {secret_key}",              # applied here, never returned
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                     # network/timeout — unobservable
        raise KYCUnreachable(f"kyc: provider unreachable, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise KYCUnreachable(f"kyc: unparseable response (status {status_code})")
    if 400 <= int(status_code) < 500:
        err = resp.get("error") or resp.get("message") or f"http {status_code}"
        raise KYCError(f"kyc: provider rejected the request (bad request) — {err}")
    if int(status_code) >= 500:
        raise KYCUnreachable(f"kyc: provider server error (status {status_code}) — outcome unknown")

    # Persona/Onfido-style bodies nest under `data` / `attributes`; tolerate a flat shape.
    data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
    attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    provider_ref = data.get("id") or resp.get("id")
    raw_status = attrs.get("status") or data.get("status") or resp.get("status")
    reasons = attrs.get("reasons") or data.get("reasons") or resp.get("reasons") or []
    return {
        "status": _map_status(raw_status),
        "provider_ref": provider_ref,
        "reasons": [nfc(str(x)) for x in reasons],
    }


def run_check(k, *, endpoint: str, applicant: dict, credential_handle: str, broker,
              agent_cell, transport=None) -> dict:
    """Run a real KYC verification and record the outcome on the Weft. Resolves the
    provider API key via CRED1 (`broker.use_secret` — applied inside the broker, never
    disclosed), submits the applicant's request through the real (wrapped) engine, and:

      - broker denial              → returns {denied: reason}; no cell recorded;
      - network / unreachable      → records a PENDING `kyc_result` (outcome unobservable,
                                     NEVER fabricated as VERIFIED);
      - success                    → records a `kyc_result` cell carrying the provider's
                                     status / provider_ref / reasons and the subject
                                     (reference id only — PII minimal, key never present,
                                     marked non-instruction data).

    Returns {kyc_result: <cell id>, status, provider_ref} (or {denied: reason})."""
    subject = nfc(str(applicant.get("reference_id", "")))
    # The endpoint is authoritative config (overrides any applicant-supplied value).
    req = {**applicant, "endpoint": endpoint}

    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: verify(key, req, transport=transport))
    except KYCUnreachable as e:
        # Outcome unobservable — record PENDING, never fabricate a determination.
        return _record(k, agent_cell, status=PENDING, provider_ref=None,
                       reasons=[f"provider unreachable: {e}"], subject=subject)
    if "denied" in r:                                          # handle revoked / unauthorized
        return {"denied": r["denied"]}

    result = r["ok"]                                           # the provider's determination
    return _record(k, agent_cell, status=result["status"],
                   provider_ref=result.get("provider_ref"),
                   reasons=list(result.get("reasons", [])), subject=subject)


def _record(k, agent_cell, *, status, provider_ref, reasons, subject) -> dict:
    """Write a `kyc_result` cell (never the key; PII minimal; non-instruction data)."""
    author = agent_cell.content["principal"]
    cid = content_id({"kyc_result": provider_ref or subject, "n": k.weft.lamport})
    assert_content(k.weft, author, cid, KYC_RESULT, {
        "status": status,
        "provider_ref": provider_ref,
        "reasons": reasons,
        "subject": subject,                                   # reference id only — PII minimal
        "instruction_eligible": False,                        # applicant data is never a command
        "disclosed": False,                                   # the API key is never on the Weft
    })
    return {"kyc_result": cid, "status": status, "provider_ref": provider_ref}
