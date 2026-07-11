"""Durable plan/step Cells + the fold-based scheduler (DEC-040..043).

Builds a real Plan with a dependency DAG on a Weft, folds it, and proves the scheduler
computes readiness purely from the fold, that status transitions are durable Weft events,
and that a fresh process over the same log rebuilds identical state (durability is
structural — nothing lives only in memory).
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells, scheduler
from decima.runtime.cells import StepStatus


def _setup():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(db, kr), author, db, kr


def _dag(weft, author):
    """A → {B, C} → D."""
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    a = cells.create_step(weft, author, plan_id=plan, description="A")
    b = cells.create_step(weft, author, plan_id=plan, description="B", dependency_ids=[a])
    c = cells.create_step(weft, author, plan_id=plan, description="C", dependency_ids=[a])
    d = cells.create_step(weft, author, plan_id=plan, description="D", dependency_ids=[b, c])
    return plan, a, b, c, d


def test_only_dependency_free_step_is_ready_initially():
    weft, author, _db, _kr = _setup()
    plan, a, b, c, d = _dag(weft, author)
    weave = Weave.fold(weft)
    ready = {s.id for s in scheduler.ready_steps(weave, plan)}
    assert ready == {a}, "only A (no deps) should be ready"
    blocked = {s.id for s in scheduler.blocked_steps(weave, plan)}
    assert blocked == {b, c, d}


def test_readiness_advances_as_dependencies_succeed():
    weft, author, _db, _kr = _setup()
    plan, a, b, c, d = _dag(weft, author)

    cells.set_status(weft, author, Weave.fold(weft).get(a), StepStatus.SUCCEEDED)
    weave = Weave.fold(weft)
    assert {s.id for s in scheduler.ready_steps(weave, plan)} == {b, c}

    for step in (b, c):
        cells.set_status(weft, author, Weave.fold(weft).get(step), StepStatus.SUCCEEDED)
    weave = Weave.fold(weft)
    assert {s.id for s in scheduler.ready_steps(weave, plan)} == {d}
    assert not scheduler.plan_is_complete(weave, plan)

    cells.set_status(weft, author, Weave.fold(weft).get(d), StepStatus.SUCCEEDED)
    assert scheduler.plan_is_complete(Weave.fold(weft), plan)


def test_reconcile_readiness_persists_status():
    weft, author, _db, _kr = _setup()
    plan, a, b, c, d = _dag(weft, author)

    r1 = scheduler.reconcile_readiness(weft, author, Weave.fold(weft), plan)
    moves = {t["step"]: t["to"] for t in r1["transitions"]}
    assert moves[a] == StepStatus.READY
    assert moves[b] == moves[c] == moves[d] == StepStatus.BLOCKED

    # Idempotent: a second reconcile with no dependency change makes no transitions.
    assert scheduler.reconcile_readiness(weft, author, Weave.fold(weft), plan)["transitions"] == []

    # After A succeeds, reconcile unblocks B and C.
    cells.set_status(weft, author, Weave.fold(weft).get(a), StepStatus.SUCCEEDED)
    r2 = scheduler.reconcile_readiness(weft, author, Weave.fold(weft), plan)
    moves2 = {t["step"]: t["to"] for t in r2["transitions"]}
    assert moves2 == {b: StepStatus.READY, c: StepStatus.READY}


def test_state_survives_a_restart():
    """A fresh Weft over the same db rebuilds identical scheduler state by folding."""
    weft, author, db, kr = _setup()
    plan, a, b, c, d = _dag(weft, author)
    cells.set_status(weft, author, Weave.fold(weft).get(a), StepStatus.SUCCEEDED)
    root_before = Weave.fold(weft).state_root()

    # "restart": a new Weft object over the SAME db + a warm keyring.
    weft2 = Weft(db, kr)
    weave2 = Weave.fold(weft2)
    assert weave2.state_root() == root_before
    assert {s.id for s in scheduler.ready_steps(weave2, plan)} == {b, c}


def test_agent_cell_roundtrips_with_budgets():
    weft, author, _db, _kr = _setup()
    aid = cells.create_agent(
        weft, author, objective="do work", principal=author, token_budget=1000, deadline=50
    )
    agent = Weave.fold(weft).get(aid)
    assert agent.content["status"] == cells.AgentStatus.CREATED
    assert agent.content["token_budget"] == 1000
    assert agent.content["deadline"] == 50
    cells.set_status(weft, author, agent, cells.AgentStatus.RUNNING)
    assert Weave.fold(weft).get(aid).content["status"] == cells.AgentStatus.RUNNING
