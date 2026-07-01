"""Real messaging rail — wrap a REAL carrier (Twilio SMS / SendGrid email style), an
outbound COMMUNICATION effect, over stdlib urllib (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — putting words on a stranger's phone/inbox is an irreversible OUTWARD effect,
and re-rolling a carrier is the liability. A messaging provider is an HTTPS API, so the
real engine rides stdlib `urllib` (zero pip deps). This check drives the rail entirely
OFFLINE via an injected fake transport (the real `urllib` transport is never called), so
the oracle stays deterministic and network-free while proving the full contract:

  - Morta-gated: unapproved → denied, and NO carrier request is made before approval;
  - success: an accepted/queued send (injected 201) → SUCCEEDED receipt carrying the
    carrier `provider_ref` (message id), COMMUNICATION class, and the idempotency key;
  - idempotent replay: the same key returns the prior receipt and makes NO second send;
  - invalid recipient / 4xx → FAILED (nothing left the box);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - HTTPS-only: a non-https endpoint is refused BEFORE any request (key never in clear);
  - dispense-don't-disclose: the raw carrier key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import comms, secrets

CARRIER_KEY = "SK_test_DECIMA_CARRIER_9f3c"
ENDPOINT = "https://api.carrier.example/v1/messages"


def _transport(calls, response):
    """A fake carrier transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL MESSAGING RAIL (wrapped carrier, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("carrier", CARRIER_KEY, service="carrier")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("carrier", decima, "send messages")

    # 1. SUCCESS + Morta gate + provider_ref + idempotency. ─────────────────────────────
    calls = []
    cap = comms.install_rail(
        kk, cap=1000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="msg_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (201, {"sid": "SM_abc123", "status": "queued"})))
    # Morta: no approval yet → denied, and NO carrier request made.
    denied = kk.invoke(kk.weave().get(kk.decima_agent_id), cap,
                       {"channel": "sms", "to": "+15551230001", "body": "hi there",
                        "idempotency_key": "msg-1", "cost": 0})
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no carrier request may be made before Morta approval"
    kk.approve(cap)
    ok = kk.invoke(kk.weave().get(kk.decima_agent_id), cap,
                   {"channel": "sms", "to": "+15551230001", "body": "hi there",
                    "idempotency_key": "msg-1", "cost": 0})
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "SM_abc123" and rc["rail"] == "comms" \
        and rc["effect_class"] == "COMMUNICATION" and rc["channel"] == "sms", rc
    assert len(calls) == 1, calls
    line("  success: Morta-gated (no call pre-approval) → SUCCEEDED receipt with carrier "
         "provider_ref (message id), COMMUNICATION class ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second send. ─────────
    before = len(calls)
    again = kk.invoke(kk.weave().get(kk.decima_agent_id), cap,
                      {"channel": "sms", "to": "+15551230001", "body": "hi there",
                       "idempotency_key": "msg-1", "cost": 0})
    assert again["status"] == "SUCCEEDED", again
    rc2 = kk.weave().get(again["result_cell"]).content
    assert rc2.get("idempotent_replay") is True and rc2["provider_ref"] == "SM_abc123", rc2
    assert len(calls) == before, "a replay must not make a second carrier send"
    line("  idempotent replay: same key → prior receipt, no second send ✓")

    # 3. INVALID RECIPIENT (4xx) → FAILED (nothing left the box). ───────────────────────
    bcalls = []
    cap_b = comms.install_rail(
        kk, cap=1000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="msg_bad", endpoint=ENDPOINT,
        transport=_transport(bcalls, (400, {"error": {"message": "invalid 'to' number"}})))
    kk.approve(cap_b)
    bad = kk.invoke(kk.weave().get(kk.decima_agent_id), cap_b,
                    {"channel": "sms", "to": "not-a-number", "body": "x",
                     "idempotency_key": "msg-2", "cost": 0})
    assert bad["status"] == "FAILED", bad
    line("  invalid recipient (4xx) → FAILED receipt — a definite no-effect (nothing sent) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ──────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = comms.install_rail(
        kk, cap=1000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="msg_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = kk.invoke(kk.weave().get(kk.decima_agent_id), cap_t,
                    {"channel": "email", "to": "user@example.com", "subject": "hey",
                     "body": "y", "idempotency_key": "msg-3", "cost": 0})
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 5. HTTPS-ONLY — a non-https endpoint is refused BEFORE any request. ───────────────
    hcalls = []
    cap_h = comms.install_rail(
        kk, cap=1000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="msg_http", endpoint="http://api.carrier.example/v1/messages",
        transport=_transport(hcalls, (201, {"sid": "SM_x", "status": "queued"})))
    kk.approve(cap_h)
    ref = kk.invoke(kk.weave().get(kk.decima_agent_id), cap_h,
                    {"channel": "sms", "to": "+15551230009", "body": "z",
                     "idempotency_key": "msg-4", "cost": 0})
    assert ref["status"] == "FAILED", ref
    assert hcalls == [], "a non-HTTPS endpoint must be refused before any carrier request"
    line("  HTTPS-only: a non-https endpoint is refused before any request — the carrier "
         "key never goes on the wire in clear ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ──────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert CARRIER_KEY not in all_payloads, \
        "a raw carrier key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw carrier key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real messaging carrier is wrapped over stdlib urllib (zero deps): Morta-"
         "gated, idempotent, receipts map queued/invalid/timeout → SUCCEEDED/FAILED/UNKNOWN "
         "with provider_ref; HTTPS-only; recipient/content are untrusted data; key never disclosed.")
