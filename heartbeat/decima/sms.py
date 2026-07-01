"""Real telephony / SMS rail — wrap a REAL carrier (Twilio style), never reimplement
outbound texting (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it. Putting a text on a stranger's phone is an
irreversible OUTWARD effect that COSTS MONEY (a carrier bills per segment), and rolling
your own SMPP/carrier interconnect is itself the liability. A telephony provider
(Twilio's Messages API) is just an HTTPS API, so the real engine rides stdlib `urllib`
with **zero pip dependencies**: real engine, still pure-stdlib.

This wraps the carrier behind the SAME spine the payment / comms rails enforce — it
registers an SMS, Morta-gated, budget-capped, idempotent effect via
`kernel.integrate_tool`. Two lanes:

  SEND (an EFFECT — money leaves the box). The receipt maps the carrier's outcome to
  WEFT §8 status:
    - an accepted / queued / sending / sent message → SUCCEEDED, carrying the carrier
                                                       `provider_ref` (the message SID)
                                                       and the idempotency key;
    - an invalid number / bad request (4xx)         → FAILED (nothing left the box);
    - a network error / timeout                     → UNKNOWN (we cannot observe whether
                                                       it went out — never fabricated,
                                                       FOLD §11 #8).

  DELIVERY STATUS (a READ — no money, no Morta). `delivery_status` GETs a message by SID
  and maps the carrier delivery state to the same trinity:
    - delivered / sent / received       → SUCCEEDED
    - failed / undelivered              → FAILED
    - queued / sending / accepted / …   → UNKNOWN (still in flight — never guessed).

GUARDRAILS (mirroring the comms / Stripe rails):
  - **recipient + body are UNTRUSTED DATA** — `to` / `from` / `body` are normalized and
    carried as the message payload only; they never become instructions
    (`instruction_eligible=False`) and are never interpolated into anything executable.
  - **per-message cost is an INT** (minor units — cents / millicents; never a float). A
    production send MUST carry a positive cost (real money is accounted); a sandbox send
    MUST be free (cost 0). A non-int cost fails closed.
  - **TEST / SANDBOX-mode guard** — a rail installed with `test_mode=True` is a sandbox:
    the send is stamped `test_mode=True` and MUST NOT bill (cost 0). A production rail
    (`test_mode=False`) MUST bill a positive int. A mismatch fails closed (a definite
    no-effect), so a sandbox misconfig can never quietly cost — or a production send
    quietly go unbilled.
  - **HTTPS-only** — refuses to send the carrier key to a non-`https://` endpoint before
    any request (never leak the key in cleartext); a definite no-effect (FAILED for a
    send, `{"denied"}` for a status read).
  - **credentials via CRED1** — the carrier auth (Twilio AccountSID:AuthToken) lives in
    the secrets broker; the handler calls `broker.use_secret`, which applies the auth
    INSIDE the broker (never returned, never logged, never on the Weft). The raw auth
    never appears in the receipt / audit / any event.
  - **Morta-gated + idempotent** — a send is denied until the capability is approved; a
    replay of the same idempotency_key returns the prior receipt and sends nothing twice.
  - **Transport seam** — `send` / `fetch_status` take a
    `transport(url, headers, body, method) -> (status, json)`. The default is a real
    `urllib` request; tests inject a fake, so the offline oracle exercises the full
    contract with NO network.

Pure composition (executor / secrets / model / manifest / kernel public APIs). No core edit.
"""
import base64
import json
from urllib.parse import urlencode

from decima import executor
from decima import manifest as M
from decima.model import assert_content
from decima.hashing import content_id, nfc

SMS = "SMS"                                       # effect_class — outward send, costs money
RESULT = "result"                                 # the EffectReceipt cell type the kernel asserts
SMS_STATUS = "sms_status"                         # the on-Weft record of a delivery-status READ

# The carrier's message states the SEND accepts (Twilio Messages create).
_OK_SEND = ("accepted", "queued", "sending", "sent")
# Delivery states → WEFT §8 trinity, for the status READ.
_DELIVERED = ("delivered", "sent", "received")
_FAILED = ("failed", "undelivered")


def _urllib_transport(url: str, headers: dict, body: str, method: str = "POST"):
    """The real transport: a stdlib `urllib` request (no pip dep). POST for a send, GET
    for a status read. Returns (status_code, parsed_json). A 4xx/5xx surfaces as
    (code, error-json) rather than raising, so the caller decides SUCCEEDED/FAILED/UNKNOWN.
    A transport-level failure (DNS, timeout, TLS) raises — the caller maps that to UNKNOWN
    (send) or a SmsError (status read). Never used by the offline oracle (tests inject a
    fake transport)."""
    import urllib.request
    import urllib.error
    data = body.encode("utf-8") if (body and method == "POST") else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                       # 4xx/5xx carry a JSON body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"message": f"http {e.code}"}


class SmsError(Exception):
    """A telephony READ failure (delivery status) — no `sms_status` cell may be recorded
    (fail closed). Covers a non-HTTPS endpoint, an unreachable/timed-out endpoint, and an
    unparseable / error provider body."""


