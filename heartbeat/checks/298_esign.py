"""Real e-signature rail — a REAL legal-signature engine, wrapped (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — sending a document out for a legally binding signature (DocuSign /
Dropbox-Sign style) is an OUTWARD, high-liability effect; recreating the signing ceremony
IS the liability. An e-sign provider is an HTTPS API, so the real engine rides stdlib
`urllib` (zero pip deps). This check drives the rail entirely OFFLINE via an injected fake
transport (the real `urllib` transport is never called), so the oracle stays deterministic
and network-free while proving the full contract. Unlike the Stripe rail this is a
COMMUNICATION/LEGAL effect, not FINANCIAL:

  - Morta-gated: unapproved send → denied, and NO provider request is made pre-approval;
  - success: an accepted envelope (201) → SUCCEEDED receipt carrying the provider
    `provider_ref` (the envelope id), COMMUNICATION class, recipient count, and the
    idempotency key sent as the provider Idempotency-Key header;
  - idempotent replay: the same key returns the prior receipt and makes NO second send
    (no duplicate legal envelope);
  - bad request / 4xx → FAILED (nothing was sent);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - HTTPS-only invariant: a non-`https://` endpoint is refused BEFORE any request;
  - dispense-don't-disclose: the raw provider key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud. Owns its OWN fresh Kernel + SecretsBroker.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import esign, secrets

API_KEY = "esk_live_DECIMA_SECRET_KEY_123"       # the provider API key (never leaves CRED1)
ENDPOINT = "https://api.esign.example/v1/envelopes"


def _transport(calls, response):
    """A fake e-sign transport: records each call and returns `response` (a (status, json)
    tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL E-SIGNATURE RAIL (wrapped legal engine, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("esign", API_KEY, service="esign")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("esign", decima, "send legal envelopes")

    # 1. SUCCESS + Morta gate + provider_ref + idempotency header. ─────────────────────
    calls = []
    cap = esign.install_rail(
        kk, cap=10, broker=broker, agent_cell=decima, credential_handle=handle,
        name="esign_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (201, {"envelope_id": "env_abc123", "status": "sent"})))
    # Morta: no approval yet → denied, and NO provider request made.
    denied = esign.send(kk, kk.weave().get(kk.decima_agent_id), cap,
                        idempotency_key="doc-1", document="sha256:deadbeef",
                        recipients=["alice@example.com", "bob@example.com"], subject="Please sign")
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no provider request may be made before Morta approval"
    kk.approve(cap)
    ok = esign.send(kk, kk.weave().get(kk.decima_agent_id), cap,
                    idempotency_key="doc-1", document="sha256:deadbeef",
                    recipients=["alice@example.com", "bob@example.com"], subject="Please sign")
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "env_abc123", rc
    assert rc["rail"] == "esign" and rc["effect_class"] == "COMMUNICATION", rc
    assert rc["recipients"] == 2 and isinstance(rc["recipients"], int), rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "doc-1", calls
    assert calls[0]["url"] == ENDPOINT, calls
    line("  success: Morta-gated (no request pre-approval) → SUCCEEDED receipt with provider "
         "envelope id; idempotency key sent as the provider header; recipient count is an int ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second send. ───────
    before = len(calls)
    again = esign.send(kk, kk.weave().get(kk.decima_agent_id), cap,
                       idempotency_key="doc-1", document="sha256:deadbeef",
                       recipients=["alice@example.com", "bob@example.com"], subject="Please sign")
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert again["provider_ref"] == "env_abc123", again
    assert len(calls) == before, "a replay must not send a second envelope"
    line("  idempotent replay: same key → prior receipt, no duplicate legal envelope ✓")

    # 3. BAD REQUEST (4xx) → FAILED (nothing was sent). ────────────────────────────────
    bcalls = []
    cap_b = esign.install_rail(
        kk, cap=10, broker=broker, agent_cell=decima, credential_handle=handle,
        name="esign_bad", endpoint=ENDPOINT,
        transport=_transport(bcalls, (400, {"error": {"message": "invalid recipient email"}})))
    kk.approve(cap_b)
    bad = esign.send(kk, kk.weave().get(kk.decima_agent_id), cap_b,
                     idempotency_key="doc-2", document="sha256:cafe",
                     recipients=["not-an-email"], subject="Sign")
    assert bad["status"] == "FAILED", bad
    assert len(bcalls) == 1, "the bad request did reach the provider and was rejected"
    line("  bad request (4xx) → FAILED receipt — a definite no-effect (nothing was sent) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = esign.install_rail(
        kk, cap=10, broker=broker, agent_cell=decima, credential_handle=handle,
        name="esign_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = esign.send(kk, kk.weave().get(kk.decima_agent_id), cap_t,
                     idempotency_key="doc-3", document="sha256:beef",
                     recipients=["carol@example.com"], subject="Sign")
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — we cannot observe whether it sent, never fabricated ✓")

    # 5. HTTPS-ONLY invariant — a non-https endpoint is refused BEFORE any request. ────
    hcalls = []
    cap_h = esign.install_rail(
        kk, cap=10, broker=broker, agent_cell=decima, credential_handle=handle,
        name="esign_http", endpoint="http://api.esign.example/v1/envelopes",
        transport=_transport(hcalls, (201, {"envelope_id": "env_x", "status": "sent"})))
    kk.approve(cap_h)
    refused = esign.send(kk, kk.weave().get(kk.decima_agent_id), cap_h,
                         idempotency_key="doc-4", document="sha256:feed",
                         recipients=["dave@example.com"], subject="Sign")
    assert refused["status"] == "FAILED", refused
    assert hcalls == [], "a non-HTTPS endpoint must be refused before any request is made"
    line("  https-only: a non-https endpoint is refused before any request — the document and "
         "key never travel in cleartext ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ─────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in all_payloads, \
        "the raw provider key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw provider key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real e-sign engine is wrapped over stdlib urllib (zero deps): Morta-gated, "
         "idempotent, receipts map sent/rejected/timeout → SUCCEEDED/FAILED/UNKNOWN with the "
         "envelope provider_ref; https-only; the key is never disclosed.")
