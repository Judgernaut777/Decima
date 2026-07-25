"""The full EffectReceipt: multi-attempt reconciliation, COMPENSATED/CANCELLED, cost (T2.1).

Proves the WEFT §8 properties the runtime now carries, each one a thing that would silently
lie if it regressed:

  * §8.1 immutability — progress is a NEW receipt at a later ``(attempt, observation)``, so an
    attempt's history (UNKNOWN → an observed SUCCEEDED) is an append-log, never an edit; and
    re-delivering the SAME observation still folds to ONE cell (a flaky worker cannot inflate
    history, nor a duplicate double-charge).
  * §8.5 multi-attempt — a retry after UNKNOWN reuses the idempotency key and runs as a NEW
    attempt whose number is strictly higher; both attempts stay on the log with their costs.
  * §8.2 state machine — CANCELLED and COMPENSATED are reachable exactly where the table says
    and nowhere else; an out-of-table transition becomes a durable error Cell, never state.
  * §8.3 UNKNOWN — no edge invents a terminal outcome: a resting UNKNOWN is not re-dispatched
    unless a reconciler authorized it, and IRREVERSIBLE/FINANCIAL/COMMUNICATION effects are
    never auto-retried whatever their idempotency strategy claims.
  * §8.1 cost — every amount is an INT in signed content (a float is refused at the seam),
    costs sum across attempts, and the budget ledger folds from them.
"""

from __future__ import annotations

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import budgets, cells, reconciliation, scheduler, supervisor
from decima.runtime.cells import ReceiptStatus, StepStatus
from decima.runtime.reconciliation import EffectState, IdempotencyStrategy


@pytest.fixture()
def env(tmp_path):
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(str(tmp_path / "weft.db"), kr), author


def _plan_with_step(weft, author, **step_kwargs):
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    step = cells.create_step(weft, author, plan_id=plan, description="A", **step_kwargs)
    return plan, step


def _strand(weft, author, step, *, extra=None):
    """Put a step in the dispatch crash window: a lapsed lease + RUNNING, no terminal receipt."""
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


# ── §8.1 cost: integers only, canonical, and folded ──────────────────────────
def test_cost_is_normalized_to_sorted_summed_integer_lines():
    lines = cells.normalize_cost(
        [
            cells.CostItem(cells.COST_TOKENS, 30),
            cells.CostItem(cells.COST_TOKENS, 12),
            {"resource": cells.COST_MONETARY, "amount": 4, "unit": "microcents"},
        ]
    )
    assert lines == [
        {"resource": "monetary", "amount": 4, "unit": "microcents", "provider_ref": None},
        {"resource": "tokens", "amount": 42, "unit": "tokens", "provider_ref": None},
    ], "duplicate resources SUM and the list is sorted → byte-identical content"
    # The same spend expressed differently normalizes identically (same receipt id).
    assert cells.normalize_cost({cells.COST_TOKENS: 42}) == cells.normalize_cost(
        [cells.CostItem(cells.COST_TOKENS, 40), cells.CostItem(cells.COST_TOKENS, 2)]
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"tokens": 1.5},
        # Deliberately ill-typed: the point is that the RUNTIME guard refuses a float even
        # when a caller bypasses the (int-typed) constructor contract, so the annotation is
        # not the only thing standing between a float and signed content.
        [cells.CostItem("tokens", 2.0)],  # type: ignore[arg-type]
        [{"resource": "tokens", "amount": 0.1}],
        {"tokens": True},
    ],
)
def test_a_float_or_bool_cost_is_refused_never_encoded(bad):
    """Determinism is load-bearing: costs ride in signed, hashed content, so a float must
    fail LOUD at the seam — canonical CBOR would happily encode one."""
    with pytest.raises(TypeError):
        cells.normalize_cost(bad)


def test_no_float_reaches_signed_receipt_content(env):
    weft, author = env
    _plan, step = _plan_with_step(weft, author)

    def runner(_step):
        return {"status": StepStatus.SUCCEEDED, "token_cost": 7, "monetary_cost": 3}

    supervisor.dispatch_step(weft, author, Weave.fold(weft), step, runner, now=0)
    receipt = cells.receipts_of_step(Weave.fold(weft), step)[-1]
    assert cells.receipt_cost(receipt) == {"tokens": 7, "monetary": 3}
    for line in receipt.content["cost"]:
        assert isinstance(line["amount"], int) and not isinstance(line["amount"], bool)
    assert not _floats(receipt.content), "no float anywhere in a receipt's signed content"


