"""Real KYC / identity-verification rail — wrap the compliant engine (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — recreating identity verification (KYC/AML) is itself the compliance liability.
A Persona/Onfido-style provider is an HTTPS API, so the real, compliant engine rides
stdlib `urllib` (zero pip deps). This check drives the rail entirely OFFLINE via an
injected fake transport (the real `urllib` transport is never called), proving:

  - VERIFIED: an injected approved response → a `kyc_result` cell with status VERIFIED
    carrying the provider's inquiry id (provider_ref); the determination is the provider's;
  - REJECTED: an injected rejected response → status REJECTED (never silently passed);
  - network error / timeout → status PENDING (outcome unobservable — never fabricated as
    VERIFIED);
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (the key never
    goes on a cleartext wire);
  - dispense-don't-disclose: the raw provider API key never appears in any event payload
    on the Weft (CRED1 applies it inside the broker).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import kyc
from decima.secrets import SecretsBroker

API_KEY = "persona_sk_live_SUPER_SECRET_VALUE"
ENDPOINT = "https://withpersona.com/api/v1/inquiries"


def _transport(calls, response):
    """A fake KYC transport: records each call and returns `response` (a (status, json)
    tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL KYC RAIL (wrapped identity-verification engine, offline) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = SecretsBroker(kk)
    broker.store("persona", API_KEY, service="persona")
    handle = broker.issue("persona", _decima(kk), "kyc identity verification")

    applicant = {"reference_id": "applicant-42",
                 "document_number": "P<UTOPIA1234", "full_name": "A. Person"}

    # 1. VERIFIED — the provider approves; the determination is the provider's word. ─────
    calls = []
    res = kyc.run_check(
        kk, endpoint=ENDPOINT, applicant=applicant, credential_handle=handle,
        broker=broker, agent_cell=_decima(kk),
        transport=_transport(calls, (200, {"data": {"id": "inq_ABC123",
                                                    "attributes": {"status": "approved"}}})))
    assert res["status"] == kyc.VERIFIED and res["provider_ref"] == "inq_ABC123", res
    cell = kk.weave().get(res["kyc_result"]).content
    assert cell["status"] == kyc.VERIFIED and cell["provider_ref"] == "inq_ABC123", cell
    assert cell["subject"] == "applicant-42" and cell["instruction_eligible"] is False, cell
    assert len(calls) == 1 and calls[0]["url"] == ENDPOINT, calls
    line("  VERIFIED: approved response → kyc_result cell with status VERIFIED + provider_ref "
         "(the provider decides, not Decima) ✓")

    # 2. REJECTED — a declined applicant is surfaced, never silently passed. ─────────────
    rej = kyc.run_check(
        kk, endpoint=ENDPOINT, applicant=applicant, credential_handle=handle,
        broker=broker, agent_cell=_decima(kk),
        transport=_transport([], (200, {"data": {"id": "inq_DEF456",
                                                 "attributes": {"status": "declined",
                                                                "reasons": ["document_expired"]}}})))
    assert rej["status"] == kyc.REJECTED, rej
    rcell = kk.weave().get(rej["kyc_result"]).content
    assert rcell["status"] == kyc.REJECTED and "document_expired" in rcell["reasons"], rcell
    line("  REJECTED: declined response → status REJECTED (surfaced with reasons, never "
         "silently passed) ✓")

    # 3. NETWORK ERROR / TIMEOUT → PENDING (outcome unobservable, NOT fabricated). ──────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    pend = kyc.run_check(
        kk, endpoint=ENDPOINT, applicant=applicant, credential_handle=handle,
        broker=broker, agent_cell=_decima(kk), transport=_transport(tcalls, boom))
    assert pend["status"] == kyc.PENDING and pend["provider_ref"] is None, pend
    pcell = kk.weave().get(pend["kyc_result"]).content
    assert pcell["status"] == kyc.PENDING, pcell
    line("  timeout → PENDING kyc_result — outcome unobservable, never fabricated as VERIFIED ✓")

    # 4. HTTPS-ONLY — a non-HTTPS endpoint is refused before any request. ───────────────
    http_calls = []
    refused = False
    try:
        kyc.verify(API_KEY, {**applicant, "endpoint": "http://withpersona.com/api/v1/inquiries"},
                   transport=_transport(http_calls, (200, {"data": {"id": "x", "attributes": {"status": "approved"}}})))
    except kyc.KYCError as e:
        refused = "HTTPS" in str(e)
    assert refused, "a non-HTTPS endpoint must raise KYCError before sending the key"
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: a non-HTTPS endpoint is refused before the API key is sent ✓")

    # 5. DISPENSE-DON'T-DISCLOSE — the raw API key never appears on the Weft. ───────────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in all_payloads, \
        "the raw KYC API key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw KYC API key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real, compliant KYC engine is wrapped over stdlib urllib (zero deps): the "
         "provider decides VERIFIED/REJECTED, unobservable outcomes stay PENDING (never "
         "fabricated), the endpoint is HTTPS-only, and the key is never disclosed.")
