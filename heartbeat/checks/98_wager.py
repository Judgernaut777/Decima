"""WV1 — the Wager/Verdict loop (judgment that compounds).

Receipts say what happened; the wager/verdict pair says *predicted vs. got*. This check
proves:
  - a wager → action → verdict records hit/miss with provenance (a `verdict_of` edge);
  - calibration aggregates resolved wagers and reflects accuracy — a well-calibrated agent's
    high-confidence band hits more often than its low-confidence band;
  - a SIGNIFICANT wager binds to a Morta-gated action: the action is denied until approved,
    then proceeds — and only then is its verdict recorded (the D3 pattern: bet → approve →
    act → verify).

Contract: run(k, line). Fail loud.
"""
from decima import wager as wv
from decima import executor
from decima.capability import capability_content
from decima.hashing import content_id
from decima.weft import ASSERT


def run(k, line):
    line("\n== WAGER / VERDICT (predict → act → measure → calibrate) — WV1 ==")

    # 1. A wager, an action, a verdict — a hit (within tolerance), with provenance.
    w1 = wv.wager(k, "ship change X", prediction=200, confidence=900_000)  # +2.00% predicted
    r1 = wv.verdict(k, w1, observed=203, tolerance=10)                     # +2.03% observed
    wv_cell = k.weave().get(r1["verdict"])
    prov = k.weave().edges_from(r1["verdict"], "verdict_of")
    assert r1["hit"] and prov and prov[0]["dst"] == w1, (r1, prov)
    line(f"  wager(+200, conf 0.90) → observed 203 → HIT (delta {r1['delta']:+d}); "
         f"verdict_of→{w1[:8]} (provenance ✓)")

    # 2. A miss — a low-confidence prediction that didn't pan out.
    w2 = wv.wager(k, "risky bet Y", prediction=500, confidence=300_000)
    r2 = wv.verdict(k, w2, observed=120, tolerance=10)
    assert not r2["hit"], r2
    line(f"  wager(+500, conf 0.30) → observed 120 → MISS (delta {r2['delta']:+d})")

    # 3. Calibration: a batch where high-confidence hits and low-confidence misses, so the
    #    high band's hit-rate exceeds the low band's (the agent knows when it knows).
    for i in range(4):
        h = wv.wager(k, f"confident call {i}", prediction=100, confidence=950_000)
        wv.verdict(k, h, observed=100, tolerance=5)          # hits
        lo = wv.wager(k, f"hunch {i}", prediction=100, confidence=200_000)
        wv.verdict(k, lo, observed=900, tolerance=5)         # misses
    cal = wv.calibration(k)
    hi, low = cal["bands"]["high"]["hit_rate"], cal["bands"]["low"]["hit_rate"]
    line(f"  calibration: {cal['resolved']} resolved, overall hit-rate "
         f"{cal['hit_rate']/wv.FULL:.0%} · high-band {hi/wv.FULL:.0%} vs low-band {low/wv.FULL:.0%}")
    assert hi > low, cal["bands"]               # high confidence really does hit more
    assert cal["bands"]["high"]["hit_rate"] is not None and cal["bands"]["low"]["hit_rate"] == 0

    # 4. A SIGNIFICANT wager on a Morta-gated action (the D3 flow: a real spend).
    #    Register the effect + a requires_approval capability, grant it to Decima.
    executor.register("adspend", lambda impl, args: {"out": f"spent ${args.get('usd', 0)}"})
    cap = content_id({"wvcap": "adspend"})
    k.weft.append(k.root.id, ASSERT, {"cell": cap, "type": "capability", "content":
        capability_content(name="adspend", effect="adspend",
                           caveats={"requires_approval": True, "effect_class": "FINANCIAL"})})
    k.grant(cap, k.decima_agent_id)
    big = wv.wager(k, "buy ads: +5% signups", prediction=500, confidence=700_000,
                   significant=True, action_cap=cap)
    bound = k.weave().edges_from(big, "wagers_on")
    assert bound and bound[0]["dst"] == cap

    dec = lambda: k.weave().get(k.decima_agent_id)
    blocked = k.invoke(dec(), cap, {"usd": 1000})                 # Morta: not approved yet
    assert "denied" in blocked, blocked
    k.approve(cap)                                                # human/Morta signs off
    done = k.invoke(dec(), cap, {"usd": 1000})
    assert "ok" in done, done
    res = wv.verdict(k, big, observed=300, tolerance=100)         # bet +5%, got +3% → miss
    line(f"  significant wager → action ✋ {blocked['denied'].split(';')[0]}; "
         f"after approve → acted; verdict: {'HIT' if res['hit'] else 'MISS'} (delta {res['delta']:+d})")
    line("  → predict-before-act + measure-after, calibrated, with big bets Morta-gated. "
         "Decima learns which decisions work, not just which capabilities.")