def _floats(value) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_floats(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_floats(v) for v in value)
    return False


def test_legacy_diagnostics_cost_still_folds(env):
    """An append-only log keeps pre-T2.1 receipts forever, so their cost must keep counting."""
    weft, author = env
    _plan, step = _plan_with_step(weft, author)
    cells.record_receipt(
        weft,
        author,
        step_id=step,
        lease_id="lease-legacy",
        idempotency_key=step,
        status=ReceiptStatus.SUCCEEDED,
        diagnostics={"token_cost": 11, "monetary_cost": 2},
    )
    receipt = cells.receipts_of_step(Weave.fold(weft), step)[-1]
    assert cells.receipt_cost(receipt) == {"tokens": 11, "monetary": 2}


# ── §8.5 multi-attempt reconciliation ────────────────────────────────────────
def test_retry_after_unknown_is_a_new_attempt_under_the_same_idempotency_key(env):
    weft, author = env
    plan, step = _plan_with_step(weft, author)
    _strand(weft, author, step, extra={"idempotency_strategy": IdempotencyStrategy.IDEMPOTENCY_KEY})

    out = reconciliation.reconcile_step(weft, author, step, now=200)
    assert out["action"] == "safe-to-retry" and out["retried"] is True
    # §8.4: the stranded attempt got its OWN durable UNKNOWN receipt before any retry.
    stranded = Weave.fold(weft).get(out["receipt"])
    assert stranded is not None
    assert stranded.content["status"] == ReceiptStatus.UNKNOWN
    assert stranded.content["retry_authorized"] is True

    def runner(_step):
        return {"status": StepStatus.SUCCEEDED, "token_cost": 5}

    again = supervisor.dispatch_step(weft, author, Weave.fold(weft), step, runner, now=201)
    assert again["attempt"] > stranded.content["attempt"], "a retry is a NEW attempt (§8.5)"

    weave = Weave.fold(weft)
    history = reconciliation.receipt_history(weave, step)
    assert [fold.status for fold in history] == [ReceiptStatus.UNKNOWN, ReceiptStatus.SUCCEEDED]
    assert [fold.attempt for fold in history] == sorted({f.attempt for f in history})
    # The stranded attempt is NOT overwritten — the whole history is on the log …
    assert len(cells.receipts_for_idempotency_key(weave, step)) == 2
    # … and the same key is reused, so the provider dedupes the physical retry.
    assert {r.content["idempotency_key"] for r in cells.receipts_of_step(weave, step)} == {step}
    assert reconciliation.effect_status(weave, step) == ReceiptStatus.SUCCEEDED
    assert scheduler.plan_is_complete(weave, plan)


def test_resting_unknown_is_not_redispatched_without_authorization(env):
    """§8.3: UNKNOWN is resting, not terminal — and nothing may assume its way out of it."""
    weft, author = env
    _plan, step = _plan_with_step(weft, author)
    cells.set_status(weft, author, Weave.fold(weft).get(step), StepStatus.RUNNING)
    cells.record_receipt(
        weft,
        author,
        step_id=step,
        lease_id="lease-1",
        idempotency_key=step,
        status=ReceiptStatus.UNKNOWN,
        attempt=1,
    )

    def must_not_run(_step):
        raise AssertionError("an unresolved UNKNOWN effect was re-executed")

    out = supervisor.dispatch_step(weft, author, Weave.fold(weft), step, must_not_run, now=5)
    assert out["status"] == ReceiptStatus.UNKNOWN and "refused" in out
    # An UNKNOWN receipt is NOT a final receipt, so "is this finished" answers honestly.
    weave = Weave.fold(weft)
    assert cells.final_receipt_for_idempotency_key(weave, step) is None
    assert cells.receipt_for_idempotency_key(weave, step) is not None
    # An explicit operator override DOES reopen it — as a new attempt, never a rewrite.
    ran = supervisor.dispatch_step(
        weft,
        author,
        weave,
        step,
        lambda _s: {"status": StepStatus.SUCCEEDED},
        now=6,
        retry_unknown=True,
    )
    assert ran["attempt"] == 2


