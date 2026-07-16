"""Effect reconciliation across the crash window (DEC-048).

Proves the load-bearing properties: a step stranded RUNNING with no terminal receipt is
reconciled to a DEFINED state — safe-to-retry effects return to READY, not-safely-retryable
effects fail closed to UNKNOWN (never a silent retry) — and a duplicate receipt does NOT
create a duplicate current state (content-addressed receipts fold to one).
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Weft
from decima.runtime import cells, reconciliation, supervisor
from decima.runtime.cells import StepStatus
from decima.runtime.reconciliation import EffectState, IdempotencyStrategy


def _setup():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(db, kr), author, db, kr


def _crashed_step(weft, author, *, strategy=None, description="A"):
    """A step in the crash window: a live lease + RUNNING status, but NO terminal receipt."""
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    extra = {"idempotency_strategy": strategy} if strategy else None
    step = cells.create_step(weft, author, plan_id=plan, description=description)
    if extra:
        cell = Weave.fold(weft).get(step)
        assert cell is not None
        content = dict(cell.content)
        content.update(extra)
        cells.assert_content(weft, author, step, cells.PLAN_STEP, content)
    cells.create_lease(
        weft,
        author,
        step_id=step,
        worker=author,
        issued_frontier=0,
        expiry=100,
        attempt=1,
        idempotency_key=step,
    )
    cells.set_status(weft, author, Weave.fold(weft).get(step), StepStatus.RUNNING)
    return plan, step


def test_running_lease_no_receipt_classifies_unknown_when_lease_lapsed():
    weft, author, _db, _kr = _setup()
    _plan, step = _crashed_step(weft, author)
    weave = Weave.fold(weft)
    # While the lease is valid it is DISPATCHED; once the frontier passes expiry, UNKNOWN.
    assert reconciliation.classify_effect(weave, step, now=0) == EffectState.DISPATCHED
    assert reconciliation.classify_effect(weave, step, now=200) == EffectState.UNKNOWN


def test_not_safely_retryable_reconciles_to_unknown_not_a_retry():
    weft, author, _db, _kr = _setup()
    _plan, step = _crashed_step(weft, author, strategy=IdempotencyStrategy.NOT_SAFELY_RETRYABLE)

    out = reconciliation.reconcile_step(weft, author, step, now=200)
    assert out["state"] == EffectState.UNKNOWN
    assert out["retried"] is False, "an unsafe effect must NOT be silently retried"
    # The ambiguity is durable: the step is UNKNOWN and an UNKNOWN receipt was recorded.
    weave = Weave.fold(weft)
    step_cell = weave.get(step)
    assert step_cell is not None
    assert step_cell.content["status"] == StepStatus.UNKNOWN
    unknowns = [
        r
        for r in reconciliation.receipts_for_step(weave, step)
        if isinstance(r, Cell) and r.content.get("status") == StepStatus.UNKNOWN
    ]
    assert unknowns, "a durable UNKNOWN receipt records the unobserved outcome"


def test_safe_to_retry_returns_step_to_ready():
    weft, author, _db, _kr = _setup()
    _plan, step = _crashed_step(weft, author, strategy=IdempotencyStrategy.IDEMPOTENCY_KEY)

    out = reconciliation.reconcile_step(weft, author, step, now=200)
    assert out["state"] == EffectState.RECONCILING
    assert out["retried"] is True
    step_cell = Weave.fold(weft).get(step)
    assert step_cell is not None
    assert step_cell.content["status"] == StepStatus.READY


def test_already_succeeded_converges_step_not_retry():
    weft, author, _db, _kr = _setup()
    _plan, step = _crashed_step(weft, author, strategy=IdempotencyStrategy.NOT_SAFELY_RETRYABLE)
    # A SUCCEEDED receipt lands (the effect DID happen) before reconciliation runs.
    cells.record_receipt(
        weft,
        author,
        step_id=step,
        lease_id="lease-done",
        idempotency_key=step,
        status=StepStatus.SUCCEEDED,
    )
    out = reconciliation.reconcile_step(weft, author, step, now=200)
    assert out["state"] == EffectState.SUCCEEDED
    assert out["action"] == "already-succeeded"
    step_cell = Weave.fold(weft).get(step)
    assert step_cell is not None
    assert step_cell.content["status"] == StepStatus.SUCCEEDED


def test_duplicate_receipt_does_not_duplicate_current_state():
    weft, author, _db, _kr = _setup()
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    step = cells.create_step(weft, author, plan_id=plan, description="A")

    def runner(_step):
        return {"status": StepStatus.SUCCEEDED}

    supervisor.dispatch_step(weft, author, Weave.fold(weft), step, runner, now=0)
    lease = Weave.fold(weft).of_type(cells.LEASE)[0].id
    before = reconciliation.receipts_for_step(Weave.fold(weft), step)
    assert len(before) == 1

    # Record the SAME outcome again (same step/lease/idempotency-key) — a duplicate.
    cells.record_receipt(
        weft,
        author,
        step_id=step,
        lease_id=lease,
        idempotency_key=step,
        status=StepStatus.SUCCEEDED,
    )
    after = reconciliation.receipts_for_step(Weave.fold(weft), step)
    assert len(after) == 1, "a duplicate receipt folds to ONE current state (idempotence)"
    assert reconciliation.classify_effect(Weave.fold(weft), step, now=0) == EffectState.SUCCEEDED
