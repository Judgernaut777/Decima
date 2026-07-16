"""E2E scenario E — CRASH RECOVERY on the durable stack.

An independent end-to-end drive of the integrated kernel + runtime + projections. A
two-step plan A->B runs A to a terminal receipt; B enters the dispatch crash window (a
live lease + RUNNING status, no terminal receipt); the process is DROPPED (a fresh Weft
folds the SAME db); on restart B is classified from its lease+receipt, reconciled, and
the plan resumes — WITHOUT ever repeating A. The interruption is durably visible in the
activity read-model rebuilt from the restarted Weft.

Load-bearing property: recovery derives entirely from the Weft (leases + receipts).
Nothing about "how far we got" lives in process memory, so a fresh process resumes at
exactly the un-terminal work and never re-executes a step that already has a receipt.
"""

from __future__ import annotations

import os
import tempfile
from typing import cast

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.projections.activity import ActivityProjection
from decima.projections.engine import ProjectionDriver
from decima.runtime import cells, reconciliation, scheduler, supervisor
from decima.runtime.cells import StepStatus
from decima.runtime.reconciliation import EffectState, IdempotencyStrategy


def _setup() -> tuple[Weft, str, str, Keyring]:
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(db, kr), author, db, kr


def _set_strategy(weft: Weft, author: str, step: str, strategy: str) -> None:
    cell = Weave.fold(weft).get(step)
    assert cell is not None
    content = dict(cell.content)
    content["idempotency_strategy"] = strategy
    cells.assert_content(weft, author, step, cells.PLAN_STEP, content)


def test_crash_recovery_resumes_from_the_weft_without_repeating_completed_work():
    weft, author, db, kr = _setup()
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    a = cells.create_step(weft, author, plan_id=plan, description="A")
    b = cells.create_step(weft, author, plan_id=plan, description="B", dependency_ids=[a])

    ran: list[str] = []

    def runner(step):
        ran.append(step.description)
        return {"status": StepStatus.SUCCEEDED}

    # --- pre-crash: complete step 1 (A), then BEGIN step 2 (B) mid-flight. -----------
    supervisor.run_once(weft, author, plan, runner, now=0)  # dispatches + completes A
    assert ran == ["A"]
    assert cells.receipt_for_idempotency_key(Weave.fold(weft), a) is not None

    # "Begin B": it is safe-to-retry, gets a live lease and RUNNING status, but the
    # process dies before any terminal receipt lands — the classic dispatch crash window.
    _set_strategy(weft, author, b, IdempotencyStrategy.IDEMPOTENCY_KEY)
    cells.create_lease(
        weft,
        author,
        step_id=b,
        worker=author,
        issued_frontier=0,
        expiry=100,
        attempt=1,
        idempotency_key=b,
    )
    cells.set_status(weft, author, Weave.fold(weft).get(b), StepStatus.RUNNING)
    boundary = weft.count()  # every event after this is post-crash recovery work
    assert not scheduler.plan_is_complete(Weave.fold(weft), plan)

    # --- DROP THE PROCESS: a brand-new Weft folds the same durable db. ---------------
    weft2 = Weft(db, kr)
    ran.clear()

    # B is classified purely from its folded lease+receipt. The lease has lapsed (the
    # frontier passed its expiry) and there is no terminal receipt -> reconcile it.
    assert reconciliation.classify_effect(Weave.fold(weft2), b, now=200) == EffectState.UNKNOWN
    out = reconciliation.reconcile_step(weft2, author, b, now=200)
    assert out["state"] == EffectState.RECONCILING
    assert out["retried"] is True, "a safe-to-retry stranded effect returns to READY"
    b_cell = Weave.fold(weft2).get(b)
    assert b_cell is not None
    assert b_cell.content["status"] == StepStatus.READY

    # Resume driving. A already has a receipt -> NEVER re-run; only B executes.
    report = supervisor.run_to_completion(weft2, author, plan, runner, now=201)
    assert report["complete"]
    assert ran == ["B"], "recovery resumes at B and must not repeat A"
    assert scheduler.plan_is_complete(Weave.fold(weft2), plan)

    # --- the interruption is durably visible in the activity read-model. -------------
    driver = ProjectionDriver(weft2)
    driver.register(ActivityProjection())
    activity = cast(ActivityProjection, driver.get("activity"))
    post_crash_b = [
        e for e in activity.timeline() if e.cell == b and e.seq is not None and e.seq > boundary
    ]
    assert post_crash_b, "the recovery of B must appear in the activity timeline"
    # And a rebuilt-from-scratch activity feed agrees byte-for-byte (Law 5: disposable).
    assert activity.state_root() == driver.rebuild("activity").state_root
