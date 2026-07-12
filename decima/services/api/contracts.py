"""Shared application contracts for the 0.3 Path-A product lanes (Q&A / planning / workspace).

These are the STABLE typed shapes three parallel feature lanes build against:

  * grounded Q&A       → ``decima/services/api/qa_service.py``       (qa lane)
  * model-planned work → ``decima/services/api/plan_service.py``      (planning lane)
  * coding workspace   → ``decima/services/api/workspace_service.py`` (workspace lane)

A lane implements its own service module + its own frontend screen + its own capability/
runtime glue and NEVER edits a shared file: the route table, the command registry, the
event families, and these contracts are frozen here. Every shape is plain data with an
``as_dict()`` (house style) so it serializes to JSON deterministically; every number
that can become recorded Weft content is an INT (invariant 6 — no floats on the log).

Contracts carry ZERO authority. A ``PlanProposal`` is a model's PROPOSAL (invariant 4):
deterministic code in the owning service validates and a human accepts before anything
durable happens. A ``WorkspacePolicy`` cannot even express network access. Existing
domain types are REUSED, not duplicated: ``Citation`` wraps ``decima.capabilities.qa``'s
citation, plan/agent statuses come from ``decima.runtime.cells``, and a proposal request
converts to a ``decima.models.routing.TaskSpec`` for the recorded routing decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from decima.capabilities import qa as _qa
from decima.models.routing import TaskSpec
from decima.runtime.cells import AgentStatus, PlanStatus, StepStatus
from decima.services.api.events import StreamEvent

__all__ = [
    "AgentRunSummary",
    "AgentStatus",
    "ApplicationError",
    "Citation",
    "CitationLocation",
    "CommandError",
    "ContractError",
    "KnowledgeScope",
    "NOT_IMPLEMENTED",
    "PlanAcceptance",
    "PlanProposal",
    "PlanProposalRequest",
    "PlanStatus",
    "ProposalStatus",
    "ProposedPlanStep",
    "QuestionRequest",
    "QuestionRun",
    "QuestionStatus",
    "StepStatus",
    "StreamEvent",
    "WorkspaceArtifact",
    "WorkspacePolicy",
    "WorkspaceRequest",
    "WorkspaceRun",
    "WorkspaceRunStatus",
]

# ── stable error envelope ─────────────────────────────────────────────────────
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class CommandError(Exception):
    """A fail-closed command refusal with a stable ``reason_code`` and HTTP status.

    Canonical definition (re-exported by ``decima.services.api.commands`` for
    compatibility). Service-module stubs raise
    ``CommandError(NOT_IMPLEMENTED, ..., http_status=501)`` until their lane lands."""

    def __init__(self, reason_code: str, message: str = "", http_status: int = 400) -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.http_status = http_status


class ContractError(ValueError):
    """A request body did not satisfy a contract shape (deterministic validation)."""


@dataclass(frozen=True)
class ApplicationError:
    """The stable JSON error envelope every application surface returns on refusal.

    Matches the failing ``CommandResult`` shape (``ok``/``reason_code``/``error``) so a
    frontend handles command failures and reader failures identically."""

    reason_code: str
    message: str = ""
    http_status: int = 400

    def as_dict(self) -> dict:
        return {
            "ok": False,
            "reason_code": self.reason_code,
            "error": self.message or self.reason_code,
        }


# ── validation helpers (deterministic, fail closed) ──────────────────────────
def _require_str(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"missing or invalid field {key!r}")
    return value


def _opt_str(args: dict, key: str, default: str = "") -> str:
    value = args.get(key, default)
    if not isinstance(value, str):
        raise ContractError(f"field {key!r} must be a string")
    return value


def _opt_int(args: dict, key: str, default: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"field {key!r} must be an int (never a float — invariant 6)")
    return value


def _check_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{name} must be an int (never a float — invariant 6)")


# ── knowledge scope (the explicit horizon) ────────────────────────────────────
@dataclass(frozen=True)
class KnowledgeScope:
    """The EXPLICIT selection of projects a question/proposal may draw on.

    ``projects=None`` means unrestricted (the operator's own full horizon); an empty
    tuple means the scope sees NOTHING (fail closed) — mirroring ``qa._horizon_set``."""

    projects: tuple[str, ...] | None = None

    def horizon(self) -> frozenset[str] | None:
        """The value ``decima.capabilities.qa.retrieve(horizon=...)`` expects."""
        return None if self.projects is None else frozenset(self.projects)

    def as_dict(self) -> dict:
        return {
            "projects": None if self.projects is None else list(self.projects),
        }

    @classmethod
    def from_value(cls, value: object) -> KnowledgeScope:
        """Parse a JSON-shaped scope: None, a project id, or a list of project ids."""
        if value is None:
            return cls(projects=None)
        if isinstance(value, dict):
            value = value.get("projects")
            if value is None:
                return cls(projects=None)
        if isinstance(value, str):
            return cls(projects=(value,))
        if isinstance(value, list | tuple):
            out = []
            for p in value:
                if not isinstance(p, str):
                    raise ContractError("scope projects must be strings")
                out.append(p)
            return cls(projects=tuple(out))
        raise ContractError("scope must be None, a project id, or a list of project ids")


# ── grounded Q&A ──────────────────────────────────────────────────────────────
class QuestionStatus:
    """Lifecycle of a recorded question run."""

    PENDING = "PENDING"
    ANSWERED = "ANSWERED"
    FAILED = "FAILED"

    TERMINAL = frozenset({ANSWERED, FAILED})


@dataclass(frozen=True)
class CitationLocation:
    """WHERE a citation resolves: the imported source document + offset within it."""

    source_document: str
    source: str = ""
    offset: int = 0

    def __post_init__(self) -> None:
        _check_int("offset", self.offset)

    def as_dict(self) -> dict:
        return {
            "source_document": self.source_document,
            "source": self.source,
            "offset": int(self.offset),
        }


@dataclass(frozen=True)
class Citation:
    """A wrapper over ``decima.capabilities.qa.Citation`` — the pointer that RESOLVES
    to an imported source segment Cell on the Weft. The snippet is untrusted DATA."""

    segment_id: str
    location: CitationLocation
    snippet: str = ""

    @classmethod
    def from_qa(cls, cit: _qa.Citation) -> Citation:
        return cls(
            segment_id=cit.segment_id,
            location=CitationLocation(
                source_document=cit.source_document,
                source=cit.source,
                offset=int(cit.offset),
            ),
            snippet=cit.snippet,
        )

    def as_dict(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "location": self.location.as_dict(),
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class QuestionRequest:
    """What the operator asks: a question over an explicit knowledge scope."""

    question: str
    scope: KnowledgeScope = KnowledgeScope()
    limit: int = 5
    max_output_tokens: int = 512

    def __post_init__(self) -> None:
        _check_int("limit", self.limit)
        _check_int("max_output_tokens", self.max_output_tokens)

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "scope": self.scope.as_dict(),
            "limit": int(self.limit),
            "max_output_tokens": int(self.max_output_tokens),
        }

    @classmethod
    def from_args(cls, args: dict) -> QuestionRequest:
        return cls(
            question=_require_str(args, "question"),
            scope=KnowledgeScope.from_value(args.get("scope")),
            limit=_opt_int(args, "limit", 5),
            max_output_tokens=_opt_int(args, "max_output_tokens", 512),
        )


@dataclass(frozen=True)
class QuestionRun:
    """One recorded question run: the question, its answer proposal, and the citations
    that ground it. The answer text is a model PROPOSAL — inert DATA (invariant 4)."""

    id: str
    question: str
    status: str = QuestionStatus.PENDING
    answer_text: str = ""
    model: str = ""
    grounded: bool = False
    citations: tuple[Citation, ...] = ()
    scope: KnowledgeScope = KnowledgeScope()
    asked_frontier: int = 0   # lamport int, never wall-clock

    def __post_init__(self) -> None:
        _check_int("asked_frontier", self.asked_frontier)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "status": self.status,
            "answer_text": self.answer_text,
            "model": self.model,
            "grounded": self.grounded,
            "citations": [c.as_dict() for c in self.citations],
            "scope": self.scope.as_dict(),
            "asked_frontier": int(self.asked_frontier),
        }


# ── coding workspace ──────────────────────────────────────────────────────────
class WorkspaceRunStatus:
    """Lifecycle of a workspace run (mirrors the worker receipt vocabulary)."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})


@dataclass(frozen=True)
class WorkspacePolicy:
    """The bounds a workspace run executes under. Execution happens ONLY in the
    EXISTING isolated worker system (``decima.workers``): the policy cannot even
    express network access, host filesystem access, or credentials — ``network`` is
    structurally False and validated so (no push/deploy from a workspace, ever)."""

    profile: str = "workspace"       # decima.workers profile name
    timeout_seconds: int = 10
    network: bool = False            # frozen: a workspace run has NO outward path
    max_files: int = 256

    def __post_init__(self) -> None:
        _check_int("timeout_seconds", self.timeout_seconds)
        _check_int("max_files", self.max_files)
        if self.network is not False:
            raise ContractError("workspace policy cannot grant network access")

    def as_dict(self) -> dict:
        return {
            "profile": self.profile,
            "timeout_seconds": int(self.timeout_seconds),
            "network": False,
            "max_files": int(self.max_files),
        }

    @classmethod
    def from_args(cls, args: dict) -> WorkspacePolicy:
        if args.get("network", False) is not False:
            raise ContractError("workspace policy cannot grant network access")
        return cls(
            profile=_opt_str(args, "profile", "workspace"),
            timeout_seconds=_opt_int(args, "timeout_seconds", 10),
            network=False,
            max_files=_opt_int(args, "max_files", 256),
        )


@dataclass(frozen=True)
class WorkspaceRequest:
    """What the operator asks for: a named, bounded, isolated coding workspace."""

    name: str
    objective: str = ""
    policy: WorkspacePolicy = WorkspacePolicy()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "objective": self.objective,
            "policy": self.policy.as_dict(),
        }

    @classmethod
    def from_args(cls, args: dict) -> WorkspaceRequest:
        policy = args.get("policy")
        if policy is not None and not isinstance(policy, dict):
            raise ContractError("policy must be a JSON object")
        return cls(
            name=_require_str(args, "name"),
            objective=_opt_str(args, "objective", ""),
            policy=WorkspacePolicy.from_args(policy or {}),
        )


