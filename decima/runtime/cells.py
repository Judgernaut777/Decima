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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

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
    COMPENSATED = "COMPENSATED"
    UNKNOWN = "UNKNOWN"

    # COMPENSATED is TERMINAL: a compensated effect is done — its consequences were
    # reversed by a compensating invocation (WEFT §8.2) — so the scheduler must stop
    # waiting on it. UNKNOWN is deliberately NOT terminal: it is resting, and only an
    # OBSERVATION resolves it (§8.3), never an assumption.
    TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED, COMPENSATED})


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
    sandbox: bool = False,
) -> str:
    """Assert an Agent Cell (initially CREATED) and return its id.

    ``sandbox`` (Nona N1) marks an agent as the QUARANTINE RUNTIME: the actor a
    not-yet-promoted candidate is allowed to run as. It is recorded on the Cell — a
    durable, foldable fact — rather than inferred from the envelope, so "was this run
    sandboxed?" is answerable from the log forever, including for a run whose grants have
    since been retracted. It confers nothing by itself: a sandbox agent is bounded by the
    same envelope + budget every other agent is, and it is Morta's caveats and the worker
    jail that keep a quarantined organ harmless."""
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
            "sandbox": bool(sandbox),
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


class ReceiptStatus:
    """The full EffectReceipt status set (WEFT §8.2).

    ``ACCEPTED``/``RUNNING`` are in-flight progress an executor may record before any
    outcome exists. ``SUCCEEDED``/``FAILED``/``CANCELLED``/``COMPENSATED`` are FINAL for
    that attempt. ``UNKNOWN`` is *resting, not terminal*: the machine has NO edge that
    invents a terminal outcome from it — only an observation resolves it (§8.3/§8.6)."""

    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    COMPENSATED = "COMPENSATED"
    CANCELLED = "CANCELLED"

    ALL = frozenset({ACCEPTED, RUNNING, SUCCEEDED, FAILED, UNKNOWN, COMPENSATED, CANCELLED})
    FINAL = frozenset({SUCCEEDED, FAILED, CANCELLED, COMPENSATED})
    IN_FLIGHT = frozenset({ACCEPTED, RUNNING})
    # A deterministic lifecycle rank, used ONLY to break ties when two receipts of one
    # attempt share an observation index — never to decide an outcome.
    RANK = {
        ACCEPTED: 0,
        RUNNING: 1,
        UNKNOWN: 2,
        FAILED: 3,
        CANCELLED: 3,
        SUCCEEDED: 4,
        COMPENSATED: 5,
    }


# The Plan Step status a receipt status converges its step to. ACCEPTED/RUNNING keep the
# step RUNNING (in flight); each final receipt status maps to its terminal step status.
STEP_STATUS_OF_RECEIPT = {
    ReceiptStatus.ACCEPTED: StepStatus.RUNNING,
    ReceiptStatus.RUNNING: StepStatus.RUNNING,
    ReceiptStatus.SUCCEEDED: StepStatus.SUCCEEDED,
    ReceiptStatus.FAILED: StepStatus.FAILED,
    ReceiptStatus.UNKNOWN: StepStatus.UNKNOWN,
    ReceiptStatus.CANCELLED: StepStatus.CANCELLED,
    ReceiptStatus.COMPENSATED: StepStatus.COMPENSATED,
}

# ── integer cost accounting (WEFT §8.1 CostItem) ──────────────────────────────
# Canonical resource names. Amounts are ALWAYS integers in the resource's smallest unit:
# floats are forbidden in signed/hashed content (WEFT §1 / DETERMINISM), and the canonical
# CBOR encoder would happily encode one, so the refusal has to live at this seam.
COST_TOKENS = "tokens"
COST_MONETARY = "monetary"  # smallest currency unit (microcents) — an int, like every budget
COST_WALL_MS = "wall_ms"

