"""EVALOPT1 — the evaluator-optimizer loop made a REAL, recorded iteration.

`patterns.py` NAMES the evaluator-optimizer pattern (catalog #8: the writer↔editor
loop the selector picks for quality-critical work). This check proves the loop is
genuine — it actually improves the candidate, gates acceptance on evidence, never
fakes success, is bounded, and records every round on the Weft.

It proves:
  - a task that FAILS the first round and PASSES after the critique is applied — the
    loop genuinely improves it (the score rises round over round) and accepts ONLY on
    the passing evaluation (evidence-gated, like Nona's test-gate);
  - a task that NEVER passes hits max_rounds and returns the BEST-scoring candidate
    with a FAILED verdict (no silent / fake success);
  - every round is recorded on the Weft (`eval_round` Cells + `round_of` edges);
  - the loop is DETERMINISTIC — recompute the candidate digests and they match.

Contract: run(k, line). Fail loud.
"""
from decima import evalopt as EO


def run(k, line):
    line("\n== EVALUATOR-OPTIMIZER LOOP (writer↔editor, evidence-gated) — EVALOPT1 ==")

    # ── A writer + editor pair where the critique GENUINELY improves the draft. ──
    # The editor wants the draft to contain a required keyword. The writer starts
    # without it (a weak draft, low score), then — taught by the critique — adds it
    # on the next round (a strong draft that clears the bar). Both stubs are pure
    # deterministic functions of their input: the writer-editor loop, mechanical.
    BAR = 80

    def writer(candidate, critique):
        # Round 1: no critique yet → a bare draft. Later: the critique names the
        # missing keyword, so the writer appends it (the loop learns from feedback).
        if critique and "missing:keyword" in critique:
            return "report [verified]"
        return "report"

    def editor(candidate):
        if "[verified]" in candidate:
            return {"pass": True, "score": 95, "critique": "clears the bar"}
        return {"pass": False, "score": 40,
                "critique": "weak — missing:keyword '[verified]'"}

    res = EO.optimize(k, "quality brief", writer, editor, max_rounds=5)
    assert res["accepted"] is True, res
    assert res["output"] == "report [verified]", res
    assert res["rounds"] == 2, res                       # failed r1, passed r2
    # The loop genuinely IMPROVED it: the score rose round over round.
    scores = [h["score"] for h in res["history"]]
    assert scores == [40, 95], scores
    assert scores[-1] > scores[0], scores
    # Accepted ONLY on the passing evaluation (evidence-gated): r1 failed, r2 passed.
    assert res["history"][0]["pass"] is False and res["history"][-1]["pass"] is True, res
    line(f"  improve+accept: r1 score {scores[0]} (fail) → r2 score {scores[1]} (PASS) "
         f"→ accepted {res['output']!r} (evidence-gated ✓)")

    # ── A task that can NEVER clear the bar → max_rounds, best candidate, FAIL. ──
    # The writer climbs (each round a little better) but the editor's bar is out of
    # reach. The loop must NOT fake a pass; it returns the BEST attempt with a failed
    # verdict.
    def climber(candidate, critique):
        n = 0 if candidate is None else int(candidate.split(":")[1])
        return f"draft:{n + 1}"

    def strict(candidate):
        n = int(candidate.split(":")[1])
        return {"pass": False, "score": 10 * n,         # 10,20,30 — rising, never passes
                "critique": f"still short at score {10 * n}"}

    res2 = EO.optimize(k, "impossible spec", climber, strict, max_rounds=3)
    assert res2["accepted"] is False, res2               # NO silent / fake success
    assert res2["rounds"] == 3, res2                     # bounded by max_rounds
    s2 = [h["score"] for h in res2["history"]]
    assert s2 == [10, 20, 30], s2
    # The returned output is the BEST-scoring candidate seen (the last, highest here).
    assert res2["output"] == "draft:3", res2
    assert all(h["pass"] is False for h in res2["history"]), res2
    line(f"  exhaust+fail: scores {s2} over {res2['rounds']} rounds → best {res2['output']!r} "
         f"with FAIL verdict (no fake success ✓)")

    # ── Every round recorded on the Weft (eval_round Cells + round_of edges). ──
    recorded = EO.rounds_on(k, "quality brief")
    assert len(recorded) == 2, len(recorded)
    for h in res["history"]:
        cell = k.weave().get(h["cell"])
        assert cell is not None and cell.type == EO.EVAL_ROUND, cell
        assert cell.content["score"] == h["score"], cell.content
        assert cell.content["pass"] == h["pass"], cell.content
        assert isinstance(cell.content["score"], int), cell.content   # ints, never floats
        edge = k.weave().edges_from(h["cell"], EO.ROUND_OF)
        assert edge and edge[0]["dst"] == EO._run_anchor("quality brief"), edge
    line(f"  recorded {len(recorded)} eval_round Cells on the Weft "
         f"(score+verdict+digest, round_of→run anchor ✓)")

    # ── Deterministic: recompute every candidate digest and it matches. ──
    for h, output in zip(res["history"], ("report", "report [verified]")):
        assert h["digest"] == EO.candidate_digest(output), (h, output)
    assert res2["history"][-1]["digest"] == EO.candidate_digest("draft:3"), res2
    line("  deterministic: candidate digests recompute and match ✓")

    line("  → the evaluator-optimizer pattern is now a REAL loop: a writer drafts, an "
         "editor judges, the critique feeds back and genuinely improves it — accepted "
         "only on evidence, bounded, every round on the Weft.")
