"""Extension candidates and evaluation suites (Nona wave N2).

A candidate is a PROPOSAL, and this module is deliberately the half of Nona that cannot
do anything. It writes two kinds of Cell — the candidate and the suite that will judge it
— and executes nothing: no generated code runs here, no capability is minted, no
quarantine is lifted. Evaluation is N3; promotion is N4. Keeping proposal separate from
execution is what makes the dangerous part small enough to reason about.

SOURCE IS DATA, NEVER INSTRUCTION. A candidate's implementation arrives as bytes on the
log with `source_is_data: True`. Nothing in this module parses, imports, compiles or
`exec`s it; the only thing computed from it is a content digest. That digest is what every
later stage BINDS to — the Reckoner evaluates a digest, the promotion attests a digest, and
the runner refuses an implementation whose digest does not match the one that was
evaluated. So "the code that was tested is the code that runs" is a hash equality, not a
hope.

TWO EVENTS, ONE CELL. A candidate lands as `DRAFT` and then as `QUARANTINED` on the SAME
content-addressed cell. The transition is therefore PROVENANCE on the Weft — two signed
assertions whose order the fold records — rather than a row someone edited. You can always
ask "was this ever a draft, and who moved it?" and the log answers.

BORN QUARANTINED. Every candidate carries `QUARANTINE_BASELINE` from its first event:
`sandbox_only`, `no_outward_effects`, `network_allow: []`. Quarantine is not a state a
candidate is put into after review — it is the state it is born in, and only a trusted
promoter's attestation can lift it (see `anchors`).

CODEGEN IS AN INJECTED SEAM THAT FAILS CLOSED. There is no default path from "an intent" to
"generated source". A caller must supply the generator explicitly; the default raises
`CodegenUnavailable`. That means this module can never quietly acquire a model dependency,
tests are deterministic by construction, and an offline install cannot be surprised into
generating code.

DETERMINISM. All content is ints and strings — no floats, no wall-clock — so a candidate
and its suite are replayable and hashable exactly like any other Cell (thresholds are
integer comparisons, not tolerances).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from decima.kernel import model
from decima.kernel.hashing import blob_id, content_id, nfc
from decima.kernel.weft import Weft

CANDIDATE = "candidate"
EVALUATION_SUITE = "evaluation_suite"

# §3 quarantine baseline — the caveats every candidate is BORN with. Matched to the frozen
# reference (`heartbeat/decima/candidate.py`) so the shipping loop and the oracle agree.
QUARANTINE_BASELINE: dict[str, Any] = {
    "sandbox_only": True,
    "no_outward_effects": True,
    "network_allow": [],
}

# The effect-class ladder, least → most power. A candidate may DECLARE any of these; only
# a subset is ever SIGNABLE (see `anchors.SIGNABLE_TIERS`) — declaring is cheap, promoting
# is not, and the ladder is wider than the set of tiers that have an executable path.
EFFECT_CLASSES: tuple[str, ...] = (
    "pure",
    "read_only",
    "workspace_write",
    "network",
    "financial",
)

DRAFT = "DRAFT"
QUARANTINED = "QUARANTINED"


class CodegenUnavailable(RuntimeError):
    """The codegen seam was not injected. Raised by the DEFAULT generator so that no
    caller can accidentally reach a model: generation must be an explicit choice, and an
    offline install fails closed rather than acquiring a dependency by surprise."""


def _no_codegen(intent: str) -> str:
    raise CodegenUnavailable(
        "no codegen function was injected: pass codegen=<callable> to propose_candidate. "
        "There is deliberately no default path from an intent to generated source "
        "(decima/services/nona/candidate.py)"
    )


def implementation_digest(source: str) -> str:
    """The content-address of a candidate's implementation.

    This is the binding every later stage uses: evaluation records it, promotion attests
    it, and execution refuses a mismatch — so a promoted organ cannot be swapped for
    different code after the fact. Domain-separated as a blob because the source is DATA.
    """
    return blob_id(nfc(source).encode("utf-8"), kind="blob")


def propose_candidate(
    weft: Weft,
    author: str,
    *,
    intent: str,
    declared_effect_class: str,
    source: str | None = None,
    codegen: Callable[[str], str] = _no_codegen,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    eval_plan: list[str] | None = None,
    entrypoint: str = "main",
) -> dict[str, Any]:
    """Propose an extension candidate: assert it `DRAFT`, then `QUARANTINED`.

    `source` is the implementation as DATA. If omitted, `codegen(intent)` produces it —
    and the default `codegen` raises, so generation never happens implicitly.

    Nothing is executed, imported or compiled here, and no capability is minted: the
    candidate is a proposal with a digest. Returns a public summary (cell id, digest,
    lifecycle) — never the source, which lives on the log.
    """
    if declared_effect_class not in EFFECT_CLASSES:
        raise ValueError(
            f"unknown effect class {declared_effect_class!r}; the ladder is {list(EFFECT_CLASSES)}"
        )
    body = source if source is not None else codegen(intent)
    if not isinstance(body, str) or not body.strip():
        raise ValueError("a candidate implementation must be non-empty source text")

    digest = implementation_digest(body)
    base: dict[str, Any] = {
        "intent": nfc(intent),
        "author": author,
        # The implementation rides as DATA and is flagged as such, so every reader — and
        # every model that is ever shown a candidate — treats it as content, not command.
        "source": body,
        "source_is_data": True,
        "implementation_digest": digest,
        "entrypoint": nfc(entrypoint),
        "input_schema": dict(input_schema or {}),
        "output_schema": dict(output_schema or {}),
        "declared_effect_class": declared_effect_class,
        "eval_plan": [nfc(s) for s in (eval_plan or [])],
        # Born quarantined — a copy, so a caller cannot mutate the module constant.
        "quarantine": dict(QUARANTINE_BASELINE),
    }
    # The cell id is content-addressed over the BINDING facts, so re-proposing identical
    # source under the same intent is the same candidate rather than a second one.
    cell = f"candidate:{blob_id(f'{author}|{intent}|{digest}'.encode(), kind='cell')}"

    # Two events on ONE cell: the DRAFT→QUARANTINED transition is provenance, not an edit.
    model.assert_content(
        weft, author, cell, CANDIDATE, {**base, "lifecycle": DRAFT, "states": [DRAFT]}
    )
    model.assert_content(
        weft,
        author,
        cell,
        CANDIDATE,
        {**base, "lifecycle": QUARANTINED, "states": [DRAFT, QUARANTINED]},
    )
    return {
        "cell": cell,
        "implementation_digest": digest,
        "lifecycle": QUARANTINED,
        "declared_effect_class": declared_effect_class,
    }


def declare_suite(
    weft: Weft,
    author: str,
    *,
    subject_schema: dict[str, Any],
    cases: list[dict[str, Any]],
    thresholds: dict[str, int] | None = None,
    verifiers: list[str] | None = None,
    adversaries: list[str] | None = None,
    metrics: list[str] | None = None,
    repetitions: int = 1,
    environment_digest: str = "",
    version: int = 1,
) -> dict[str, Any]:
    """Declare the evaluation suite a candidate will be judged against.

    Thresholds are INTEGER comparisons on purpose: a gate that says "≥ 95" is replayable
    and cannot drift with floating-point representation, and a suite is a versioned Cell so
    "which suite passed this?" is answerable forever.

    The suite is DATA, like everything else — declaring it grants nothing and runs nothing.
    Whether a candidate may supply its own cases (and which cases are baseline-only) is a
    governance question the design leaves to Decision 6; this function records whatever the
    caller declares and takes no position.
    """
    bad = {k: v for k, v in (thresholds or {}).items() if not isinstance(v, int) or v is True}
    if bad:
        raise ValueError(
            f"thresholds must be plain ints (determinism: signed content carries no floats): {bad}"
        )
    if repetitions < 1:
        raise ValueError("repetitions must be >= 1")
    content: dict[str, Any] = {
        "subject_schema": dict(subject_schema),
        "environment_digest": nfc(environment_digest),
        "cases": [dict(c) for c in cases],
        "verifiers": [nfc(v) for v in (verifiers or [])],
        "adversaries": [nfc(a) for a in (adversaries or [])],
        "metrics": [nfc(m) for m in (metrics or [])],
        "thresholds": dict(thresholds or {}),
        "repetitions": int(repetitions),
        "contamination_policy": "baseline-and-candidate-cases-are-distinguished",
        "version": int(version),
    }
    # Content-addressed over the whole declaration: the same suite declared twice IS the
    # same cell, so an evaluation result can name exactly which suite judged it.
    cell = f"suite:{content_id(content, kind='cell')}"
    model.assert_content(weft, author, cell, EVALUATION_SUITE, content)
    return {"cell": cell, "version": int(version), "cases": len(content["cases"])}