def test_irreversible_effect_class_is_never_auto_retried(env):
    """§8.5: for IRREVERSIBLE/FINANCIAL/COMMUNICATION the effect class overrides the
    idempotency strategy — fabricating or double-firing here does real-world harm."""
    weft, author = env
    plan, step = _plan_with_step(weft, author)
    _strand(
        weft,
        author,
        step,
        extra={
            "idempotency_strategy": IdempotencyStrategy.IDEMPOTENCY_KEY,
            "effect_class": "FINANCIAL",
        },
    )
    out = reconciliation.reconcile_step(weft, author, step, now=200)
    assert out["retried"] is False and out["state"] == EffectState.UNKNOWN
    step_cell = Weave.fold(weft).get(step)
    assert step_cell is not None
    assert step_cell.content["status"] == StepStatus.UNKNOWN
    assert not scheduler.plan_is_complete(Weave.fold(weft), plan), "an UNKNOWN blocks the plan"


def test_observe_outcome_resolves_unknown_by_observation_only(env):
    weft, author = env
    _plan, step = _plan_with_step(weft, author)
    _strand(weft, author, step, extra={"idempotency_strategy": IdempotencyStrategy.WRITE_ONCE})
    cells.set_status(weft, author, Weave.fold(weft).get(step), StepStatus.RUNNING)
    reconciliation.record_transition(weft, author, step, status=ReceiptStatus.UNKNOWN, now=10)

    # Still indeterminate: UNKNOWN → UNKNOWN appends a new observation, resolving nothing.
    still = reconciliation.observe_outcome(
        weft, author, step, observed=ReceiptStatus.UNKNOWN, now=11
    )
    assert still["recorded"] is True and still["observation"] == 1
    assert reconciliation.effect_status(Weave.fold(weft), step) == ReceiptStatus.UNKNOWN

    # Then the provider is observed: UNKNOWN → SUCCEEDED, with the provider ref + its cost.
    done = reconciliation.observe_outcome(
        weft,
        author,
        step,
        observed=ReceiptStatus.SUCCEEDED,
        now=12,
        provider_ref="charge-77",
        cost={cells.COST_MONETARY: 250},
    )
    assert done["recorded"] is True
    weave = Weave.fold(weft)
    assert reconciliation.effect_status(weave, step) == ReceiptStatus.SUCCEEDED
    assert reconciliation.cost_of_step(weave, step) == {"monetary": 250}
    latest = weave.get(done["receipt"])
    assert latest is not None and latest.content["provider_ref"] == "charge-77"
    step_cell = weave.get(step)
    assert step_cell is not None
    assert step_cell.content["status"] == StepStatus.SUCCEEDED
    # Every observation stays on the log — the UNKNOWNs were not edited away.
    assert [r.content["status"] for r in cells.receipts_of_step(weave, step)] == [
        ReceiptStatus.UNKNOWN,
        ReceiptStatus.UNKNOWN,
        ReceiptStatus.SUCCEEDED,
    ]
    with pytest.raises(ValueError):
        reconciliation.observe_outcome(weft, author, step, observed="CANCELLED", now=13)


# ── §8.2 CANCELLED ───────────────────────────────────────────────────────────
def test_cancel_before_submission_records_cancelled(env):
    weft, author = env
    plan, step = _plan_with_step(weft, author)
    out = reconciliation.cancel_effect(weft, author, step, now=3, reason="operator withdrew")
    assert out["recorded"] is True
    weave = Weave.fold(weft)
    assert reconciliation.effect_status(weave, step) == ReceiptStatus.CANCELLED
    assert reconciliation.classify_effect(weave, step, now=3) == EffectState.CANCELLED
    step_cell = weave.get(step)
    assert step_cell is not None
    assert step_cell.content["status"] == StepStatus.CANCELLED
    assert scheduler.plan_is_complete(weave, plan), "CANCELLED is terminal for the scheduler"


