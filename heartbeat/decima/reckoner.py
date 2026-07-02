"""Nona — the Reckoner. The compounding engine ("becomes more advanced the
longer it runs"), made mechanical.

A forged capability is born QUARANTINED. Promotion is gated on EVIDENCE, not a
single check:
  1. a deterministic verifier runs it in the sandbox, and
  2. a static scanner inspects the candidate for risky patterns.
Promotion happens only if the test passes AND the scan is clean. A trusted
principal then ATTESTs the promotion (lifting the quarantine) and grants it;
rollback is a RETRACT. The set of things Decima can do is a fold over the Weft —
and it only ever grows, behind the gate.

`scan` is a quarantined, network-denied stub of the NVIDIA SkillSpector contract
(see specs/NONA_RECKONER.md): it produces *evidence*, never authority. A clean
scan does not prove safety, and findings are combined with the test and Morta
policy — in production also with sandbox execution traces and dependency checks.
"""
import ast
import json
import random

from decima.weft import ASSERT, ATTEST
from decima.capability import capability_content
from decima.hashing import content_id
from decima.model import assert_content, assert_edge
from decima import executor

# Pattern families (a tiny stand-in for SkillSpector's analyzer set).
_RISKY_EFFECTS = {"shell", "network", "exec", "eval"}
_AUDITED_TRANSFORMS = {"upper", "lower", "reverse", "wc"}
_DANGER_TOKENS = ["rm -rf", "curl", "wget", "| sh", "|sh", "eval(", "exec(",
                  "/etc/passwd", "exfil", "nc -", "base64 -d", "ssh "]


def scan(content: dict) -> list[dict]:
    """Static analysis of a candidate capability. Returns findings as evidence."""
    findings = []
    effect = content.get("effect")
    impl = content.get("impl") or {}
    blob = json.dumps({"name": content.get("name"), "effect": effect, "impl": impl}).lower()

    if effect in _RISKY_EFFECTS:
        findings.append({"sev": "high", "rule": "risky-effect",
                         "detail": f"effect {effect!r} requests a privileged capability"})
    if effect == "transform" and impl.get("fn") not in _AUDITED_TRANSFORMS:
        findings.append({"sev": "high", "rule": "unverified-impl",
                         "detail": f"transform fn {impl.get('fn')!r} is outside the audited set"})
    for tok in _DANGER_TOKENS:
        if tok in blob:
            findings.append({"sev": "high", "rule": "dangerous-pattern",
                             "detail": f"matched {tok!r} in the candidate"})
    return findings


class ForgeReport:
    def __init__(self, cap_id, name, promoted, detail, findings=None):
        self.cap_id = cap_id
        self.name = name
        self.promoted = promoted
        self.detail = detail
        self.findings = findings or []

    def __str__(self):
        status = "PROMOTED ✓" if self.promoted else "REJECTED ✗"
        return f"[Nona] forge {self.name!r} {self.cap_id[:8]} → {status} — {self.detail}"


