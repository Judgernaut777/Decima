"""Library-level tests for the planning lane's runtime composition (``execution``).

These exercise the composition seams directly over a temp Weft — no API, no model:
fail-closed cancellation of unrunnable steps (dead dependencies, terminal agents),
the ACTIVE-only dispatch gate (pause enforced at the runtime, not the UI), plan
completion as a durable transition, and agent-status sync derived purely from the
fold. Everything durable is asserted through ``runtime.cells``; a re-fold of the
same Weft reproduces every decision.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells, execution
from decima.runtime.cells import AgentStatus, PlanStatus, StepStatus

AUTHOR = "app"


@pytest.fixture()
def weft():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    return Weft(db, Keyring(seed=bytes(32)))


def _runner_ok(step_view):
    return {"status": StepStatus.SUCCEEDED, "token_cost": 1, "monetary_cost": 0}


def _mk_plan(weft, *, agent_budget=100):
    plan_id = cells.create_plan(weft, AUTHOR, objective="obj", creator_principal="human")
    agent_a = cells.create_agent(
        weft, AUTHOR, objective="a", principal="agent:a", token_budget=agent_budget
    )
    agent_b = cells.create_agent(
        weft,
        AUTHOR,
        objective="b",
        principal="agent:b",
        parent_agent_id=agent_a,
        token_budget=agent_budget,
    )
    for aid in (agent_a, agent_b):
        cell = Weave.fold(weft).get(aid)
        content = dict(cell.content)
        content["plan_id"] = plan_id
        cells.assert_content(weft, AUTHOR, aid, cells.AGENT, content)
    s1 = cells.create_step(
        weft, AUTHOR, plan_id=plan_id, description="one", assigned_agent_id=agent_b
    )
    s2 = cells.create_step(
        weft,
        AUTHOR,
        plan_id=plan_id,
        description="two",
        dependency_ids=[s1],
        assigned_agent_id=agent_b,
    )
    return plan_id, agent_a, agent_b, s1, s2


def _activate(weft, plan_id):
    cells.set_status(weft, AUTHOR, Weave.fold(weft).get(plan_id), PlanStatus.ACTIVE)


def test_non_active_plan_dispatches_nothing(weft):
    plan_id, *_ = _mk_plan(weft)
    report = execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)
    assert report["dispatched"] == [] and report["status"] == PlanStatus.DRAFT
    cells.set_status(weft, AUTHOR, Weave.fold(weft).get(plan_id), PlanStatus.PAUSED)
    report = execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=2)
    assert report["dispatched"] == []  # pause is runtime-enforced
    assert Weave.fold(weft).of_type(cells.RECEIPT) == []


def test_active_plan_runs_to_durable_completion(weft):
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft)
    _activate(weft, plan_id)
    r1 = execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)
    assert [d["step"] for d in r1["dispatched"]] == [s1]  # dependency order respected
    r2 = execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=2)
    assert [d["step"] for d in r2["dispatched"]] == [s2]
    assert r2["complete"] is True
    weave = Weave.fold(weft)
    assert weave.get(plan_id).content["status"] == PlanStatus.COMPLETED
    assert len(weave.of_type(cells.RECEIPT)) == 2  # one receipt per step


def test_terminal_agent_steps_are_cancelled_not_dispatched(weft):
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft)
    cells.set_status(weft, AUTHOR, Weave.fold(weft).get(agent_b), AgentStatus.TERMINATED)
    _activate(weft, plan_id)
    report = execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)
    assert report["dispatched"] == []
    assert sorted(report["cancelled_steps"]) == sorted([s1, s2])
    weave = Weave.fold(weft)
    assert weave.get(s1).content["status"] == StepStatus.CANCELLED
    assert weave.of_type(cells.RECEIPT) == []  # nothing ever ran
    assert report["complete"] is True  # bounded terminal fold


def test_dead_dependency_cascades_transitively(weft):
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft)
    s3 = cells.create_step(
        weft,
        AUTHOR,
        plan_id=plan_id,
        description="three",
        dependency_ids=[s2],
        assigned_agent_id=agent_b,
    )
    cells.set_status(weft, AUTHOR, Weave.fold(weft).get(s1), StepStatus.FAILED)
    _activate(weft, plan_id)
    report = execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)
    weave = Weave.fold(weft)
    assert weave.get(s2).content["status"] == StepStatus.CANCELLED
    assert weave.get(s3).content["status"] == StepStatus.CANCELLED
    assert report["complete"] is True


def test_budget_refusal_blocks_before_effect(weft):
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft, agent_budget=0)
    _activate(weft, plan_id)
    report = execution.drive_plan_once(
        weft,
        AUTHOR,
        plan_id,
        _runner_ok,
        now=1,
        cost_of=lambda s: {"tokens": 5, "monetary": 0},
    )
    assert report["dispatched"] == [] and report["refused"]
    weave = Weave.fold(weft)
    assert weave.get(agent_b).content["status"] == "BUDGET_BLOCKED"
    assert weave.of_type(cells.RECEIPT) == []


def test_sync_agent_statuses_derives_from_the_fold(weft):
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft)
    _activate(weft, plan_id)
    execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)  # s1 done
    changes = execution.sync_agent_statuses(weft, AUTHOR, plan_id)
    weave = Weave.fold(weft)
    assert weave.get(agent_b).content["status"] == AgentStatus.RUNNING
    assert weave.get(agent_a).content["status"] == AgentStatus.RUNNING  # parent follows
    execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=2)  # s2 done
    execution.sync_agent_statuses(weft, AUTHOR, plan_id)
    weave = Weave.fold(weft)
    assert weave.get(agent_b).content["status"] == AgentStatus.COMPLETED
    assert weave.get(agent_a).content["status"] == AgentStatus.COMPLETED
    assert changes  # transitions were recorded


def test_refold_reproduces_every_decision(weft):
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft)
    _activate(weft, plan_id)
    execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)
    root_before = Weave.fold(weft).state_root()
    root_after = Weave.fold(weft).state_root()  # a fresh fold, same log
    assert root_before == root_after
