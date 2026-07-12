"""Durable budget enforcement (DEC-046).

Proves the load-bearing property: an EXHAUSTED budget durably BLOCKS dispatch — the gate
runs before the effect, the runner is never called, and the block is a folded Cell that
survives a restart. Spend is folded from receipts, so accumulated cost across attempts is
what trips the bound.
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import budgets, cells
from decima.runtime.cells import AgentStatus, StepStatus


def _setup():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(db, kr), author, db, kr


def _agent_with_step(weft, author, *, token_budget=None, description="A"):
    agent = cells.create_agent(
        weft, author, objective="work", principal=author, token_budget=token_budget
    )
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    step = cells.create_step(
        weft, author, plan_id=plan, description=description, assigned_agent_id=agent
    )
    return agent, plan, step


def test_spend_folds_from_receipts_and_exhaustion_blocks_dispatch():
    weft, author, _db, _kr = _setup()
    agent, _plan, step1 = _agent_with_step(weft, author, token_budget=100, description="A")
    plan = Weave.fold(weft).get(step1).content["plan_id"]
    step2 = cells.create_step(weft, author, plan_id=plan, description="B", assigned_agent_id=agent)

    calls = {"n": 0}

    def runner(_step):
        calls["n"] += 1
        return {"status": StepStatus.SUCCEEDED, "token_cost": 100}

    # First dispatch fits (0 spent + 100 <= 100) and runs, recording spend=100.
    out1 = budgets.guarded_dispatch_step(weft, author, step1, runner, now=0, cost={"tokens": 100})
    assert out1["dispatched"] is True
    assert calls["n"] == 1
    assert budgets.spend_ledger(Weave.fold(weft), agent).tokens == 100

    # Second dispatch would push spend over budget → REFUSED before the runner is called.
    out2 = budgets.guarded_dispatch_step(weft, author, step2, runner, now=1, cost={"tokens": 1})
    assert out2["dispatched"] is False, "an exhausted budget must refuse dispatch"
    assert calls["n"] == 1, "the runner must NOT be called once the budget is exhausted"

    # The refusal is DURABLE state, not a log line: the agent is BUDGET_BLOCKED and the
    # step BLOCKED, and check_budget keeps failing closed.
    fresh = Weave.fold(weft)
    assert fresh.get(agent).content["status"] == budgets.BUDGET_BLOCKED
    assert fresh.get(step2).content["status"] == StepStatus.BLOCKED
    ok, _ = budgets.check_budget(fresh, agent, {"tokens": 1}, 1)
    assert ok is False


def test_block_survives_restart():
    weft, author, db, kr = _setup()
    agent, _plan, step = _agent_with_step(weft, author, token_budget=0)

    def runner(_step):
        raise AssertionError("must not run under an exhausted budget")

    out = budgets.guarded_dispatch_step(weft, author, step, runner, now=0, cost={"tokens": 1})
    assert out["dispatched"] is False

    # Fresh Weft over the SAME db folds the block back — durability is structural.
    weft2 = Weft(db, kr)
    assert Weave.fold(weft2).get(agent).content["status"] == budgets.BUDGET_BLOCKED


def test_deadline_and_structural_limits_gate():
    weft, author, _db, _kr = _setup()
    agent = cells.create_agent(weft, author, objective="work", principal=author, deadline=10)
    weave = Weave.fold(weft)
    ok, reason = budgets.check_budget(weave, agent, None, now=10)
    assert ok is False and "deadline" in reason

    # max_child_agents: spawning a second child is refused once the cap is reached.
    budgets.set_limits(weft, author, agent, max_child_agents=1)
    cells.create_agent(weft, author, objective="child", principal=author, parent_agent_id=agent)
    weave = Weave.fold(weft)
    ok, reason = budgets.check_budget(weave, agent, None, now=0)
    assert ok is False and "child" in reason


def test_unbudgeted_agent_runs_and_terminal_agent_refused():
    weft, author, _db, _kr = _setup()
    agent, _plan, step = _agent_with_step(weft, author, token_budget=None)

    ran = {"n": 0}

    def runner(_step):
        ran["n"] += 1
        return {"status": StepStatus.SUCCEEDED}

    out = budgets.guarded_dispatch_step(weft, author, step, runner, now=0)
    assert out["dispatched"] is True and ran["n"] == 1

    # A terminal agent is always refused (fail closed).
    cells.set_status(weft, author, Weave.fold(weft).get(agent), AgentStatus.FAILED)
    ok, reason = budgets.check_budget(Weave.fold(weft), agent, None, now=0)
    assert ok is False and "terminal" in reason
