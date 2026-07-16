"""Durable Cell schemas: Agent, Plan, Plan Step, Job, Lease (DEC-040/041/042).

Each is a content Cell on the Weft, addressed by a stable id, mutated only by asserting a
new CONTENT version (Law 1: no in-place update). Status transitions are new assertions;
the fold's last-writer-wins over a cell gives the current status. Nothing here mints
authority — a Cell is data; the authority a step/agent may wield is a capability grant
referenced by id and checked through the kernel at invoke time.

Logical time only: budgets, deadlines, and clocks are integers on the Weft frontier
(lamport), never wall-clock — so recorded content stays deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

from decima.kernel import hashing
from decima.kernel.model import assert_content
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Event, Weft

AGENT = "agent"
PLAN = "plan"
PLAN_STEP = "plan_step"
JOB = "job"
LEASE = "lease"


class AgentStatus:
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TERMINATED = "TERMINATED"

    TERMINAL = frozenset({COMPLETED, FAILED, TERMINATED})


class PlanStatus:
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    TERMINAL = frozenset({COMPLETED, CANCELLED})


class StepStatus:
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})


def _cid(kind: str, key: dict) -> str:
    """A stable cell id for a runtime object, domain-separated by kind."""
    return hashing.content_id({"runtime": kind, **key}, kind="cell")


# ── Plan ────────────────────────────────────────────────────────────────────
def create_plan(
    weft: Weft,
    author: str,
    *,
    objective: str,
    creator_principal: str,
    version: int = 1,
    plan_id: str | None = None,
) -> str:
    """Assert a Plan Cell and return its id."""
    pid = plan_id or _cid(
        PLAN, {"objective": objective, "creator": creator_principal, "v": version}
    )
    assert_content(
        weft,
        author,
        pid,
        PLAN,
        {
            "objective": objective,
            "creator_principal": creator_principal,
            "status": PlanStatus.DRAFT,
            "step_ids": [],
            "version": int(version),
        },
    )
    return pid


# ── Plan Step ─────────────────────────────────────────────────────────────────
def create_step(
    weft: Weft,
    author: str,
    *,
    plan_id: str,
    description: str,
    dependency_ids: list[str] | None = None,
    required_capability_selector: dict | None = None,
    assigned_agent_id: str | None = None,
    retry_policy: dict | None = None,
    idempotency_key: str | None = None,
    deadline: int | None = None,
    step_id: str | None = None,
) -> str:
    """Assert a Plan Step Cell (initially PENDING) and return its id."""
    deps = list(dependency_ids or [])
    sid = step_id or _cid(PLAN_STEP, {"plan": plan_id, "desc": description, "deps": sorted(deps)})
    assert_content(
        weft,
        author,
        sid,
        PLAN_STEP,
        {
            "plan_id": plan_id,
            "description": description,
            "dependency_ids": deps,
            "assigned_agent_id": assigned_agent_id,
            "required_capability_selector": required_capability_selector or {},
            "status": StepStatus.PENDING,
            "input_cell_ids": [],
            "output_cell_ids": [],
            "retry_policy": retry_policy or {"max_attempts": 1},
            "idempotency_key": idempotency_key or sid,
            "deadline": None if deadline is None else int(deadline),
            "attempt": 0,
        },
    )
    return sid


# ── Agent ─────────────────────────────────────────────────────────────────────
def create_agent(
    weft: Weft,
    author: str,
    *,
    objective: str,
    principal: str,
    parent_agent_id: str | None = None,
    brain_policy_id: str | None = None,
    capability_grant_ids: list[str] | None = None,
    visible_horizon: list[str] | None = None,
    token_budget: int | None = None,
    monetary_budget: int | None = None,
    deadline: int | None = None,
    agent_id: str | None = None,
) -> str:
    """Assert an Agent Cell (initially CREATED) and return its id."""
    aid = agent_id or _cid(AGENT, {"objective": objective, "principal": principal})
    assert_content(
        weft,
        author,
        aid,
        AGENT,
        {
            "principal": principal,
            "parent_agent_id": parent_agent_id,
            "objective": objective,
            "status": AgentStatus.CREATED,
            "brain_policy_id": brain_policy_id,
            "capability_grant_ids": list(capability_grant_ids or []),
            "envelope": list(capability_grant_ids or []),  # kernel authorize reads `envelope`
            "visible_horizon": list(visible_horizon or []),
            "token_budget": None if token_budget is None else int(token_budget),
            "monetary_budget": None if monetary_budget is None else int(monetary_budget),
            "deadline": None if deadline is None else int(deadline),
        },
    )
    return aid


# ── transitions ───────────────────────────────────────────────────────────────
def set_status(weft: Weft, author: str, cell: Cell | None, status: str) -> Event:
    """Assert a new CONTENT version of a runtime Cell with an updated status.

    `cell` is a folded Cell (has .content and .type); the new version copies its content
    and overwrites `status`, so the fold's LWW yields the new status. Fails closed if the
    Cell is unknown."""
    if cell is None:
        raise ValueError("cannot transition a nonexistent cell")
    content = dict(cell.content)
    content["status"] = status
    return assert_content(weft, author, cell.id, cell.type, content)


@dataclass(frozen=True)
class StepView:
    """A read-model projection of a Plan Step (from the fold)."""

    id: str
    plan_id: str
    description: str
    dependency_ids: tuple[str, ...]
    status: str
    assigned_agent_id: str | None

    @classmethod
    def of(cls, cell: Cell) -> StepView:
        c = cell.content
        return cls(
            id=cell.id,
            plan_id=c["plan_id"],
            description=c["description"],
            dependency_ids=tuple(c.get("dependency_ids", [])),
            status=c["status"],
            assigned_agent_id=c.get("assigned_agent_id"),
        )


def steps_of_plan(weave: Weave, plan_id: str) -> list[StepView]:
    """All Plan Step views for a plan, from the current fold."""
    return [StepView.of(c) for c in weave.of_type(PLAN_STEP) if c.content.get("plan_id") == plan_id]


RECEIPT = "receipt"


def create_lease(
    weft: Weft,
    author: str,
    *,
    step_id: str,
    worker: str,
    capability_ids: list[str] | None = None,
    issued_frontier: int,
    expiry: int,
    attempt: int,
    idempotency_key: str,
) -> str:
    """Mint a durable execution lease (DEC-042): the bounded authority + window under
    which one attempt of a step runs. A stale lease (past `expiry` at the frontier) must
    not remain usable — the dispatcher checks it before honoring the lease."""
    lid = _cid(
        LEASE,
        {
            "step": step_id,
            "worker": worker,
            "attempt": int(attempt),
            "frontier": int(issued_frontier),
        },
    )
    assert_content(
        weft,
        author,
        lid,
        LEASE,
        {
            "step_id": step_id,
            "worker": worker,
            "capability_ids": list(capability_ids or []),
            "issued_frontier": int(issued_frontier),
            "expiry": int(expiry),
            "attempt": int(attempt),
            "idempotency_key": idempotency_key,
        },
    )
    return lid


def record_receipt(
    weft: Weft,
    author: str,
    *,
    step_id: str,
    lease_id: str,
    idempotency_key: str,
    status: str,
    output_cell_ids: list[str] | None = None,
    diagnostics: dict | None = None,
) -> str:
    """Append an effect receipt (DEC-019/048): the durable, terminal-or-UNKNOWN outcome of
    a dispatched step attempt, keyed by its idempotency key so a replay can find the prior
    result instead of re-executing."""
    rid = _cid(RECEIPT, {"step": step_id, "lease": lease_id, "idem": idempotency_key})
    assert_content(
        weft,
        author,
        rid,
        RECEIPT,
        {
            "step_id": step_id,
            "lease_id": lease_id,
            "idempotency_key": idempotency_key,
            "status": status,
            "output_cell_ids": list(output_cell_ids or []),
            "diagnostics": diagnostics or {},
        },
    )
    return rid


def receipt_for_idempotency_key(weave: Weave, idempotency_key: str) -> Cell | None:
    """The terminal receipt (if any) already recorded for an idempotency key — the seam
    that makes re-dispatch a no-op (replay executes no effect, DEC-011 property 10)."""
    for c in weave.of_type(RECEIPT):
        if c.content.get("idempotency_key") == idempotency_key:
            return c
    return None
