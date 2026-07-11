"""Verification lane — property + fault-injection tests over the INTEGRATED durable stack.

Independent probes that hammer the invariants the release rests on, using randomness and
simulated faults rather than hand-picked cases:

  * fold determinism      — shuffled + duplicated event delivery folds to ONE state_root;
  * receipt idempotence   — duplicate worker responses don't duplicate current state;
  * fail-closed recovery  — a kill between dispatch and receipt never silently retries a
                            not-safely-retryable effect;
  * budget monotonicity   — an over-budget dispatch is strictly blocked, runner untouched;
  * projection disposability — rebuild == incremental after ARBITRARY interleavings.

Each asserts a load-bearing property; none mutates source.
"""

from __future__ import annotations

import os
import random
import tempfile

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from decima.kernel.crypto import Keyring
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.projections.activity import ActivityProjection
from decima.projections.agents import AgentsProjection
from decima.projections.engine import ProjectionDriver
from decima.projections.knowledge import KnowledgeProjection
from decima.projections.tasks import TasksProjection
from decima.runtime import budgets, cells, reconciliation, supervisor
from decima.runtime.cells import StepStatus
from decima.runtime.reconciliation import EffectState, IdempotencyStrategy


def _setup() -> tuple[Weft, str, str, Keyring]:
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(db, kr), author, db, kr


def _seed_history(weft: Weft, author: str) -> str:
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    a = cells.create_step(weft, author, plan_id=plan, description="A")
    b = cells.create_step(weft, author, plan_id=plan, description="B", dependency_ids=[a])
    cells.set_status(weft, author, Weave.fold(weft).get(a), StepStatus.SUCCEEDED)
    cells.set_status(weft, author, Weave.fold(weft).get(b), StepStatus.RUNNING)
    assert_content(weft, author, "note:x", "note", {"text": "a durable note"})
    return plan


# --------------------------------------------------------------------------------------
# 1) FOLD DETERMINISM: shuffled + duplicated delivery => one comparable state_root.
# --------------------------------------------------------------------------------------
@settings(max_examples=25, deadline=None)
@given(seed=st.integers(min_value=0, max_value=1 << 30))
def test_shuffled_and_duplicated_delivery_folds_to_one_state_root(seed):
    weft, author, _db, _kr = _setup()
    _seed_history(weft, author)
    canonical = Weave.fold(weft).state_root()

    events = list(weft.events())
    rnd = random.Random(seed)
    delivered = list(events)
    # Re-deliver a random subset (duplicate delivery: a re-fed sync queue).
    delivered += rnd.sample(events, k=rnd.randint(0, len(events)))
    rnd.shuffle(delivered)

    replay = Weave()
    for ev in delivered:
        replay._apply(ev)
    replay._ensure_cascade()
    assert replay.state_root() == canonical, (
        "state_root must be independent of arrival order and duplicate delivery"
    )


# --------------------------------------------------------------------------------------
# 2) RECEIPT IDEMPOTENCE: duplicate worker responses fold to one current state.
# --------------------------------------------------------------------------------------
@settings(max_examples=15, deadline=None)
@given(dupes=st.integers(min_value=1, max_value=6))
def test_duplicate_worker_responses_do_not_duplicate_state(dupes):
    weft, author, _db, _kr = _setup()
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    step = cells.create_step(weft, author, plan_id=plan, description="A")

    def runner(_step):
        return {"status": StepStatus.SUCCEEDED}

    supervisor.dispatch_step(weft, author, Weave.fold(weft), step, runner, now=0)
    lease = Weave.fold(weft).of_type(cells.LEASE)[0].id
    # A flaky worker delivers the SAME terminal outcome many times.
    for _ in range(dupes):
        cells.record_receipt(
            weft, author, step_id=step, lease_id=lease, idempotency_key=step,
            status=StepStatus.SUCCEEDED,
        )
    receipts = reconciliation.receipts_for_step(Weave.fold(weft), step)
    assert len(receipts) == 1, "content-addressed receipts collapse to ONE current state"
    assert (
        reconciliation.classify_effect(Weave.fold(weft), step, now=0) == EffectState.SUCCEEDED
    )


# --------------------------------------------------------------------------------------
# 3) FAIL-CLOSED RECOVERY: a kill between dispatch and receipt never silently retries a
#    not-safely-retryable effect.
# --------------------------------------------------------------------------------------
def test_kill_between_dispatch_and_receipt_never_retries_unsafe_effect():
    weft, author, db, kr = _setup()
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    step = cells.create_step(weft, author, plan_id=plan, description="charge-card")
    # Mark it NOT safely retryable, then strand it in the dispatch crash window.
    cell = Weave.fold(weft).get(step)
    content = dict(cell.content)
    content["idempotency_strategy"] = IdempotencyStrategy.NOT_SAFELY_RETRYABLE
    cells.assert_content(weft, author, step, cells.PLAN_STEP, content)
    cells.create_lease(
        weft, author, step_id=step, worker=author, issued_frontier=0, expiry=100,
        attempt=1, idempotency_key=step,
    )
    cells.set_status(weft, author, Weave.fold(weft).get(step), StepStatus.RUNNING)

    # "Kill": a fresh process over the same db reconciles the stranded effect.
    weft2 = Weft(db, kr)

    def must_not_run(_step):
        raise AssertionError("an unsafe effect was silently re-executed")

    out = reconciliation.reconcile_step(weft2, author, step, now=200)
    assert out["state"] == EffectState.UNKNOWN
    assert out["retried"] is False, "not-safely-retryable must NOT be retried"
    assert Weave.fold(weft2).get(step).content["status"] == StepStatus.UNKNOWN

    # Driving the plan afterwards must never dispatch the UNKNOWN step (fail closed): the
    # plan stalls rather than re-charging the card.
    report = supervisor.run_to_completion(weft2, author, plan, must_not_run, now=201)
    assert not report["complete"]
    # A durable UNKNOWN receipt records the unobserved outcome for a human to resolve.
    unknowns = [
        r for r in reconciliation.receipts_for_step(Weave.fold(weft2), step)
        if r.content.get("status") == StepStatus.UNKNOWN
    ]
    assert unknowns, "the ambiguous outcome is durable, not lost"


