"""Real employment background-check rail — wrap the compliant screener (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — recreating an employment background check is itself the compliance liability
(FCRA / adverse-action law is unforgiving). A Checkr-style provider is an HTTPS API, so
the real, compliant engine rides stdlib `urllib` (zero pip deps). This check drives the
rail entirely OFFLINE via an injected fake transport (the real `urllib` transport is never
called), proving:

  - CLEAR: an injected clear response → a `background_check` cell with status CLEAR carrying
    the provider's report id (provider_ref); the determination is the provider's;
  - CONSIDER: an injected consider response → status CONSIDER with the provider's reasons
    (needs human review — never silently cleared);
  - network error / timeout → status PENDING (outcome unobservable — never fabricated as
    CLEAR);
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (the key never goes
    on a cleartext wire);
  - dispense-don't-disclose: the raw provider API key never appears in any event payload on
    the Weft (CRED1 applies it inside the broker).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import background_check as bg
from decima.secrets import SecretsBroker

API_KEY = "checkr_sk_live_SUPER_SECRET_VALUE"
ENDPOINT = "https://api.checkr.com/v1/reports"


def _transport(calls, response):
    """A fake screening transport: records each call and returns `response` (a (status,
    json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL BACKGROUND-CHECK RAIL (wrapped screening engine, offline) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = SecretsBroker(kk)
    broker.store("checkr", API_KEY, service="checkr")
    handle = broker.issue("checkr", _decima(kk), "employment background screening")

    candidate = {"reference_id": "candidate-42", "package": "driver_pro"}

    # 1. CLEAR — the provider clears the report; the determination is the provider's word. ─
    calls = []
    res = bg.run_screen(
        kk, endpoint=ENDPOINT, candidate=candidate, credential_handle=handle,
        broker=broker, agent_cell=_decima(kk),
        transport=_transport(calls, (200, {"data": {"id": "rep_ABC123",
                                                    "attributes": {"result": "clear"}}})))
    assert res["status"] == bg.CLEAR and res["provider_ref"] == "rep_ABC123", res
    cell = kk.weave().get(res["background_check"]).content
    assert cell["status"] == bg.CLEAR and cell["provider_ref"] == "rep_ABC123", cell
    assert cell["subject"] == "candidate-42" and cell["instruction_eligible"] is False, cell
    assert len(calls) == 1 and calls[0]["url"] == ENDPOINT, calls
    line("  CLEAR: clear response → background_check cell with status CLEAR + provider_ref "
         "(the provider decides, not Decima) ✓")

    # 2. CONSIDER — a needs-review report is surfaced with reasons, never silently cleared. ─
    con = bg.run_screen(
        kk, endpoint=ENDPOINT, candidate=candidate, credential_handle=handle,
        broker=broker, agent_cell=_decima(kk),
        transport=_transport([], (200, {"data": {"id": "rep_DEF456",
                                                 "attributes": {"result": "consider",
                                                                "reasons": ["criminal_record"]}}})))
    assert con["status"] == bg.CONSIDER, con
    ccell = kk.weave().get(con["background_check"]).content
    assert ccell["status"] == bg.CONSIDER and "criminal_record" in ccell["reasons"], ccell
    line("  CONSIDER: consider response → status CONSIDER (surfaced with reasons for human "
         "review, never silently cleared) ✓")

    # 3. NETWORK ERROR / TIMEOUT → PENDING (outcome unobservable, NOT fabricated). ─────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    pend = bg.run_screen(
        kk, endpoint=ENDPOINT, candidate=candidate, credential_handle=handle,
        broker=broker, agent_cell=_decima(kk), transport=_transport(tcalls, boom))
    assert pend["status"] == bg.PENDING and pend["provider_ref"] is None, pend
    pcell = kk.weave().get(pend["background_check"]).content
    assert pcell["status"] == bg.PENDING, pcell
    line("  timeout → PENDING background_check — outcome unobservable, never fabricated as CLEAR ✓")

    # 4. HTTPS-ONLY — a non-HTTPS endpoint is refused before any request. ──────────────────
    http_calls = []
    refused = False
    try:
        bg.screen(API_KEY, {**candidate, "endpoint": "http://api.checkr.com/v1/reports"},
                  transport=_transport(http_calls, (200, {"data": {"id": "x", "attributes": {"result": "clear"}}})))
    except bg.BackgroundCheckError as e:
        refused = "HTTPS" in str(e)
    assert refused, "a non-HTTPS endpoint must raise BackgroundCheckError before sending the key"
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: a non-HTTPS endpoint is refused before the API key is sent ✓")

    # 5. DISPENSE-DON'T-DISCLOSE — the raw API key never appears on the Weft. ──────────────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in all_payloads, \
        "the raw screening API key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw screening API key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real, compliant screening engine is wrapped over stdlib urllib (zero deps): "
         "the provider decides CLEAR/CONSIDER, unobservable outcomes stay PENDING (never "
         "fabricated), the endpoint is HTTPS-only, and the key is never disclosed.")
