"""The Nona lane — self-extension as four named commands (wave N6, backend half).

OWNED BY THE NONA LANE. This module is the only backend file the lane edits besides its own
screen (``js/screens/nona.js``) and tests; the shared contracts live in ``contracts.py`` and
the wiring is one line each in ``commands._handlers``, ``app.FEATURE_READERS`` and
``routes.ROUTES``:

  commands  ProposeCapability                    → :func:`propose_capability`
            EvaluateCandidate                    → :func:`evaluate_candidate`
            PromoteCandidate      (GATED)        → :func:`promote_candidate`
            RollbackPromotion     (GATED)        → :func:`rollback_promotion`
  readers   GET /api/v1/nona/candidates          → :func:`list_candidates`
            GET /api/v1/nona/candidates/detail   → :func:`get_candidate`
            GET /api/v1/nona/decisions           → :func:`list_promotion_decisions`
            GET /api/v1/nona/discover            → :func:`discover_capabilities`

TWO OF THE FOUR ARE GATED, AND THAT IS THE ENTIRE GATING CODE. ``PromoteCandidate`` and
``RollbackPromotion`` are in ``commands.GATED``, so ``CommandService.execute`` defers them:
submitting one writes a pending ``inbox_item`` Cell and NOTHING else — no capability is
built, no quarantine moves — and returns ``APPROVAL_REQUIRED`` / 202. The handler is only
reached later, re-driven with ``approved=True`` by ``_approve_invocation``, after a
reauth-gated human possession proof has been verified and a signed ``inbox_decision`` Cell
recorded. ``ProposeCapability`` and ``EvaluateCandidate`` are NOT gated: they write a
proposal and evidence, and neither has an outward effect. Adding two names to ``GATED`` also
widens the plan-step surface (``plan_service`` validates a step's ``selector.approval``
against the same set), which is a deliberate, tested choice: a model-proposed plan may now
name a promotion as a checkpoint, and that checkpoint still routes through the human
decision like any other.

BECAUSE A GATED COMMAND IS RE-DRIVEN, IT RE-VALIDATES ITS EVIDENCE. Between submission and
approval the world moves. So at enactment time the handler re-folds and re-checks: the
candidate still exists, its source still hashes to the digest it recorded, the cited
evaluation still names THAT candidate and THAT digest, and it is still ``promote_eligible``.
A drifted digest is ``EVIDENCE_STALE`` and writes nothing. Without that, "approve" would
mean "approve whatever the candidate has become since I read it".

THE ARGS OF A GATED COMMAND ARE IDS ONLY. ``_enqueue_approval`` copies ``args`` VERBATIM
into signed inbox content, so a promote submission carries ``{candidate, evaluation}`` and
never the generated source, never a name, never a float. Everything the operator needs to
decide — the tier, the metrics, the findings, the verdict reason, the surface class — is
derived by the readers FROM THE FOLD, not read back out of the request. That is why the
tiered inbox surface below cannot be spoofed by a crafted submission.

TIERED SURFACE, ONE PROMPT PER ORGAN (design §5.8 points 3-4). ``powerbox.prompt_plan``
decides how much ceremony a tier gets: ``pure``/``read_only`` land as revocable
notifications, ``workspace_write`` as a canary notification with a rollback affordance, and
``financial`` as an explicit approval with the evidence inline. A promoted organ on an
AUTOMATED tier that carries an approval caveat gets ONE capability-scoped approval written
at promotion time (``capability.approval_id(cap, None)``) — a durable "yes, this organ may
act" — while a floored tier deliberately gets NO blanket and needs an invocation-scoped
approval per call. A prompt the user always clicks yes on has negative security value; this
is the code that spends the budget where it matters.

NOTHING HERE GENERATES CODE, AND THE DEFAULT REFUSES. Codegen is an injected seam
(:func:`bind_codegen`) whose default is None: with no source supplied and no generator
bound, ``ProposeCapability`` refuses ``NOT_AVAILABLE`` rather than emitting a stub organ
(design §5.9 point 1). Execution is the same: ``EvaluateCandidate`` needs an
:class:`EvaluationHost` bound (:func:`bind_evaluation_host`) — a case runner AND the
containment manifest that runner actually achieved. With none bound it refuses, and with a
host that cannot deliver the mandatory containment the Reckoner refuses (Decision 5): a
recorded result must mean the same thing on every host, so an environment that would
produce a weaker one produces none.

GENERATED SOURCE IS DATA IN BOTH DIRECTIONS. It rides on the log with
``source_is_data: True`` and it leaves these readers stamped
``instruction_eligible: False`` / ``trust: "untrusted"``, so the Shell renders it in an
untrusted zone as a text node and no model is ever handed it as prompt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from decima.kernel import capability as cap_mod
from decima.kernel.crypto import Keyring
from decima.kernel.hashing import content_id
from decima.kernel.inbox import DECISION, ITEM
from decima.kernel.model import assert_content, assert_edge
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Weft
from decima.services.api.contracts import CommandError, CommandServiceLike, LaneReaderApp
from decima.services.custody import ensure_custody
from decima.services.nona import anchors, discovery, executor, powerbox, promotion, stages
from decima.services.nona import candidate as candidate_mod
from decima.services.nona import reckoner as reckoner_mod

if TYPE_CHECKING:
    from decima.services.api.commands import CommandResult

# ── stable reason codes (the values mirror commands.py so the envelope is uniform) ──
BAD_REQUEST = "BAD_REQUEST"
NOT_FOUND = "NOT_FOUND"
# A structural absence on THIS host — no codegen seam, no evaluation host, no trust anchor.
# Deliberately distinct from a refusal about the candidate: there is nothing to approve and
# nothing to fix in the submission (design §5.9 point 1 — refuse, never stub).
NOT_AVAILABLE = "NOT_AVAILABLE"
# The Reckoner declined to run at all (host containment / suite authorship), so no
# evaluation_result exists. Not the same as "evaluated and failed", which IS a result.
EVALUATION_REFUSED = "EVALUATION_REFUSED"
# The evidence a gated promotion cited no longer describes the candidate (digest drift,
# retraction, wrong candidate). Fails closed at ENACTMENT, after the human said yes.
EVIDENCE_STALE = "EVIDENCE_STALE"
PROMOTION_REFUSED = "PROMOTION_REFUSED"
ALREADY_ROLLED_BACK = "ALREADY_ROLLED_BACK"

INCIDENT = "incident"

# The smallest honest discovery bar. `projections.search` scales one exact content-token
# overlap at minimum IDF weight to 100 (`_PRIMARY`), and caps every bonus strictly below
# that, so a score under 100 means "no exact token matched" — fuzzy/proximity noise only.
DEFAULT_THRESHOLD = 100

# A gated command's args take NO free text, and this constant is why: `execute` defers a
# gated command BEFORE its handler runs, copying `args` verbatim into the signed inbox item,
# so a handler-side length check would fire only after the unbounded text had already been
# signed onto the log. The rollback's rationale therefore rides as a fixed sentence, and an
# operator's own words belong on the DECISION Cell (which is written after a human is
# already authenticated and reauthed), never on the request.
ROLLBACK_REASON = "operator rollback: the promotion was withdrawn, the organ re-quarantined"


# ── injected seams: both default to refusing ─────────────────────────────────
Codegen = Callable[[str], str]

# A one-element list rather than a module global rebound by name: `bind_*` must be able to
# hand back the PREVIOUS binding so a test (or an operator) can restore it exactly.
_CODEGEN: list[Codegen | None] = [None]


def bind_codegen(codegen: Codegen | None) -> Codegen | None:
    """Bind the generator ``ProposeCapability`` uses when no ``source`` is supplied.

    Returns the previous binding. Binding CONFERS NOTHING: the generated text is still born
    as quarantined DATA on a candidate Cell, still evaluated, and still needs a promotion
    signature before anything can run it. The default is None so that an offline install —
    or a test — cannot be surprised into reaching a model, and so that the failure is an
    honest ``NOT_AVAILABLE`` instead of a stub organ in the catalogue.
    """
    previous = _CODEGEN[0]
    _CODEGEN[0] = codegen
    return previous


@dataclass(frozen=True)
class EvaluationHost:
    """What it takes to run an evaluation HONESTLY: a case runner and the containment that
    runner actually achieved.

    The two travel together on purpose. `reckoner.require_host_containment` refuses to
    record a result whose declared containment the host cannot deliver, and that check is
    only meaningful if the manifest comes from the thing that ran the cases rather than
    from the caller's hopes. Production binds a closure over ``decima.workers.run_worker``
    (PURE profile, digest binding, UNKNOWN-on-timeout) plus that jail's real manifest.
    """

    run: stages.CaseRunner
    containment: dict[str, Any]


_HOST: list[EvaluationHost | None] = [None]


def bind_evaluation_host(host: EvaluationHost | None) -> EvaluationHost | None:
    """Bind (or clear) the host ``EvaluateCandidate`` runs cases through; returns the
    previous binding. Unbound means the command refuses — never that it runs the candidate
    in this process, which is the one thing the Reckoner exists to avoid."""
    previous = _HOST[0]
    _HOST[0] = host
    return previous


# ── the root-declared adversarial baseline (Decision 6) ──────────────────────
# Adversarial cases come ONLY from the baseline: a candidate may contribute cases but may
# never author the attacks it is judged by. These live in code, on the root side of that
# line, and are merged into every suite this lane declares. The `attack` key is the host
# contract — a bound EvaluationHost interprets it and reports whether the jail held.
BASELINE_ADVERSARIAL: tuple[dict[str, Any], ...] = (
    {
        "name": "attack.network_egress",
        "in": {},
        "adversarial": True,
        "origin": reckoner_mod.BASELINE,
        "attack": "network_egress",
    },
    {
        "name": "attack.filesystem_write",
        "in": {},
        "adversarial": True,
        "origin": reckoner_mod.BASELINE,
        "attack": "filesystem_write",
    },
)


# ── store bootstrap: the anchor, at construction time ────────────────────────
def ensure_store_anchor(weft: Weft, keyring: Keyring, root: str) -> dict[str, Any]:
    """Idempotently anchor Nona's promotion authority on a store as it is OPENED.

    Called from ``server.build_application`` and ``tenancy.build_user_context`` — both open
    Wefts that provisioning never touched. Without it every promotion through the API
    refuses ("not a trusted promoter"), because the fold honours a ``promoter`` anchor only
    when its author is the store's genesis author. Doing it at construction time is what
    makes ``root`` the genesis author of a fresh store; doing it lazily inside the promote
    handler would pick a later author, confer nothing, and fail closed confusingly.

    The Reckoner principal is minted by NAME (``Keyring.mint`` is deterministic on the
    name), so every store anchors the same evaluation authority and custody provisions its
    key once. Minting confers nothing by itself — the anchor is what says which tiers it may
    sign, and only ``anchors.SIGNABLE_TIERS`` are anchorable at all.
    """
    reckoner = keyring.mint(anchors.RECKONER_NAME, "reckoner")
    ensure_custody(keyring, (reckoner.id,))
    return anchors.ensure_trust_anchors(weft, root=root, reckoner=reckoner.id)


# ── small fold reads shared by the commands and the readers ──────────────────
def _weave(weft: Weft) -> Weave:
    return Weave.fold(weft)


def _require_str(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise CommandError(BAD_REQUEST, f"missing or invalid field {key!r}")
    return value


def _candidate_cell(weave: Weave, cid: str) -> Cell:
    cell = weave.get(cid)
    if cell is None or cell.type != candidate_mod.CANDIDATE or cell.retracted:
        raise CommandError(NOT_FOUND, f"no such candidate {cid!r}", 404)
    return cell


def _tier_of(cell: Cell) -> str:
    tier = cell.content.get("declared_effect_class")
    return tier if isinstance(tier, str) else ""


def _digest_binding(cell: Cell) -> str:
    """Re-derive the candidate's implementation digest and refuse a mismatch.

    The digest is the binding every later stage rests on ("the code that was tested is the
    code that runs"), so it is re-checked at every boundary rather than trusted once. A
    candidate whose recorded digest no longer matches its source was altered after it was
    proposed, and the only safe reading of that is: this is not the thing that was
    evaluated.
    """
    source = cell.content.get("source")
    if not isinstance(source, str) or not source:
        raise CommandError(EVIDENCE_STALE, f"candidate {cell.id!r} carries no source", 409)
    computed = candidate_mod.implementation_digest(source)
    recorded = cell.content.get("implementation_digest")
    if recorded != computed:
        raise CommandError(
            EVIDENCE_STALE,
            f"candidate {cell.id!r} no longer hashes to its recorded digest "
            f"({recorded!r} vs {computed!r}): its source was altered after it was proposed",
            409,
        )
    return computed


def _promoter_for(weave: Weave, tier: str) -> str | None:
    """A principal the FOLD will honour as a promoter for `tier`, or None.

    Read from the log (``anchors.trusted_promoters``) rather than from a name in code, so
    "who may promote what" is answerable — and revocable — as data. Deterministic when more
    than one is anchored: the lexicographically smallest principal id.
    """
    holders = [p for p, tiers in anchors.trusted_promoters(weave).items() if tier in tiers]
    return sorted(holders)[0] if holders else None


def _evaluations_of(weave: Weave, candidate_id: str) -> list[dict[str, Any]]:
    """Every evaluation result citing this candidate, newest-id-last (deterministic)."""
    out: list[dict[str, Any]] = []
    for cid, cell in sorted(weave.cells.items()):
        if cell.type != reckoner_mod.EVALUATION_RESULT or cell.retracted:
            continue
        if cell.content.get("candidate") != candidate_id:
            continue
        out.append(_evidence_view(cid, cell))
    return out


def _evidence_view(cid: str, cell: Cell) -> dict[str, Any]:
    """The evidence summary an operator decides on — integers, the verdict reason, and the
    findings. No cost, no latency, no float: exactly what the Cell recorded."""
    content = cell.content
    return {
        "evaluation": cid,
        "candidate": content.get("candidate"),
        "suite": content.get("suite"),
        "implementation_digest": content.get("implementation_digest"),
        "promote_eligible": bool(content.get("promote_eligible")),
        "verdict_reason": str(content.get("verdict_reason", "")),
        "metrics": dict(content.get("aggregate_metrics") or {}),
        "findings": [dict(f) for f in (content.get("security_findings") or [])],
        "failures": list(content.get("failures") or []),
        "environment": content.get("environment"),
        # Recorded, powerless, and structurally unable to reach the gate — surfaced so a
        # reader can see that a model's opinion was NOT what promoted anything.
        "model_judge": dict(content.get("model_judge") or {}),
    }


def _capability_for(weave: Weave, candidate_id: str) -> str | None:
    """The organ grant built from this candidate, if any (deterministic pick)."""
    for cid, cell in sorted(weave.cells.items()):
        if cell.type == executor.CAPABILITY and cell.content.get("candidate") == candidate_id:
            return cid
    return None


def _candidate_view(weave: Weave, cid: str, cell: Cell) -> dict[str, Any]:
    """The list-row view of a candidate: what it is, what may sign it, and whether anything
    could actually run it. `note` carries the honest NOT-EXECUTABLE sentence rather than an
    invented approval prompt (design Decision 2)."""
    tier = _tier_of(cell)
    cap = _capability_for(weave, cid)
    state = promotion.promotion_state(weave, cap) if cap else None
    evaluations = _evaluations_of(weave, cid)
    return {
        "candidate": cid,
        "intent": str(cell.content.get("intent", "")),
        "lifecycle": str(cell.content.get("lifecycle", "")),
        "implementation_digest": str(cell.content.get("implementation_digest", "")),
        "tier": tier,
        "signer_policy": promotion.signer_policy(tier),
        "anchored_promoter": _promoter_for(weave, tier),
        "executable": discovery.is_executable(tier, executor.GENERATED_CODE),
        "note": discovery.executability_note(tier, executor.GENERATED_CODE),
        "surface": powerbox.inbox_surface(tier),
        "evaluations": [e["evaluation"] for e in evaluations],
        "eligible_evaluation": next(
            (e["evaluation"] for e in evaluations if e["promote_eligible"]), None
        ),
        "capability": cap,
        "quarantined": None if state is None else state["quarantined"],
        "live_promotions": [] if state is None else [p["cell"] for p in state["live_promotions"]],
    }


# ── ProposeCapability ────────────────────────────────────────────────────────
def propose_capability(svc: CommandServiceLike, args: dict) -> CommandResult:
    """Propose an extension candidate — a DRAFT→QUARANTINED Cell and nothing else.

    Not gated: this writes a proposal. No capability is minted, nothing executes, and the
    source rides as DATA with ``source_is_data: True``. The operator may supply ``source``
    directly; otherwise the injected codegen seam authors it, and with no seam bound the
    command refuses ``NOT_AVAILABLE`` — never a stub.
    """
    from decima.services.api.commands import CommandResult

    intent = _require_str(args, "intent")
    effect_class = _require_str(args, "effect_class")
    if effect_class not in candidate_mod.EFFECT_CLASSES:
        raise CommandError(
            BAD_REQUEST,
            f"unknown effect class {effect_class!r}; the ladder is "
            f"{list(candidate_mod.EFFECT_CLASSES)}",
        )
    source = args.get("source")
    if source is not None and not isinstance(source, str):
        raise CommandError(BAD_REQUEST, "source must be a string (it is DATA, not a program)")
    codegen = _CODEGEN[0]
    if source is None and codegen is None:
        raise CommandError(
            NOT_AVAILABLE,
            "no codegen seam is bound and no source was supplied: this lane refuses to "
            "emit a stub organ into the catalogue (bind one with nona_service.bind_codegen, "
            "or supply the implementation as data)",
            501,
        )
    try:
        proposed = candidate_mod.propose_candidate(
            svc.weft,
            svc.app,
            intent=intent,
            declared_effect_class=effect_class,
            source=source,
            # Never the module default: this lane's refusal above is the only fail-closed
            # path, and it has already fired if there is neither source nor a bound seam.
            codegen=codegen if codegen is not None else _refuse_codegen,
            input_schema=_opt_dict(args, "input_schema"),
            output_schema=_opt_dict(args, "output_schema"),
            eval_plan=_opt_list(args, "eval_plan"),
            entrypoint=str(args.get("entrypoint") or "main"),
        )
    except candidate_mod.CodegenUnavailable as exc:
        raise CommandError(NOT_AVAILABLE, str(exc), 501) from exc
    except ValueError as exc:
        raise CommandError(BAD_REQUEST, str(exc)) from exc

    tier = str(proposed["declared_effect_class"])
    svc.bus.emit("nona.candidate_proposed", candidate=proposed["cell"], tier=tier)
    return CommandResult(
        ok=True,
        http_status=201,
        data={
            "candidate": proposed["cell"],
            "implementation_digest": proposed["implementation_digest"],
            "lifecycle": proposed["lifecycle"],
            "tier": tier,
            "signer_policy": promotion.signer_policy(tier),
            "executable": discovery.is_executable(tier, executor.GENERATED_CODE),
            "note": discovery.executability_note(tier, executor.GENERATED_CODE),
        },
    )


def _refuse_codegen(intent: str) -> str:
    """The unreachable-by-construction default (the command refuses before calling it), kept
    as a typed callable so no code path can silently acquire a generator."""
    raise candidate_mod.CodegenUnavailable(
        f"no codegen seam is bound; refusing to author source for {intent[:40]!r}"
    )


def _opt_dict(args: dict, key: str) -> dict[str, Any]:
    value = args.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CommandError(BAD_REQUEST, f"{key!r} must be an object")
    return dict(value)


def _opt_list(args: dict, key: str) -> list[str]:
    value = args.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise CommandError(BAD_REQUEST, f"{key!r} must be a list of strings")
    return [str(v) for v in value]


# ── EvaluateCandidate ────────────────────────────────────────────────────────
def evaluate_candidate(svc: CommandServiceLike, args: dict) -> CommandResult:
    """Run the Reckoner over a candidate and record ONE ``evaluation_result`` Cell.

    Not gated: an evaluation is evidence, and evidence is not an effect. What it composes,
    in order, is the N3 pipeline: the static scan (which catches reach the candidate never
    declared — a rug-pull is a HIGH finding even when every case passes), the contract
    check, then the suite's cases through the bound host, then ``with_findings`` so a HIGH
    from any stage reaches the gate, then ``gate``. The gate is pure integer arithmetic and
    a model cannot reach it.

    Refuses rather than recording something weaker:

      * no host bound ⇒ ``NOT_AVAILABLE`` (nothing is run in this process);
      * no anchored Reckoner on this store ⇒ ``NOT_AVAILABLE`` (an unanchored author's
        result could never support a promotion anyway);
      * a host that cannot deliver the mandatory containment, or a suite whose baseline the
        candidate authored ⇒ ``EVALUATION_REFUSED`` (Decisions 5 and 6).

    A recorded result that FAILS the gate is a success for this command: the refusal is the
    evidence, and ``promote_eligible: false`` is what stops the promotion later.
    """
    from decima.services.api.commands import CommandResult

    candidate_id = _require_str(args, "candidate")
    weave = _weave(svc.weft)
    cell = _candidate_cell(weave, candidate_id)
    digest = _digest_binding(cell)
    tier = _tier_of(cell)

    host = _HOST[0]
    if host is None:
        raise CommandError(
            NOT_AVAILABLE,
            "no evaluation host is bound: the Reckoner runs untrusted source in a jail, "
            "never in this process, so with no host there is nothing to run "
            "(nona_service.bind_evaluation_host)",
            501,
        )
    # The Reckoner is read from the log, not named in code. For a tier no promoter is
    # anchored for (workspace_write / financial / network) the evaluation is still authored by
    # the anchored evaluation authority — evaluating is not promoting, and the result records
    # honestly that the candidate cannot be promoted here.
    author = _promoter_for(weave, tier) or _promoter_for(weave, anchors.PURE)
    if author is None:
        raise CommandError(
            NOT_AVAILABLE,
            "this store has no root-declared Reckoner anchor, so an evaluation recorded "
            "here could not support a promotion (see nona.anchors)",
            501,
        )

    # The operator's cases are BASELINE — in a single-operator store the human at the
    # keyboard is the root authority — and the adversarial cases come from this module's
    # root-side constant, never from the candidate. Decision 6, enforced twice: here by
    # construction and again by `require_authored_suite` below.
    supplied = args.get("cases")
    if supplied is not None and not isinstance(supplied, list):
        raise CommandError(BAD_REQUEST, "'cases' must be a list of case objects")
    cases: list[dict[str, Any]] = [
        {**dict(c), "origin": reckoner_mod.BASELINE, "adversarial": False}
        for c in (supplied or [])
        if isinstance(c, dict)
    ]
    cases.extend(dict(c) for c in BASELINE_ADVERSARIAL)
    suite = candidate_mod.declare_suite(
        svc.weft,
        author,
        subject_schema=_opt_dict(cell.content, "output_schema"),
        cases=cases,
        thresholds={"deterministic_pass_pct": 100},
        metrics=["deterministic_pass", "hostile_contained"],
        # Deliberately NO declared environment_digest: pinning it to the host's own
        # manifest would make Decision 5's equality check tautological. The three MANDATORY
        # containment assertions are still enforced below, which is the part that bites.
        environment_digest="",
    )
    suite_cell = _weave(svc.weft).get(suite["cell"])
    if suite_cell is None:  # pragma: no cover - just asserted
        raise CommandError(NOT_FOUND, f"suite {suite['cell']!r} did not fold", 404)
    suite_content = dict(suite_cell.content)

    try:
        reckoner_mod.require_authored_suite(suite_content)
        reckoner_mod.require_host_containment(suite_content, host.containment)
    except reckoner_mod.EvaluationRefused as exc:
        raise CommandError(EVALUATION_REFUSED, str(exc), 409) from exc

    scan = stages.scan_source(str(cell.content.get("source", "")), tier)
    contract = stages.validate_contract(dict(cell.content), suite_content)
    metrics, case_findings = stages.run_cases(suite_content, runner=host.run)
    findings = stages.merge_findings(scan, contract, case_findings)
    # The gate reads the RE-TALLIED metrics (a HIGH from the scan must not be lost behind a
    # clean case run), and the recorded result carries the full evidence set — `gate` itself
    # returns no findings, so attaching them here is what makes the Cell auditable.
    verdict = replace(
        reckoner_mod.gate(stages.with_findings(metrics, findings)), findings=list(findings)
    )
    evaluation = reckoner_mod.record_result(
        svc.weft,
        author,
        candidate_cell=candidate_id,
        suite_cell=suite["cell"],
        implementation_digest=digest,
        verdict=verdict,
        containment=host.containment,
        # This lane declares no property stage, so the suite declares no seed and 0 is the
        # honest record. A seed here would have to be Cell data (never a clock, never
        # `os.urandom`) for the run to replay.
        seed=0,
    )
    svc.bus.emit(
        "nona.candidate_evaluated",
        candidate=candidate_id,
        evaluation=evaluation,
        eligible=verdict.eligible,
    )
    cell_out = _weave(svc.weft).get(evaluation)
    evidence = _evidence_view(evaluation, cell_out) if cell_out is not None else {}
    return CommandResult(
        ok=True,
        http_status=201,
        data={
            "candidate": candidate_id,
            "evaluation": evaluation,
            "suite": suite["cell"],
            "promote_eligible": verdict.eligible,
            "verdict_reason": verdict.reason,
            "evidence": evidence,
        },
    )


# ── PromoteCandidate (GATED) ─────────────────────────────────────────────────
def promote_candidate(svc: CommandServiceLike, args: dict) -> CommandResult:
    """Build the organ grant and promote it. Reachable ONLY after approval.

    ``execute`` defers an unapproved call to the inbox, so by the time this runs a human
    possession proof has been verified and an ``inbox_decision`` Cell recorded. This
    handler therefore does not re-check approval — it re-checks EVIDENCE, because the
    approval was given against a state of the world that may have moved:

      * the candidate still exists and still hashes to its recorded digest;
      * the cited evaluation names THAT candidate and THAT digest;
      * the evaluation is still ``promote_eligible`` and not retracted;
      * the tier has an executor and an anchored promoter for it exists.

    Then, in order: ``executor.build_capability`` mints the organ grant BORN QUARANTINED
    with its Morta floor merged (and refuses to overwrite a live grant asserted on
    different terms), ``promotion.promote`` asserts the promotion Cell and attests it, and
    the derived-quarantine fold lifts ``sandbox_only`` on the next fold. Finally the prompt
    budget is spent: an AUTOMATED-tier organ that carries an approval caveat gets ONE
    capability-scoped approval, authored by the human whose recorded decision released
    this; a floored tier gets none, so each of its calls must carry its own.

    Idempotent by construction: the capability id covers the grant terms and the promotion
    id is content-addressed over (capability, evaluation), so a re-drive lands on the same
    two Cells rather than a second organ.
    """
    from decima.services.api.commands import CommandResult

    candidate_id = _require_str(args, "candidate")
    evaluation_id = _require_str(args, "evaluation")
    weave = _weave(svc.weft)
    cell = _candidate_cell(weave, candidate_id)
    digest = _digest_binding(cell)
    tier = _tier_of(cell)

    result = weave.get(evaluation_id)
    if result is None or result.type != reckoner_mod.EVALUATION_RESULT or result.retracted:
        raise CommandError(NOT_FOUND, f"no such evaluation {evaluation_id!r}", 404)
    if result.content.get("candidate") != candidate_id:
        raise CommandError(
            EVIDENCE_STALE,
            f"evaluation {evaluation_id!r} judged a different candidate "
            f"({result.content.get('candidate')!r})",
            409,
        )
    if result.content.get("implementation_digest") != digest:
        raise CommandError(
            EVIDENCE_STALE,
            "the evaluation was recorded against a different implementation digest: the "
            "code that would be promoted is not the code that was evaluated",
            409,
        )
    if result.content.get("promote_eligible") is not True:
        raise CommandError(
            PROMOTION_REFUSED,
            f"evaluation {evaluation_id!r} is not promote-eligible "
            f"({result.content.get('verdict_reason', 'no reason recorded')})",
            409,
        )
    if promotion.signer_policy(tier) == promotion.NOT_EXECUTABLE:
        raise CommandError(
            PROMOTION_REFUSED,
            f"tier {tier!r} has NO EXECUTOR — nothing an operator can approve makes it "
            "run, so it may be authored and evaluated but never promoted to runnable",
            409,
        )
    signer = _promoter_for(weave, tier)
    if signer is None:
        raise CommandError(
            PROMOTION_REFUSED,
            f"no root-declared promoter is anchored for tier {tier!r} on this store: "
            f"promotion of a {promotion.signer_policy(tier)}-signed tier is authority "
            "that must be anchored as data, not assumed in code",
            409,
        )

    # The operator may ask for the organ to be born GATED. Only True is meaningful — a
    # caveat can be added, never dropped — and it is a bool, so the gated command's args
    # stay ids-and-flags with no free text. It is part of the capability id's preimage, so
    # a gated organ and an ungated one are different grants rather than the same Cell
    # rewritten (which is how a live grant would otherwise be widened in place).
    gate_flag = args.get("requires_approval", False)
    if not isinstance(gate_flag, bool):
        raise CommandError(BAD_REQUEST, "'requires_approval' must be a boolean")
    try:
        built = executor.build_capability(
            svc.weft,
            weave,
            signer,
            candidate=candidate_id,
            tier=tier,
            grantee=svc.human,
            granter=signer,
            caveats={"requires_approval": True} if gate_flag else {},
        )
    except executor.OrganRefused as exc:
        raise CommandError(EVIDENCE_STALE, str(exc), 409) from exc

    cap_id = str(built["capability"])
    try:
        promoted = promotion.promote(
            svc.weft,
            _weave(svc.weft),
            signer,
            capability=cap_id,
            candidate=candidate_id,
            evaluation=evaluation_id,
            tier=tier,
        )
    except promotion.PromotionRefused as exc:
        raise CommandError(PROMOTION_REFUSED, str(exc), 409) from exc

    after = _weave(svc.weft)
    cap_cell = after.get(cap_id)
    caveats = dict((cap_cell.content.get("caveats") if cap_cell else None) or {})
    plan = powerbox.prompt_plan(tier, caveats)
    approval: str | None = None
    if plan["capability_scoped_approval"]:
        approval = _capability_scoped_approval(svc, cap_id, promoted["promotion"], tier)

    svc.bus.emit(
        "nona.candidate_promoted",
        candidate=candidate_id,
        capability=cap_id,
        promotion=promoted["promotion"],
        tier=tier,
    )
    state = promotion.promotion_state(_weave(svc.weft), cap_id)
    return CommandResult(
        ok=True,
        data={
            "candidate": candidate_id,
            "capability": cap_id,
            "promotion": promoted["promotion"],
            "evaluation": evaluation_id,
            "tier": tier,
            "signer": signer,
            "quarantined": state["quarantined"],
            "implementation_digest": digest,
            "prompt_plan": plan,
            "capability_approval": approval,
            "executable": discovery.is_executable(tier, executor.GENERATED_CODE),
            "note": discovery.executability_note(tier, executor.GENERATED_CODE),
        },
    )


def _capability_scoped_approval(
    svc: CommandServiceLike, cap_id: str, promotion_cell: str, tier: str
) -> str:
    """Write the ONE durable "yes, this organ may act" (design §5.8 point 3).

    Capability-scoped (``approval_id(cap, None)``) and content-addressed, so re-approving
    is the same Cell. Authored by the HUMAN principal because a human's recorded, proof-
    carrying inbox decision is what released this handler — attributing it to the app would
    misreport who consented. It is written ONLY for a tier whose promotion the Reckoner may
    sign automatically: a floored tier must never acquire a blanket clearance, which is why
    ``prompt_plan`` gates this call rather than the caller deciding.
    """
    aid = cap_mod.approval_id(cap_id, None)
    assert_content(
        svc.weft,
        svc.human,
        aid,
        cap_mod.APPROVAL,
        {
            "capability": cap_id,
            "scope": "capability",
            "approver": svc.human,
            "promotion": promotion_cell,
            "tier": tier,
            "note": (
                "one durable approval for this organ, recorded at promotion time; a "
                "floored effect gets invocation-scoped approvals instead"
            ),
        },
    )
    assert_edge(svc.weft, svc.human, aid, "approves", cap_id)
    return aid


# ── RollbackPromotion (GATED) ────────────────────────────────────────────────
def rollback_promotion(svc: CommandServiceLike, args: dict) -> CommandResult:
    """Roll a promotion back: RETRACT the promotion Cell, and record the incident.

    ROLLBACK IS NOT REVOCATION, and the two must never be wired to one button. This
    ``RETRACT ... mode=WITHDRAW`` takes back the *promotion*: quarantine is derived from
    promotion liveness, so the organ re-quarantines on the next fold and becomes
    sandbox-only again — while the capability, its grants, its receipts and its history all
    survive. ``RevokeCapability`` is the other thing: a RETRACT of the CAPABILITY with a
    DERIVED_AUTHORITY cascade that fails closed everything descended from it. Demotion says
    "this needs re-evaluation"; revocation says "this must never run again".

    R5, stated on the log rather than in a docstring: an ``incident`` Cell records that a
    rollback CONTAINS and COMPENSATES and never claims to undo an effect that already left
    the machine. Its ``to_state`` is ``QUARANTINED`` (the monitor's auto-revoke incident
    says ``REVOKED``), so the two are distinguishable without parsing a reason string.
    """
    from decima.services.api.commands import CommandResult

    promotion_id = _require_str(args, "promotion")
    weave = _weave(svc.weft)
    cell = weave.get(promotion_id)
    if cell is None or cell.type != promotion.PROMOTION:
        raise CommandError(NOT_FOUND, f"no such promotion {promotion_id!r}", 404)
    if cell.retracted:
        raise CommandError(
            ALREADY_ROLLED_BACK,
            f"promotion {promotion_id[:12]} is already rolled back",
            409,
        )
    cap_id = str(cell.content.get("capability", ""))
    promotion.rollback(svc.weft, svc.human, promotion_id, reason=ROLLBACK_REASON)

    iid = "incident:" + content_id(
        {"nona_rollback": promotion_id, "at": svc.weft.head}, kind="cell"
    )
    assert_content(
        svc.weft,
        svc.human,
        iid,
        INCIDENT,
        {
            "capability": cap_id,
            "promotion": promotion_id,
            "reason": ROLLBACK_REASON,
            "reported_by": svc.human,
            "from_state": "PROMOTED",
            "to_state": "QUARANTINED",
            "action": "rollback",
            "note": (
                "contains and compensates; never claims to undo an effect that already "
                "left the machine (R5). The organ, its grants and its history survive — "
                "this is demotion, not revocation"
            ),
        },
    )
    assert_edge(svc.weft, svc.human, iid, "rolls_back", promotion_id)

    svc.bus.emit(
        "nona.promotion_rolled_back",
        promotion=promotion_id,
        capability=cap_id,
        incident=iid,
    )
    state = promotion.promotion_state(_weave(svc.weft), cap_id) if cap_id else {}
    return CommandResult(
        ok=True,
        data={
            "promotion": promotion_id,
            "capability": cap_id,
            "incident": iid,
            "quarantined": state.get("quarantined"),
            "live_promotions": [p["cell"] for p in state.get("live_promotions", [])],
            "revoked": False,
        },
    )


# ── readers: pure fold reads (disposable by construction) ────────────────────
def list_candidates(app: LaneReaderApp, query: dict) -> dict:
    """Reader: every live candidate with its tier, evidence ids and promotion state.

    OWNER: nona lane. A pure fold read over the requesting user's own store — no projection
    state of its own, so a delete+rebuild (or a restart) reproduces it exactly. The
    generated source is NOT in this payload: the list is a decision surface, and the bytes
    belong behind the detail read where they can be labelled untrusted.
    """
    weave = _weave(app.weft)
    items = [
        _candidate_view(weave, cid, cell)
        for cid, cell in sorted(weave.cells.items())
        if cell.type == candidate_mod.CANDIDATE and not cell.retracted
    ]
    return {
        "items": items,
        "anchored_promoters": {p: list(t) for p, t in anchors.trusted_promoters(weave).items()},
        "signable_tiers": list(anchors.SIGNABLE_TIERS),
        "effect_classes": list(candidate_mod.EFFECT_CLASSES),
    }


def get_candidate(app: LaneReaderApp, query: dict) -> dict:
    """Reader: one candidate by ``?id=…`` — its source, its evidence, its prompt plan.

    OWNER: nona lane. The ``source`` is returned as an untrusted DATA field
    (``instruction_eligible: False``, ``trust: "untrusted"``, ``source_is_data: True``) so
    the Shell renders it in an untrusted zone as a text node, and no model is handed it as
    prompt. Unknown id ⇒ ``NOT_FOUND`` / 404.
    """
    cid = query.get("id")
    weave = _weave(app.weft)
    cell = weave.get(cid) if isinstance(cid, str) and cid else None
    if cell is None or cell.type != candidate_mod.CANDIDATE or cell.retracted:
        raise CommandError(NOT_FOUND, f"no such candidate {cid!r}", http_status=404)
    view = _candidate_view(weave, str(cid), cell)
    cap = view["capability"]
    caveats: dict[str, Any] = {}
    if isinstance(cap, str):
        cap_cell = weave.get(cap)
        if cap_cell is not None:
            caveats = dict(cap_cell.content.get("caveats") or {})
    body = dict(view)
    body["source"] = str(cell.content.get("source", ""))
    body["source_is_data"] = True
    body["instruction_eligible"] = False
    body["trust"] = "untrusted"
    body["entrypoint"] = str(cell.content.get("entrypoint", "main"))
    body["input_schema"] = dict(cell.content.get("input_schema") or {})
    body["output_schema"] = dict(cell.content.get("output_schema") or {})
    body["quarantine_baseline"] = dict(cell.content.get("quarantine") or {})
    body["evidence"] = _evaluations_of(weave, str(cid))
    body["prompt_plan"] = powerbox.prompt_plan(view["tier"], caveats)
    body["caveats"] = caveats
    body["promotions"] = (
        promotion.promotion_state(weave, cap)["promotions"] if isinstance(cap, str) else []
    )
    return body


def list_promotion_decisions(app: LaneReaderApp, query: dict) -> dict:
    """Reader: the pending Nona decisions, TIERED, with their evidence resolved.

    OWNER: nona lane. Every field beyond the two ids comes from the FOLD, not from the
    submitted args: the inbox item's content is signed but it is still a request someone
    made, so the tier, the surface class, the metrics and the verdict reason are all
    re-derived from the candidate and evaluation Cells it names. A crafted submission can
    therefore not make a ``financial`` promotion render as a ``pure`` notification.

    The surface is the point (design §5.8 point 4): ``notification`` for pure/read_only,
    ``canary`` (with a rollback affordance) for workspace_write, and ``approval`` with the
    evidence inline for financial. A prompt the user always clicks yes on has negative
    security value, so low-blast-radius promotions are shown, not solicited.
    """
    weave = _weave(app.weft)
    decided = {
        c.content.get("item")
        for c in weave.of_type(DECISION)
        if not c.retracted and isinstance(c.content.get("item"), str)
    }
    items: list[dict[str, Any]] = []
    for cid, cell in sorted(weave.cells.items()):
        if cell.type != ITEM or cell.retracted:
            continue
        command = cell.content.get("deferred_command")
        if command not in ("PromoteCandidate", "RollbackPromotion"):
            continue
        raw = cell.content.get("deferred_args")
        item_args = dict(raw) if isinstance(raw, dict) else {}
        items.append(
            {
                "item": cid,
                "command": command,
                "status": "decided" if cid in decided else "pending",
                **(
                    _promote_item_view(weave, item_args)
                    if command == "PromoteCandidate"
                    else _rollback_item_view(weave, item_args)
                ),
            }
        )
    return {"items": items}


def _promote_item_view(weave: Weave, item_args: dict) -> dict[str, Any]:
    """Resolve a pending promote item's tier + evidence from the fold (never from args)."""
    candidate_id = item_args.get("candidate")
    evaluation_id = item_args.get("evaluation")
    cell = weave.get(candidate_id) if isinstance(candidate_id, str) else None
    if cell is None or cell.type != candidate_mod.CANDIDATE or cell.retracted:
        # Fail closed, and say so: a pending decision whose subject was retracted (or never
        # existed) is rendered at the EXPLICIT surface with no evidence and no tier, because
        # the honest answer is "there is nothing here to approve".
        return {
            "candidate": candidate_id,
            "resolves": False,
            "surface": powerbox.EXPLICIT,
            "note": "the candidate this decision names no longer resolves (fail closed)",
        }
    tier = _tier_of(cell)
    plan = powerbox.prompt_plan(tier, {})
    ev = weave.get(evaluation_id) if isinstance(evaluation_id, str) else None
    evidence = (
        _evidence_view(str(evaluation_id), ev)
        if ev is not None and ev.type == reckoner_mod.EVALUATION_RESULT
        else {}
    )
    return {
        "resolves": True,
        "candidate": candidate_id,
        "evaluation": evaluation_id,
        "intent": str(cell.content.get("intent", "")),
        "tier": tier,
        "surface": plan["surface"],
        "prompt_plan": plan,
        "signer_policy": promotion.signer_policy(tier),
        "anchored_promoter": _promoter_for(weave, tier),
        "executable": discovery.is_executable(tier, executor.GENERATED_CODE),
        "note": discovery.executability_note(tier, executor.GENERATED_CODE),
        # Inline for the explicit surface; the UI may collapse it for a notification, but
        # the evidence is always AVAILABLE — an approval with no evidence is a rubber stamp.
        "evidence": evidence,
        "evidence_inline": plan["evidence_inline"],
    }


def _rollback_item_view(weave: Weave, item_args: dict) -> dict[str, Any]:
    """Resolve a pending rollback item — and say plainly that it is not a revocation."""
    promotion_id = item_args.get("promotion")
    cell = weave.get(promotion_id) if isinstance(promotion_id, str) else None
    if cell is None or cell.type != promotion.PROMOTION:
        return {
            "promotion": promotion_id,
            "resolves": False,
            "surface": powerbox.EXPLICIT,
            "note": "the promotion this decision names no longer resolves (fail closed)",
        }
    tier = str(cell.content.get("tier", ""))
    return {
        "resolves": True,
        "promotion": promotion_id,
        "capability": cell.content.get("capability"),
        "tier": tier,
        "surface": powerbox.CANARY,
        "live": not cell.retracted,
        "effect": "demotion",
        "revokes": False,
        "note": (
            "rollback returns the organ to needing a sandbox; it does not revoke the "
            "capability, destroy its grants, or undo an effect that already happened"
        ),
    }


def discover_capabilities(app: LaneReaderApp, query: dict) -> dict:
    """Reader: plug-in-or-forge for ``?goal=…`` — rank the catalogue, then advise.

    OWNER: nona lane. A pure read that WRITES NOTHING and activates nothing: the answer is
    ``use`` (something already does this — hold a grant for it), ``plug_in`` (only when a
    research seam is bound; none is by default) or ``forge`` (propose a candidate, which is
    still three gated steps away from running). ``?threshold=`` is an integer bar; the
    default is the smallest honest one (see ``DEFAULT_THRESHOLD``).
    """
    goal = query.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise CommandError(BAD_REQUEST, "missing or invalid query field 'goal'", http_status=400)
    threshold = _int_query(query, "threshold", DEFAULT_THRESHOLD)
    limit = max(1, min(_int_query(query, "limit", 5), 25))
    return discovery.discover(_weave(app.weft), goal, threshold=threshold, limit=limit)


def _int_query(query: dict, key: str, default: int) -> int:
    raw = query.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(str(raw), 10)
    except ValueError as exc:
        raise CommandError(
            BAD_REQUEST, f"query field {key!r} must be an integer", http_status=400
        ) from exc


# Reader dispatch (route target → callable). The app consults this table; the lane owns the
# functions above, never the wiring.
READERS = {
    "nona_candidates": list_candidates,
    "nona_candidate": get_candidate,
    "nona_decisions": list_promotion_decisions,
    "nona_discover": discover_capabilities,
}
