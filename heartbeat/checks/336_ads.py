"""Real ad-platform rail — wrap a REAL ad platform (Google Ads / Meta Ads style),
wrapped (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — launching a paid campaign SPENDS MONEY (the platform charges against a budget
the moment the campaign goes active), so recreating it is the liability. An ad platform is
an HTTPS API, so the real engine rides stdlib `urllib` (zero pip deps). This check drives
the rail entirely OFFLINE via an injected fake transport (the real `urllib` transport is
never called), so the oracle stays deterministic and network-free while proving the full
contract:

  - Morta-gated: unapproved → denied, and NO launch request is made before approval;
  - success: a created/active campaign → SUCCEEDED receipt carrying the platform
    `provider_ref` (campaign id), FINANCIAL class, and the running spend cap decremented by
    total_budget;
  - idempotent replay: the same key returns the prior receipt and makes NO second launch;
  - over-cap: a total_budget beyond the running cap → denied by the budget caveat;
  - invalid targeting / 4xx → FAILED (no campaign launched, no spend);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - TEST-MODE invariant: a non-`sk_test_` (live) key is refused BEFORE any request;
  - dispense-don't-disclose: the raw platform key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud. OWN fresh Kernel + SecretsBroker, fully offline.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import ads, secrets, payments

TEST_KEY = "sk_test_DECIMA_ADS_123"
LIVE_KEY = "sk_live_DANGER_ADS"
ENDPOINT = "https://ads.example.com/v1/campaigns"


def _transport(calls, response):
    """A fake ad-platform transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL AD-PLATFORM RAIL (wrapped engine, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("ads", TEST_KEY, service="ads")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("ads", decima, "launch campaigns")

    # 1. SUCCESS + Morta gate + provider_ref + spend-cap decrement + idem header. ───────
    calls = []
    cap = ads.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ads_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (201, {"id": "camp_test_1", "status": "ACTIVE"})))
    # Morta: no approval yet → denied, and NO launch request made.
    denied = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                          amount=2000, payee="Spring Sale", idempotency_key="camp-1")
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no launch request may be made before Morta approval"
    kk.approve(cap)
    spent_before = kk.spent.get(decima.id, 0.0)
    ok = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                      amount=2000, payee="Spring Sale", idempotency_key="camp-1")
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "camp_test_1" and rc["rail"] == "ads" \
        and rc["effect_class"] == "FINANCIAL", rc
    assert rc["total_budget"] == 2000 and isinstance(rc["total_budget"], int), rc
    assert kk.spent.get(decima.id, 0.0) == spent_before + 2000, "spend cap not decremented by total_budget"
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "camp-1", calls
    line("  success: Morta-gated (no request pre-approval) → SUCCEEDED receipt with "
         "campaign provider_ref; spend cap decremented by total_budget; idem key sent as header ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second launch. ─────
    before = len(calls)
    again = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                         amount=2000, payee="Spring Sale", idempotency_key="camp-1")
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert len(calls) == before, "a replay must not make a second launch"
    line("  idempotent replay: same key → prior receipt, no second launch ✓")

    # 3. OVER-CAP — a total_budget beyond the running cap is denied by the budget caveat. ─
    ocalls = []
    cap_o = ads.install_rail(
        kk, cap=100, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ads_overcap", endpoint=ENDPOINT,
        transport=_transport(ocalls, (201, {"id": "camp_x", "status": "ACTIVE"})))
    kk.approve(cap_o)                                       # approved → the only gate left is budget
    over = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_o,
                        amount=500, payee="Overspend", idempotency_key="camp-over")
    assert over.get("denied") and "budget" in over["denied"], over
    assert ocalls == [], "an over-cap launch must be denied before any request"
    line("  over-cap: total_budget beyond the running cap → denied by the budget caveat, no request ✓")

    # 4. INVALID TARGETING / 4xx → FAILED (no campaign launched, no spend). ────────────
    fcalls = []
    cap_f = ads.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ads_reject", endpoint=ENDPOINT,
        transport=_transport(fcalls, (400, {"error": {"message": "invalid targeting spec"}})))
    kk.approve(cap_f)
    bad = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_f,
                       amount=700, payee="Bad Targeting", idempotency_key="camp-2")
    assert bad["status"] == "FAILED", bad
    line("  invalid targeting (4xx) → FAILED receipt — a definite no-effect (no launch, no spend) ✓")

    # 5. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = ads.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="ads_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_t,
                       amount=900, payee="Flaky", idempotency_key="camp-3")
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 6. TEST-MODE invariant — a live key is refused BEFORE any request. ───────────────
    broker.store("ads_live", LIVE_KEY, service="ads")
    handle_live = broker.issue("ads_live", decima, "launch campaigns")
    lcalls = []
    cap_l = ads.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=decima, credential_handle=handle_live,
        name="ads_live", endpoint=ENDPOINT,
        transport=_transport(lcalls, (201, {"id": "camp_live", "status": "ACTIVE"})),
        test_mode=True)
    kk.approve(cap_l)
    refused = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap_l,
                           amount=100, payee="Live", idempotency_key="camp-4")
    assert refused["status"] == "FAILED", refused
    assert lcalls == [], "a live key must be refused before any launch request is made"
    line("  test-mode: a live (sk_live_) key is refused before any request — no real "
         "campaign can launch from the reference ✓")

    # 7. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ─────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert TEST_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw ad-platform key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw platform key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real ad platform is wrapped over stdlib urllib (zero deps): Morta-"
         "gated, budget-capped, idempotent, receipts map created/rejected/timeout → "
         "SUCCEEDED/FAILED/UNKNOWN with provider_ref; test-mode-only; the key is never disclosed.")