# Runner-result keys the supervisor lifts into first-class receipt fields (§8.1) instead of
# dumping into free-form diagnostics.
RESERVED_RESULT_KEYS = frozenset(
    {"status", "cost", "token_cost", "monetary_cost", "output_cell_ids", "provider_ref", "error"}
)


@dataclass(frozen=True)
class CostItem:
    """One integer cost line of a receipt (WEFT §8.1 ``CostItem``). ``unit`` defaults to the
    resource name; ``provider_ref`` ties the line to an external charge id."""

    resource: str
    amount: int
    unit: str = ""
    provider_ref: str | None = None


def _as_int(value: Any, default: int = 0) -> int:
    """A real int, or ``default``. A bool is NOT an int here (a bool in an int field is a
    bug, not 0/1), and a float never silently truncates into signed content."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return int(value)


def _int_amount(resource: str, value: Any) -> int:
    """An integer cost amount, or a hard failure. Floats/bools are REFUSED: costs ride in
    signed, hashed content, where a float is non-deterministic across implementations
    (WEFT §1). Fail loud here rather than encode one."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"cost amount for {resource!r} must be an int (no floats in signed content), "
            f"got {type(value).__name__}"
        )
    return int(value)


def _cost_item(raw: Any) -> CostItem:
    """Coerce one cost line (CostItem or mapping) into a validated CostItem."""
    if isinstance(raw, CostItem):
        resource = str(raw.resource)
        return CostItem(
            resource=resource,
            amount=_int_amount(resource, raw.amount),
            unit=str(raw.unit or resource),
            provider_ref=raw.provider_ref,
        )
    if not isinstance(raw, Mapping):
        raise TypeError(f"cost item must be a mapping or CostItem, got {type(raw).__name__}")
    resource = str(raw["resource"])
    ref = raw.get("provider_ref")
    return CostItem(
        resource=resource,
        amount=_int_amount(resource, raw.get("amount")),
        unit=str(raw.get("unit") or resource),
        provider_ref=None if ref is None else str(ref),
    )


def normalize_cost(cost: Any) -> list[dict[str, Any]]:
    """Canonicalize a cost spec into a receipt's deterministic ``cost`` list.

    Accepts ``None``, a ``{resource: int}`` mapping, one :class:`CostItem` (or a
    CostItem-shaped mapping), or an iterable of those. Lines sharing
    ``(resource, unit, provider_ref)`` are SUMMED and the result is sorted, so two callers
    reporting the same spend produce byte-identical content — and therefore the same receipt
    id. Ints only: a float raises instead of landing in signed content."""
    if cost is None:
        return []
    items: list[CostItem]
    if isinstance(cost, CostItem):
        items = [_cost_item(cost)]
    elif isinstance(cost, Mapping):
        if "resource" in cost:  # a single CostItem-shaped mapping
            items = [_cost_item(cost)]
        else:
            items = [
                CostItem(resource=str(k), amount=_int_amount(str(k), v)) for k, v in cost.items()
            ]
    elif isinstance(cost, Iterable) and not isinstance(cost, (str, bytes)):
        items = [_cost_item(line) for line in cost]
    else:
        raise TypeError(f"unsupported cost spec {type(cost).__name__}")
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        resource = str(item.resource)
        unit = str(item.unit or resource)
        key = (resource, unit, item.provider_ref or "")
        entry = merged.setdefault(
            key,
            {"resource": resource, "amount": 0, "unit": unit, "provider_ref": item.provider_ref},
        )
        entry["amount"] = _as_int(entry["amount"]) + _int_amount(resource, item.amount)
    return [merged[key] for key in sorted(merged)]


def cost_summary(lines: Any) -> dict[str, int]:
    """``{resource: int}`` over a normalized cost list — the shape budgets/reports read."""
    out: dict[str, int] = {}
    for line in lines or []:
        if not isinstance(line, Mapping):
            continue
        resource = str(line.get("resource", ""))
        amount = line.get("amount", 0)
        if not resource or isinstance(amount, bool) or not isinstance(amount, int):
            continue
        out[resource] = out.get(resource, 0) + int(amount)
    return out


