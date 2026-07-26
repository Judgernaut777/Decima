"""The Reckoner — the evaluation gate (Nona wave N3).

This is the organ that decides whether a candidate may ever be promoted, and it is a
SERVICE rather than kernel code for one reason: it executes untrusted source, so it must
live outside the TCB. Every stage is stdlib plus the existing `decima.workers` jail — no
new dependency, and nothing here is trusted with authority.

THE GATE IS A PURE FUNCTION OF RECORDED INTEGERS. `promote_eligible` is computed in exactly
one place (`gate`), from counts that are all `int`, over evidence that is already on the
log. It is not a policy scattered across stages, and it is not a judgement — it is
arithmetic anyone can re-run against the recorded `evaluation_result`. That is what makes
"why was this promoted?" answerable forever, and what makes the answer checkable by someone
who does not trust the Reckoner.

THREE HONESTY RULES (carried verbatim from `specs/NONA_RECKONER.md`):

  1. A candidate that ERRORS in the sandbox FAILS. It never passes by accident.
  2. A worker killed by the wall-clock/CPU backstop yields `UNKNOWN`, and **UNKNOWN IS NOT
     A PASS**. The system says "I don't know" and refuses, rather than rewriting ignorance
     as success.
  3. A model judgment can NEVER override a deterministic failure. The judge is recorded
     with `authority: False` and is not an input to `gate` at all — it is commentary in the
     record, structurally incapable of flipping the arithmetic.

DETERMINISM. Same candidate + same suite must yield the same `evaluation_result` content and
therefore the same `state_root`. So: no wall-clock, no unseeded randomness (the property
seed is Cell DATA), and no host-variable facts in hashed content. Cost and latency are
wall-clock by nature and stay in unhashed diagnostics, out of the gate.

HOST VARIANCE FAILS CLOSED (owner Decision 5). The containment matrix is host-dependent —
the seccomp layer is aarch64-only and namespaces can be unavailable. Rather than record a
WEAKER result under the same suite id (which would make two evaluation results with the
same identity mean different things), a host that cannot deliver the suite's declared
containment REFUSES TO EVALUATE. The raw manifest is kept in diagnostics; only
determinism-safe assertions enter the Cell.

SUITE AUTHORSHIP FAILS CLOSED (owner Decision 6). A candidate may contribute cases, but the
baseline is root-declared and the candidate's cases are MERGED WITH, never substituted for,
it — and every ADVERSARIAL case comes from the baseline only. This is the line between "the
tests gate the code" and "the code chose its tests".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from decima.kernel import model
from decima.kernel.hashing import content_id, nfc
from decima.kernel.weft import Weft

EVALUATION_RESULT = "evaluation_result"
FINDING = "finding"

# Severities. Only HIGH blocks the gate; the rest are recorded evidence.
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

# Case provenance. `BASELINE` cases are root-declared; `CANDIDATE` cases are the
# candidate's own contribution. Adversarial cases MUST be BASELINE (Decision 6).
BASELINE = "baseline"
CANDIDATE_SUPPLIED = "candidate"


class EvaluationRefused(RuntimeError):
    """The evaluation could not be run HONESTLY, so it was not run at all.

    Raised when the host cannot deliver the containment the suite declares (Decision 5), or
    when a suite violates the authorship rule (Decision 6). Refusing is the point: a
    recorded result must mean the same thing on every host, so an environment that would
    produce a weaker one produces none.
    """


@dataclass(frozen=True)
class Metrics:
    """The integer evidence the gate reads. Every field is a COUNT, never a ratio — a
    ratio would invite a float into signed content, and a float would break replay."""

    deterministic_cases: int = 0
    deterministic_pass: int = 0
    hostile_cases: int = 0
    hostile_contained: int = 0
    property_cases: int = 0
    property_pass: int = 0
    differential_cases: int = 0
    differential_agree: int = 0
    high_findings: int = 0
    unknown_outcomes: int = 0
    errored_cases: int = 0

    def as_content(self) -> dict[str, int]:
        return {
            "deterministic_cases": int(self.deterministic_cases),
            "deterministic_pass": int(self.deterministic_pass),
            "hostile_cases": int(self.hostile_cases),
            "hostile_contained": int(self.hostile_contained),
            "property_cases": int(self.property_cases),
            "property_pass": int(self.property_pass),
            "differential_cases": int(self.differential_cases),
            "differential_agree": int(self.differential_agree),
            "high_findings": int(self.high_findings),
            "unknown_outcomes": int(self.unknown_outcomes),
            "errored_cases": int(self.errored_cases),
        }


@dataclass(frozen=True)
class Verdict:
    eligible: bool
    reason: str
    metrics: Metrics
    findings: list[dict[str, Any]] = field(default_factory=list)


def gate(metrics: Metrics, *, has_incumbent: bool = False) -> Verdict:
    """THE gate. A pure function of integers — no I/O, no model, no host facts.

    Deliberately structured as "every condition must hold", with the first failure naming
    itself, so a refusal is explainable without re-deriving the arithmetic. A model judge is
    not a parameter here: it CANNOT reach this function, which is how rule 3 is enforced
    structurally rather than by convention.
    """
    m = metrics
    if m.errored_cases:
        # Honesty rule 1: erroring in the sandbox is a failure, never a pass.
        return Verdict(False, f"{m.errored_cases} case(s) errored in the sandbox", m)
    if m.unknown_outcomes:
        # Honesty rule 2: UNKNOWN is not a pass. A backstop-killed run means we do not
        # know the answer, and "we do not know" must never promote.
        return Verdict(False, f"{m.unknown_outcomes} case(s) resolved UNKNOWN (not a pass)", m)
    if m.deterministic_cases <= 0:
        return Verdict(False, "no deterministic cases were run", m)
    if m.deterministic_pass != m.deterministic_cases:
        return Verdict(
            False,
            f"deterministic {m.deterministic_pass}/{m.deterministic_cases}",
            m,
        )
    if m.hostile_cases < 1:
        # A suite that never attacked the candidate has not evaluated it.
        return Verdict(False, "no adversarial case was run (>=1 required)", m)
    if m.hostile_contained != m.hostile_cases:
        return Verdict(False, f"hostile containment {m.hostile_contained}/{m.hostile_cases}", m)
    if m.property_pass != m.property_cases:
        return Verdict(False, f"property {m.property_pass}/{m.property_cases}", m)
    if has_incumbent and m.differential_agree != m.differential_cases:
        return Verdict(
            False,
            f"differential regression {m.differential_agree}/{m.differential_cases}",
            m,
        )
    if m.high_findings:
        return Verdict(False, f"{m.high_findings} high security finding(s)", m)
    return Verdict(True, "all gates passed", m)


def require_authored_suite(suite: dict[str, Any]) -> None:
    """Enforce Decision 6: the baseline governs, and adversaries are baseline-only.

    A candidate may add cases; it may not replace the baseline and it may not author the
    attacks it is judged by. Violating either makes the evaluation meaningless, so this
    refuses rather than recording a result that looks the same but proves less.
    """
    cases = suite.get("cases") or []
    if not any(c.get("origin", BASELINE) == BASELINE for c in cases):
        raise EvaluationRefused(
            "suite has no baseline case: a candidate may contribute cases but may not "
            "REPLACE the root-declared baseline (Decision 6)"
        )
    self_authored_attacks = [
        c for c in cases if c.get("adversarial") and c.get("origin", BASELINE) != BASELINE
    ]
    if self_authored_attacks:
        raise EvaluationRefused(
            f"{len(self_authored_attacks)} adversarial case(s) are candidate-authored: "
            "adversarial cases come ONLY from the baseline, or the code chose its own "
            "tests (Decision 6)"
        )


def require_host_containment(suite: dict[str, Any], containment: dict[str, Any]) -> None:
    """Enforce Decision 5: refuse to evaluate where the declared containment is absent.

    `suite["environment_digest"]` pins the containment matrix the result is only meaningful
    under. A host that cannot deliver it does not get to record a weaker result under the
    same suite identity — it gets an `EvaluationRefused`. The raw manifest stays in
    diagnostics; only the determinism-safe assertions below ever enter the Cell.
    """
    declared = suite.get("environment_digest") or ""
    actual = environment_digest(containment)
    if declared and declared != actual:
        raise EvaluationRefused(
            f"host containment {actual!r} does not match the suite's declared "
            f"{declared!r}: refusing to record a weaker result under the same suite id "
            "(Decision 5)"
        )
    for required in ("no_new_privs", "network_denied", "chroot"):
        if not containment.get(required):
            raise EvaluationRefused(
                f"host cannot deliver required containment {required!r}: refusing to "
                "evaluate rather than recording an unsound result (Decision 5)"
            )


def environment_digest(containment: dict[str, Any]) -> str:
    """A content-address over the DETERMINISM-SAFE containment assertions only.

    Host-variable detail (kernel version, which seccomp arch, timings) is deliberately
    excluded: it would make the digest differ between honest hosts and turn a safety pin
    into noise. What is included is exactly what the result's soundness depends on.
    """
    safe = {
        "no_new_privs": bool(containment.get("no_new_privs")),
        "network_denied": bool(containment.get("network_denied")),
        "chroot": bool(containment.get("chroot")),
        "namespaces": bool(containment.get("namespaces")),
        "matrix_version": int(containment.get("matrix_version", 0)),
    }
    return content_id(safe, kind="cell")


def record_result(
    weft: Weft,
    author: str,
    *,
    candidate_cell: str,
    suite_cell: str,
    implementation_digest: str,
    verdict: Verdict,
    containment: dict[str, Any],
    model_judge: dict[str, Any] | None = None,
    seed: int = 0,
) -> str:
    """Assert the `evaluation_result` Cell — the evidence a promotion must cite.

    `model_judge` is recorded with `authority: False` because it is commentary, not a vote:
    `gate` cannot read it. Cost and latency are NOT recorded here at all; they are
    wall-clock by nature and would break replay if they entered hashed content.
    """
    content: dict[str, Any] = {
        "candidate": candidate_cell,
        "suite": suite_cell,
        "implementation_digest": implementation_digest,
        "environment": environment_digest(containment),
        "aggregate_metrics": verdict.metrics.as_content(),
        "failures": [] if verdict.eligible else [nfc(verdict.reason)],
        "security_findings": list(verdict.findings),
        "reproducibility": {"seed": int(seed)},
        "promote_eligible": bool(verdict.eligible),
        "verdict_reason": nfc(verdict.reason),
        # Recorded, powerless, and structurally unable to reach the gate.
        "model_judge": {**(model_judge or {}), "authority": False},
    }
    cell = f"evaluation:{content_id(content, kind='cell')}"
    model.assert_content(weft, author, cell, EVALUATION_RESULT, content)
    return cell
