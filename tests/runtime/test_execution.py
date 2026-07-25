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
from decima.runtime import cells, execution, seam
from decima.runtime.cells import AgentStatus, PlanStatus, StepStatus

AUTHOR = "app"


def _fresh_weft():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    return Weft(db, Keyring(seed=bytes(32)))


@pytest.fixture()
def weft():
    return _fresh_weft()


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
        assert cell is not None
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
    plan_cell = weave.get(plan_id)
    assert plan_cell is not None
    assert plan_cell.content["status"] == PlanStatus.COMPLETED
    assert len(weave.of_type(cells.RECEIPT)) == 2  # one receipt per step


def test_terminal_agent_steps_are_cancelled_not_dispatched(weft):
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft)
    cells.set_status(weft, AUTHOR, Weave.fold(weft).get(agent_b), AgentStatus.TERMINATED)
    _activate(weft, plan_id)
    report = execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)
    assert report["dispatched"] == []
    assert sorted(report["cancelled_steps"]) == sorted([s1, s2])
    weave = Weave.fold(weft)
    s1_cell = weave.get(s1)
    assert s1_cell is not None
    assert s1_cell.content["status"] == StepStatus.CANCELLED
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
    s2_cell = weave.get(s2)
    s3_cell = weave.get(s3)
    assert s2_cell is not None
    assert s3_cell is not None
    assert s2_cell.content["status"] == StepStatus.CANCELLED
    assert s3_cell.content["status"] == StepStatus.CANCELLED
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
    agent_b_cell = weave.get(agent_b)
    assert agent_b_cell is not None
    assert agent_b_cell.content["status"] == "BUDGET_BLOCKED"
    assert weave.of_type(cells.RECEIPT) == []


def test_sync_agent_statuses_derives_from_the_fold(weft):
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft)
    _activate(weft, plan_id)
    execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)  # s1 done
    changes = execution.sync_agent_statuses(weft, AUTHOR, plan_id)
    weave = Weave.fold(weft)
    agent_b_cell = weave.get(agent_b)
    agent_a_cell = weave.get(agent_a)
    assert agent_b_cell is not None
    assert agent_a_cell is not None
    assert agent_b_cell.content["status"] == AgentStatus.RUNNING
    assert agent_a_cell.content["status"] == AgentStatus.RUNNING  # parent follows
    execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=2)  # s2 done
    execution.sync_agent_statuses(weft, AUTHOR, plan_id)
    weave = Weave.fold(weft)
    agent_b_cell = weave.get(agent_b)
    agent_a_cell = weave.get(agent_a)
    assert agent_b_cell is not None
    assert agent_a_cell is not None
    assert agent_b_cell.content["status"] == AgentStatus.COMPLETED
    assert agent_a_cell.content["status"] == AgentStatus.COMPLETED
    assert changes  # transitions were recorded


def test_refold_reproduces_every_decision(weft):
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft)
    _activate(weft, plan_id)
    execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)
    root_before = Weave.fold(weft).state_root()
    root_after = Weave.fold(weft).state_root()  # a fresh fold, same log
    assert root_before == root_after


def test_incremental_fold_seam_equals_genesis_fold(weft):
    """Mandatory P4.1 equivalence gate: the checkpoint/incremental-fold seam drive_plan_once
    uses is byte-for-byte equal to a genesis fold over a representative plan log (FOLD §11.1).

    A base frozen at pass entry — and one frozen mid-run — must, once the whole runtime tail
    (readiness, leases, receipts, status transitions, plan completion) is folded onto it
    incrementally, produce the identical state_root as folding the entire log from genesis. If
    this ever regressed, the seam would become a second, divergent source of truth (Law 5)."""
    plan_id, agent_a, agent_b, s1, s2 = _mk_plan(weft)
    _activate(weft, plan_id)

    # Frozen BEFORE any runtime events, exactly as drive_plan_once's seam does at pass entry.
    base = Weave.fold(weft).checkpoint()
    execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=1)

    # Frozen mid-run (after pass 1), advanced across pass 2 — the same seam a later pass uses.
    mid = Weave.fold(weft).checkpoint()
    execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=2)

    genesis = Weave.fold(weft)
    assert Weave.fold_incremental(weft, base).state_root() == genesis.state_root()
    assert Weave.fold_incremental(weft, mid).state_root() == genesis.state_root()

    # Observable outcome unchanged: the plan still reached durable COMPLETED.
    plan_cell = genesis.get(plan_id)
    assert plan_cell is not None
    assert plan_cell.content["status"] == PlanStatus.COMPLETED
    assert len(genesis.of_type(cells.RECEIPT)) == 2