# --------------------------------------------------------------------------------------
# 4) BUDGET MONOTONICITY: an over-budget dispatch is strictly blocked, runner untouched.
# --------------------------------------------------------------------------------------
@settings(max_examples=40, deadline=None)
@given(
    budget=st.integers(min_value=0, max_value=500),
    cost=st.integers(min_value=1, max_value=500),
)
def test_over_budget_dispatch_is_strictly_blocked(budget, cost):
    weft, author, _db, _kr = _setup()
    agent = cells.create_agent(
        weft, author, objective="work", principal=author, token_budget=budget
    )
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    step = cells.create_step(
        weft, author, plan_id=plan, description="A", assigned_agent_id=agent
    )
    calls = {"n": 0}

    def runner(_step):
        calls["n"] += 1
        return {"status": StepStatus.SUCCEEDED, "token_cost": cost}

    out = budgets.guarded_dispatch_step(
        weft, author, step, runner, now=0, cost={"tokens": cost}
    )
    fits = cost <= budget
    assert out["dispatched"] is fits, "dispatch happens iff the cost fits the budget"
    assert calls["n"] == (1 if fits else 0), "runner runs iff the budget admitted it"
    if not fits:
        # The refusal is durable and keeps failing closed.
        fresh = Weave.fold(weft)
        assert fresh.get(agent).content["status"] == budgets.BUDGET_BLOCKED
        ok, _ = budgets.check_budget(fresh, agent, {"tokens": cost}, 0)
        assert ok is False


# --------------------------------------------------------------------------------------
# 5) PROJECTION DISPOSABILITY: rebuild == incremental after ARBITRARY interleavings.
# --------------------------------------------------------------------------------------
_FACTORIES = (TasksProjection, AgentsProjection, KnowledgeProjection, ActivityProjection)

# An action alphabet the interpreter can always apply against the live fold:
#   0 -> add a note   1 -> add a step   2 -> advance a step's status   3 -> add an agent
_ACTIONS = st.lists(st.integers(min_value=0, max_value=3), min_size=1, max_size=30)


def _apply_action(weft, author, plan, state, action):
    if action == 0:
        n = state["notes"]
        assert_content(weft, author, f"note:{n}", "note", {"text": f"note {n}"})
        state["notes"] += 1
    elif action == 1:
        n = state["steps"]
        sid = cells.create_step(weft, author, plan_id=plan, description=f"S{n}")
        state["step_ids"].append(sid)
        state["steps"] += 1
    elif action == 2 and state["step_ids"]:
        sid = state["step_ids"][state["cursor"] % len(state["step_ids"])]
        state["cursor"] += 1
        nxt = (StepStatus.READY, StepStatus.RUNNING, StepStatus.SUCCEEDED)[
            state["cursor"] % 3
        ]
        cells.set_status(weft, author, Weave.fold(weft).get(sid), nxt)
    elif action == 3:
        n = state["agents"]
        cells.create_agent(weft, author, objective=f"a{n}", principal=author)
        state["agents"] += 1


@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(prefix=_ACTIONS, suffix=_ACTIONS)
def test_rebuild_equals_incremental_after_arbitrary_interleavings(prefix, suffix):
    weft, author, _db, _kr = _setup()
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    state = {"notes": 0, "steps": 0, "agents": 0, "cursor": 0, "step_ids": []}

    # Apply an arbitrary PREFIX, then build the incremental projections over it.
    for act in prefix:
        _apply_action(weft, author, plan, state, act)
    incremental = ProjectionDriver(weft)
    for factory in _FACTORIES:
        incremental.register(factory())

    # Apply an arbitrary SUFFIX (the tail an incremental update must fold), then update.
    for act in suffix:
        _apply_action(weft, author, plan, state, act)
    incremental.update()

    # A brand-new driver rebuilds every projection from genesis in one shot.
    rebuilt = ProjectionDriver(weft)
    for factory in _FACTORIES:
        rebuilt.register(factory())

    for name in incremental.names():
        assert incremental.lag(name) == 0
        assert incremental.get(name).state_root() == rebuilt.get(name).state_root(), (
            f"{name}: incremental fold diverged from a clean rebuild"
        )
        assert incremental.get(name).view() == rebuilt.get(name).view()
