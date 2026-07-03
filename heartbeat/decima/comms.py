"""Real messaging rail — wrap a REAL carrier (Twilio SMS / SendGrid email), never
reimplement outbound delivery (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it — putting words on a stranger's phone or inbox is
an irreversible OUTWARD effect, and re-rolling a carrier is itself the liability. A
messaging provider is just an HTTPS API, so the real engine rides stdlib `urllib` with
**zero pip dependencies**: real engine, still pure-stdlib.

This wraps the carrier behind the SAME spine NOTIFY1's outbound split already enforces —
it registers a COMMUNICATION, Morta-gated, budget-capped, idempotent effect via
`kernel.integrate_tool`. The receipt maps the carrier's outcome to WEFT §8 status:
  - an accepted / queued / sent message → SUCCEEDED, carrying the carrier `provider_ref`
                                          (the message id/sid) and the idempotency key;
  - an invalid number / address (4xx)   → FAILED (nothing left the box);
  - a network error / timeout           → UNKNOWN (we cannot observe whether it went out —
                                          never fabricated as success or failure, FOLD §11 #8).

GUARDRAILS (mirroring the Stripe rail):
  - **recipient + content are UNTRUSTED DATA** — `to` / `body` / `subject` are normalized
    and carried as data only; they never become instructions and are never interpolated
    into anything executable (they are the message payload, nothing more).
  - **HTTPS-only** — refuses to send the carrier key to a non-`https://` endpoint before
    any request (never leak the key in cleartext); a definite no-effect (FAILED).
  - **credentials via CRED1** — the carrier key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Morta-gated + idempotent** — a send is denied until the capability is approved; a
    replay of the same idempotency_key returns the prior receipt and sends nothing twice.
  - **Transport seam** — `send` takes a `transport(url, headers, body) -> (status, json)`.
    The default is a real `urllib` POST; tests inject a fake transport, so the offline
    oracle exercises the full contract with NO network.
  - **ints, not floats** in signed content.

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlencode

from decima import executor
from decima.hashing import nfc

COMMUNICATION = "COMMUNICATION"
RESULT = "result"                                # the EffectReceipt cell type the kernel asserts
_CHANNELS = ("sms", "email")
_OK_STATUSES = ("accepted", "queued", "sent", "delivered")


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
        "comms", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def send(secret_key: str, args: dict, *, transport=None) -> dict:
    """Send a message via the carrier, mapping the outcome to an EffectReceipt-shaped
    result. Raises `executor.ExecError` for a definite no-effect (non-HTTPS endpoint,
    bad request, invalid recipient / 4xx → FAILED) and `executor.Ambiguous` for an
    unobservable outcome (network/unexpected → UNKNOWN). On success returns the output
    dict spread into a SUCCEEDED receipt, carrying the carrier `provider_ref`.

    `to` / `body` / `subject` are UNTRUSTED DATA: normalized and carried as the message
    payload only, never interpreted as instructions.

    HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on the
    wire (no request is made)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the carrier key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("comms: refusing to send the carrier key to a non-HTTPS endpoint")

    channel = str(args.get("channel", ""))
    if channel not in _CHANNELS:
        raise executor.ExecError(f"comms: channel must be one of {list(_CHANNELS)}")
    to = nfc(str(args.get("to", "")))                        # UNTRUSTED recipient (data only)
    if not to:
        raise executor.ExecError("comms: a recipient (to) is required")
    body = nfc(str(args.get("body", "")))                    # UNTRUSTED content (data only)
    subject = nfc(str(args.get("subject", "")))              # UNTRUSTED content (email only)
    idem = nfc(str(args.get("idempotency_key") or ""))

    fields = {"channel": channel, "to": to, "body": body}
    if channel == "email":
        fields["subject"] = subject
    if idem:
        fields["idempotency_key"] = idem                     # provider-level de-dupe hint
    payload = urlencode(fields)
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, payload)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"comms: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"comms: unparseable response (status {status_code})")
    provider_status = resp.get("status")
    if status_code in (200, 201, 202) and provider_status in _OK_STATUSES:
        provider_ref = resp.get("sid") or resp.get("id") or resp.get("message_id")
        return {"out": f"sent {channel} to {to}",
                "channel": channel, "to": to,
                "body_len": int(len(body)),                  # int, not float — no content on the Weft
                "idempotency_key": idem, "provider_ref": provider_ref,
                "provider_status": provider_status, "rail": "comms"}
    if status_code and 400 <= status_code < 500:             # invalid number / address — definite no-effect
        msg = (resp.get("error", {}) or {}).get("message") or resp.get("message") \
            or provider_status or f"http {status_code}"
        raise executor.ExecError(f"comms: rejected — {msg}")
    raise executor.Ambiguous(f"comms: unexpected response (status {status_code}) — outcome unknown")


def find_message(weave, idempotency_key: str):
    """A prior SUCCEEDED message receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each send; matching on the caller's key makes a replay a no-op
    (mirrors `payments.find_payment`)."""
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
                 name: str = "comms", endpoint: str, transport=None) -> str:
    """Register a REAL carrier message effect and grant Decima a COMMUNICATION capability
    to run it: a hard message cap (`budget`), Morta `requires_approval` (a send leaves the
    box, so a human/policy must approve), and a sandbox profile that allows ONLY this
    effect with network pinned to the rail. Returns the capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without a second send — then asks the
    CRED1 broker to apply the carrier key (`use_secret`) to the real send; the key never
    leaves the broker. `endpoint` is injected by the handler (never taken from caller
    args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_message(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-send
            prev = existing.content
            return {"out": prev.get("out"), "channel": prev.get("channel"),
                    "to": prev.get("to"), "body_len": prev.get("body_len"),
                    "idempotency_key": idem, "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"), "rail": "comms",
                    "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: send(key, call_args, transport=transport))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"comms: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": COMMUNICATION,
        "budget": int(cap),                                  # hard cap on messages
        "requires_approval": True,                           # Morta gate — a send leaves the box
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