# ── the seam is a COST change, never a semantic one ───────────────────────────────
#
# Two oracles, because "equal state_root" and "equal decisions" are different claims.
#
# 1. FRONTIER equality (whitebox): every read the seam hands out must equal a fold from
#    genesis at that moment. This is the mandatory P4.1 gate — proven, not assumed.
# 2. TWIN-WEFT A/B (blackbox): every id in this lane is content-addressed and every clock
#    is logical, so two Wefts built by the identical call sequence under the same keyring
#    seed are byte-identical logs. Drive one seamed and one with `use_seam=False` (folding
#    from genesis at every read) and demand identical reports AND identical state_roots.
#    An extra event, a dropped one, a different order, or a decision taken on a stale fold
#    all change the root.


def _twin_plans(**kw):
    """Two independent Wefts holding the identical, ACTIVE plan log."""
    out = []
    for _ in range(2):
        w = _fresh_weft()
        plan_id, _a, _b, _s1, _s2 = _mk_plan(w, **kw)
        _activate(w, plan_id)
        out.append((w, plan_id))
    return out


def test_twin_wefts_are_deterministic_before_the_a_b():
    """The A/B's premise: identical construction ⇒ identical log. Asserted, not assumed —
    if this ever failed, every twin-Weft comparison below would be vacuous."""
    (w1, p1), (w2, p2) = _twin_plans()
    assert p1 == p2
    assert Weave.fold(w1).state_root() == Weave.fold(w2).state_root()


def test_every_seam_read_equals_a_genesis_fold(weft):
    """MANDATORY P4.1 equivalence gate, at every boundary a pass crosses. The cursor is
    advanced across the cancellation, readiness, dispatch and completion writes of two real
    passes; after each, the advanced fold's state_root must equal a fold from genesis."""
    plan_id, _a, _b, _s1, _s2 = _mk_plan(weft)
    _activate(weft, plan_id)
    cursor = seam.Cursor.at(weft)
    assert cursor.read(weft).state_root() == Weave.fold(weft).state_root()
    for now in (1, 2):
        execution.drive_plan_once(weft, AUTHOR, plan_id, _runner_ok, now=now)
        assert cursor.read(weft).state_root() == Weave.fold(weft).state_root()
        # ...and the advanced fold is a fold, not a lookalike: same cells, same statuses.
        advanced, genesis = cursor.read(weft), Weave.fold(weft)
        assert sorted(advanced.cells) == sorted(genesis.cells)
        assert len(advanced.of_type(cells.RECEIPT)) == len(genesis.of_type(cells.RECEIPT))
    assert seam.read(weft, None).state_root() == Weave.fold(weft).state_root()


def test_seamed_pass_equals_genesis_only_pass():
    """Happy path, two passes to durable completion: seamed and un-seamed must be
    indistinguishable in both the report and the resulting log."""
    (w_seam, plan), (w_gen, _) = _twin_plans()
    for now in (1, 2):
        r_seam = execution.drive_plan_once(w_seam, AUTHOR, plan, _runner_ok, now=now)
        r_gen = execution.drive_plan_once(w_gen, AUTHOR, plan, _runner_ok, now=now, use_seam=False)
        assert r_seam == r_gen
    assert Weave.fold(w_seam).state_root() == Weave.fold(w_gen).state_root()
    plan_cell = Weave.fold(w_seam).get(plan)
    assert plan_cell is not None
    assert plan_cell.content["status"] == PlanStatus.COMPLETED


def test_seamed_budget_refusal_equals_genesis_only():
    """The refusal branch writes MID-read (block the agent, then re-read the step) — the one
    place a stale fold would silently change a decision. It must A/B identically."""
    (w_seam, plan), (w_gen, _) = _twin_plans(agent_budget=0)
    cost = {"tokens": 5, "monetary": 0}
    r_seam = execution.drive_plan_once(
        w_seam, AUTHOR, plan, _runner_ok, now=1, cost_of=lambda s: cost
    )
    r_gen = execution.drive_plan_once(
        w_gen, AUTHOR, plan, _runner_ok, now=1, cost_of=lambda s: cost, use_seam=False
    )
    assert r_seam == r_gen and r_seam["refused"]
    assert Weave.fold(w_seam).state_root() == Weave.fold(w_gen).state_root()


