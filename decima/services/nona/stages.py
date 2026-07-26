"""The Reckoner's evaluation stages (Nona wave N3, part 2).

`reckoner.gate` decides; these stages produce the integers it decides on. They are kept in a
separate module because they are the part that TOUCHES the candidate — parsing it, running it
in a jail, attacking it — while the gate stays a pure function that anyone can re-check.

Stage order follows `specs/NONA_RECKONER.md`:

  1. contract      — the candidate's declared schemas, and every case, must typecheck
  2. deterministic — exact-output over seeded cases, in a PURE worker
  3. hostile       — at least one baseline adversarial case; containment asserted from the
                     IN-CHILD manifest, not from what the parent hoped happened
  4. differential  — the same inputs against a promoted incumbent's digest
  5. property      — `random.Random(seed)` where the SEED IS CELL DATA, never `os.urandom`
                     or the clock, so a property run replays identically
  6. scan          — AST + token scan, including the RUG-PULL check
  7. rubric        — human review, out of scope for N0–N7 (Decision 6)
  8. judge         — a model's opinion, recorded with `authority: False` and never an input
                     to the gate

THE RUG-PULL CHECK IS THE POINT OF STAGE 6. A candidate can behave perfectly on every case
and still be refused, because behaviour is not capability: if the source IMPORTS something
that implies network or process reach the candidate never declared, then it is asking for
power it did not admit to, and that is a HIGH finding. Testing what code does on chosen
inputs can never establish what it is able to do — so the scan looks at reach, not results.

EXECUTION IS AN INJECTED SEAM. `run_case` takes a `runner`, defaulting to the real jailed
`decima.workers.run_worker`. That is not for convenience: it keeps this module honest under
test (deterministic, no spawning) while the production path is the same jail every other
untrusted effect uses, with its digest binding and UNKNOWN-on-timeout semantics intact.
"""

from __future__ import annotations

import ast
import random
from collections.abc import Callable, Sequence
from typing import Any

from decima.services.nona import candidate as candidate_mod
from decima.services.nona.reckoner import (
    BASELINE,
    HIGH,
    MEDIUM,
    Metrics,
)

# Import roots that imply reach beyond a pure computation. Mapped to the LEAST effect class
# that may legitimately ask for them; anything below that on the ladder is a rug-pull.
_REACH: dict[str, str] = {
    # network
    "socket": "network",
    "http": "network",
    "urllib": "network",
    "requests": "network",
    "ftplib": "network",
    "smtplib": "network",
    "asyncio": "network",
    # process / arbitrary local effect
    "subprocess": "workspace_write",
    "multiprocessing": "workspace_write",
    "ctypes": "workspace_write",
    "shutil": "workspace_write",
    # filesystem writes (reading is read_only; the scan cannot tell which, so treat the
    # module as write-capable and let a read_only candidate justify itself another way)
    "pathlib": "read_only",
    "os": "read_only",
    "io": "read_only",
}

# Dynamic-execution builtins. These defeat the scan itself — source that builds code at run
# time cannot be statically characterised — so they are a HIGH finding at ANY tier.
_DYNAMIC = frozenset({"eval", "exec", "compile", "__import__", "globals", "breakpoint"})

_LADDER = candidate_mod.EFFECT_CLASSES


def _rank(effect_class: str) -> int:
    return _LADDER.index(effect_class) if effect_class in _LADDER else len(_LADDER)


def finding(severity: str, rule: str, detail: str) -> dict[str, Any]:
    """A `finding` shaped the way `weave.canary_health` already looks for."""
    return {"severity": severity, "rule": rule, "detail": detail}


# ── stage 1: contract ────────────────────────────────────────────────────────
def validate_contract(candidate: dict[str, Any], suite: dict[str, Any]) -> list[dict[str, Any]]:
    """Check the candidate declares a usable contract and the suite's cases fit it.

    A candidate with no output schema cannot be judged for correctness — "it returned
    something" is not a pass — so a missing contract is a finding rather than a silent
    default.
    """
    out: list[dict[str, Any]] = []
    if not candidate.get("output_schema"):
        out.append(
            finding(MEDIUM, "contract.no_output_schema", "candidate declares no output schema")
        )
    declared = candidate.get("declared_effect_class")
    if declared not in _LADDER:
        out.append(
            finding(HIGH, "contract.unknown_effect_class", f"undeclared/unknown tier {declared!r}")
        )
    for i, case in enumerate(suite.get("cases") or []):
        if "in" not in case:
            out.append(finding(MEDIUM, "contract.case_without_input", f"case {i} has no input"))
    return out


# ── stage 5: seeded property inputs ──────────────────────────────────────────
def property_inputs(suite: dict[str, Any], *, count: int = 8) -> list[int]:
    """Generate property inputs from the suite's SEED, which is Cell data.

    Seeded from the log rather than from `os.urandom` or the clock, so re-running an
    evaluation of the same candidate against the same suite explores the same inputs and
    produces the same recorded result. Unseeded fuzzing would make every evaluation
    unreplayable, which would quietly break Law 5 for the whole promotion path.
    """
    seed = int(suite.get("reproducibility", {}).get("seed", suite.get("seed", 0)))
    rng = random.Random(seed)
    return [rng.randint(-(2**31), 2**31 - 1) for _ in range(int(count))]