def receipt_cost(receipt: Cell) -> dict[str, int]:
    """A receipt's integer cost as ``{resource: int}``.

    Falls back, PER RESOURCE, to the pre-T2.1 ``diagnostics.token_cost`` /
    ``monetary_cost`` convention, so receipts already on an existing log keep reporting
    their spend after this upgrade — an event log is append-only; old receipts are never
    rewritten."""
    out = cost_summary(receipt.content.get("cost"))
    diagnostics = receipt.content.get("diagnostics") or {}
    if isinstance(diagnostics, Mapping):
        for legacy, resource in (("token_cost", COST_TOKENS), ("monetary_cost", COST_MONETARY)):
            if resource in out:
                continue
            value = diagnostics.get(legacy)
            if isinstance(value, int) and not isinstance(value, bool):
                out[resource] = int(value)
    return out


def cost_from_result(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The integer cost lines a runner reported — the structured ``cost`` key and/or the
    legacy ``token_cost``/``monetary_cost`` scalars, merged into one canonical list."""
    items: list[CostItem] = [_cost_item(line) for line in normalize_cost(result.get("cost"))]
    for key, resource in (("token_cost", COST_TOKENS), ("monetary_cost", COST_MONETARY)):
        value = result.get(key)
        if value is not None:
            items.append(CostItem(resource=resource, amount=_int_amount(resource, value)))
    return normalize_cost(items)


def normalize_error(error: Any) -> dict[str, Any] | None:
    """Canonicalize a ``StructuredError`` (WEFT §8.1): ``code`` is stable and
    machine-routable, ``retryable`` is the executor's CLASSIFICATION (never a license to
    auto-retry — §8.5's effect_class decides that), and ``at`` is LOGICAL time (an int on
    the frontier), never a wall clock."""
    if error is None:
        return None
    if isinstance(error, str):
        error = {"code": error}
    if not isinstance(error, Mapping):
        raise TypeError(f"error must be a mapping or a code string, got {type(error).__name__}")
    at = error.get("at")
    if at is not None and (isinstance(at, bool) or not isinstance(at, int)):
        raise TypeError("error.at must be an int (logical time), never a float or wall-clock")
    message = error.get("message")
    provider_code = error.get("provider_code")
    return {
        "code": str(error.get("code", "unspecified")),
        "retryable": bool(error.get("retryable", False)),
        "provider_code": None if provider_code is None else str(provider_code),
        "message": None if message is None else str(message),
        "at": None if at is None else int(at),
    }


def receipt_order(receipt: Cell) -> tuple[int, int, int, str]:
    """The deterministic order of a step's receipt append-log: attempt, then the executor's
    observation index within that attempt, then §8.2 lifecycle rank, then cell id. Never
    fold order — two processes folding the same log must read the SAME history."""
    content = receipt.content
    return (
        _as_int(content.get("attempt")),
        _as_int(content.get("observation")),
        ReceiptStatus.RANK.get(str(content.get("status")), -1),
        receipt.id,
    )


def receipt_attempt(receipt: Cell) -> int:
    """The physical attempt a receipt reports on (§8.1 field 3)."""
    return _as_int(receipt.content.get("attempt"))


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


def _attempt_of_lease(weft: Weft, lease_id: str) -> int:
    """The attempt a lease claims (§8.4). 0 when the lease is unknown — a caller recording a
    receipt outside the lease path (e.g. a workspace test artifact) has no attempt series.
    Only reached when the caller passed no explicit ``attempt``."""
    lease = Weave.fold(weft).get(lease_id)
    if lease is None or lease.type != LEASE:
        return 0
    return _as_int(lease.content.get("attempt"))


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
    attempt: int | None = None,
    observation: int = 0,
    executor: str | None = None,
    effect_class: str | None = None,
    provider_ref: str | None = None,
    cost: Any = None,
    error: Any = None,
    compensates: str | None = None,
    retry_authorized: bool = False,
) -> str:
    """Append an effect receipt (DEC-019/048, WEFT §8.1): the durable, IMMUTABLE record of
    ONE observation about ONE attempt of a dispatched step, keyed by its idempotency key so
    a replay finds the prior result instead of re-executing.

    A receipt is never edited. Progress is a NEW receipt for the same logical operation at a
    later ``(attempt, observation)`` (§8.1), so the cell id includes
    ``attempt``/``observation``/``status``:

      * re-delivering the SAME observation lands on the SAME cell — duplicates fold to one
        current state, so a flaky worker cannot inflate history; and
      * a genuinely new observation is a NEW cell — the multi-attempt history is explicit
        and folded, never overwritten.

    ``cost`` is integer-only (``{resource: int}`` or :class:`CostItem`s) and a float is
    refused outright (WEFT §1); ``error`` is a ``StructuredError`` (§8.1). ``attempt``
    defaults to the attempt named by the lease this receipt was produced under (§8.4), so a
    caller holding a lease need not repeat it. ``retry_authorized`` marks an UNKNOWN receipt
    that a reconciler judged safe to re-dispatch (§8.5) — the only way a resting UNKNOWN
    becomes retryable without an explicit operator override."""
    if status not in ReceiptStatus.ALL:
        raise ValueError(f"unknown receipt status {status!r} (WEFT §8.2)")
    resolved = _as_int(attempt) if attempt is not None else _attempt_of_lease(weft, lease_id)
    obs = _as_int(observation)
    rid = _cid(
        RECEIPT,
        {
            "step": step_id,
            "lease": lease_id,
            "idem": idempotency_key,
            "attempt": resolved,
            "status": status,
            "obs": obs,
        },
    )
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
            "attempt": resolved,
            "observation": obs,
            "executor": executor,
            "effect_class": effect_class,
            "provider_ref": provider_ref,
            "output_cell_ids": list(output_cell_ids or []),
            "cost": normalize_cost(cost),
            "error": normalize_error(error),
            "compensates": compensates,
            "retry_authorized": bool(retry_authorized),
            "diagnostics": diagnostics or {},
        },
    )
    return rid


def receipts_of_step(weave: Weave, step_id: str) -> list[Cell]:
    """A step's receipts in deterministic order — its whole attempt history."""
    return sorted(
        (c for c in weave.of_type(RECEIPT) if c.content.get("step_id") == step_id),
        key=receipt_order,
    )


