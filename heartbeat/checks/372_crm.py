"""Real CRM write rail — wrap the system of record, WRITE contacts/companies/deals
(dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — a CRM is the SYSTEM OF RECORD for customers and revenue, and re-rolling that
store is the liability. A CRM is an HTTPS API, so the real engine rides stdlib `urllib`
(zero pip deps). This check drives the rail entirely OFFLINE via an injected fake
transport (the real `urllib` transport is never called), so the oracle stays
deterministic and network-free while proving the full contract:

  - Morta-gated: unapproved → denied, and NO CRM request is made before approval;
  - success: a created record → SUCCEEDED receipt carrying the CRM `provider_ref`, the CRM
    effect_class, and the idempotency key sent as the CRM Idempotency-Key header;
  - idempotent replay: the same key returns the prior receipt and makes NO second write;
  - invalid field / 4xx → FAILED (nothing was written);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - HTTPS-only invariant: a non-`https://` endpoint is refused BEFORE any request;
  - dispense-don't-disclose: the raw CRM key never appears on the Weft (receipts, audits,
    any event) — CRED1 applies it inside the broker;
  - the manifest is discoverable in the registry (effect_class CRM, requires_approval).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import crm_engine, secrets, manifest

CRM_KEY = "crm_sk_DECIMA_secret_123"                # a raw CRM key that must never leak


def _transport(calls, response):
    """A fake CRM transport: records each call and returns `response` (a (status, json)
    tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _agent(kk):
    """A FRESH decima agent cell — refetched before each invoke (its spend/lease state
    advances on the Weave, so a stale cell must never drive a later invoke)."""
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL CRM WRITE RAIL (wrapped system of record, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("crm", CRM_KEY, service="crm")
    handle = broker.issue("crm", _agent(kk), "write CRM records")
    HTTPS = "https://api.crm.example/v1/records"

    # 1. SUCCESS + Morta gate + provider_ref + idempotency header. ─────────────────────
    calls = []
    cap = crm_engine.install_rail(
        kk, cap=1000, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="crm_ok", endpoint=HTTPS,
        transport=_transport(calls, (201, {"id": "rec_001", "status": "created"})))
    args = {"kind": "contact", "fields": {"email": "ada@example.com", "name": "Ada"},
            "external_id": "ext-1", "idempotency_key": "crm-1", "cost": 0}
    # Morta: no approval yet → denied, and NO CRM request made.
    denied = kk.invoke(_agent(kk), cap, args)
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no CRM request may be made before Morta approval"
    kk.approve(cap)
    ok = kk.invoke(_agent(kk), cap, args)
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "rec_001" and rc["rail"] == "crm", rc
    assert rc["effect_class"] == "CRM" and rc["kind"] == "contact", rc
    assert rc["provider_status"] == "created" and rc["field_count"] == 2, rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "crm-1", calls
    assert calls[0]["url"] == HTTPS, calls
    line("  success: Morta-gated (no call pre-approval) → SUCCEEDED receipt with CRM "
         "provider_ref; idempotency key sent as the CRM header ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior record, NO second write. ───────
    before = len(calls)
    again = kk.invoke(_agent(kk), cap, args)
    assert again["status"] == "SUCCEEDED", again
    rc2 = kk.weave().get(again["result_cell"]).content
    assert rc2.get("idempotent_replay") is True, rc2
    assert rc2["provider_ref"] == "rec_001", rc2
    assert len(calls) == before, "a replay must not make a second CRM write"
    line("  idempotent replay: same key → prior record id, no second write ✓")

    # 3. INVALID FIELD / 4xx → FAILED (nothing was written). ───────────────────────────
    fcalls = []
    cap_f = crm_engine.install_rail(
        kk, cap=1000, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="crm_bad", endpoint=HTTPS,
        transport=_transport(fcalls, (422, {"error": {"message": "invalid field: phone"}})))
    kk.approve(cap_f)
    bad = kk.invoke(_agent(kk), cap_f, {"kind": "company", "fields": {"phone": "???"},
                                        "idempotency_key": "crm-2", "cost": 0})
    assert bad["status"] == "FAILED", bad
    assert len(fcalls) == 1, "the 4xx write was attempted once and definitively failed"
    line("  invalid field (4xx) → FAILED receipt — a definite no-effect (nothing written) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    cap_t = crm_engine.install_rail(
        kk, cap=1000, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="crm_timeout", endpoint=HTTPS, transport=_transport([], boom))
    kk.approve(cap_t)
    unk = kk.invoke(_agent(kk), cap_t, {"kind": "deal", "fields": {"name": "Q3 renewal"},
                                        "idempotency_key": "crm-3", "cost": 0})
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 5. HTTPS-ONLY invariant — a non-HTTPS endpoint is refused BEFORE any request. ────
    hcalls = []
    cap_h = crm_engine.install_rail(
        kk, cap=1000, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="crm_http", endpoint="http://api.crm.example/v1/records",
        transport=_transport(hcalls, (201, {"id": "rec_x", "status": "created"})))
    kk.approve(cap_h)
    refused = kk.invoke(_agent(kk), cap_h, {"kind": "contact", "fields": {"email": "x@x.com"},
                                            "idempotency_key": "crm-4", "cost": 0})
    assert refused["status"] == "FAILED", refused
    assert hcalls == [], "a non-HTTPS endpoint must be refused before any CRM request/key on the wire"
    line("  HTTPS-only: a non-https endpoint is refused before any request — the key never "
         "travels in cleartext ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw CRM key never appears anywhere on the Weft. ─
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert CRM_KEY not in all_payloads, \
        "a raw CRM key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw CRM key on the Weft — CRED1 applies it inside the broker ✓")

    # 7. DISCOVERABLE MANIFEST — registered and findable in the registry. ──────────────
    mid = crm_engine.register_manifest(kk)
    assert mid, "register_manifest must return a manifest cell id"
    m = manifest.get(kk, "crm")
    assert m is not None and m.content["effect_class"] == "CRM", m
    assert m.content["archetype"] == "EFFECT", m.content
    assert m.content["caveats"].get("requires_approval") is True, m.content
    found = manifest.find(kk, query="contact")
    assert any(c.content["name"] == "crm" for c in found), "crm manifest must be discoverable by query"
    line("  manifest 'crm' (EFFECT, effect_class CRM, requires_approval) discoverable in the registry ✓")

    line("  → the real CRM engine is wrapped over stdlib urllib (zero deps): Morta-gated, "
         "idempotent, receipts map created/rejected/timeout → SUCCEEDED/FAILED/UNKNOWN with "
         "provider_ref; HTTPS-only; record fields are untrusted data; the key is never disclosed.")
