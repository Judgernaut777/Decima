"""Real payroll rail — a REAL external engine, wrapped (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — recreating payroll is the liability. A payroll provider (Gusto / ADP) is an
HTTPS API, so the real engine rides stdlib `urllib` (zero pip deps). This check drives
the rail entirely OFFLINE via an injected fake transport (the real `urllib` transport is
never called), so the oracle stays deterministic and network-free while proving the full
contract:

  - Morta-gated: unapproved → denied, and NO payroll request is made before approval;
  - success: a submitted run → SUCCEEDED receipt carrying the provider `provider_ref`
    (payroll run id), the headcount, the reconciled total, FINANCIAL class, and the
    idempotency key sent as the provider Idempotency-Key header; the running spend cap is
    decremented by the total;
  - idempotent replay: the same key returns the prior receipt and makes NO second run;
  - unbalanced total (sum(lines) != total_amount) → FAILED, refused BEFORE any request;
  - provider 4xx (insufficient funds / invalid employee) → FAILED (no money moved);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - TEST-MODE invariant: a non-`sk_test_` (live) key is refused BEFORE any request;
  - dispense-don't-disclose: the raw provider key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker.

`payments.pay` hard-codes its invoke args (amount / payee / cost / idempotency_key) and
cannot carry per-employee line items, so the rail is driven at the SAME `k.invoke`
boundary `payments.pay` wraps (feeding `args["lines"]`), while `payments.pay` itself
proves the Morta gate (amount→cost→cap, args-compatible) and the idempotent dedupe
(`find_payment`) against that very receipt. A fresh agent cell is fetched for each
`payments.pay` call (the broker re-asserts the agent cell when granting the handle).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import payroll, secrets, payments, executor
from decima.hashing import nfc

TEST_KEY = "sk_test_DECIMA_PAYROLL"
LIVE_KEY = "sk_live_DANGER_PAYROLL"

LINES = [{"amount": 250_000, "employee": "alice"},
         {"amount": 300_000, "employee": "bob"},
         {"amount": 450_000, "employee": "carol"}]
TOTAL = 1_000_000            # == sum(LINES); reconciles
HEADCOUNT = 3
PERIOD = "2026-07 pay period"


def _transport(calls, response):
    """A fake payroll transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def run(k, line):
    line("\n== REAL PAYROLL RAIL (wrapped engine, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("payroll", TEST_KEY, service="gusto")
    decima = kk.weave().get(kk.decima_agent_id)
    handle = broker.issue("payroll", decima, "run payroll")

    # 1. MORTA GATE + SUCCESS + provider_ref + headcount + spend cap decremented. ────────
    calls = []
    cap = payroll.install_rail(
        kk, cap=5_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="pr_ok", transport=_transport(calls, (200, {"id": "pr_run_1", "status": "submitted"})))
    key = nfc("pp-2026-07")
    # Morta: no approval yet → denied, and NO payroll request made (args-compat via pay).
    denied = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                          amount=TOTAL, payee=PERIOD, idempotency_key=key)
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no payroll request may be made before Morta approval"

    kk.approve(cap)
    spent_before = kk.spent.get(kk.decima_agent_id, 0.0)
    agent = kk.weave().get(kk.decima_agent_id)
    ok = kk.invoke(agent, cap, {"lines": LINES, "total_amount": TOTAL, "amount": TOTAL,
                                "cost": TOTAL, "payee": PERIOD, "idempotency_key": key})
    assert ok["status"] == executor.SUCCEEDED, ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "pr_run_1", rc
    assert rc["headcount"] == HEADCOUNT and rc["total_amount"] == TOTAL, rc
    assert rc["rail"] == "payroll" and rc["effect_class"] == "FINANCIAL", rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == key, calls
    spent_after = kk.spent.get(kk.decima_agent_id, 0.0)
    assert spent_after - spent_before == float(TOTAL), (spent_before, spent_after)
    line("  success: Morta-gated (no request pre-approval) → SUCCEEDED receipt with the "
         "payroll run provider_ref + headcount; running spend cap decremented by the total ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second run. ──────────
    before = len(calls)
    again = payments.pay(kk, kk.weave().get(kk.decima_agent_id), cap,
                         amount=TOTAL, payee=PERIOD, idempotency_key=key)
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert len(calls) == before, "a replay must not run a second payroll"
    line("  idempotent replay: same key → prior receipt, no second payroll run ✓")

    # 3. UNBALANCED TOTAL — sum(lines) != total_amount → FAILED before any request. ──────
    ucalls = []
    cap_u = payroll.install_rail(
        kk, cap=5_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="pr_unbalanced",
        transport=_transport(ucalls, (200, {"id": "pr_x", "status": "submitted"})))
    kk.approve(cap_u)
    ag_u = kk.weave().get(kk.decima_agent_id)
    unbal = kk.invoke(ag_u, cap_u, {"lines": LINES, "total_amount": TOTAL + 1,
                                    "amount": TOTAL + 1, "cost": TOTAL + 1,
                                    "payee": PERIOD, "idempotency_key": nfc("pp-unbal")})
    assert unbal["status"] == executor.FAILED, unbal
    assert ucalls == [], "an unreconciled total must be refused before any payroll request"
    line("  unbalanced: sum(lines) != total_amount → FAILED, refused before any request ✓")

    # 4. PROVIDER 4xx — insufficient funds / invalid employee → FAILED (no money moved). ─
    dcalls = []
    cap_d = payroll.install_rail(
        kk, cap=5_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="pr_reject",
        transport=_transport(dcalls, (402, {"error": {"message": "insufficient funds"}})))
    kk.approve(cap_d)
    ag_d = kk.weave().get(kk.decima_agent_id)
    dec = kk.invoke(ag_d, cap_d, {"lines": LINES, "total_amount": TOTAL, "amount": TOTAL,
                                  "cost": TOTAL, "payee": PERIOD, "idempotency_key": nfc("pp-rej")})
    assert dec["status"] == executor.FAILED, dec
    assert len(dcalls) == 1, "the provider was reached, and returned a definite 4xx"
    line("  provider 4xx (insufficient funds) → FAILED receipt — a definite no-effect ✓")

    # 5. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ───────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = payroll.install_rail(
        kk, cap=5_000_000, broker=broker, agent_cell=decima, credential_handle=handle,
        name="pr_timeout", transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    ag_t = kk.weave().get(kk.decima_agent_id)
    unk = kk.invoke(ag_t, cap_t, {"lines": LINES, "total_amount": TOTAL, "amount": TOTAL,
                                  "cost": TOTAL, "payee": PERIOD, "idempotency_key": nfc("pp-to")})
    assert unk["status"] == executor.UNKNOWN, unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 6. TEST-MODE invariant — a live key is refused BEFORE any request. ────────────────
    broker.store("payroll_live", LIVE_KEY, service="gusto")
    handle_live = broker.issue("payroll_live", decima, "run payroll")
    lcalls = []
    cap_l = payroll.install_rail(
        kk, cap=5_000_000, broker=broker, agent_cell=decima, credential_handle=handle_live,
        name="pr_live",
        transport=_transport(lcalls, (200, {"id": "pr_live_x", "status": "submitted"})),
        test_mode=True)
    kk.approve(cap_l)
    ag_l = kk.weave().get(kk.decima_agent_id)
    refused = kk.invoke(ag_l, cap_l, {"lines": LINES, "total_amount": TOTAL, "amount": TOTAL,
                                      "cost": TOTAL, "payee": PERIOD, "idempotency_key": nfc("pp-live")})
    assert refused["status"] == executor.FAILED, refused
    assert lcalls == [], "a live key must be refused before any payroll request is made"
    line("  test-mode: a live (sk_live_) key is refused before any request — no real "
         "payroll can run from the reference ✓")

    # 7. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ──────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert TEST_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw payroll key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw payroll key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → the real payroll engine is wrapped over stdlib urllib (zero deps): Morta-"
         "gated, idempotent, totals reconcile, receipts map submitted/rejected/timeout → "
         "SUCCEEDED/FAILED/UNKNOWN with provider_ref + headcount; test-mode-only; the key "
         "is never disclosed.")