@dataclass(frozen=True)
class WorkspaceRun:
    """One recorded run of checks/tests over a workspace's working tree. Carries the
    ids of its durable artifacts + receipt — refs into the Weft, never copies."""

    id: str
    workspace_id: str
    name: str = ""
    status: str = WorkspaceRunStatus.CREATED
    artifact_ids: tuple[str, ...] = ()
    receipt_id: str = ""
    created_frontier: int = 0

    def __post_init__(self) -> None:
        _check_int("created_frontier", self.created_frontier)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "status": self.status,
            "artifact_ids": list(self.artifact_ids),
            "receipt_id": self.receipt_id,
            "created_frontier": int(self.created_frontier),
        }


@dataclass(frozen=True)
class WorkspaceArtifact:
    """A durable workspace product on the Weft: a reviewable diff or a test outcome
    (cell types ``diff_artifact`` / ``test_artifact`` from ``capabilities.workspace``).
    Its content (diff text, test output) is UNTRUSTED — rendered as text only."""

    id: str
    workspace_id: str
    kind: str                        # "diff_artifact" | "test_artifact"
    digest: str = ""
    status: str = ""
    applied: bool = False

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            "digest": self.digest,
            "status": self.status,
            "applied": self.applied,
        }


# ── model-planned agents ──────────────────────────────────────────────────────
class ProposalStatus:
    """Lifecycle of a plan proposal: a model PROPOSES, a human ACCEPTS or REJECTS."""

    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"

    TERMINAL = frozenset({ACCEPTED, REJECTED})