def receipts_for_idempotency_key(weave: Weave, idempotency_key: str) -> list[Cell]:
    """Every receipt recorded under one idempotency key — the FULL multi-attempt history of
    that logical operation (§8.5), in deterministic :func:`receipt_order`."""
    return sorted(
        (c for c in weave.of_type(RECEIPT) if c.content.get("idempotency_key") == idempotency_key),
        key=receipt_order,
    )


def receipt_for_idempotency_key(weave: Weave, idempotency_key: str) -> Cell | None:
    """The LATEST receipt recorded for an idempotency key — the seam that makes re-dispatch
    a no-op (replay executes no effect, DEC-011 property 10).

    Latest, not "whichever the fold yielded first": once a logical operation has a
    multi-attempt history several receipts share the key, and taking the first match made
    the answer depend on fold iteration order. Callers asking "is this operation finished"
    want :func:`final_receipt_for_idempotency_key` — an UNKNOWN receipt is resting, not
    final (§8.2)."""
    history = receipts_for_idempotency_key(weave, idempotency_key)
    return history[-1] if history else None


def final_receipt_for_idempotency_key(weave: Weave, idempotency_key: str) -> Cell | None:
    """The last FINAL receipt for a key (SUCCEEDED/FAILED/CANCELLED/COMPENSATED), or None.
    ``UNKNOWN`` is deliberately excluded: it is resting and must be reconciled (§8.3)."""
    for receipt in reversed(receipts_for_idempotency_key(weave, idempotency_key)):
        if str(receipt.content.get("status")) in ReceiptStatus.FINAL:
            return receipt
    return None


