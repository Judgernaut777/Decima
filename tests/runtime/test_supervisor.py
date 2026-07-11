"""The durable supervisor: dispatch under lease, idempotency, crash recovery (DEC-044/045/048).

Drives a real plan to completion through a deterministic injected runner (no live model /
worker), and proves the two properties that make it trustworthy: re-dispatch of a
succeeded step executes no effect (idempotency by receipt), and a crash mid-run is
recoverable from the Weft (a fresh process resumes without repeating completed work).
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells, scheduler, supervisor
from decima.runtime.cells import StepStatus


def _setup():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(db, kr), author, db, kr


def _linear_plan(weft, author):
    """A → B → C, a linear dependency chain."""
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    a = cells.create_step(weft, author, plan_id=plan, description="A")
    b = cells.create_step(weft, author, plan_id=plan, description="B", dependency_ids=[a])
    c = cells.create_step(weft, author, plan_id=plan, description="C", dependency_ids=[b])
    return plan, a, b, c


def test_run_to_completion_executes_every_step_once():
    weft, author, _db, _kr = _setup()
    plan, a, b, c = _linear_plan(weft, author)

    ran: list[str] = []

    def runner(step):
        ran.append(step.description)
        return {"status": StepStatus.SUCCEEDED}

    report = supervisor.run_to_completion(weft, author, plan, runner)
    assert report["complete"]
    assert ran == ["A", "B", "C"], "steps must run in dependency order, each exactly once"
    assert scheduler.plan_is_complete(Weave.fold(weft), plan)


def test_redispatch_is_idempotent_no_second_effect():
    weft, author, _db, _kr = _setup()
    plan, a, _b, _c = _linear_plan(weft, author)

    calls = {"n": 0}

    def runner(step):
        calls["n"] += 1
        return {"status": StepStatus.SUCCEEDED}

    # dispatch A once
    supervisor.dispatch_step(weft, author, Weave.fold(weft), a, runner, now=0)
    assert calls["n"] == 1
    # dispatch A AGAIN — a terminal receipt exists for its idempotency key → no re-run
    out = supervisor.dispatch_step(weft, author, Weave.fold(weft), a, runner, now=1)
    assert calls["n"] == 1, "a succeeded step must not re-execute its effect"
    assert out["idempotent_hit"] is True


def test_failed_runner_marks_step_failed_not_crash():
    weft, author, _db, _kr = _setup()
    plan, a, _b, _c = _linear_plan(weft, author)

    def boom(step):
        raise RuntimeError("effect blew up")

    out = supervisor.dispatch_step(weft, author, Weave.fold(weft), a, boom, now=0)
    assert out["status"] == StepStatus.FAILED
    assert Weave.fold(weft).get(a).content["status"] == StepStatus.FAILED
    # a receipt was still recorded (failure is durable, not silent)
    assert cells.receipt_for_idempotency_key(Weave.fold(weft), a) is not None


def test_crash_recovery_resumes_without_repeating_completed_work():
    weft, author, db, kr = _setup()
    plan, a, b, c = _linear_plan(weft, author)

    ran: list[str] = []

    def runner(step):
        ran.append(step.description)
        return {"status": StepStatus.SUCCEEDED}

    # Partial run: complete A and B, then "crash" (stop driving).
    supervisor.run_once(weft, author, plan, runner, now=0)  # dispatches A
    supervisor.run_once(weft, author, plan, runner, now=1)  # dispatches B
    assert ran == ["A", "B"]
    assert not scheduler.plan_is_complete(Weave.fold(weft), plan)

    # "restart": fresh Weft over the SAME db; resume driving. A and B must NOT re-run.
    weft2 = Weft(db, kr)
    ran.clear()
    report = supervisor.run_to_completion(weft2, author, plan, runner, now=2)
    assert report["complete"]
    assert ran == ["C"], "recovery must resume at C, never repeat A or B"


def test_a_failed_dependency_stalls_the_plan_without_crashing():
    weft, author, _db, _kr = _setup()
    plan, a, b, c = _linear_plan(weft, author)

    def runner(step):
        # A fails; B and C depend on it and can never become ready.
        return {"status": StepStatus.FAILED if step.description == "A" else StepStatus.SUCCEEDED}

    report = supervisor.run_to_completion(weft, author, plan, runner)
    assert not report["complete"]
    assert report["stalled"] is True
    assert Weave.fold(weft).get(a).content["status"] == StepStatus.FAILED
    assert Weave.fold(weft).get(b).content["status"] in (StepStatus.BLOCKED, StepStatus.PENDING)
