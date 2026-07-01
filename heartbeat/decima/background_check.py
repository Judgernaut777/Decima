"""Real employment background-check rail — wrap the compliant screener, never rebuild it.

Dependency policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals
WRAP THE REAL ENGINE rather than reimplement it. Employment screening is the textbook
case — recreating a criminal/records background check is itself the compliance liability
(a home-grown "screener" that says CLEAR is worthless and dangerous, and FCRA/adverse-
action law is unforgiving). A real provider (Checkr-style) is just an HTTPS API, so the
real, compliant engine is reachable over stdlib `urllib` with **zero pip dependencies**:
real engine, pure stdlib.

`screen` POSTs a candidate's screening request to the provider's real report endpoint and
maps the provider's response to a status in {CLEAR, CONSIDER, PENDING} plus the provider's
report id (`provider_ref`) and any reasons. `run_screen` composes that with CRED1 and
records the outcome on the Weft.

POLICY / GUARDRAILS:
  - **wrap the real engine** — the screening decision is ALWAYS the provider's word;
    Decima never invents a background determination (recreating screening is a liability).
  - **CLEAR is never fabricated** — it is only ever what the provider returned. When the
    outcome is unobservable (network error / timeout / server error) the result is
    PENDING, never a manufactured CLEAR. CONSIDER (needs human review) is surfaced with
    the provider's reasons, never silently cleared.
  - **key via CRED1** — the provider API key lives in the secrets broker and is applied
    INSIDE the broker (`use_secret`); it is never returned, never logged, never on the
    Weft.
  - **HTTPS-only** — refuses to send the key to a non-`https://` endpoint (never leak the
    key in cleartext), before any request is made.
  - **untrusted candidate input** — the candidate reference id / package fields are treated
    as UNTRUSTED DATA: they are sent to the provider and the recorded cell is marked
    non-instruction data; PII is stored minimally (a reference id + the status).
  - **transport seam** — `screen` takes a `transport(url, headers, body) -> (status,
    json)`; the default is a real `urllib` POST, tests inject a fake so the offline
    oracle exercises the full contract with NO network.

Zero pip deps. Composes public secrets / model / kernel APIs only. No core edit.
"""
import json

from decima.model import assert_content
from decima.hashing import content_id, nfc

BACKGROUND_CHECK = "background_check"
CLEAR = "CLEAR"
CONSIDER = "CONSIDER"
PENDING = "PENDING"

# The provider's raw status vocabulary → Decima's three-valued status. Checkr semantics:
# a report result is "clear" (no adverse findings) or "consider" (needs human review);
# a report that is still running is "pending"/"processing". Anything the provider does not
# clearly mark CLEAR or CONSIDER stays PENDING (never fabricated).
_CLEAR = ("clear", "complete_clear", "passed")
_CONSIDER = ("consider", "needs_review", "dispute", "suspended")


class BackgroundCheckError(Exception):
    """A definite client-side / bad-request failure (e.g. a non-HTTPS endpoint or a
    provider 4xx). Fail loud — this is not an unobservable outcome."""


class BackgroundCheckUnreachable(Exception):
    """The provider was unreachable (network / timeout / 5xx). The outcome is
    UNOBSERVABLE — the caller maps this to PENDING, never to CLEAR."""


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `screen` decides the status. A transport-level failure (DNS, timeout,
    TLS) raises — `screen` maps that to BackgroundCheckUnreachable. Never used by the
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
            return e.code, {"error": f"http {e.code}"}


def _map_status(raw) -> str:
    s = nfc(str(raw or "")).lower()
    if s in _CLEAR:
        return CLEAR
    if s in _CONSIDER:
        return CONSIDER
    return PENDING                                             # pending / processing / unknown


