"""Real cloud-compute rail — wrap a REAL compute provider (AWS EC2 / GCP style), a
FINANCIAL irreversible effect, over stdlib urllib (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — provisioning a compute instance SPENDS MONEY and is irreversible the instant
it starts billing (a reservation is made, a machine boots, the meter runs), and
re-rolling a provider is the liability. A compute provider is an HTTPS API, so the real
engine rides stdlib `urllib` (zero pip deps). This check drives the rail entirely OFFLINE
via an injected fake transport (the real `urllib` transport is never called), so the
oracle stays deterministic and network-free while proving the full contract:

  - Morta-gated: unapproved → denied, and NO provider request is made before approval;
  - success: a running/pending instance (injected 201) → SUCCEEDED receipt carrying the
    provider `provider_ref` (instance/reservation id) + `count`, FINANCIAL class, the
    idempotency key sent as the provider Idempotency-Key header, and the spend cap
    decremented;
  - idempotent replay: the same key returns the prior receipt and makes NO second provision;
  - quota exceeded / invalid instance type (4xx) → FAILED (nothing provisioned, no spend);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - TEST/SANDBOX-MODE invariant: a live (non-`sandbox_`) key is refused BEFORE any request;
  - dispense-don't-disclose: the raw provider key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

The rail is args-compatible with PAY1, so `payments.pay` drives it (amount → est_hourly_cost
→ cost → the running compute spend cap). After `install_rail`/`broker.issue` a FRESH agent
cell is fetched for each `payments.pay` call.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import cloud_compute, secrets, payments

TEST_KEY = "sandbox_DECIMA123"
LIVE_KEY = "AKIA_LIVE_DANGER"
ENDPOINT = "https://ec2.sandbox.example/v1/instances"


def _transport(calls, response):
    """A fake provider transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL CLOUD-COMPUTE RAIL (wrapped provider, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("compute", TEST_KEY, service="ec2")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("compute", decima, "provision instances")

    # 1. SUCCESS + Morta gate + provider_ref + count + idempotency + spend cap. ──────────
    calls = []
    cap = cloud_compute.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="compute_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (201, {"id": "i-0abc123", "status": "pending",
                                           "count": 1})))
    # Morta: no approval yet → denied, and NO provider request made.
    denied = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                          amount=2000, payee="c5.large", idempotency_key="cmp-1")
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no provider request may be made before Morta approval"
    kk.approve(cap)
    ok = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                      amount=2000, payee="c5.large", idempotency_key="cmp-1")
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "i-0abc123" and rc["rail"] == "cloud_compute" \
        and rc["effect_class"] == "FINANCIAL", rc
    assert rc["count"] == 1 and rc["instance_type"] == "c5.large", rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "cmp-1", calls
    assert kk.spent[decima.id] == 2000, kk.spent          # amount → cost → running spend cap
    line("  success: Morta-gated (no call pre-approval) → SUCCEEDED receipt with provider "
         "provider_ref (instance id) + count, FINANCIAL class; idempotency key sent as the "
         "provider header; spend cap decremented ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second provision. ────
    before = len(calls)
    again = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                         amount=2000, payee="c5.large", idempotency_key="cmp-1")
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert len(calls) == before, "a replay must not make a second provision"
    assert kk.spent[decima.id] == 2000, "a replay must not spend again"
    line("  idempotent replay: same key → prior receipt, no second provision, no second spend ✓")

    # 3. QUOTA EXCEEDED / INVALID INSTANCE TYPE (4xx) → FAILED (no money committed). ─────
    bcalls = []
    cap_b = cloud_compute.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="compute_quota", endpoint=ENDPOINT,
        transport=_transport(bcalls, (429, {"error": {"message": "instance quota exceeded"}})))
    kk.approve(cap_b)
    spent_before = kk.spent[decima.id]
    bad = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_b,
                       amount=500, payee="p4d.24xlarge", idempotency_key="cmp-2")
    assert bad["status"] == "FAILED", bad
    assert kk.spent[decima.id] == spent_before, "a FAILED provision must not spend"
    line("  quota exceeded (4xx) → FAILED receipt — a definite no-effect (nothing provisioned, no spend) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ───────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = cloud_compute.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="compute_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_t,
                       amount=700, payee="c5.large", idempotency_key="cmp-3")
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 5. TEST/SANDBOX-MODE invariant — a live key is refused BEFORE any request. ─────────
    broker.store("compute_live", LIVE_KEY, service="ec2")
    handle_live = broker.issue("compute_live", decima, "provision instances")
    lcalls = []
    cap_l = cloud_compute.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle_live,
        name="compute_live", endpoint=ENDPOINT,
        transport=_transport(lcalls, (201, {"id": "i-x", "status": "running", "count": 1})),
        test_mode=True)
    kk.approve(cap_l)
    refused = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_l,
                           amount=100, payee="c5.large", idempotency_key="cmp-4")
    assert refused["status"] == "FAILED", refused
    assert lcalls == [], "a live key must be refused before any provider request is made"
    line("  test-mode: a live (non-sandbox_) key is refused before any request — no real "
         "instance can be provisioned from the reference ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ──────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert TEST_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw provider key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw provider key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real cloud-compute provider is wrapped over stdlib urllib (zero deps): Morta-"
         "gated, spend-capped, idempotent, receipts map running-pending/rejected/timeout → "
         "SUCCEEDED/FAILED/UNKNOWN with provider_ref + count; sandbox-mode-only; the key is "
         "never disclosed.")