def test_seamed_cancellation_equals_genesis_only():
    """The cancellation phase writes before readiness is reconciled; the seam must carry
    those events into every later read exactly as a genesis re-fold would."""
    (w_seam, plan), (w_gen, _) = _twin_plans()
    for w in (w_seam, w_gen):
        agent_b = execution.agents_of_plan(Weave.fold(w), plan)[-1]
        cells.set_status(w, AUTHOR, Weave.fold(w).get(agent_b.id), AgentStatus.TERMINATED)
    r_seam = execution.drive_plan_once(w_seam, AUTHOR, plan, _runner_ok, now=1)
    r_gen = execution.drive_plan_once(w_gen, AUTHOR, plan, _runner_ok, now=1, use_seam=False)
    assert r_seam == r_gen and r_seam["cancelled_steps"]
    assert Weave.fold(w_seam).state_root() == Weave.fold(w_gen).state_root()


def test_seamed_dispatchable_bound_equals_genesis_only():
    """The `dispatchable` bound is evaluated against the pre-dispatch fold in both paths: a
    step outside the bound is left untouched, seamed or not."""
    (w_seam, plan), (w_gen, _) = _twin_plans()
    bound = lambda step, cell: False  # noqa: E731 — nothing is dispatchable
    r_seam = execution.drive_plan_once(w_seam, AUTHOR, plan, _runner_ok, now=1, dispatchable=bound)
    r_gen = execution.drive_plan_once(
        w_gen, AUTHOR, plan, _runner_ok, now=1, dispatchable=bound, use_seam=False
    )
    assert r_seam == r_gen and r_seam["dispatched"] == []
    assert Weave.fold(w_seam).state_root() == Weave.fold(w_gen).state_root()
    assert Weave.fold(w_seam).of_type(cells.RECEIPT) == []  # nothing ran


def test_seam_reads_scale_with_the_TAIL_not_the_log(monkeypatch):
    """The POINT of the seam, asserted as a bound rather than a benchmark: an un-seamed pass
    re-reads the whole log once per ready step (and every read re-verifies every signature
    in `Weft.events` — that reader IS where a fold's cost lives), while a seamed pass reads
    the log once plus its own tail. Two twin Wefts are given the IDENTICAL work so the
    counts are comparable, and the reports must still match."""
    (w_seam, plan), (w_gen, _) = _twin_plans()
    for w in (w_seam, w_gen):  # widen the plan: "per step" must differ from "per pass"
        for i in range(6):
            cells.create_step(w, AUTHOR, plan_id=plan, description=f"extra-{i}")
    log = Weave.fold(w_seam).last_seq

    counted = {"n": 0}
    real_events = Weft.events

    def counting_events(self, *a, **kw):
        for ev in real_events(self, *a, **kw):
            counted["n"] += 1
            yield ev

    monkeypatch.setattr(Weft, "events", counting_events)
    r_seam = execution.drive_plan_once(w_seam, AUTHOR, plan, _runner_ok, now=1)
    seamed = counted["n"]
    counted["n"] = 0
    r_gen = execution.drive_plan_once(w_gen, AUTHOR, plan, _runner_ok, now=1, use_seam=False)
    unseamed = counted["n"]
    monkeypatch.undo()

    tail = Weave.fold(w_seam).last_seq - log  # events this pass itself appended
    assert r_seam == r_gen, "the cheap pass must decide exactly what the expensive one did"
    assert len(r_seam["dispatched"]) >= 7, "the bound is only meaningful with several steps"
    assert seamed <= log + tail, (
        f"a seamed pass must read each event AT MOST ONCE — the log ({log}) plus its own "
        f"tail ({tail}) — but read {seamed}"
    )
    assert unseamed > 5 * seamed, (
        f"the un-seamed pass re-reads the whole log per step: {unseamed} vs {seamed}"
    )
    assert Weave.fold(w_seam).state_root() == Weave.fold(w_gen).state_root()
