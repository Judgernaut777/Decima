"""EVALOPT1 — the evaluator-optimizer loop made a REAL, recorded iteration.

`patterns.py` *names* the evaluator-optimizer pattern (catalog #8: a generator and
an evaluator iterate, writer↔editor, until the output clears a bar — the shape the
selector picks for quality-critical work). This module *realizes* it as a genuine
loop on the Weft.

The loop is the writer-editor pair made mechanical and EVIDENCE-GATED, in the same
spirit as Nona's Reckoner (`reckoner.py`): a candidate is *accepted* only when the
evaluator returns a passing verdict — never on a hunch, never silently. The
generator and evaluator are caller-provided DETERMINISTIC stubs (a real model wraps
them later, exactly as Nona's `executor` stub stands in for a sandbox):

  - `generate(candidate, critique)` — the writer. Given the previous candidate and
    the editor's critique (both None on the first round), return the next candidate.
  - `evaluate(candidate)` — the editor. Return {pass: bool, score: int, critique:
    str}. `pass` is the bar; `score` ranks how good a candidate is (an int, so it
    reaches the signed log cleanly); `critique` is the feedback fed back to the
    writer for the next round.

Laws this module upholds:
  - **Evidence-gated acceptance.** A candidate is accepted ONLY on a passing
    evaluation (`pass is True`) — mirroring Nona's test-gate. No passing verdict, no
    acceptance.
  - **No silent success.** If nothing passes by `max_rounds`, the loop returns the
    BEST-scoring candidate it saw, with a FAILED verdict (`accepted=False`). It never
    fakes a pass.
  - **Bounded.** The loop runs at most `max_rounds` rounds (an int ≥ 1) and always
    terminates — no unbounded generate/evaluate spin.
  - **Ints, not floats.** Scores are ints; never a float reaches the Weft (WEFT
    §4/§7).
  - **Every round on the Weft.** Each round writes an `eval_round` Cell carrying the
    candidate's digest, its score, the verdict, and the critique — plus a
    `round_of` edge to the run anchor — so the whole iteration is auditable and
    time-travelable. Recompute the digests and they match (deterministic).

Public `model`/`weave`/`hashing` API only — no core edit.
"""
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

EVAL_ROUND = "eval_round"      # the Cell type recording one writer→editor round
ROUND_OF = "round_of"          # edge: round → round_of → run anchor


def candidate_digest(candidate) -> str:
    """A stable content digest of a candidate, so a round records WHAT was judged
    without pinning the (possibly large/None) candidate into the signed body. Pure
    and deterministic — recompute on the same candidate and it matches."""
    return content_id({"candidate": candidate})


def _run_anchor(task: str) -> str:
    """A stable per-task anchor Cell id, so every round of one optimize() call
    converges on a single run node the rounds' `round_of` edges point at."""
    return content_id({"evalopt_run": nfc(str(task))})


def optimize(k, task, generate, evaluate, *, max_rounds, author=None):
    """Run the evaluator-optimizer loop for `task`: the writer (`generate`) proposes
    a candidate, the editor (`evaluate`) judges it; on a pass we ACCEPT, otherwise we
    feed the critique back and regenerate — up to `max_rounds` rounds.

    `generate(candidate, critique)` returns the next candidate (both args None on the
    first round). `evaluate(candidate)` returns {pass: bool, score: int, critique:
    str}. Each round is recorded as an `eval_round` Cell on the Weft.

    Returns {accepted, output, rounds, history}:
      - accepted — True iff some round's evaluation passed.
      - output   — the accepted candidate, or (on no pass) the BEST-scoring one seen.
      - rounds   — how many rounds actually ran (≤ max_rounds).
      - history  — a list of per-round records (round, digest, score, pass, critique,
                   cell id) — the same evidence written to the Weft.

    Evidence-gated (accept only on a pass) and bounded; on exhaustion it returns the
    best candidate with a FAILED verdict — never a silent success."""
    author = author or k.decima_agent_id
    if int(max_rounds) < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds!r}")
    max_rounds = int(max_rounds)
    anchor = _run_anchor(task)

    history = []
    candidate = None        # the previous candidate (the writer revises it)
    critique = None         # the editor's last feedback (fed back to the writer)
    best = None             # {output, score, digest, round, critique} — the best seen

    for r in range(1, max_rounds + 1):
        # The writer proposes the next candidate from the prior one + the critique.
        candidate = generate(candidate, critique)
        verdict = evaluate(candidate)
        passed = bool(verdict["pass"])
        score = int(verdict["score"])         # ints only — never a float on the Weft
        critique = nfc(str(verdict.get("critique", "")))
        digest = candidate_digest(candidate)

        # Record THIS round on the Weft: a content cell + an edge to the run anchor.
        cid = content_id({"eval_round": task, "round": r, "digest": digest,
                          "at": k.weft.head})
        assert_content(k.weft, author, cid, EVAL_ROUND, {
            "task": nfc(str(task)),
            "round": int(r),
            "digest": digest,
            "score": score,
            "pass": passed,
            "critique": critique,
        })
        assert_edge(k.weft, author, cid, ROUND_OF, anchor)

        rec = {"round": int(r), "digest": digest, "score": score,
               "pass": passed, "critique": critique, "cell": cid}
        history.append(rec)

        # Track the best-scoring candidate seen, so a non-passing run can still
        # return its strongest attempt (no silent discard). Ties keep the earliest.
        if best is None or score > best["score"]:
            best = {"output": candidate, "score": score, "digest": digest,
                    "round": int(r), "critique": critique}

        if passed:
            # Evidence-gated acceptance: a passing evaluation, and only that, accepts.
            return {"accepted": True, "output": candidate, "rounds": int(r),
                    "history": history}

    # Bounded exhaustion: nothing cleared the bar. Return the BEST candidate with a
    # FAILED verdict — no fake success.
    return {"accepted": False, "output": best["output"], "rounds": max_rounds,
            "history": history}


def rounds_on(k, task=None) -> list:
    """Fold the recorded `eval_round` Cells (optionally for one task), in appearance
    order. A pure read over the Weave — the audit trail of an optimization run."""
    return [c for c in k.weave().of_type(EVAL_ROUND)
            if not c.retracted and (task is None or c.content.get("task") == nfc(str(task)))]