def forge(kernel, name, effect, fn, test_input, expect, command=None) -> ForgeReport:
    root = kernel.root.id
    reckoner = kernel.reckoner.id

    # 1. ASSERT the new capability — born quarantined, sandbox_only. `command` is
    #    an inert payload field used to demonstrate the scanner catching a hidden
    #    payload even when the behavior under test looks benign.
    impl = {"fn": fn}
    if command:
        impl["command"] = command
    content = capability_content(
        name=name, effect=effect, impl=impl,
        caveats={"sandbox_only": True}, quarantined=True,
    )
    cap_id = content_id({"forged": name, "effect": effect, "impl": impl})
    kernel.weft.append(root, ASSERT,
                       {"cell": cap_id, "type": "capability", "content": content})

    # 2. Evidence A — deterministic verifier (sandboxed execution).
    try:
        got = executor.execute(effect, impl, {"text": test_input}).get("out")
        test_pass = (got == expect)
        test_detail = f"ran {test_input!r} → {got!r}, expected {expect!r}"
    except Exception as e:  # noqa: BLE001 - a failed forge is data, not a crash
        test_pass, test_detail = False, f"sandbox error: {e}"

    # 3. Evidence B — static scan (quarantined, network-denied; SkillSpector stub).
    findings = scan(content)
    high = [f for f in findings if f["sev"] == "high"]

    # 4. Gate: promote only if BOTH the test passes and the scan is clean.
    promote = test_pass and not high
    scan_note = "clean" if not high else "; ".join(f["rule"] for f in high)
    detail = f"test:{'pass' if test_pass else 'fail'} ({test_detail})  ·  scan:{scan_note}"

    # 5. ATTEST the outcome. Promotion lifts quarantine; otherwise it just records.
    kernel.weft.append(reckoner, ATTEST,
                       {"target_cell": cap_id, "claim": detail, "promote": promote})
    if promote:
        kernel.grant(cap_id, kernel.decima_agent_id)

    return ForgeReport(cap_id, name, promote, detail, findings)


# ═══════════════════════════════════════════════════════════════════════════
# STAGE B — Reckoner-real: run a GENERATED-CODE ExtensionCandidate across the
# full verifier hierarchy (NONA_RECKONER §5), producing an EvaluationResult Cell
# (§4) as the EVIDENCE and a promote-eligibility verdict. This stage NEVER lifts
# quarantine or grants anything — that is Stage C's trusted, tiered promotion.
# It only judges, deterministically and offline.
# ═══════════════════════════════════════════════════════════════════════════

EVALUATION_RESULT = "evaluation_result"

# Source-text danger tokens (SkillSpector-style). Data, not authority (§5): a
# match is EVIDENCE that blocks promotion, never proof of intent on its own.
_SOURCE_DANGER_TOKENS = (
    "rm -rf", "curl ", "wget ", "| sh", "|sh", "eval(", "exec(", "compile(",
    "os.system", "os.popen", "__import__", "/etc/passwd", "exfil", "nc -",
    "base64 -d", "socket.socket", "subprocess",
)
# Modules whose IMPORT implies an effect class the manifest must have DECLARED.
_NETWORK_MODULES = frozenset({
    "socket", "urllib", "http", "httplib", "ftplib", "smtplib", "telnetlib",
    "poplib", "imaplib", "asyncio", "requests", "xmlrpc", "ssl",
})
_SHELL_MODULES = frozenset({"subprocess", "pty", "multiprocessing"})


def scan_source(source, declared_effect_class="pure", name=None) -> list:
    """Static/security scan of the candidate's SOURCE TEXT (the SkillSpector
    adapter, NONA_RECKONER §5.6). Produces findings as immutable EVIDENCE — a high
    finding blocks promotion, but a clean scan is never proof of safety.

    Two families of high finding:
      • danger tokens in the raw source (shell/network/eval signatures);
      • an import whose effect class the manifest did NOT declare — undeclared
        NETWORK or SHELL/process use. This is the rug-pull check: a finding fires
        exactly when the manifest and the implementation DISAGREE (§5)."""
    findings = []
    text = source if isinstance(source, str) else ""
    low = text.lower()
    for tok in _SOURCE_DANGER_TOKENS:
        if tok in low:
            findings.append({"sev": "high", "rule": "dangerous-pattern",
                             "detail": f"source matched danger token {tok!r}"})
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        findings.append({"sev": "high", "rule": "unparseable-source",
                         "detail": f"generated source does not parse: {e}"})
        return findings
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    net = sorted(imported & _NETWORK_MODULES)
    if net and declared_effect_class != "network":
        findings.append({"sev": "high", "rule": "undeclared-network",
                         "detail": f"imports {net} but declared_effect_class="
                                   f"{declared_effect_class!r} — manifest and impl disagree"})
    shell = sorted(imported & _SHELL_MODULES)
    if shell:
        findings.append({"sev": "high", "rule": "undeclared-shell",
                         "detail": f"imports process-spawning module(s) {shell}; "
                                   "no declared shell/process effect class"})
    return findings


