"""SPEND GOVERNANCE — the Weft-folded budget meter, confirm-charge, quota + scorecards.

VISION "Advanced model strategy — compose, not replace": a routing that lands on a PAID
provider lane implies real money leaving on the user's behalf. `decima/spend.py` is the
native control plane for that spend — a budget meter, a CONFIRM-CHARGE gate so money never
leaves autonomously, per-provider quota, and learned per-provider scorecards — all
fold-derived from the Weft, all ints (micro-cents / pressure 0..100 / scorecard -100..100 /
quotas), all offline with an INJECTED logical-time int (never a wall-clock).

This check is an adversarial detector against the REAL kernel + inbox spine (the same
`ApprovalInbox.approve` → `approve_invocation`/`invoke`/`authorize` path any gated turn
drives). It proves:

  (a) METER — the budget remaining / pressure fold as INTS from charge Cells (a paid charge
      recorded → remaining drops by exactly that many micro-cents, pressure rises), and the
      paced allowance keys off an injected logical tick, not a clock;
  (b) FAIL CLOSED — with NO budget configured, a paid (external_paid / private_rented)
      dispatch is DENIED "budget_not_configured" and enqueues nothing;
  (c) CONFIRM-CHARGE — a paid dispatch ENQUEUES to the ApprovalInbox and spends NOTHING
      until a human approves; APPROVE enacts through the Morta gate → a `spend_charge` Cell
      lands and the budget decrements; DENY records no charge and leaves the budget
      unchanged;
  (d) NO AMBIENT AUTHORITY — approving a charge whose spend capability was REVOKED fails
      CLOSED at the gate: no charge Cell, budget unchanged (the meter never records a charge
      the gate did not enact);
  (e) QUOTA — per-provider remaining folds from dispatch receipts, decrements per dispatch,
      exhausts to 0 and then blocks; a logical-time reset boundary rolls the period over;
  (f) SCORECARDS — a provider's scorecard is 0 until N samples, then a bounded int in
      [-100,100];
  (g) INTS-NOT-FLOATS — every recorded numeric on every spend Cell is an int (no float
      dollars anywhere).

Deterministic + offline: fresh Kernels, injected logical-time ints, no network, no clocks.
Contract: run(k, line). Fail loud (assert / expected SpendError).
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.inbox import ApprovalInbox
from decima import spend
from decima.spend import SpendMeter, SpendError, CHARGE, DISPATCH, microcents_for


def _fresh():
    """A fresh, isolated Kernel + the decima agent + an inbox + a spend meter over it."""
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    agent = k.weave().get(k.decima_agent_id)
    return k, agent, ApprovalInbox(k), SpendMeter(k)


def _assert_int(x, what):
    assert isinstance(x, int) and not isinstance(x, bool), \
        f"{what} must be an int (ints-not-floats), got {type(x).__name__} {x!r}"


def run(k, line):
    line("\n== SPEND GOVERNANCE — budget meter + confirm-charge + quota + scorecards ==")

    # (b) FAIL CLOSED — no budget configured ⇒ a paid dispatch is DENIED, nothing queued. ─
    k0, ag0, ib0, m0 = _fresh()
    cap0 = m0.mint_spend_capability(ag0, "openrouter")
    assert not m0.is_configured(), "a fresh meter must start UNCONFIGURED"
    assert m0.budget_block() == {"remaining_microcents": 0, "pressure": 0, "configured": False}, \
        "an unconfigured meter must report a fail-closed budget block"
    r = m0.request_charge(ib0, ag0, cap0, provider_id="openrouter", tokens=2000,
                          cost_per_1k_microcents=300, privacy_tier="external_paid", now_tick=5)
    assert r == {"denied": "budget_not_configured"}, \
        f"a paid dispatch with no budget must fail closed, got {r}"
    assert not ib0.pending(), "a fail-closed paid dispatch must ENQUEUE nothing"
    assert not k0.weave().of_type(CHARGE), "a fail-closed paid dispatch must record no charge"
    line("  fail closed: with NO budget configured a paid (external_paid) dispatch is DENIED "
         "'budget_not_configured' — nothing queued, nothing charged ✓")

    # (a)+(c) METER + CONFIRM-CHARGE — enqueue, spend NOTHING until a human approves. ─────
    k1, ag1, ib1, m1 = _fresh()
    cap1 = m1.mint_spend_capability(ag1, "openrouter")
    m1.configure_budget(1_000_000, per_tick_allowance=10_000, start_tick=0)
    assert m1.is_configured() and m1.remaining_microcents() == 1_000_000
    micro = microcents_for(2000, 300)                 # 2000 tok @ 300/1k = 600 micro-cents
    assert micro == 600, f"micro-cents must be int floor(tok·rate/1000)=600, got {micro}"
    req = m1.request_charge(ib1, ag1, cap1, provider_id="openrouter", tokens=2000,
                            cost_per_1k_microcents=300, privacy_tier="external_paid", now_tick=5)
    assert "queued" in req and req["microcents"] == 600, f"a paid dispatch must queue: {req}"
    item = req["queued"]
    # it is queued and, crucially, NOTHING is spent yet — no autonomous spend.
    assert [c.id for c in ib1.pending()] == [item], "the confirm-charge must be the sole pending item"
    assert m1.spent_microcents() == 0, "a queued charge must spend NOTHING before approval"
    assert m1.remaining_microcents() == 1_000_000, "the budget must be untouched pre-approval"
    assert not k1.weave().of_type(CHARGE), "no charge Cell may exist before human approval"
    line("  confirm-charge: a paid dispatch ENQUEUES to the ApprovalInbox describing the "
         "charge (600 microcents) and spends NOTHING until a human approves ✓")

    # APPROVE — enact through the Morta gate → a charge Cell lands, budget decrements. ────
    out = m1.approve_charge(ib1, item)
    assert "charged" in out and out["microcents"] == 600, f"approval must enact the charge: {out}"
    charges = k1.weave().of_type(CHARGE)
    assert len(charges) == 1, "approval must record EXACTLY ONE charge Cell"
    ch = charges[0].content
    assert ch["microcents"] == 600 and ch["provider"] == "openrouter" and ch["tokens"] == 2000
    assert ch["approver"] == k1.human.id, "the human is the approver of record on the charge"
    # the money Cell carries provenance to the gate receipt that authorized it (Law 4).
    assert any(e["rel"] == "charged_via" for e in charges[0].edges_out), \
        "a charge Cell must link to the gate receipt that authorized it (provenance)"
    # the meter folds the new remaining / pressure as INTS from that charge Cell.
    assert m1.spent_microcents() == 600, "the charge must fold into spent"
    assert m1.remaining_microcents() == 999_400, "remaining = total − folded charge (int)"
    _assert_int(m1.pressure(), "pressure")
    assert 0 <= m1.pressure() <= 100, "pressure is a bounded int in [0,100]"
    blk = m1.budget_block()
    assert blk == {"remaining_microcents": 999_400, "pressure": m1.pressure(),
                   "configured": True}, f"budget block must fold from the charge: {blk}"
    # the paced allowance keys off an INJECTED logical tick, not a clock: more ticks → more
    # headroom, and the same call is pure (identical result on a re-invocation).
    paced_early = m1.paced_allowance(now_tick=10)
    paced_late = m1.paced_allowance(now_tick=90)
    _assert_int(paced_early, "paced_allowance")
    assert paced_late > paced_early, "paced allowance must grow with the injected logical tick"
    assert m1.paced_allowance(now_tick=10) == paced_early, "paced allowance must be a pure fold"
    line("  approve: enacts through authorize/Morta → ONE charge Cell (approver=human, "
         "charged_via provenance); remaining folds to 999400, pressure a bounded int, and "
         "the paced allowance keys off an injected logical tick ✓")

    # DENY — records no charge, leaves the budget untouched. ─────────────────────────────
    req2 = m1.request_charge(ib1, ag1, cap1, provider_id="openrouter", tokens=1000,
                             cost_per_1k_microcents=300, privacy_tier="external_paid", now_tick=6)
    spent_before = m1.spent_microcents()
    m1.deny_charge(ib1, req2["queued"], reason="too pricey")
    assert m1.spent_microcents() == spent_before, "DENY must spend nothing"
    assert len(k1.weave().of_type(CHARGE)) == 1, "DENY must record no new charge Cell"
    assert req2["queued"] not in [c.id for c in ib1.pending()], "a denied item leaves the queue"
    line("  deny: a denied confirm-charge records NO charge Cell and leaves the budget "
         "unchanged — the money never leaves ✓")

    # (d) NO AMBIENT AUTHORITY — approving a REVOKED-capability charge fails CLOSED. ──────
    k3, ag3, ib3, m3 = _fresh()
    cap3 = m3.mint_spend_capability(ag3, "openrouter")
    m3.configure_budget(1_000_000)
    req3 = m3.request_charge(ib3, ag3, cap3, provider_id="openrouter", tokens=2000,
                             cost_per_1k_microcents=300, privacy_tier="external_paid", now_tick=1)
    k3.revoke(cap3)                                   # Morta withdraws the spend capability
    denied = m3.approve_charge(ib3, req3["queued"])
    assert "denied" in denied and "charged" not in denied, \
        f"a revoked spend cap must fail CLOSED at the gate: {denied}"
    assert not k3.weave().of_type(CHARGE), "a gate-refused charge must record NO charge Cell"
    assert m3.remaining_microcents() == 1_000_000, "a gate-refused charge must not decrement the budget"
    line("  no ambient authority: approving a charge whose spend capability was REVOKED "
         "fails CLOSED at the Morta gate — no charge Cell, budget unchanged ✓")

    # (e) QUOTA — folds from dispatch receipts, decrements, exhausts to 0, then blocks. ───
    k4, _ag4, _ib4, m4 = _fresh()
    m4.configure_quota("groq", 5000)
    assert m4.quota_remaining("groq", now_tick=0) == 5000, "quota starts at the configured cap"
    m4.record_dispatch("groq", tokens=2000, now_tick=1, score=40)
    m4.record_dispatch("groq", tokens=2000, now_tick=2, score=60)
    assert m4.quota_remaining("groq", now_tick=3) == 1000, "quota must decrement per dispatch"
    assert m4.quota_ok("groq", 1000, now_tick=3) and not m4.quota_ok("groq", 1001, now_tick=3)
    m4.record_dispatch("groq", tokens=1000, now_tick=3, score=80)
    assert m4.quota_remaining("groq", now_tick=4) == 0, "quota must exhaust to exactly 0"
    assert not m4.quota_ok("groq", 1, now_tick=4), "an exhausted quota must BLOCK further dispatch"
    _assert_int(m4.quota_remaining("groq", now_tick=4), "quota_remaining")
    # a logical-time reset boundary rolls the period over (prior usage no longer draws down).
    k5, _ag5, _ib5, m5 = _fresh()
    m5.configure_quota("groq", 1000, reset_boundary=10)
    m5.record_dispatch("groq", tokens=800, now_tick=3)
    assert m5.quota_remaining("groq", now_tick=5) == 200, "pre-reset usage draws the quota down"
    assert m5.quota_remaining("groq", now_tick=10) == 1000, \
        "at/after the injected reset boundary the quota rolls over to the full cap"
    line("  quota: per-provider remaining folds from dispatch receipts, decrements per "
         "dispatch, exhausts to 0 then BLOCKS; an injected logical-time boundary resets it ✓")

    # (f) SCORECARDS — 0 until N samples, then a bounded int in [-100,100]. ───────────────
    assert spend.SCORECARD_MIN_SAMPLES >= 2, "N must be a real sample floor"
    assert m4.scorecard("groq") == 60, "with ≥N scored samples the scorecard is the clamped mean"
    _assert_int(m4.scorecard("groq"), "scorecard")
    assert -100 <= m4.scorecard("groq") <= 100, "a scorecard is bounded to [-100,100]"
    k6, _ag6, _ib6, m6 = _fresh()
    # fewer than N samples ⇒ ZERO (a signal that has not earned its voice stays silent).
    for i in range(spend.SCORECARD_MIN_SAMPLES - 1):
        m6.record_dispatch("groq", tokens=10, now_tick=i, score=90)
    assert m6.scorecard("groq") == 0, "a scorecard must be 0 below the sample floor"
    m6.record_dispatch("groq", tokens=10, now_tick=99, score=90)     # crosses the floor
    assert m6.scorecard("groq") != 0 and -100 <= m6.scorecard("groq") <= 100, \
        "at the sample floor the scorecard becomes a bounded non-zero int"
    # an out-of-range score is refused loud (bounds are enforced at the door).
    try:
        m6.record_dispatch("groq", tokens=1, now_tick=100, score=500)
        raise AssertionError("an out-of-range score was accepted (must fail loud)")
    except SpendError:
        pass
    line("  scorecards: a provider's learned-quality signal is 0 below N samples, then a "
         "bounded int in [-100,100]; an out-of-range score fails loud ✓")

    # (g) INTS-NOT-FLOATS — every recorded numeric on every spend Cell is an int. ─────────
    for c in (k1.weave().of_type(CHARGE) + k4.weave().of_type(DISPATCH)
              + k5.weave().of_type(DISPATCH)):
        for key in ("microcents", "tokens", "at_tick", "cap_tokens"):
            if key in c.content and c.content[key] is not None:
                _assert_int(c.content[key], f"{c.type}.{key}")
        if c.type == DISPATCH and c.content.get("score") is not None:
            _assert_int(c.content["score"], "dispatch.score")
    # a float amount is refused at the door — no float dollar can enter signed content.
    try:
        m1.configure_budget(10.5)                     # a float budget must fail loud
        raise AssertionError("a float budget was accepted (ints-not-floats violated)")
    except SpendError:
        pass
    line("  ints-not-floats: every recorded numeric (micro-cents, tokens, ticks, quotas, "
         "scores) is an int; a float amount is refused at the door ✓")

    line("  → spend is now GOVERNED: the budget meter folds from charge Cells, a paid "
         "charge routes through the ApprovalInbox + Morta gate (never autonomous, fail "
         "closed unconfigured / revoked), quota folds + exhausts, and scorecards stay "
         "evidence-gated and bounded — all ints, all offline, all provenance on the Weft.")
