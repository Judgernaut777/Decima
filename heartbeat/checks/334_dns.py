"""Real DNS / domains rail — wrap a REAL provider (Route53 / Namecheap style), offline.

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — a live DNS record change is an irreversible OUTWARD infrastructure effect (it
can take a service down or hijack a name the instant it propagates), and a registration
spends money, so re-rolling the zone controller/registrar IS the liability. A DNS/domains
provider is just an HTTPS API, so the real engine rides stdlib `urllib` (zero pip deps).
This check drives the rail entirely OFFLINE via an injected fake transport (the real
`urllib` transport is never called), so the oracle stays deterministic and network-free
while proving the full contract:

  - Morta-gated: unapproved change → denied, and NO provider request is made pre-approval;
  - success: an applied change → SUCCEEDED receipt carrying the provider `provider_ref`
    (the change id), INFRA class, ttl (int), and the idempotency key;
  - idempotent replay: the same key returns the prior receipt and makes NO second apply;
  - invalid zone / record (4xx) → FAILED (no record changed);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (key never leaks);
  - dispense-don't-disclose: the raw provider key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud. OWN fresh Kernel + SecretsBroker; fully offline.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import dns, secrets

API_KEY = "dnskey_DECIMA_super_secret_123"


def _transport(calls, response):
    """A fake DNS-provider transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL DNS / DOMAINS RAIL (wrapped engine, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("dns", API_KEY, service="dns")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("dns", decima, "apply dns records")
    ENDPOINT = "https://api.dns-provider.example/v1/changes"

    def agent():                                              # a FRESH agent cell per invoke
        return kk.weave().get(kk.decima_agent_id)

    # 1. SUCCESS + Morta gate + provider_ref + idempotency header. ─────────────────────
    calls = []
    cap = dns.install_rail(
        kk, cap=50, broker=broker, agent_cell=decima, credential_handle=handle,
        name="dns_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (200, {"change_id": "chg_A1", "status": "applied"})))
    change = {"zone": "example.com", "name": "www.example.com", "type": "A",
              "value": "203.0.113.7", "ttl": 300, "idempotency_key": "chg-1", "cost": 0}
    # Morta: no approval yet → denied, and NO provider request made.
    denied = kk.invoke(agent(), cap, dict(change))
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no DNS request may be made before Morta approval"
    kk.approve(cap)
    ok = kk.invoke(agent(), cap, dict(change))
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "chg_A1" and rc["rail"] == "dns" and rc["effect_class"] == "INFRA", rc
    assert rc["ttl"] == 300 and isinstance(rc["ttl"], int) and not isinstance(rc["ttl"], bool), rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "chg-1", calls
    line("  success: Morta-gated (no request pre-approval) → SUCCEEDED receipt with "
         "provider change id; INFRA class; ttl int; idempotency key sent as header ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second apply. ──────
    before = len(calls)
    again = kk.invoke(agent(), cap, dict(change))
    assert again["status"] == "SUCCEEDED" and again["ok"].get("idempotent_replay") is True, again
    assert again["ok"]["provider_ref"] == "chg_A1", again
    assert len(calls) == before, "a replay must not make a second DNS change"
    line("  idempotent replay: same key → prior receipt, no second apply ✓")

    # 3. INVALID ZONE / RECORD (4xx) → FAILED (no record changed). ─────────────────────
    dcalls = []
    cap_d = dns.install_rail(
        kk, cap=50, broker=broker, agent_cell=decima, credential_handle=handle,
        name="dns_bad", endpoint=ENDPOINT,
        transport=_transport(dcalls, (400, {"error": {"message": "no such hosted zone"}})))
    kk.approve(cap_d)
    bad = kk.invoke(agent(), cap_d, {"zone": "nope.invalid", "name": "x.nope.invalid",
                                     "type": "A", "value": "203.0.113.9", "ttl": 60,
                                     "idempotency_key": "chg-2", "cost": 0})
    assert bad["status"] == "FAILED", bad
    assert len(dcalls) == 1, "an invalid zone is only known after the provider replies 4xx"
    line("  invalid zone (4xx) → FAILED receipt — a definite no-effect (no record changed) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = dns.install_rail(
        kk, cap=50, broker=broker, agent_cell=decima, credential_handle=handle,
        name="dns_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = kk.invoke(agent(), cap_t, {"zone": "example.com", "name": "mx.example.com",
                                     "type": "MX", "value": "10 mail.example.com", "ttl": 3600,
                                     "idempotency_key": "chg-3", "cost": 0})
    assert unk["status"] == "UNKNOWN", unk
    assert kk.weave().get(unk["result_cell"]).content.get("out") is None, "no fabricated output"
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 5. HTTPS-ONLY — a non-https endpoint is refused BEFORE any request. ──────────────
    hcalls = []
    cap_h = dns.install_rail(
        kk, cap=50, broker=broker, agent_cell=decima, credential_handle=handle,
        name="dns_http", endpoint="http://api.dns-provider.example/v1/changes",
        transport=_transport(hcalls, (200, {"change_id": "chg_X", "status": "applied"})))
    kk.approve(cap_h)
    refused = kk.invoke(agent(), cap_h, {"zone": "example.com", "name": "www.example.com",
                                         "type": "A", "value": "203.0.113.7", "ttl": 300,
                                         "idempotency_key": "chg-4", "cost": 0})
    assert refused["status"] == "FAILED", refused
    assert hcalls == [], "a non-HTTPS endpoint must be refused before any request is made"
    line("  https-only: a non-https endpoint is refused before any request — the key "
         "never travels in cleartext ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ─────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in all_payloads, \
        "a raw DNS provider key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw provider key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real DNS engine is wrapped over stdlib urllib (zero deps): Morta-gated, "
         "idempotent, receipts map applied/invalid/timeout → SUCCEEDED/FAILED/UNKNOWN with "
         "provider_ref; HTTPS-only; record values are untrusted data; the key is never disclosed.")