def screen(secret_key: str, candidate: dict, *, transport=None) -> dict:
    """Submit a candidate's screening request to the provider's real report endpoint and
    map the response. `candidate` carries the provider `endpoint`, a `reference_id`, and a
    `package`/scope — ALL of which are treated as UNTRUSTED DATA (minimal PII). Returns
    {status, provider_ref, reasons}; `status` is one of {CLEAR, CONSIDER, PENDING} and is
    ALWAYS the provider's determination (never fabricated here).

    Raises `BackgroundCheckError` on a non-HTTPS endpoint or a provider 4xx (bad request),
    and `BackgroundCheckUnreachable` on a network error / timeout / 5xx (outcome
    unobservable)."""
    transport = transport or _urllib_transport
    endpoint = str(candidate.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the provider API key on the wire in cleartext. Fail before sending.
        raise BackgroundCheckError(
            "refusing to send the screening API key to a non-HTTPS endpoint")

    reference_id = nfc(str(candidate.get("reference_id", "")))
    if not reference_id:
        raise BackgroundCheckError("background_check: a reference_id is required")
    package = nfc(str(candidate.get("package", "standard")))   # scope of the check, untrusted
    body = json.dumps({"reference_id": reference_id, "package": package}, sort_keys=True)
    headers = {
        "Authorization": f"Bearer {secret_key}",              # applied here, never returned
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                     # network/timeout — unobservable
        raise BackgroundCheckUnreachable(
            f"background_check: provider unreachable, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise BackgroundCheckUnreachable(
            f"background_check: unparseable response (status {status_code})")
    if 400 <= int(status_code) < 500:
        err = resp.get("error") or resp.get("message") or f"http {status_code}"
        raise BackgroundCheckError(
            f"background_check: provider rejected the request (bad request) — {err}")
    if int(status_code) >= 500:
        raise BackgroundCheckUnreachable(
            f"background_check: provider server error (status {status_code}) — outcome unknown")

    # Checkr-style bodies nest under `data` / `attributes`; tolerate a flat shape.
    data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
    attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    provider_ref = data.get("id") or resp.get("id")
    # A report reports both a result (clear/consider) and a status (pending/complete);
    # prefer the result, fall back to the status, so a still-running report stays PENDING.
    raw = (attrs.get("result") or data.get("result") or resp.get("result")
           or attrs.get("status") or data.get("status") or resp.get("status"))
    reasons = attrs.get("reasons") or data.get("reasons") or resp.get("reasons") or []
    return {
        "status": _map_status(raw),
        "provider_ref": provider_ref,
        "reasons": [nfc(str(x)) for x in reasons],
    }


def run_screen(k, *, endpoint: str, candidate: dict, credential_handle: str, broker,
               agent_cell, transport=None) -> dict:
    """Run a real background check and record the outcome on the Weft. Resolves the
    provider API key via CRED1 (`broker.use_secret` — applied inside the broker, never
    disclosed), submits the candidate's request through the real (wrapped) engine, and:

      - broker denial              → returns {denied: reason}; no cell recorded;
      - network / unreachable      → records a PENDING `background_check` (outcome
                                     unobservable, NEVER fabricated as CLEAR);
      - success                    → records a `background_check` cell carrying the
                                     provider's status / provider_ref / reasons and the
                                     subject (reference id only — PII minimal, key never
                                     present, marked non-instruction data).

    Returns {background_check: <cell id>, status, provider_ref} (or {denied: reason})."""
    subject = nfc(str(candidate.get("reference_id", "")))
    # The endpoint is authoritative config (overrides any candidate-supplied value).
    req = {**candidate, "endpoint": endpoint}

    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: screen(key, req, transport=transport))
    except BackgroundCheckUnreachable as e:
        # Outcome unobservable — record PENDING, never fabricate a determination.
        return _record(k, agent_cell, status=PENDING, provider_ref=None,
                       reasons=[f"provider unreachable: {e}"], subject=subject)
    if "denied" in r:                                          # revoked / unauthorized handle
        return {"denied": r["denied"]}

    result = r["ok"]                                           # the provider's determination
    return _record(k, agent_cell, status=result["status"],
                   provider_ref=result.get("provider_ref"),
                   reasons=list(result.get("reasons", [])), subject=subject)


def _record(k, agent_cell, *, status, provider_ref, reasons, subject) -> dict:
    """Write a `background_check` cell (never the key; PII minimal; non-instruction data)."""
    author = agent_cell.content["principal"]
    cid = content_id({"background_check": provider_ref or subject, "n": k.weft.lamport})
    assert_content(k.weft, author, cid, BACKGROUND_CHECK, {
        "status": status,
        "provider_ref": provider_ref,
        "reasons": reasons,
        "subject": subject,                                   # reference id only — PII minimal
        "instruction_eligible": False,                        # candidate data is never a command
        "disclosed": False,                                   # the API key is never on the Weft
    })
    return {"background_check": cid, "status": status, "provider_ref": provider_ref}