def test_cancel_of_an_in_flight_effect_needs_provider_acknowledgement(env):
    """§8.3: after submission "I cancelled it" is a claim about the outside world. Without
    acknowledgement the honest answer is UNKNOWN, so the cancel is refused and NOTHING is
    written — no fabricated CANCELLED."""
    weft, author = env
    _plan, step = _plan_with_step(weft, author)
    _strand(weft, author, step)

    refused = reconciliation.cancel_effect(weft, author, step, now=200)
    assert refused["recorded"] is False
    assert "acknowledgement" in refused["reason"]
    assert cells.receipts_of_step(Weave.fold(weft), step) == []

    acked = reconciliation.cancel_effect(weft, author, step, now=201, provider_acknowledged=True)
    assert acked["recorded"] is True
    assert reconciliation.effect_status(Weave.fold(weft), step) == ReceiptStatus.CANCELLED


# ── §8.2 COMPENSATED ─────────────────────────────────────────────────────────
def test_compensating_a_succeeded_effect_records_the_link_and_its_cost(env):
    weft, author = env
    plan, step = _plan_with_step(weft, author)
    supervisor.dispatch_step(
        weft,
        author,
        Weave.fold(weft),
        step,
        lambda _s: {"status": StepStatus.SUCCEEDED, "token_cost": 9},
        now=0,
    )
    undo = cells.create_step(weft, author, plan_id=plan, description="undo A")

    out = reconciliation.compensate_effect(
        weft,
        author,
        step,
        compensation_step_id=undo,
        now=7,
        cost={cells.COST_MONETARY: 5},
    )
    assert out["recorded"] is True and out["from"] == ReceiptStatus.SUCCEEDED
    weave = Weave.fold(weft)
    receipt = weave.get(out["receipt"])
    assert receipt is not None
    assert receipt.content["compensates"] == undo, "the compensation link is on the Log"
    assert reconciliation.effect_status(weave, step) == ReceiptStatus.COMPENSATED
    assert reconciliation.classify_effect(weave, step, now=7) == EffectState.COMPENSATED
    step_cell = weave.get(step)
    assert step_cell is not None
    assert step_cell.content["status"] == StepStatus.COMPENSATED
    # The original effect's cost is NOT erased; the compensation adds its own.
    assert reconciliation.cost_of_step(weave, step) == {"tokens": 9, "monetary": 5}
    assert reconciliation.cost_of_plan(weave, plan) == {"tokens": 9, "monetary": 5}


def test_compensating_a_failed_effect_is_refused_as_a_durable_error(env):
    weft, author = env
    _plan, step = _plan_with_step(weft, author)
    supervisor.dispatch_step(
        weft, author, Weave.fold(weft), step, lambda _s: {"status": StepStatus.FAILED}, now=0
    )
    out = reconciliation.compensate_effect(weft, author, step, compensation_step_id="x", now=4)
    assert out["recorded"] is False, "you cannot reverse an effect that never took effect"
    weave = Weave.fold(weft)
    error_cell = weave.get(out["error_cell"])
    assert error_cell is not None
    assert error_cell.type == reconciliation.EFFECT_TRANSITION_ERROR
    assert error_cell.content["to_status"] == ReceiptStatus.COMPENSATED
    assert reconciliation.effect_status(weave, step) == ReceiptStatus.FAILED, "state unchanged"


# ── §8.2 the reducer rejects out-of-table transitions ────────────────────────
def test_an_out_of_table_receipt_is_surfaced_and_never_applied(env):
    """A raw receipt writer (or a forged one) cannot move the machine somewhere the table
    forbids: SUCCEEDED → CANCELLED is rejected by the fold and reported, never state."""
    weft, author = env
    _plan, step = _plan_with_step(weft, author)
    supervisor.dispatch_step(
        weft, author, Weave.fold(weft), step, lambda _s: {"status": StepStatus.SUCCEEDED}, now=0
    )
    cells.record_receipt(
        weft,
        author,
        step_id=step,
        lease_id="lease-forged",
        idempotency_key=step,
        status=ReceiptStatus.CANCELLED,
        attempt=1,
        observation=9,
    )
    weave = Weave.fold(weft)
    assert reconciliation.effect_status(weave, step) == ReceiptStatus.SUCCEEDED
    bad = reconciliation.invalid_transitions(weave, step)
    assert len(bad) == 1 and bad[0]["to"] == ReceiptStatus.CANCELLED
    assert not reconciliation.may_transition(ReceiptStatus.SUCCEEDED, ReceiptStatus.CANCELLED)
    # And no edge fabricates a terminal outcome out of UNKNOWN without an observation.
    assert not reconciliation.may_transition(None, ReceiptStatus.SUCCEEDED)
    assert reconciliation.TRANSITIONS[ReceiptStatus.UNKNOWN] == frozenset(
        {ReceiptStatus.SUCCEEDED, ReceiptStatus.FAILED, ReceiptStatus.UNKNOWN}
    )
    with pytest.raises(ValueError):
        cells.record_receipt(
            weft,
            author,
            step_id=step,
            lease_id="l",
            idempotency_key=step,
            status="TOTALLY_FINE",
        )