def _entrypoint(source: str) -> str:
    """The candidate's callable: the first top-level `def` in the generated source.
    Deterministic and content-derived — no trust in a model-supplied name."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            return node.name
    raise ExtensionEvaluationError("generated source declares no top-level function")


class ExtensionEvaluationError(Exception):
    """The candidate could not be evaluated at all (malformed shape) — distinct
    from a candidate that ran and FAILED (which is ordinary evidence, not a crash)."""
    pass


class EvaluationOutcome:
    """The verdict + a handle to the EvaluationResult Cell (§4) on the Weft."""
    def __init__(self, result_cell, promote_eligible, metrics, findings,
                 failures, case_receipts, reproducibility, reason, model_judge):
        self.result_cell = result_cell
        self.promote_eligible = promote_eligible
        self.metrics = metrics
        self.findings = findings
        self.failures = failures
        self.case_receipts = case_receipts
        self.reproducibility = reproducibility
        self.reason = reason
        self.model_judge = model_judge

    def __str__(self):
        v = "PROMOTE-ELIGIBLE ✓" if self.promote_eligible else "INELIGIBLE ✗"
        return f"[Nona] evaluate → {v} — {self.reason}"


# The fuzz alphabet — letters (mixed case), digits, and whitespace (space/tab/
# newline) + punctuation, so seeded inputs exercise case-folding, whitespace
# collapse, and idempotence. The seed is DATA off the EvaluationSuite; the PRNG is
# a SEEDED random.Random (never os.urandom / time), so fuzzing is reproducible.
_FUZZ_ALPHABET = "abcDEFghiJKL  \t\n0123456789.,-"


def _fuzz_inputs(seed: int, n: int) -> list:
    rng = random.Random(int(seed))
    out = []
    for _ in range(n):
        length = rng.randint(1, 24)
        out.append("".join(rng.choice(_FUZZ_ALPHABET) for _ in range(length)))
    return out


def _text_invariants(out) -> tuple:
    """Contract invariants of a pure text normalizer, checked on a sandbox output.
    Returns (ok, reason)."""
    if not isinstance(out, str):
        return False, f"output is not a string: {type(out).__name__}"
    if out != out.lower():
        return False, "output is not lowercased"
    if out.strip() != out:
        return False, "output has leading/trailing whitespace"
    if "  " in out or "\t" in out or "\n" in out:
        return False, "output has an uncollapsed whitespace run"
    return True, "ok"


def evaluate(kernel, candidate, *, incumbent=None, model_judge=None, fuzz_n=6):
    """Run the EvaluationSuite for a generated-code `candidate` across the verifier
    hierarchy (NONA_RECKONER §5, evidence order) and record an EvaluationResult Cell
    (§4). Returns an `EvaluationOutcome` with a promote-eligibility verdict.

    `candidate` is the dict `candidate.author_candidate` returns. Stages, in order:
      (a) DETERMINISTIC exact-output tests on the suite's seeded cases;
      (b) SANDBOXED execution of the hostile-input adversaries with containment
          invariants (the generated code runs ONLY via the isolation seam);
      (c) PROPERTY-BASED + FUZZ testing with a SEEDED PRNG — idempotence + the
          normalizer contract invariants over N generated inputs;
      (d) DIFFERENTIAL test against the promoted `incumbent` (if one exists) —
          a regression check over the same fuzz inputs (§4);
      (e) STATIC/SECURITY source scan (`scan_source`, the SkillSpector adapter).

    Gate (§4 failure transparency — "no fabricated success"): promote-eligible iff
    ALL deterministic + sandbox + property + fuzz + differential cases pass AND there
    is no high-severity finding. A candidate that ERRORS or TIMES OUT in the sandbox
    FAILS — it never passes. A `model_judge`, if supplied, is recorded as evidence
    (§5 rank 8) but NEVER overrides a deterministic failure."""
    reckoner = kernel.reckoner.id
    name = candidate["name"]
    source = candidate["source_blobs"]
    digest = candidate["implementation_digest"]
    cand_cell = candidate["cell"]
    suite_id = candidate["suite"]
    suite = candidate["suite_content"]
    decl = candidate.get("declared_effect_class", "pure")
    entry = candidate.get("entrypoint") or _entrypoint(source)
    impl = {"source_blobs": source, "entrypoint": entry}
    seed = int(candidate["content"]["eval_plan"]["seed"])

    def run_impl(the_impl, inp):
        return executor.execute("generated_code", the_impl, inp)

    case_receipts, failures = [], []

    # (a) DETERMINISTIC exact-output tests on the seeded cases (§5.2). ───────────
    det_pass = 0
    for case in suite["cases"]:
        r = run_impl(impl, case["input"])
        ok = bool(r.get("ok")) and r.get("out") == case["expect"]
        det_pass += 1 if ok else 0
        if not ok:
            failures.append({"stage": "deterministic", "seed": case["seed"],
                             "input": case["input"], "expect": case["expect"],
                             "got": r.get("out"), "error": r.get("error")})
        case_receipts.append({"stage": "deterministic", "seed": int(case["seed"]),
                              "input": case["input"], "expect": case["expect"],
                              "got": r.get("out"), "ok": ok, "ran": bool(r.get("ran")),
                              "isolation": r.get("isolation")})

    # (b) SANDBOXED hostile-input execution + containment invariants (§5.3). ─────
    hostile_contained = 0
    for adv in suite["adversaries"]:
        r = run_impl(impl, adv["input"])
        man = r.get("isolation") or {}
        # Contained = it RAN in the sandbox, returned inert DATA (a string), and the
        # honest isolation manifest confirms confinement engaged. A pure normalizer
        # structurally cannot shell out or open a socket inside spawn_worker.
        contained = (bool(r.get("ok")) and isinstance(r.get("out"), str)
                     and man.get("no_new_privs") is True)
        hostile_contained += 1 if contained else 0
        if not contained:
            failures.append({"stage": "hostile", "seed": adv["seed"],
                             "input": adv["input"], "got": r.get("out"),
                             "error": r.get("error")})
        case_receipts.append({"stage": "hostile", "seed": int(adv["seed"]),
                              "input": adv["input"], "got": r.get("out"),
                              "contained": contained, "ok": contained,
                              "ran": bool(r.get("ran")), "isolation": man or None})

    # (c) PROPERTY-BASED + FUZZ with a SEEDED PRNG (§5.5) + (d) DIFFERENTIAL (§5.4).
    fuzz_inputs = _fuzz_inputs(seed, fuzz_n)
    prop_pass = 0
    diff_agree = 0
    inc_impl = None
    if incumbent is not None:
        inc_src = incumbent["source_blobs"]
        inc_impl = {"source_blobs": inc_src,
                    "entrypoint": incumbent.get("entrypoint") or _entrypoint(inc_src)}
    for s in fuzz_inputs:
        r = run_impl(impl, {"text": s})
        out, out2 = r.get("out"), r.get("out2")
        inv_ok, inv_why = _text_invariants(out)
        idempotent = (out == out2)
        prop_ok = bool(r.get("ok")) and inv_ok and idempotent
        prop_pass += 1 if prop_ok else 0
        if not prop_ok:
            failures.append({"stage": "property", "input": {"text": s},
                             "got": out, "out2": out2, "idempotent": idempotent,
                             "reason": inv_why if not inv_ok else "not idempotent",
                             "error": r.get("error")})
        rec = {"stage": "property", "input": {"text": s}, "got": out, "out2": out2,
               "idempotent": idempotent, "ok": prop_ok, "ran": bool(r.get("ran")),
               "isolation": r.get("isolation")}
        if inc_impl is not None:
            ir = run_impl(inc_impl, {"text": s})
            agree = bool(ir.get("ok")) and ir.get("out") == out
            diff_agree += 1 if agree else 0
            rec["differential"] = {"incumbent_out": ir.get("out"), "agree": agree}
            if not agree:
                failures.append({"stage": "differential", "input": {"text": s},
                                 "got": out, "incumbent_out": ir.get("out")})
        case_receipts.append(rec)

    # (e) STATIC/SECURITY source scan (SkillSpector adapter, §5.6). ──────────────
    findings = scan_source(source, decl, name)
    high = [f for f in findings if f.get("sev") == "high"]

    # -- aggregate metrics: INTS ONLY (no floats in signed content). ────────────
    metrics = {
        "deterministic_cases": len(suite["cases"]),
        "deterministic_pass": det_pass,
        "hostile_cases": len(suite["adversaries"]),
        "hostile_contained": hostile_contained,
        "property_cases": fuzz_n,
        "property_pass": prop_pass,
        "differential_cases": (fuzz_n if inc_impl is not None else 0),
        "differential_agree": diff_agree,
        "high_findings": len(high),
        "total_cases": len(case_receipts),
    }

    det_ok = metrics["deterministic_pass"] == metrics["deterministic_cases"]
    hostile_ok = (metrics["hostile_cases"] >= 1
                  and metrics["hostile_contained"] == metrics["hostile_cases"])
    prop_ok = metrics["property_pass"] == metrics["property_cases"]
    diff_ok = (inc_impl is None
               or metrics["differential_agree"] == metrics["differential_cases"])
    scan_ok = metrics["high_findings"] == 0

    # THE GATE. Model judgments never enter it (§5): deterministic evidence rules.
    promote_eligible = det_ok and hostile_ok and prop_ok and diff_ok and scan_ok

    # A model judge (§5 rank 8) is recorded as EVIDENCE only — it can neither
    # promote nor override a deterministic failure.
    model_judge_rec = None
    if model_judge is not None:
        verdict = bool(model_judge(candidate, {"metrics": metrics, "findings": findings}))
        model_judge_rec = {"verdict": verdict, "authority": False,
                           "note": "advisory only; never overrides deterministic evidence"}

    reason = (f"det {det_pass}/{metrics['deterministic_cases']}, "
              f"hostile {hostile_contained}/{metrics['hostile_cases']}, "
              f"property {prop_pass}/{fuzz_n}, "
              f"differential {diff_agree}/{metrics['differential_cases']}, "
              f"high-findings {len(high)}")

    reproducibility = {"seed": seed, "deterministic": True, "fuzz_n": int(fuzz_n),
                       "environment_digest": suite.get("environment_digest")}

    # -- record the EvaluationResult Cell (§4) as the evidence, on the Weft. ─────
    result_content = {
        "candidate": cand_cell,
        "suite": suite_id,
        "environment": suite.get("environment_digest"),
        "implementation_digest": digest,
        "case_receipts": case_receipts,
        "aggregate_metrics": metrics,
        "failures": failures,
        "security_findings": findings,
        "reproducibility": reproducibility,
        "promote_eligible": promote_eligible,
        "verdict_reason": reason,
        "model_judge": model_judge_rec,
        "incumbent": (incumbent["cell"] if incumbent is not None else None),
    }
    result_id = content_id({"evaluation_result": name, "candidate": cand_cell,
                            "suite": suite_id, "impl": digest}, kind="cell")
    assert_content(kernel.weft, reckoner, result_id, EVALUATION_RESULT, result_content)
    assert_edge(kernel.weft, reckoner, cand_cell, "evaluated_result", result_id)
    assert_edge(kernel.weft, reckoner, result_id, "for_suite", suite_id)

    return EvaluationOutcome(result_id, promote_eligible, metrics, findings,
                             failures, case_receipts, reproducibility, reason,
                             model_judge_rec)
