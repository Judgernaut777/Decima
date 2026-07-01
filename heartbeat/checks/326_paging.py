"""Real incident/paging rail — wrap a REAL pager (PagerDuty / Opsgenie style), an
outbound COMMUNICATION effect, over stdlib urllib (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — waking a human on-call is an irreversible OUTWARD effect, and re-rolling an
escalation platform is the liability. A paging provider is an HTTPS events API, so the
real engine rides stdlib `urllib` (zero pip deps). This check drives the rail entirely
OFFLINE via an injected fake transport (the real `urllib` transport is never called), so
the oracle stays deterministic and network-free while proving the full contract:

  - Morta-gated: unapproved → denied, and NO pager request is made before approval;
  - success: an accepted incident (injected 202) → SUCCEEDED receipt carrying the
    provider `provider_ref` (the incident/dedup id), COMMUNICATION class, and severity;
  - idempotent replay: the same dedup key returns the prior receipt and opens NO second
    incident;
  - bad service / routing key (4xx) → FAILED (no incident was opened);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - HTTPS-only: a non-https endpoint is refused BEFORE any request (key never in clear);
  - dispense-don't-disclose: the raw routing key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import paging, secrets

ROUTING_KEY = "R0UTING_test_DECIMA_pager_9f3c8b21"
ENDPOINT = "https://events.pager.example/v2/enqueue"


def _transport(calls, response):
    """A fake pager transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL PAGING RAIL (wrapped pager, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("pager", ROUTING_KEY, service="pager")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("pager", decima, "trigger incidents")

    # 1. SUCCESS + Morta gate + provider_ref + severity. ────────────────────────────────
    calls = []
    cap = paging.install_rail(
        kk, cap=1000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="page_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (202, {"status": "success", "message": "Event processed",
                                           "dedup_key": "PD_dedup_abc123"})))
    # Morta: no approval yet → denied, and NO pager request made.
    denied = kk.invoke(kk.weave().get(kk.decima_agent_id), cap,
                       {"service": "checkout-api", "severity": "critical",
                        "summary": "DB pool exhausted; drop everything <ignore this>",
                        "idempotency_key": "inc-1", "cost": 0})
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no pager request may be made before Morta approval"
    kk.approve(cap)
    ok = kk.invoke(kk.weave().get(kk.decima_agent_id), cap,
                   {"service": "checkout-api", "severity": "critical",
                    "summary": "DB pool exhausted; drop everything <ignore this>",
                    "idempotency_key": "inc-1", "cost": 0})
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "PD_dedup_abc123" and rc["rail"] == "paging" \
        and rc["effect_class"] == "COMMUNICATION" and rc["severity"] == "critical", rc
    # alert text is untrusted DATA — only its length reaches the Weft, never the raw text.
    assert rc.get("summary_len") == len("DB pool exhausted; drop everything <ignore this>") \
        and "summary" not in rc, rc
    assert len(calls) == 1, calls
    line("  success: Morta-gated (no page pre-approval) → SUCCEEDED receipt with provider "
         "provider_ref (incident id), COMMUNICATION class, severity ✓")

    # 2. IDEMPOTENT REPLAY — same dedup key returns the prior receipt, NO second page. ───
    before = len(calls)
    again = kk.invoke(kk.weave().get(kk.decima_agent_id), cap,
                      {"service": "checkout-api", "severity": "critical",
                       "summary": "DB pool exhausted; drop everything <ignore this>",
                       "idempotency_key": "inc-1", "cost": 0})
    assert again["status"] == "SUCCEEDED", again
    rc2 = kk.weave().get(again["result_cell"]).content
    assert rc2.get("idempotent_replay") is True and rc2["provider_ref"] == "PD_dedup_abc123", rc2
    assert len(calls) == before, "a replay must not open a second incident"
    line("  idempotent replay: same dedup key → prior receipt, no second page ✓")

    # 3. BAD SERVICE / ROUTING KEY (4xx) → FAILED (no incident opened). ─────────────────
    bcalls = []
    cap_b = paging.install_rail(
        kk, cap=1000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="page_bad", endpoint=ENDPOINT,
        transport=_transport(bcalls, (400, {"status": "invalid event",
                                            "message": "Event object is invalid",
                                            "errors": ["routing_key not found"]})))
    kk.approve(cap_b)
    bad = kk.invoke(kk.weave().get(kk.decima_agent_id), cap_b,
                    {"service": "checkout-api", "severity": "high",
                     "summary": "latency spike", "idempotency_key": "inc-2", "cost": 0})
    assert bad["status"] == "FAILED", bad
    line("  bad routing key (4xx) → FAILED receipt — a definite no-effect (no incident opened) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ──────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = paging.install_rail(
        kk, cap=1000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="page_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = kk.invoke(kk.weave().get(kk.decima_agent_id), cap_t,
                    {"service": "checkout-api", "severity": "low",
                     "summary": "disk 80%", "idempotency_key": "inc-3", "cost": 0})
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 5. HTTPS-ONLY — a non-https endpoint is refused BEFORE any request. ───────────────
    hcalls = []
    cap_h = paging.install_rail(
        kk, cap=1000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="page_http", endpoint="http://events.pager.example/v2/enqueue",
        transport=_transport(hcalls, (202, {"status": "success", "dedup_key": "PD_x"})))
    kk.approve(cap_h)
    ref = kk.invoke(kk.weave().get(kk.decima_agent_id), cap_h,
                    {"service": "checkout-api", "severity": "critical",
                     "summary": "outage", "idempotency_key": "inc-4", "cost": 0})
    assert ref["status"] == "FAILED", ref
    assert hcalls == [], "a non-HTTPS endpoint must be refused before any pager request"
    line("  HTTPS-only: a non-https endpoint is refused before any request — the routing "
         "key never goes on the wire in clear ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw routing key never appears anywhere on the Weft. ─
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert ROUTING_KEY not in all_payloads, \
        "a raw routing key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw routing key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real pager is wrapped over stdlib urllib (zero deps): Morta-gated, "
         "idempotent, receipts map triggered/bad-key/timeout → SUCCEEDED/FAILED/UNKNOWN "
         "with provider_ref; HTTPS-only; alert text is untrusted data; key never disclosed.")