def _require_int(name: str, v) -> int:
    """Guard that a money value the engine will fold / sign is an int minor unit (never a
    float / bool). Real money on the Weft is integer cents/millicents — a float fails
    closed rather than land on the Log."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise executor.ExecError(f"sms: {name} must be an int (minor units), got {v!r}")
    return int(v)


def _basic_auth(secret_key: str) -> str:
    """Build a Twilio-style HTTP Basic credential from the CRED1-dispensed secret
    (`AccountSID:AuthToken`). Applied INSIDE the broker; the raw secret never leaves it and
    never touches the Weft."""
    return "Basic " + base64.b64encode(secret_key.encode("utf-8")).decode("ascii")


def send(secret_key: str, args: dict, *, transport=None) -> dict:
    """Send a text via the carrier, mapping the outcome to an EffectReceipt-shaped result.
    Raises `executor.ExecError` for a definite no-effect (non-HTTPS endpoint, missing
    recipient, cost/test-mode violation, invalid number / 4xx → FAILED, nothing sent) and
    `executor.Ambiguous` for an unobservable outcome (network/unexpected → UNKNOWN). On
    success returns the output dict spread into a SUCCEEDED receipt, carrying the carrier
    `provider_ref` (the message SID).

    `to` / `from` / `body` are UNTRUSTED DATA: normalized and carried as the message
    payload only, stamped `instruction_eligible=False`, never interpreted as instructions.

    HTTPS INVARIANT: a non-`https://` endpoint is refused before the auth is put on the
    wire (no request is made)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the carrier auth on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("sms: refusing to send the carrier auth to a non-HTTPS endpoint")

    to = nfc(str(args.get("to", "")))                        # UNTRUSTED recipient (data only)
    if not to:
        raise executor.ExecError("sms: a recipient (to) is required")
    sender = nfc(str(args.get("from") or args.get("from_") or ""))   # UNTRUSTED sender number
    body = nfc(str(args.get("body", "")))                    # UNTRUSTED content (data only)
    idem = nfc(str(args.get("idempotency_key") or ""))

    # Money + sandbox invariant: cost is an int; a sandbox send is free, a production send
    # bills a positive int. This is what keeps a misconfig from quietly costing (or a real
    # send from quietly going unbilled).
    test_mode = bool(args.get("test_mode"))
    cost = _require_int("cost", args.get("cost", 0))
    if test_mode and cost != 0:
        raise executor.ExecError("sms: a sandbox (test_mode) send must not bill (cost must be 0)")
    if not test_mode and cost <= 0:
        raise executor.ExecError("sms: a production send must carry a positive per-message cost")

    fields = {"To": to, "Body": body}
    if sender:
        fields["From"] = sender
    payload = urlencode(fields)
    headers = {
        "Authorization": _basic_auth(secret_key),            # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level de-dupe hint
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, payload, "POST")
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"sms: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"sms: unparseable response (status {status_code})")
    provider_status = str(resp.get("status") or "")
    if status_code in (200, 201, 202) and provider_status in _OK_SEND:
        provider_ref = resp.get("sid") or resp.get("id") or resp.get("message_id")
        return {"out": f"sent sms to {to}",
                "to": to, "from": sender,
                "body_len": int(len(body)),                  # int, not float — no content on the Weft
                "cost": int(cost),                           # int minor units — real money
                "test_mode": test_mode,
                "instruction_eligible": False,               # body/recipient are DATA, never obeyed
                "idempotency_key": idem, "provider_ref": provider_ref,
                "provider_status": provider_status, "rail": "sms"}
    if status_code and 400 <= int(status_code) < 500:        # invalid number — definite no-effect
        msg = (resp.get("error", {}) or {}).get("message") if isinstance(resp.get("error"), dict) \
            else resp.get("error")
        msg = msg or resp.get("message") or provider_status or f"http {status_code}"
        raise executor.ExecError(f"sms: rejected — {msg}")
    # 5xx / anything else after submission — we cannot observe whether it sent.
    raise executor.Ambiguous(f"sms: unexpected response (status {status_code}) — outcome unknown")


def _map_delivery(raw: str) -> str:
    """Map a carrier delivery state to the WEFT §8 trinity — never guessed."""
    s = (raw or "").lower()
    if s in _DELIVERED:
        return executor.SUCCEEDED
    if s in _FAILED:
        return executor.FAILED
    return executor.UNKNOWN                                   # still in flight / unknown