# ── stage 6: static scan, including the rug-pull check ───────────────────────
def scan_source(source: str, declared_effect_class: str) -> list[dict[str, Any]]:
    """AST-scan a candidate for reach it did not declare.

    Two rules:

    * RUG-PULL — an import implying network or process reach that the declared tier does not
      cover is HIGH. This is what makes "all cases passed" insufficient: cases show what the
      code did, imports show what it CAN do.
    * DYNAMIC EXECUTION — `eval`/`exec`/`compile`/`__import__` are HIGH at any tier, because
      code assembled at run time cannot be characterised by reading it, which defeats this
      stage rather than merely exceeding a tier.

    Unparseable source is itself HIGH: something that cannot be read cannot be cleared.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [finding(HIGH, "scan.unparseable", f"source does not parse: {exc.msg}")]

    out: list[dict[str, Any]] = []
    allowed = _rank(declared_effect_class)
    seen_roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen_roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            seen_roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Name) and node.id in _DYNAMIC:
            out.append(
                finding(
                    HIGH,
                    "scan.dynamic_execution",
                    f"{node.id}() builds or runs code at run time, so the source cannot be "
                    "statically characterised",
                )
            )

    for root in sorted(seen_roots):
        needed = _REACH.get(root)
        if needed is None:
            continue
        if _rank(needed) > allowed:
            out.append(
                finding(
                    HIGH,
                    "scan.rug_pull",
                    f"imports {root!r}, which implies {needed!r} reach, but the candidate "
                    f"declared only {declared_effect_class!r}",
                )
            )
    return out


# ── stages 2/3/4: cases in the jail ─────────────────────────────────────────
CaseRunner = Callable[[dict[str, Any]], dict[str, Any]]


def _default_runner(_case: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(
        "inject a case runner: production passes a closure over decima.workers.run_worker "
        "(PURE profile), which enforces the implementation-digest binding and yields UNKNOWN "
        "rather than a fabricated result on a backstop kill"
    )


def run_cases(
    suite: dict[str, Any],
    *,
    runner: CaseRunner = _default_runner,
) -> tuple[Metrics, list[dict[str, Any]]]:
    """Run the suite's cases and tally the integers the gate reads.

    Each `runner(case)` returns `{"status": ..., "output": ...}` where status is one of
    `SUCCEEDED` / `FAILED` / `UNKNOWN` — the same vocabulary the worker layer already uses.
    Three tallies matter, and each maps to an honesty rule:

    * a case whose status is UNKNOWN increments `unknown_outcomes` — never a pass;
    * a case whose runner raised increments `errored_cases` — never a pass;
    * a HOSTILE case counts as contained only when the jail held, which for an adversarial
      case means the run did NOT succeed in doing what it attempted. A hostile case that
      "succeeded" is a containment failure, not a passing test.
    """
    det = det_pass = hostile = contained = prop = prop_pass = unknown = errored = 0
    findings: list[dict[str, Any]] = []

    for case in suite.get("cases") or []:
        adversarial = bool(case.get("adversarial"))
        is_property = bool(case.get("property"))
        if adversarial and case.get("origin", BASELINE) != BASELINE:
            # Decision 6 is enforced in reckoner.require_authored_suite; if a
            # candidate-authored attack reaches here it is recorded, never counted.
            findings.append(
                finding(HIGH, "suite.candidate_authored_attack", "attack not from the baseline")
            )
            continue
        try:
            result = runner(case)
        except Exception as exc:  # a runner blow-up is a FAILED case, never a pass
            errored += 1
            findings.append(
                finding(MEDIUM, "case.errored", f"{type(exc).__name__} while running a case")
            )
            continue

        status = str(result.get("status", "UNKNOWN"))
        if status == "UNKNOWN":
            unknown += 1
            continue
        if adversarial:
            hostile += 1
            # The jail held iff the attack did not achieve its effect.
            if status != "SUCCEEDED" or result.get("contained") is True:
                contained += 1
            else:
                findings.append(
                    finding(HIGH, "containment.escaped", "an adversarial case ran to success")
                )
            continue
        if is_property:
            prop += 1
            prop_pass += (
                1 if status == "SUCCEEDED" and result.get("output") == case.get("out") else 0
            )
            continue
        det += 1
        det_pass += 1 if status == "SUCCEEDED" and result.get("output") == case.get("out") else 0

    return (
        Metrics(
            deterministic_cases=det,
            deterministic_pass=det_pass,
            hostile_cases=hostile,
            hostile_contained=contained,
            property_cases=prop,
            property_pass=prop_pass,
            high_findings=sum(1 for f in findings if f["severity"] == HIGH),
            unknown_outcomes=unknown,
            errored_cases=errored,
        ),
        findings,
    )


def merge_findings(*groups: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten stage findings, preserving order (evidence reads chronologically)."""
    out: list[dict[str, Any]] = []
    for g in groups:
        out.extend(g)
    return out


def with_findings(metrics: Metrics, findings: Sequence[dict[str, Any]]) -> Metrics:
    """Re-tally `high_findings` over the FULL evidence set.

    The gate reads one number for "did anything serious turn up", so it must be counted once
    over every stage's findings — not per stage, where a HIGH from the scan could be lost
    behind a clean case run.
    """
    highs = sum(1 for f in findings if f.get("severity") == HIGH)
    return Metrics(**{**metrics.__dict__, "high_findings": highs})