@dataclass(frozen=True)
class PlanProposalRequest:
    """What the operator asks a model to plan. ``sensitivity`` defaults to private so
    the recorded routing decision can only ever select a LOCAL model (enforced by
    ``decima.models.routing`` — sensitive ⇒ local-only)."""

    objective: str
    scope: KnowledgeScope = KnowledgeScope()
    max_steps: int = 16
    sensitivity: str = "private"
    token_budget: int | None = None
    monetary_budget_microcents: int | None = None

    def __post_init__(self) -> None:
        _check_int("max_steps", self.max_steps)
        if self.token_budget is not None:
            _check_int("token_budget", self.token_budget)
        if self.monetary_budget_microcents is not None:
            _check_int("monetary_budget_microcents", self.monetary_budget_microcents)

    def task_spec(self) -> TaskSpec:
        """The vendor-neutral routing spec for this proposal (reuses routing.TaskSpec;
        the decision it produces is DATA and is recorded, invariant 1)."""
        return TaskSpec(
            task_class="plan",
            sensitivity=self.sensitivity,
            cost_budget_microcents=self.monetary_budget_microcents,
            structured_output=True,
        )

    def as_dict(self) -> dict:
        return {
            "objective": self.objective,
            "scope": self.scope.as_dict(),
            "max_steps": int(self.max_steps),
            "sensitivity": self.sensitivity,
            "token_budget": None if self.token_budget is None else int(self.token_budget),
            "monetary_budget_microcents": (
                None if self.monetary_budget_microcents is None
                else int(self.monetary_budget_microcents)
            ),
        }

    @classmethod
    def from_args(cls, args: dict) -> PlanProposalRequest:
        token_budget = args.get("token_budget")
        if token_budget is not None:
            _check_int("token_budget", token_budget)
        monetary = args.get("monetary_budget_microcents")
        if monetary is not None:
            _check_int("monetary_budget_microcents", monetary)
        return cls(
            objective=_require_str(args, "objective"),
            scope=KnowledgeScope.from_value(args.get("scope")),
            max_steps=_opt_int(args, "max_steps", 16),
            sensitivity=_opt_str(args, "sensitivity", "private"),
            token_budget=token_budget,
            monetary_budget_microcents=monetary,
        )