def current_attempt(weave: Weave, step_id: str, idempotency_key: str = "") -> int:
    """The highest attempt already witnessed on the log for a step's logical operation — its
    own counter, its leases (§8.4), and its receipts. 0 means nothing has been attempted."""
    high = 0
    step = weave.get(step_id)
    if step is not None:
        high = max(high, _as_int(step.content.get("attempt")))
    for lease in weave.of_type(LEASE):
        if lease.content.get("step_id") == step_id:
            high = max(high, _as_int(lease.content.get("attempt")))
    for receipt in weave.of_type(RECEIPT):
        content = receipt.content
        if content.get("step_id") == step_id or (
            idempotency_key and content.get("idempotency_key") == idempotency_key
        ):
            high = max(high, _as_int(content.get("attempt")))
    return high


def next_attempt(weave: Weave, step_id: str, idempotency_key: str = "") -> int:
    """The next physical attempt number for a logical operation (§8.5): strictly greater than
    every attempt on the log. Monotone across restarts AND reconciliations, so a retry can
    never reuse an attempt number and overwrite a prior attempt's receipt."""
    return current_attempt(weave, step_id, idempotency_key) + 1


def next_observation(weave: Weave, step_id: str, attempt: int) -> int:
    """The next observation index within ONE attempt — how a later receipt for the same
    attempt (RUNNING → UNKNOWN → reconciled SUCCEEDED) is appended as a new immutable value
    instead of editing the prior one (§8.1)."""
    high = -1
    for receipt in weave.of_type(RECEIPT):
        content = receipt.content
        if content.get("step_id") != step_id or _as_int(content.get("attempt")) != int(attempt):
            continue
        high = max(high, _as_int(content.get("observation")))
    return high + 1


# ── durable anti-replay: consumed-lease markers (DEC-042 single-use) ──────────
LEASE_CONSUMPTION = "lease_consumption"


def record_lease_consumption(
    weft: Weft,
    author: str,
    *,
    idempotency_key: str,
    attempt: int,
) -> str:
    """Durably mark a runtime lease's ``(idempotency_key, attempt)`` as CONSUMED so a
    replay of that lease in a LATER process fails closed. The worker dispatch path's
    ``LeaseGuard`` remembers consumptions only in process memory and forgets them across a
    restart; this marker is the folded, durable projection a store-holding dispatcher
    seeds that guard from (see :func:`consumed_lease_keys`). Idempotent: one stable Cell
    per key, so re-recording is last-writer-wins over that Cell, never a duplicate."""
    cid = _cid(LEASE_CONSUMPTION, {"idem": idempotency_key, "attempt": int(attempt)})
    assert_content(
        weft,
        author,
        cid,
        LEASE_CONSUMPTION,
        {
            "idempotency_key": idempotency_key,
            "attempt": int(attempt),
            "instruction_eligible": False,
        },
    )
    return cid


def consumed_lease_keys(weave: Weave) -> set[tuple[str, int]]:
    """The durable set of consumed lease keys — one ``(idempotency_key, attempt)`` per
    :func:`record_lease_consumption` marker on the fold. A store-holding dispatcher seeds
    a ``LeaseGuard`` from this before invoking the pure worker, so a lease consumed in a
    PRIOR process is refused as a replay (durable single-use across restarts)."""
    keys: set[tuple[str, int]] = set()
    for cell in weave.of_type(LEASE_CONSUMPTION):
        idem = cell.content.get("idempotency_key")
        attempt = cell.content.get("attempt")
        if isinstance(idem, str) and isinstance(attempt, int) and not isinstance(attempt, bool):
            keys.add((idem, int(attempt)))
    return keys
