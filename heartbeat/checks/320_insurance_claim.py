"""Real insurance-claim rail — wrap the carrier's filing engine (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — FILING a claim is an outward legal/financial submission to a real carrier, so
faking it IS the liability. A carrier's claims API is an HTTPS endpoint, so the real engine
rides stdlib `urllib` (zero pip deps). This check drives the rail entirely OFFLINE via an
injected fake transport (the real `urllib` transport is never called), so the oracle stays
deterministic and network-free while proving the full contract:

  - Morta-gated: an unapproved filing → denied, and NO carrier request is made pre-approval;
  - success: an injected 201 → SUCCEEDED receipt carrying the carrier `provider_ref` (claim
    id), LEGAL class, the claimed amount (int), and the idempotency key sent as the header;
  - idempotent replay: the same key → the prior receipt, NO second filing (no duplicate claim);
  - bad request / 4xx (invalid policy) → FAILED (nothing was filed);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - HTTPS-only invariant: a non-`https://` endpoint is refused BEFORE any request;
  - dispense-don't-disclose: the raw carrier key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud. OWN fresh Kernel + SecretsBroker, fully offline.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import insurance_claim, secrets

CARRIER_KEY = "sk_carrier_DECIMA_SECRET_123"
ENDPOINT = "https://api.acme-carrier.example/v1/claims"
HTTP_ENDPOINT = "http://api.acme-carrier.example/v1/claims"   # cleartext — must be refused


def _transport(calls, response):
    """A fake carrier transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _agent(kk):
    """A FRESH Decima agent cell (its envelope grows as caps/handles are granted)."""
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL INSURANCE-CLAIM RAIL (wrapped carrier, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("carrier", CARRIER_KEY, service="acme-carrier")
    handle = broker.issue("carrier", _agent(kk), "file insurance claims")

    common = dict(policy="POL-889", description="water damage to kitchen ceiling",
                  incident_date=1_719_792_000, amount=250_000,
                  evidence=["blob:photo1", "blob:invoice"])

    # 1. SUCCESS + Morta gate + provider_ref + idempotency header. ─────────────────────
    calls = []
    cap = insurance_claim.install_rail(
        kk, cap=5, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="claim_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (201, {"claim_id": "CLM-0001", "status": "under_review"})))
    # Morta: no approval yet → denied, and NO carrier request made.
    denied = kk.invoke(_agent(kk), cap, {**common, "idempotency_key": "clm-1", "cost": 0})
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no carrier request may be made before Morta approval"
    kk.approve(cap)
    ok = kk.invoke(_agent(kk), cap, {**common, "idempotency_key": "clm-1", "cost": 0})
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "CLM-0001" and rc["rail"] == "insclaim", rc
    assert rc["effect_class"] == "LEGAL" and rc["provider_status"] == "under_review", rc
    assert rc["claimed_amount"] == 250_000 and isinstance(rc["claimed_amount"], int), rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "clm-1", calls
    assert calls[0]["url"] == ENDPOINT, calls
    line("  success: Morta-gated (no filing pre-approval) → SUCCEEDED receipt with carrier "
         "provider_ref (claim id); idempotency key sent as the carrier header ✓")

    # 2. IDEMPOTENT REPLAY — same key → prior receipt, NO second filing. ───────────────
    before = len(calls)
    again = kk.invoke(_agent(kk), cap, {**common, "idempotency_key": "clm-1", "cost": 0})
    assert again["status"] == "SUCCEEDED", again
    ar = kk.weave().get(again["result_cell"]).content
    assert ar.get("idempotent_replay") is True, ar
    assert ar["provider_ref"] == "CLM-0001", ar
    assert len(calls) == before, "a replay must not file a second claim (no duplicate)"
    line("  idempotent replay: same key → prior receipt, no second filing (no duplicate claim) ✓")

    # 3. BAD REQUEST / 4xx (invalid policy) → FAILED (nothing filed). ──────────────────
    bcalls = []
    cap_b = insurance_claim.install_rail(
        kk, cap=5, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="claim_bad", endpoint=ENDPOINT,
        transport=_transport(bcalls, (400, {"error": {"message": "unknown policy reference"}})))
    kk.approve(cap_b)
    bad = kk.invoke(_agent(kk), cap_b, {**common, "policy": "POL-BOGUS",
                                        "idempotency_key": "clm-2", "cost": 0})
    assert bad["status"] == "FAILED", bad
    assert len(bcalls) == 1, "the bad request was sent once and definitively rejected"
    line("  bad request (4xx, invalid policy) → FAILED receipt — a definite no-effect (nothing filed) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = insurance_claim.install_rail(
        kk, cap=5, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="claim_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = kk.invoke(_agent(kk), cap_t, {**common, "idempotency_key": "clm-3", "cost": 0})
    assert unk["status"] == "UNKNOWN", unk
    ur = kk.weave().get(unk["result_cell"]).content
    assert ur.get("out") is None, "UNKNOWN must not fabricate an outcome"
    line("  timeout → UNKNOWN receipt — we cannot observe whether it filed, never fabricated ✓")

    # 5. HTTPS-ONLY — a non-https endpoint is refused BEFORE any request. ──────────────
    hcalls = []
    cap_h = insurance_claim.install_rail(
        kk, cap=5, broker=broker, agent_cell=_agent(kk), credential_handle=handle,
        name="claim_http", endpoint=HTTP_ENDPOINT,
        transport=_transport(hcalls, (201, {"claim_id": "CLM-X", "status": "received"})))
    kk.approve(cap_h)
    refused = kk.invoke(_agent(kk), cap_h, {**common, "idempotency_key": "clm-4", "cost": 0})
    assert refused["status"] == "FAILED", refused
    assert hcalls == [], "a non-HTTPS endpoint must be refused before any carrier request"
    line("  HTTPS-only: a cleartext (http://) endpoint is refused before any request — the "
         "claim and the key never travel in cleartext ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw carrier key never appears on the Weft. ──────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert CARRIER_KEY not in all_payloads, \
        "a raw carrier key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw carrier key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real carrier engine is wrapped over stdlib urllib (zero deps): Morta-"
         "gated, idempotent (no duplicate filing), receipts map received/rejected/timeout → "
         "SUCCEEDED/FAILED/UNKNOWN with provider_ref; HTTPS-only; the key is never disclosed.")