def fetch_status(secret_key: str, args: dict, *, transport=None) -> dict:
    """Fetch a message's delivery status from the carrier (a READ — GET by SID). Returns
    {provider_ref, delivery_status (raw), status (mapped trinity), error_code}. Raises
    `SmsError` on a non-HTTPS endpoint, an unreachable endpoint, or an unparseable body —
    a READ fails closed (the caller records NO status cell).

    HTTPS INVARIANT: a non-`https://` endpoint is refused before the auth is put on the
    wire (no request is made)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        raise SmsError("refusing to send the carrier auth to a non-HTTPS endpoint")
    headers = {
        "Authorization": _basic_auth(secret_key),            # applied here, never returned
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, "", "GET")
    except Exception as e:                                    # network/timeout — unreachable
        raise SmsError(f"carrier status endpoint unreachable: {e}")
    if not isinstance(resp, dict):
        raise SmsError(f"unparseable carrier status response (status {status_code})")
    if not (status_code and 200 <= int(status_code) < 300):
        msg = resp.get("message") or resp.get("error") or f"http {status_code}"
        raise SmsError(f"carrier rejected the status read: {msg}")
    raw = str(resp.get("status") or "")
    return {
        "provider_ref": resp.get("sid") or resp.get("id") or resp.get("message_id"),
        "delivery_status": raw,
        "status": _map_delivery(raw),
        "error_code": resp.get("error_code"),
    }


def delivery_status(k, *, endpoint: str, provider_ref: str, credential_handle: str, broker,
                    agent_cell, transport=None) -> dict:
    """READ a message's delivery status and record it on the Weft as DATA (fail closed).

    Resolves the carrier auth via CRED1 (`broker.use_secret`, applied INSIDE the broker,
    never disclosed), GETs the message by SID against the HTTPS `endpoint`, and on success
    asserts an `sms_status` cell carrying the mapped trinity status, the raw delivery
    state, the `provider_ref` (SID), and any error_code — NEVER the auth. The cell is
    stamped `instruction_eligible=False`: a delivery receipt is DATA to be recalled, never
    text to be obeyed. Returns {sms_status: <cell id>, status, delivery_status,
    provider_ref}. This is a READ — no Morta gate, no money.

    On a denied credential or any engine error (non-HTTPS, unreachable, provider error) it
    records NO cell and returns {"denied": reason}."""
    args = {"endpoint": endpoint, "provider_ref": nfc(str(provider_ref))}
    try:
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: fetch_status(key, args, transport=transport))
    except SmsError as e:
        return {"denied": f"sms: {e}"}                        # fail closed — no status cell
    if "denied" in r:
        return {"denied": r["denied"]}                        # credential handle denied
    result = r["ok"]

    content = {
        "provider_ref": result.get("provider_ref"),
        "delivery_status": result.get("delivery_status"),
        "status": result.get("status"),                      # mapped WEFT §8 trinity
        "error_code": result.get("error_code"),
        "instruction_eligible": False,                        # a delivery receipt is DATA, not an instruction
    }
    cid = content_id({"sms_status": content})
    assert_content(k.weft, k.decima_agent_id, cid, SMS_STATUS, content)
    return {
        "sms_status": cid,
        "status": content["status"],
        "delivery_status": content["delivery_status"],
        "provider_ref": content["provider_ref"],
    }


def statuses(k) -> list:
    """All folded `sms_status` cells on the Weft."""
    return list(k.weave().of_type(SMS_STATUS))


def find_message(weave, idempotency_key: str):
    """A prior SUCCEEDED SMS-send receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each send; matching on the caller's key makes a replay a no-op — no
    duplicate text, no double charge (mirrors `comms.find_message`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == SMS
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "sms", endpoint: str, test_mode: bool = False,
                 transport=None) -> str:
    """Register a REAL carrier SMS-send effect and grant Decima an SMS capability to run
    it: a hard message cap (`budget`), Morta `requires_approval` (a text leaves the box and
    costs money, so a human/policy must approve), and a sandbox profile that allows ONLY
    this effect with network pinned to the rail. Returns the capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without a second send — then asks the
    CRED1 broker to apply the carrier auth (`use_secret`) to the real send; the auth never
    leaves the broker. `endpoint` and the rail's `test_mode` are injected by the handler
    (never taken from caller args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_message(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-send
            prev = existing.content
            return {"out": prev.get("out"), "to": prev.get("to"), "from": prev.get("from"),
                    "body_len": prev.get("body_len"), "cost": prev.get("cost"),
                    "test_mode": prev.get("test_mode"),
                    "instruction_eligible": False,
                    "idempotency_key": idem, "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"), "rail": "sms",
                    "idempotent_replay": True}
        # endpoint + test_mode are injected by the rail, never taken from caller args.
        call_args = {**args, "endpoint": endpoint, "test_mode": bool(test_mode)}
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: send(key, call_args, transport=transport))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"sms: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": SMS,
        "budget": int(cap),                                  # hard cap on messages
        "requires_approval": True,                           # Morta gate — a send leaves the box + bills
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)


def register_manifest(k) -> str:
    """Record a discoverable manifest for the SMS/telephony send rail (source="builtin"),
    so the plug-in-or-forge discovery layer can find the real carrier engine before forging
    a new one. A manifest GRANTS NOTHING (manifest.py, Law) — the rail keeps its own gated
    install path; this only makes it findable. Returns the manifest cell id."""
    m = M.capability_manifest(
        "sms",
        description="send an SMS/text message via a telephony carrier (Twilio style)",
        archetype="EFFECT", effect_class=SMS,
        caveats={"requires_approval": True},                 # a send leaves the box and costs money
        source="builtin", version=1,
        tags=["sms", "twilio", "telephony", "messaging", "text"])
    return M.register(k, m)
