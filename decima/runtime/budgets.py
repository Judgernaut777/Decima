"""Durable budget enforcement for agents (DEC-046).

A budget is not a log line the supervisor prints and forgets — it is folded state and,
on exhaustion, a durable status transition on the Weft (Law 1 / invariant 2.1). Every
limit an agent carries — ``token_budget``, ``monetary_budget``, ``deadline``,
``max_attempts``, ``max_child_agents``, ``max_concurrent`` — is an integer bound (logical
time / logical units, never a float or a wall-clock, DETERMINISM §1). The SPEND against
each bound is a pure projection folded from the receipts, leases, and agent cells already
on the log; nothing lives only in memory.

The gate runs BEFORE dispatch: :func:`check_budget` is a pure read that answers
"may this next unit of work run", and :func:`guarded_dispatch_step` composes it with the
supervisor so that an exhausted agent's step is REFUSED and the agent is transitioned to a
durable ``BUDGET_BLOCKED`` status — a new assertion, not a side channel. A blocked agent
stays blocked across a restart because the block is a Cell, so a fresh process folds it and
still refuses the work. This module mints no authority: it only reads the fold and asserts
status/limit Cells through the kernel's content path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from decima.kernel.weave import Weave
from decima.runtime import cells, supervisor
from decima.runtime.cells import AgentStatus, StepStatus

# A durable status an agent enters when a budget is exhausted. It is NOT terminal — an
# operator can raise the limit and unblock — but while set, dispatch fails closed.
BUDGET_BLOCKED = "BUDGET_BLOCKED"

# Limit fields read from an agent Cell. All are optional (None ⇒ that bound is unlimited).
# `token_budget`/`monetary_budget`/`deadline` are set by cells.create_agent; the three
# structural caps are optional extras a caller may add via `set_limits`.
_LIMIT_FIELDS = (
    "token_budget",
    "monetary_budget",
    "deadline",
    "max_attempts",
    "max_child_agents",
    "max_concurrent",
)


@dataclass(frozen=True)
class Cost:
    """The predicted logical cost of ONE next unit of work (all ints)."""

    tokens: int = 0
    monetary: int = 0

    @classmethod
    def of(cls, cost: Cost | Mapping[str, int] | None) -> Cost:
        """Coerce a mapping / None into a Cost (unknown keys ignored)."""
        if cost is None:
            return cls()
        if isinstance(cost, Cost):
            return cost
        return cls(tokens=int(cost.get("tokens", 0)), monetary=int(cost.get("monetary", 0)))


@dataclass(frozen=True)
class Spend:
    """The folded spend of an agent against its budgets — a pure projection."""

    tokens: int = 0
    monetary: int = 0
    attempts: int = 0
    child_agents: int = 0
    running: int = 0


def _steps_of_agent(weave: object, agent_id: str) -> list[object]:
    return [
        c for c in weave.of_type(cells.PLAN_STEP) if c.content.get("assigned_agent_id") == agent_id
    ]


def spend_ledger(weave: object, agent_id: str) -> Spend:
    """Fold an agent's spend from the Weft: token/monetary cost accreted from its steps'
    RECEIPTS (a runner reports ``token_cost``/``monetary_cost`` in its result, which the
    supervisor records into the receipt diagnostics), the attempt count from its LEASES,
    the child-agent count from Agent cells naming it as parent, and the concurrent count
    from its steps currently RUNNING. Pure read; deterministic; recomputed each call."""
    step_ids = {s.id for s in _steps_of_agent(weave, agent_id)}
    running = sum(
        1 for s in _steps_of_agent(weave, agent_id) if s.content.get("status") == StepStatus.RUNNING
    )
    tokens = monetary = 0
    for r in weave.of_type(cells.RECEIPT):
        if r.content.get("step_id") not in step_ids:
            continue
        diag = r.content.get("diagnostics") or {}
        tokens += int(diag.get("token_cost", 0) or 0)
        monetary += int(diag.get("monetary_cost", 0) or 0)
    attempts = sum(
        1 for lease in weave.of_type(cells.LEASE) if lease.content.get("step_id") in step_ids
    )
    child_agents = sum(
        1 for a in weave.of_type(cells.AGENT) if a.content.get("parent_agent_id") == agent_id
    )
    return Spend(
        tokens=tokens,
        monetary=monetary,
        attempts=attempts,
        child_agents=child_agents,
        running=running,
    )


def _limits(agent_cell: object) -> dict[str, int | None]:
    c = agent_cell.content
    return {k: c.get(k) for k in _LIMIT_FIELDS}


def check_budget(
    weave: object,
    agent_id: str,
    cost: Cost | Mapping[str, int] | None,
    now: int,
) -> tuple[bool, str]:
    """Pure pre-dispatch gate: may the agent afford ONE more unit of work costing `cost`
    at logical time `now`? Fails CLOSED — an unknown agent, an already-blocked/terminal
    agent, a passed deadline, or any bound that the pending unit would cross returns
    (False, reason). Every limit is optional; an absent bound is unlimited. No mutation."""
    agent = weave.get(agent_id)
    if agent is None or agent.type != cells.AGENT:
        return False, "no such agent"
    status = agent.content.get("status")
    if status == BUDGET_BLOCKED:
        return False, "agent already budget-blocked"
    if status in AgentStatus.TERMINAL:
        return False, f"agent is terminal ({status})"

    c = Cost.of(cost)
    spend = spend_ledger(weave, agent_id)
    lim = _limits(agent)

    deadline = lim["deadline"]
    if deadline is not None and int(now) >= int(deadline):
        return False, f"deadline exceeded (now {now} >= deadline {deadline})"

    tb = lim["token_budget"]
    if tb is not None and spend.tokens + c.tokens > int(tb):
        return False, f"token budget exhausted (spent {spend.tokens} + {c.tokens} > {tb})"

    mb = lim["monetary_budget"]
    if mb is not None and spend.monetary + c.monetary > int(mb):
        return False, f"monetary budget exhausted (spent {spend.monetary} + {c.monetary} > {mb})"

    ma = lim["max_attempts"]
    if ma is not None and spend.attempts >= int(ma):
        return False, f"max attempts reached ({spend.attempts}/{ma})"

    mc = lim["max_concurrent"]
    if mc is not None and spend.running >= int(mc):
        return False, f"max concurrent reached ({spend.running}/{mc})"

    mca = lim["max_child_agents"]
    if mca is not None and spend.child_agents >= int(mca):
        return False, f"max child agents reached ({spend.child_agents}/{mca})"

    return True, "ok"


def set_limits(
    weft: object,
    author: str,
    agent_id: str,
    *,
    token_budget: int | None = None,
    monetary_budget: int | None = None,
    deadline: int | None = None,
    max_attempts: int | None = None,
    max_child_agents: int | None = None,
    max_concurrent: int | None = None,
) -> str:
    """Durably set (or raise/lower) an agent's structural budget bounds by asserting a new
    CONTENT version of the Agent cell — the limits are DATA, folded like everything else.
    Only the provided bounds are changed; ints only (determinism). Returns the agent id."""
    weave = Weave.fold(weft)
    agent = weave.get(agent_id)
    if agent is None or agent.type != cells.AGENT:
        raise ValueError(f"no such agent {agent_id}")
    updates = {
        "token_budget": token_budget,
        "monetary_budget": monetary_budget,
        "deadline": deadline,
        "max_attempts": max_attempts,
        "max_child_agents": max_child_agents,
        "max_concurrent": max_concurrent,
    }
    content = dict(agent.content)
    for k, v in updates.items():
        if v is not None:
            content[k] = int(v)
    cells.assert_content(weft, author, agent_id, cells.AGENT, content)
    return agent_id


def block_agent(weft: object, author: str, agent_id: str, reason: str) -> str:
    """Transition an agent to the durable BUDGET_BLOCKED status (a new assertion), so a
    fresh process folding the log still refuses to dispatch its work. Idempotent: an
    already-blocked agent is not re-asserted. Returns the status set."""
    weave = Weave.fold(weft)
    agent = weave.get(agent_id)
    if agent is None:
        raise ValueError(f"no such agent {agent_id}")
    if agent.content.get("status") == BUDGET_BLOCKED:
        return BUDGET_BLOCKED
    content = dict(agent.content)
    content["status"] = BUDGET_BLOCKED
    content["budget_block_reason"] = reason
    cells.assert_content(weft, author, agent_id, cells.AGENT, content)
    return BUDGET_BLOCKED


def guarded_dispatch_step(
    weft: object,
    author: str,
    step_id: str,
    runner: supervisor.Runner,
    *,
    now: int,
    cost: Cost | Mapping[str, int] | None = None,
    lease_ttl: int = supervisor._DEFAULT_LEASE_TTL,
) -> dict:
    """Dispatch a step ONLY if its assigned agent can afford it. The budget gate runs
    BEFORE any effect: if the agent is exhausted the runner is NEVER called, the agent is
    transitioned to a durable BUDGET_BLOCKED status, the step is durably BLOCKED, and a
    refusal is returned. Otherwise the supervisor's normal (idempotent, leased) dispatch
    proceeds. A step with no assigned agent is unbudgeted and dispatched directly."""
    weave = Weave.fold(weft)
    step = weave.get(step_id)
    if step is None:
        raise ValueError(f"no such step {step_id}")
    agent_id = step.content.get("assigned_agent_id")
    if agent_id:
        ok, reason = check_budget(weave, agent_id, cost, now)
        if not ok:
            block_agent(weft, author, agent_id, reason)
            fresh_step = Weave.fold(weft).get(step_id)
            if fresh_step.content.get("status") not in StepStatus.TERMINAL:
                cells.set_status(weft, author, fresh_step, StepStatus.BLOCKED)
            return {
                "step": step_id,
                "agent": agent_id,
                "dispatched": False,
                "reason": reason,
            }
    out = supervisor.dispatch_step(
        weft, author, Weave.fold(weft), step_id, runner, now=now, lease_ttl=lease_ttl
    )
    out["dispatched"] = True
    return out