@dataclass(frozen=True)
class ProposedPlanStep:
    """ONE step of a model-proposed plan — a PROPOSAL, not a runtime step. It has no
    id, no status, and no authority: deterministic code validates it and only human
    acceptance turns it into a durable ``plan_step`` Cell (``runtime.cells``).
    ``depends_on`` are indexes into the proposal's own step list."""

    description: str
    depends_on: tuple[int, ...] = ()
    required_capability_selector: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for i in self.depends_on:
            _check_int("depends_on[]", i)

    def as_dict(self) -> dict:
        return {
            "description": self.description,
            "depends_on": [int(i) for i in self.depends_on],
            "required_capability_selector": dict(self.required_capability_selector),
        }

    @classmethod
    def from_dict(cls, value: dict) -> ProposedPlanStep:
        if not isinstance(value, dict):
            raise ContractError("a proposed step must be a JSON object")
        selector = value.get("required_capability_selector") or {}
        if not isinstance(selector, dict):
            raise ContractError("required_capability_selector must be a JSON object")
        depends = value.get("depends_on") or []
        if not isinstance(depends, list | tuple):
            raise ContractError("depends_on must be a list of step indexes")
        indexes: list[int] = []
        for i in depends:
            _check_int("depends_on[]", i)
            indexes.append(int(i))
        return cls(
            description=_require_str(value, "description"),
            depends_on=tuple(indexes),
            required_capability_selector=dict(selector),
        )


@dataclass(frozen=True)
class PlanProposal:
    """A model-proposed plan: inert DATA awaiting a human decision (invariant 4).
    ``routing_cell`` refs the recorded ``model_routing`` decision; ``plan_id`` is
    filled only after acceptance mints the durable Plan."""

    id: str
    objective: str
    steps: tuple[ProposedPlanStep, ...] = ()
    model: str = ""
    status: str = ProposalStatus.PROPOSED
    plan_id: str = ""
    routing_cell: str = ""
    proposed_frontier: int = 0

    def __post_init__(self) -> None:
        _check_int("proposed_frontier", self.proposed_frontier)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "steps": [s.as_dict() for s in self.steps],
            "model": self.model,
            "status": self.status,
            "plan_id": self.plan_id,
            "routing_cell": self.routing_cell,
            "proposed_frontier": int(self.proposed_frontier),
        }


@dataclass(frozen=True)
class PlanAcceptance:
    """The human decision that turned a proposal into a durable Plan + Steps: refs
    the proposal, the minted plan, and the minted step Cells (ids only)."""

    proposal_id: str
    plan_id: str
    step_ids: tuple[str, ...] = ()
    accepted_frontier: int = 0

    def __post_init__(self) -> None:
        _check_int("accepted_frontier", self.accepted_frontier)

    def as_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "plan_id": self.plan_id,
            "step_ids": list(self.step_ids),
            "accepted_frontier": int(self.accepted_frontier),
        }


@dataclass(frozen=True)
class AgentRunSummary:
    """A read-model summary of one agent's run — counts and refs, never a second
    store of the agent Cell's content. Budgets are ints (tokens / micro-cents)."""

    agent_id: str
    objective: str = ""
    status: str = AgentStatus.CREATED
    plan_id: str = ""
    parent_agent_id: str = ""
    token_budget: int | None = None
    monetary_budget: int | None = None
    steps_total: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0

    def __post_init__(self) -> None:
        _check_int("steps_total", self.steps_total)
        _check_int("steps_succeeded", self.steps_succeeded)
        _check_int("steps_failed", self.steps_failed)
        if self.token_budget is not None:
            _check_int("token_budget", self.token_budget)
        if self.monetary_budget is not None:
            _check_int("monetary_budget", self.monetary_budget)

    def as_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "objective": self.objective,
            "status": self.status,
            "plan_id": self.plan_id,
            "parent_agent_id": self.parent_agent_id,
            "token_budget": None if self.token_budget is None else int(self.token_budget),
            "monetary_budget": (
                None if self.monetary_budget is None else int(self.monetary_budget)
            ),
            "steps_total": int(self.steps_total),
            "steps_succeeded": int(self.steps_succeeded),
            "steps_failed": int(self.steps_failed),
        }
