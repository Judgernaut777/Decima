"""RESILIENCE1 — backpressure / rate-limit / circuit-breaker / bulkhead around
every outward INVOKE (CAPABILITY_MAP B3). Contract: run(k, line). Fail loud.

Proves, composing only the resilience module's PUBLIC API + model/weave/weft:

  - a FLAKY effect trips the breaker after N consecutive bad receipts; the NEXT
    call then fails FAST without invoking (the thunk never runs);
  - after a `cooldown` of LOGICAL ticks the breaker HALF-OPENs and admits one
    trial; a SUCCEEDED trial CLOSES it (recovery);
  - the rate-limiter REFUSES an over-budget call within a window;
  - the bulkhead refuses past the max-concurrent cap;
  - every breaker STATE CHANGE is AUDITED on the Weft (foldable `breaker` cells);
  - DETERMINISTIC — `now` is a logical int tick; two folds agree on state_root.
"""
from decima import resilience as R
from decima import executor


def run(k, line):
    line("\n== RESILIENCE: breaker + rate-limit + bulkhead wrap an authorized INVOKE — RESILIENCE1 ==")
    # budget/window are generous here so the breaker + bulkhead scenarios below are
    # never throttled by the rate-limiter; the rate-limit scenario uses its own key
    # and a per-call check of the effective budget so it stays self-contained.
    res = R.attach(k, threshold=3, cooldown=5, budget=8, window=10, max_concurrent=1)

    # A controllable flaky thunk: returns whatever status we cue, and counts the
    # times it ACTUALLY ran — so we can prove a fail-fast refusal never invokes.
    cue = {"status": executor.SUCCEEDED, "ran": 0}

    def call():
        cue["ran"] += 1
        return {"ok": {"out": "did the thing"}, "status": cue["status"]}

    KEY = "flaky.publish|decima"

    # ── breaker trips after N consecutive FAILED/UNKNOWN ─────────────────────
    # Mix FAILED and UNKNOWN — both are "bad" (an UNKNOWN/timeout erodes the breaker
    # exactly like a definite failure). Three (= threshold) bad receipts in a row.
    cue["status"] = executor.FAILED
    r1 = res.guard(KEY, call, now=0)
    cue["status"] = executor.UNKNOWN
    r2 = res.guard(KEY, call, now=1)
    cue["status"] = executor.FAILED
    r3 = res.guard(KEY, call, now=2)
    assert all("refused" not in r for r in (r1, r2, r3)), "the 3 flaky calls must INVOKE (not be refused)"
    assert cue["ran"] == 3, f"thunk should have run 3 times, ran {cue['ran']}"
    assert res.breaker.state(KEY, now=2) == R.OPEN, "breaker must be OPEN after N bad receipts"
    line(f"  3 consecutive FAILED/UNKNOWN receipts (ticks 0–2) → breaker OPEN (threshold=3) ✓")

    # ── next call fails FAST without invoking ────────────────────────────────
    before = cue["ran"]
    cue["status"] = executor.SUCCEEDED            # the effect WOULD succeed now…
    ff = res.guard(KEY, call, now=3)
    assert ff.get("refused") == R.R_OPEN, f"call on OPEN breaker must fail fast, got {ff}"
    assert cue["ran"] == before, "fail-fast must NOT invoke the thunk (no effect ran)"
    line(f"  next call (tick 3) → fails FAST [{ff['refused']}] without invoking (ran unchanged at {cue['ran']}) ✓")

    # ── after cooldown: HALF-OPEN admits one trial, success CLOSES it ────────
    # cooldown=5, opened at tick 2 → half-open eligible at now >= 7. now is LOGICAL.
    assert res.breaker.state(KEY, now=6) == R.OPEN, "still OPEN before cooldown elapses (now=6 < 2+5)"
    assert res.breaker.state(KEY, now=7) == R.HALF_OPEN, "cooldown elapsed → HALF_OPEN at now=7"
    cue["status"] = executor.SUCCEEDED
    trial = res.guard(KEY, call, now=7)           # the single admitted trial
    assert "refused" not in trial and cue["ran"] == before + 1, "half-open must admit exactly one trial"
    assert res.breaker.state(KEY, now=7) == R.CLOSED, "a SUCCEEDED trial must CLOSE the breaker"
    line(f"  after cooldown (5 ticks): half-opens @7, one trial SUCCEEDS → breaker CLOSED (recovered) ✓")

    # ── rate-limiter refuses an over-budget call in a window ─────────────────
    # Fresh key so the breaker is irrelevant here. `budget` tokens per window=10 →
    # the (budget+1)-th call inside [10,20) is refused; a call in the NEXT window
    # passes again (the bucket refills). All ticks logical.
    RK = "rate.publish|decima"
    budget, window = res.rate.budget, res.rate.window
    cue["status"] = executor.SUCCEEDED
    granted = [res.guard(RK, call, now=10 + i) for i in range(budget)]   # exactly `budget` calls
    over = res.guard(RK, call, now=10 + budget)                          # one over budget, same window
    assert all("refused" not in g for g in granted), "calls within budget must pass"
    assert over.get("refused") == R.R_RATE, f"over-budget call must be rate-limited, got {over}"
    nxt = res.guard(RK, call, now=window * 3)     # a later window → budget refills
    assert "refused" not in nxt, "a call in the next window must pass (bucket refilled)"
    line(f"  rate-limit ({budget}/window={window}): {budget} calls pass, the next REFUSED "
         f"[{over['refused']}], a call in a later window passes (refill) ✓")

    # ── bulkhead refuses past the max-concurrent cap ─────────────────────────
    # max_concurrent=1. Re-enter guard from INSIDE the thunk (a held slot) to prove
    # the (limit+1)-th concurrent call is refused; the outer call still completes.
    BK = "bulk.publish|decima"
    inner = {}

    def reentrant():
        cue["ran"] += 1
        inner["r"] = res.guard(BK, lambda: {"status": executor.SUCCEEDED}, now=30)
        return {"status": executor.SUCCEEDED}

    outer = res.guard(BK, reentrant, now=30)
    assert "refused" not in outer, "outer bulkhead call must hold the only slot and complete"
    assert inner["r"].get("refused") == R.R_BULK, f"concurrent call must be bulkhead-refused, got {inner['r']}"
    again = res.guard(BK, lambda: {"status": executor.SUCCEEDED}, now=31)
    assert "refused" not in again, "after release, a fresh call must acquire the freed slot"
    line(f"  bulkhead (max 1 concurrent): re-entrant call REFUSED [{inner['r']['refused']}], "
         "slot freed after → next passes ✓")

    # ── breaker state changes are AUDITED on the Weft (foldable) ─────────────
    breakers = [c for c in k.weave().of_type("breaker") if c.content["effect_key"] == KEY]
    states = [c.content["state"] for c in breakers]
    assert R.OPEN in states and R.HALF_OPEN in states and R.CLOSED in states, \
        f"breaker transitions must be on the Weft, saw {states}"
    line(f"  breaker transitions audited on the Weft for {KEY!r}: {sorted(set(states))} ✓")

    # ── DETERMINISM: logical ticks only; two folds agree ─────────────────────
    assert k.weave().state_root() == k.weave().state_root(), "fold must be deterministic"
    line("  → RESILIENCE1: an authorized outward INVOKE is wrapped by breaker+rate-limit+bulkhead; "
         "a flaky effect trips the breaker (fail-fast, no invoke), recovers after a logical cooldown; "
         "over-budget/over-concurrent calls are refused; every breaker change is audited; deterministic.")