def test_a_duplicate_observation_still_folds_to_one_receipt(env):
    """§8.1 idempotence survives the richer id: the SAME observation re-delivered lands on the
    SAME cell, so a flaky executor cannot inflate history or double-count cost."""
    weft, author = env
    _plan, step = _plan_with_step(weft, author)
    supervisor.dispatch_step(
        weft,
        author,
        Weave.fold(weft),
        step,
        lambda _s: {"status": StepStatus.SUCCEEDED, "token_cost": 4},
        now=0,
    )
    lease = Weave.fold(weft).of_type(cells.LEASE)[0].id
    for _ in range(4):
        cells.record_receipt(
            weft,
            author,
            step_id=step,
            lease_id=lease,
            idempotency_key=step,
            status=ReceiptStatus.SUCCEEDED,
            cost={cells.COST_TOKENS: 4},
        )
    weave = Weave.fold(weft)
    assert len(cells.receipts_of_step(weave, step)) == 1
    assert reconciliation.cost_of_step(weave, step) == {"tokens": 4}


def test_receipt_history_is_deterministic_across_refolds(env):
    weft, author = env
    plan, step = _plan_with_step(weft, author)
    _strand(weft, author, step, extra={"idempotency_strategy": IdempotencyStrategy.IDEMPOTENCY_KEY})
    reconciliation.reconcile_step(weft, author, step, now=200)
    supervisor.run_to_completion(
        weft, author, plan, lambda _s: {"status": StepStatus.SUCCEEDED, "token_cost": 2}, now=201
    )
    first, second = Weave.fold(weft), Weave.fold(weft)
    assert first.state_root() == second.state_root(), "same events ⇒ same state_root"
    assert reconciliation.receipt_history(first, step) == reconciliation.receipt_history(
        second, step
    )
    assert [r.id for r in cells.receipts_of_step(first, step)] == [
        r.id for r in cells.receipts_of_step(second, step)
    ]


# ── cost feeds the budget ledger ─────────────────────────────────────────────
def test_multi_attempt_cost_accumulates_in_the_budget_ledger(env):
    """A retry is not free: each attempt's receipt spends against the agent's budget."""
    weft, author = env
    agent = cells.create_agent(weft, author, objective="work", principal=author, token_budget=100)
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    step = cells.create_step(weft, author, plan_id=plan, description="A", assigned_agent_id=agent)
    _strand(weft, author, step, extra={"idempotency_strategy": IdempotencyStrategy.IDEMPOTENCY_KEY})
    # Attempt 1 ends UNKNOWN but still cost 10 tokens; the reconciler authorizes a retry.
    cells.record_receipt(
        weft,
        author,
        step_id=step,
        lease_id="lease-1",
        idempotency_key=step,
        status=ReceiptStatus.UNKNOWN,
        attempt=1,
        retry_authorized=True,
        cost={cells.COST_TOKENS: 10},
    )
    supervisor.dispatch_step(
        weft,
        author,
        Weave.fold(weft),
        step,
        lambda _s: {"status": StepStatus.SUCCEEDED, "token_cost": 15},
        now=201,
    )
    weave = Weave.fold(weft)
    assert budgets.spend_ledger(weave, agent).tokens == 25
    assert reconciliation.cost_of_step(weave, step) == {"tokens": 25}
    ok, reason = budgets.check_budget(weave, agent, {"tokens": 80}, 0)
    assert ok is False and "token budget" in reason
