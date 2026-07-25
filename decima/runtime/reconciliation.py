"""Effect reconciliation across the crash window (DEC-048).

An effect has a lifecycle, and a crash can strand it half-way. This module names that
lifecycle as an explicit state machine and gives the supervisor a way to recover a step
whose lease says RUNNING but which never produced a terminal receipt — the "crash window"
between "I marked myself running / dispatched the effect" and "I recorded the outcome".

The reconciler's whole job is to answer, for such a step, ONE question: did the external
effect happen? It cannot always know. So it classifies from the durable evidence
(receipts + the lease) into one of three answers:

  * already-succeeded — a SUCCEEDED receipt exists → converge the step to SUCCEEDED.
  * safe-to-retry     — the effect's idempotency strategy makes a re-dispatch harmless →
                        return the step to READY so the supervisor runs it again.
  * UNKNOWN           — the strategy is NOT safely retryable and the interruption is
                        ambiguous → DO NOT retry; record a durable UNKNOWN receipt and put
                        the step in UNKNOWN for a human/compensation, never a silent retry.

Idempotency strategy is declared PER EFFECT (on the step, defaulting to idempotency-key —
the runtime already keys receipts by an idempotency key). "Not safely retryable" is the
one strategy that must fail to UNKNOWN rather than gamble on a double-effect.

Duplicate receipts are idempotent by construction: a receipt cell is content-addressed by
(step, lease, idempotency-key, attempt, status, observation), so re-recording the SAME
observation lands on the SAME cell — the fold's last-writer-wins yields one current state,
never a duplicate — while a genuinely new observation is a new, immutable cell.

That last point is what makes the MULTI-ATTEMPT history explicit and folded (WEFT §8.1/§8.5):
a receipt is never edited, so an attempt's progress (RUNNING → UNKNOWN → an observed
SUCCEEDED) is an append-log of immutable receipts, and :func:`receipt_history` reduces that
log per attempt through the §8.2 state machine. Out-of-table transitions are REJECTED as a
durable ``effect_transition_error`` Cell rather than silently applied, and the terminal
statuses the protocol requires are all representable here: ``CANCELLED`` (withdrawn before
an irreversible effect — :func:`cancel_effect`) and ``COMPENSATED`` (a SUCCEEDED effect
reversed by a compensating invocation — :func:`compensate_effect`), plus §8.6's
observe-don't-decide resolution of UNKNOWN (:func:`observe_outcome`). Every receipt carries
integer-only cost lines (§8.1 ``CostItem``); :func:`cost_of_step` sums them across attempts,
because each physical try spends real resources — a retry is not free.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from decima.kernel import hashing
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.runtime.cells import ReceiptStatus, StepStatus


class EffectState:
    """The lifecycle of a single dispatched effect (WEFT §8 / DEC-048)."""

    PROPOSED = "PROPOSED"  # a step exists but nothing has been authorized yet
    AUTHORIZED = "AUTHORIZED"  # ready to run; authority + lease may be minted
    DISPATCHED = "DISPATCHED"  # running under a live lease; outcome not yet recorded
    SUCCEEDED = "SUCCEEDED"  # terminal: a SUCCEEDED receipt exists
    FAILED = "FAILED"  # terminal: a FAILED receipt exists
    UNKNOWN = "UNKNOWN"  # interrupted ambiguously; outcome unobserved
    RECONCILING = "RECONCILING"  # under active recovery (being re-driven / classified)
    SUPERSEDED = "SUPERSEDED"  # cancelled/replaced; no longer the live effect
    COMPENSATED = "COMPENSATED"  # a compensating effect has undone it
    CANCELLED = "CANCELLED"  # withdrawn before any irreversible effect (§8.2)

    TERMINAL = frozenset({SUCCEEDED, FAILED, COMPENSATED, CANCELLED})


class IdempotencyStrategy:
    """How safe it is to re-dispatch an effect after an ambiguous interruption."""

    NATURALLY_IDEMPOTENT = "naturally-idempotent"  # re-running has no additional effect
    IDEMPOTENCY_KEY = "idempotency-key"  # the sink dedups by a client key
    READ_BEFORE_WRITE = "read-before-write"  # re-check state, then write iff absent
    WRITE_ONCE = "write-once"  # a guard rejects a second write
    NOT_SAFELY_RETRYABLE = "not-safely-retryable"  # no dedup; a retry may double-apply


# Every strategy EXCEPT `not-safely-retryable` carries a dedup guarantee that makes a
# re-dispatch safe. Only the unguarded one must fail closed to UNKNOWN.
_SAFE_TO_RETRY = frozenset(
    {
        IdempotencyStrategy.NATURALLY_IDEMPOTENT,
        IdempotencyStrategy.IDEMPOTENCY_KEY,
        IdempotencyStrategy.READ_BEFORE_WRITE,
        IdempotencyStrategy.WRITE_ONCE,
    }
)


# ── the WEFT §8.2 status state machine ────────────────────────────────────────
# Each transition is a NEW receipt assertion; anything not in this table is an error, never
# silent state. Note what is deliberately ABSENT: no edge invents a terminal outcome from
# UNKNOWN (§8.3) — only an OBSERVATION (§8.6) may resolve it.
TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({ReceiptStatus.ACCEPTED, ReceiptStatus.RUNNING}),
    ReceiptStatus.ACCEPTED: frozenset(
        {ReceiptStatus.RUNNING, ReceiptStatus.CANCELLED, ReceiptStatus.FAILED}
    ),
    ReceiptStatus.RUNNING: frozenset(
        {
            ReceiptStatus.SUCCEEDED,
            ReceiptStatus.FAILED,
            ReceiptStatus.UNKNOWN,
            ReceiptStatus.CANCELLED,
        }
    ),
    ReceiptStatus.UNKNOWN: frozenset(
        {ReceiptStatus.SUCCEEDED, ReceiptStatus.FAILED, ReceiptStatus.UNKNOWN}
    ),
    ReceiptStatus.SUCCEEDED: frozenset({ReceiptStatus.COMPENSATED}),
    ReceiptStatus.FAILED: frozenset(),
    ReceiptStatus.CANCELLED: frozenset(),
    ReceiptStatus.COMPENSATED: frozenset(),
}

# The shipping runtime records "I am about to submit" as a durable RUNNING status on the
# STEP cell (supervisor.dispatch_step marks it BEFORE invoking the effect) instead of a
# separate RUNNING receipt — one event on the hot path instead of two. So an attempt whose
# first receipt is already an outcome is folded as if that implicit RUNNING receipt were
# there. Naming the deviation keeps the §8.2 table honest: the only added edges are the ones
# the step's own durable RUNNING marker justifies — never `(INVOKE) → COMPENSATED`.
IMPLICIT_RUNNING = frozenset(
    {
        ReceiptStatus.SUCCEEDED,
        ReceiptStatus.FAILED,
        ReceiptStatus.UNKNOWN,
        ReceiptStatus.CANCELLED,
    }
)

# §8.5: for these effect classes an UNKNOWN may NOT be auto-retried or auto-failed no
# matter what the idempotency strategy or `error.retryable` claims — this is where
# fabricating an outcome would do real-world harm, so resolution needs an observation or a
# human. Compared case-insensitively against the step's declared effect class.
NO_AUTO_RESOLVE = frozenset({"IRREVERSIBLE", "FINANCIAL", "COMMUNICATION"})

# A refused transition is recorded as this cell type — loud, queryable, never silent.
EFFECT_TRANSITION_ERROR = "effect_transition_error"


def may_transition(frm: str | None, to: str) -> bool:
    """Is ``frm → to`` in the WEFT §8.2 table (``frm=None`` being the INVOKE itself)?"""
    return to in TRANSITIONS.get(frm, frozenset())


def receipts_for_step(weave: Weave, step_id: str) -> list[Cell]:
    """All live receipt Cells recorded for a step, in deterministic receipt order (pure
    read). Ordered by ``(attempt, observation, lifecycle rank, id)`` — never fold order — so
    two processes folding the same log read the same history."""
    return cells.receipts_of_step(weave, step_id)


@dataclass(frozen=True)
class AttemptFold:
    """The reduction of ONE attempt's receipt append-log through the §8.2 machine.

    ``status`` is that attempt's current status (None if every receipt was rejected),
    ``cost`` its summed integer cost, and ``invalid`` the out-of-table transitions that were
    REFUSED — kept as evidence instead of being applied."""

    attempt: int
    status: str | None
    receipt_ids: tuple[str, ...] = ()
    cost: dict[str, int] = field(default_factory=dict)
    provider_ref: str | None = None
    error: dict[str, Any] | None = None
    invalid: tuple[dict[str, Any], ...] = ()


def reduce_attempt(receipts: Sequence[Cell]) -> AttemptFold:
    """Fold one attempt's receipts through the §8.2 table (WEFT §8.1: "the current status of
    an invocation is the fold of its receipt append-log through the state machine").

    An out-of-table transition is rejected — recorded in ``invalid`` and NOT applied — so a
    forged or buggy receipt can never move the machine into a state the protocol forbids
    (e.g. SUCCEEDED → CANCELLED, or a COMPENSATED that nothing succeeded before)."""
    ordered = sorted(receipts, key=cells.receipt_order)
    attempt = cells.receipt_attempt(ordered[0]) if ordered else 0
    status: str | None = None
    cost: dict[str, int] = {}
    provider_ref: str | None = None
    error: dict[str, Any] | None = None
    invalid: list[dict[str, Any]] = []
    applied: list[str] = []
    for receipt in ordered:
        to = str(receipt.content.get("status"))
        frm = ReceiptStatus.RUNNING if status is None and to in IMPLICIT_RUNNING else status
        if not may_transition(frm, to):
            invalid.append({"receipt": receipt.id, "attempt": attempt, "from": status, "to": to})
            continue
        status = to
        applied.append(receipt.id)
        for resource, amount in cells.receipt_cost(receipt).items():
            cost[resource] = cost.get(resource, 0) + amount
        ref = receipt.content.get("provider_ref")
        if isinstance(ref, str) and ref:
            provider_ref = ref
        err = receipt.content.get("error")
        if isinstance(err, dict):
            error = err
    return AttemptFold(
        attempt=attempt,
        status=status,
        receipt_ids=tuple(applied),
        cost=cost,
        provider_ref=provider_ref,
        error=error,
        invalid=tuple(invalid),
    )


def receipt_history(weave: Weave, step_id: str) -> tuple[AttemptFold, ...]:
    """A step's MULTI-ATTEMPT receipt history: one :class:`AttemptFold` per physical attempt,
    in attempt order (§8.5). This is the explicit, folded record the protocol requires — the
    log keeps every try of the one logical operation, not just the last."""
    by_attempt: dict[int, list[Cell]] = {}
    for receipt in cells.receipts_of_step(weave, step_id):
        by_attempt.setdefault(cells.receipt_attempt(receipt), []).append(receipt)
    return tuple(reduce_attempt(by_attempt[a]) for a in sorted(by_attempt))


def effect_status(weave: Weave, step_id: str) -> str | None:
    """The current §8.2 status of a step's effect: the fold of its LATEST attempt's receipt
    log (earlier attempts are history; the latest attempt is the live one). None when nothing
    has been receipted yet."""
    history = receipt_history(weave, step_id)
    return history[-1].status if history else None


def invalid_transitions(weave: Weave, step_id: str) -> list[dict[str, Any]]:
    """Every out-of-table transition found in a step's receipt log — refused by the reducer
    and surfaced here. A non-empty list means somebody wrote a receipt the §8.2 machine does
    not accept; the folded status ignored it."""
    return [bad for fold in receipt_history(weave, step_id) for bad in fold.invalid]


def cost_of_step(weave: Weave, step_id: str) -> dict[str, int]:
    """A step's TOTAL integer cost across every attempt (§8.1 field 11). Attempts SUM: each
    physical try spends real resources, so a retry is not free. Ints only, by construction."""
    total: dict[str, int] = {}
    for fold in receipt_history(weave, step_id):
        for resource, amount in fold.cost.items():
            total[resource] = total.get(resource, 0) + amount
    return total


def cost_of_plan(weave: Weave, plan_id: str) -> dict[str, int]:
    """The whole plan's integer cost, folded from its steps' receipts (pure read)."""
    total: dict[str, int] = {}
    for view in cells.steps_of_plan(weave, plan_id):
        for resource, amount in cost_of_step(weave, view.id).items():
            total[resource] = total.get(resource, 0) + amount
    return total


def effect_class_of(step_cell: Cell) -> str | None:
    """The effect class governing what automation is allowed on UNKNOWN/FAILED (§8.5),
    declared on the step or on its capability selector. None when unclassified."""
    content = step_cell.content
    value = content.get("effect_class")
    if value is None:
        selector = content.get("required_capability_selector") or {}
        if isinstance(selector, dict):
            value = selector.get("effect_class")
    return None if value is None else str(value)


def _leases_for_step(weave: Weave, step_id: str) -> list[Cell]:
    return [c for c in weave.of_type(cells.LEASE) if c.content.get("step_id") == step_id]


def _terminal_receipt(weave: Weave, step_id: str, status: str) -> Cell | None:
    for r in receipts_for_step(weave, step_id):
        if r.content.get("status") == status:
            return r
    return None


def strategy_of(step_cell: Cell, default: str = IdempotencyStrategy.IDEMPOTENCY_KEY) -> str:
    """The declared idempotency strategy of a step's effect, defaulting to idempotency-key
    (the runtime already keys receipts). Set it on the step's content under
    ``idempotency_strategy`` to override per effect."""
    return step_cell.content.get("idempotency_strategy", default)


_EFFECT_STATE_OF_RECEIPT = {
    ReceiptStatus.SUCCEEDED: EffectState.SUCCEEDED,
    ReceiptStatus.FAILED: EffectState.FAILED,
    ReceiptStatus.COMPENSATED: EffectState.COMPENSATED,
    ReceiptStatus.CANCELLED: EffectState.CANCELLED,
}


def classify_effect(weave: Weave, step_id: str, now: int) -> str:
    """Classify a step's effect into an :class:`EffectState` — a PURE read, no mutation.

    A FINAL receipt status is ground truth, taken from the fold of the latest attempt's
    receipt log (§8.1) rather than "whichever receipt the fold yielded first", so a step
    whose earlier attempt failed and whose latest attempt succeeded reads SUCCEEDED.
    ``UNKNOWN``/``ACCEPTED``/``RUNNING`` are not final, so the step's own status drives the
    answer, with the crash window the important case: a RUNNING step with no terminal receipt
    is DISPATCHED while its lease is still valid (``now`` < expiry) and UNKNOWN once the
    lease has lapsed with the outcome still unobserved."""
    step = weave.get(step_id)
    if step is None:
        raise ValueError(f"no such step {step_id}")
    folded = _EFFECT_STATE_OF_RECEIPT.get(effect_status(weave, step_id) or "")
    if folded is not None:
        return folded
    status = step.content.get("status")
    if status == StepStatus.UNKNOWN:
        return EffectState.UNKNOWN
    if status == StepStatus.CANCELLED:
        return EffectState.SUPERSEDED
    if status == StepStatus.RUNNING:
        leases = _leases_for_step(weave, step_id)
        live = [lease for lease in leases if int(now) < int(lease.content.get("expiry", 0))]
        return EffectState.DISPATCHED if live else EffectState.UNKNOWN
    if status == StepStatus.READY:
        return EffectState.AUTHORIZED
    return EffectState.PROPOSED


def _record_transition_error(
    weft: Weft,
    author: str,
    step_id: str,
    *,
    frm: str | None,
    to: str,
    attempt: int,
    now: int,
) -> str:
    """Record a REFUSED transition as a durable Cell — §8.2 requires an out-of-table
    transition to become an error, never silent state. Content-addressed, so refusing the
    same transition twice is idempotent (one cell, last-writer-wins)."""
    cid = hashing.content_id(
        {
            "runtime": EFFECT_TRANSITION_ERROR,
            "step": step_id,
            "from": frm or "",
            "to": to,
            "attempt": int(attempt),
            "at": int(now),
        },
        kind="cell",
    )
    cells.assert_content(
        weft,
        author,
        cid,
        EFFECT_TRANSITION_ERROR,
        {
            "step_id": step_id,
            "from_status": frm,
            "to_status": to,
            "attempt": int(attempt),
            "at": int(now),
            "reason": "transition not permitted by WEFT §8.2",
            "instruction_eligible": False,
        },
    )
    return cid


def _latest_lease_id(weave: Weave, step_id: str) -> str | None:
    """The step's most recent live lease, in deterministic order (attempt, frontier, id)."""
    leases = _leases_for_step(weave, step_id)
    if not leases:
        return None
    ordered = sorted(
        leases,
        key=lambda c: (
            int(c.content.get("attempt", 0) or 0),
            int(c.content.get("issued_frontier", 0) or 0),
            c.id,
        ),
    )
    return ordered[-1].id


def record_transition(
    weft: Weft,
    author: str,
    step_id: str,
    *,
    status: str,
    now: int,
    attempt: int | None = None,
    lease_id: str | None = None,
    cost: Any = None,
    error: Any = None,
    provider_ref: str | None = None,
    executor: str | None = None,
    effect_class: str | None = None,
    output_cell_ids: list[str] | None = None,
    compensates: str | None = None,
    retry_authorized: bool = False,
    diagnostics: dict[str, Any] | None = None,
    converge_step: bool = True,
) -> dict[str, Any]:
    """Append the next receipt for a step's live attempt, GUARDED by the §8.2 table.

    An out-of-table transition is REJECTED: no receipt is written, the refusal is recorded as
    a durable ``effect_transition_error`` Cell, and the caller gets ``recorded: False`` with
    the reason. An accepted transition appends a NEW immutable receipt at the next observation
    index of that attempt (never editing the prior one, §8.1) and — unless ``converge_step``
    is off — converges the Plan Step's status to match.

    This is the write seam every progress/terminal transition beyond the supervisor's own
    dispatch goes through: reconciliation observations, cancels, and compensations."""
    weave = Weave.fold(weft)
    step = weave.get(step_id)
    if step is None:
        raise ValueError(f"no such step {step_id}")
    if status not in ReceiptStatus.ALL:
        raise ValueError(f"unknown receipt status {status!r} (WEFT §8.2)")
    history = receipt_history(weave, step_id)
    live = history[-1] if history else None
    frm = live.status if live is not None else None
    resolved = (
        int(attempt)
        if attempt is not None
        else (live.attempt if live is not None else cells.current_attempt(weave, step_id))
    )
    effective = ReceiptStatus.RUNNING if frm is None and status in IMPLICIT_RUNNING else frm
    if not may_transition(effective, status):
        return {
            "step": step_id,
            "recorded": False,
            "from": frm,
            "to": status,
            "attempt": resolved,
            "error_cell": _record_transition_error(
                weft, author, step_id, frm=frm, to=status, attempt=resolved, now=now
            ),
            "reason": f"{frm} → {status} is not a WEFT §8.2 transition",
        }
    lease = lease_id or _latest_lease_id(weave, step_id) or step_id
    observation = cells.next_observation(weave, step_id, resolved)
    receipt_id = cells.record_receipt(
        weft,
        author,
        step_id=step_id,
        lease_id=lease,
        idempotency_key=step.content.get("idempotency_key", step_id),
        status=status,
        attempt=resolved,
        observation=observation,
        executor=executor or step.content.get("assigned_agent_id") or author,
        effect_class=effect_class if effect_class is not None else effect_class_of(step),
        provider_ref=provider_ref,
        output_cell_ids=output_cell_ids,
        cost=cost,
        error=error,
        compensates=compensates,
        retry_authorized=retry_authorized,
        diagnostics=diagnostics,
    )
    if converge_step:
        target = cells.STEP_STATUS_OF_RECEIPT.get(status)
        fresh = Weave.fold(weft).get(step_id)
        if target is not None and fresh is not None and fresh.content.get("status") != target:
            cells.set_status(weft, author, fresh, target)
    return {
        "step": step_id,
        "recorded": True,
        "from": frm,
        "to": status,
        "attempt": resolved,
        "observation": observation,
        "receipt": receipt_id,
        "lease": lease,
    }


def observe_outcome(
    weft: Weft,
    author: str,
    step_id: str,
    *,
    observed: str,
    now: int,
    provider_ref: str | None = None,
    cost: Any = None,
    error: Any = None,
) -> dict[str, Any]:
    """§8.6 reconciliation: resolve a resting UNKNOWN by OBSERVING the provider's real
    outcome — ``SUCCEEDED``, ``FAILED``, or ``UNKNOWN`` again when the provider is still
    indeterminate. There is no edge that DECIDES an outcome, only one that records an
    observation, and each observation is a new receipt (the prior UNKNOWN stays on the log)."""
    if observed not in {ReceiptStatus.SUCCEEDED, ReceiptStatus.FAILED, ReceiptStatus.UNKNOWN}:
        raise ValueError(f"an observation is SUCCEEDED, FAILED, or UNKNOWN — not {observed!r}")
    return record_transition(
        weft,
        author,
        step_id,
        status=observed,
        now=now,
        provider_ref=provider_ref,
        cost=cost,
        error=error,
        diagnostics={"reconciled": True, "observed": True},
    )


def cancel_effect(
    weft: Weft,
    author: str,
    step_id: str,
    *,
    now: int,
    provider_acknowledged: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """Record that an invocation was WITHDRAWN before any irreversible side effect (§8.2
    ``ACCEPTED``/``RUNNING`` → ``CANCELLED``).

    A cancel of an effect that may already be IN FLIGHT requires the provider to have
    ACKNOWLEDGED it: once submitted, "I cancelled it" is a claim about the outside world, and
    without acknowledgement the honest status is UNKNOWN, not CANCELLED (§8.3). That case is
    refused with nothing recorded — never a fabricated outcome. A SUCCEEDED effect can never
    be cancelled (the §8.2 table refuses it); reverse it with :func:`compensate_effect`."""
    weave = Weave.fold(weft)
    step = weave.get(step_id)
    if step is None:
        raise ValueError(f"no such step {step_id}")
    status = effect_status(weave, step_id)
    in_flight = status == ReceiptStatus.RUNNING or (
        status is None and step.content.get("status") == StepStatus.RUNNING
    )
    if in_flight and not provider_acknowledged:
        return {
            "step": step_id,
            "recorded": False,
            "from": status,
            "to": ReceiptStatus.CANCELLED,
            "reason": (
                "the effect may already be in flight; a cancel needs provider "
                "acknowledgement — absent that the honest status is UNKNOWN (WEFT §8.3)"
            ),
        }
    return record_transition(
        weft,
        author,
        step_id,
        status=ReceiptStatus.CANCELLED,
        now=now,
        error={
            "code": "cancelled",
            "retryable": False,
            "message": reason or None,
            "at": int(now),
        },
        diagnostics={"provider_acknowledged": bool(provider_acknowledged)},
    )


def compensate_effect(
    weft: Weft,
    author: str,
    step_id: str,
    *,
    compensation_step_id: str,
    now: int,
    cost: Any = None,
    provider_ref: str | None = None,
) -> dict[str, Any]:
    """Record that a SUCCEEDED effect's consequences were REVERSED by a compensating
    invocation (§8.2 ``SUCCEEDED`` → ``COMPENSATED``).

    The compensation runs as its own step with its OWN receipt chain; this receipt records
    the link (``compensates``) and the compensation's integer cost. Refused for anything that
    did not succeed — you cannot reverse an effect that never took effect — and the refusal is
    a durable error Cell, not a silent no-op."""
    return record_transition(
        weft,
        author,
        step_id,
        status=ReceiptStatus.COMPENSATED,
        now=now,
        compensates=compensation_step_id,
        cost=cost,
        provider_ref=provider_ref,
        diagnostics={"compensation_step_id": compensation_step_id},
    )


def reconcile_step(
    weft: Weft,
    author: str,
    step_id: str,
    *,
    now: int,
    default_strategy: str = IdempotencyStrategy.IDEMPOTENCY_KEY,
) -> dict:
    """Recover a step stranded in the crash window (RUNNING lease, no terminal receipt).

    Durably converges the step:
      * a SUCCEEDED receipt already exists  → converge the step to SUCCEEDED
        (already-succeeded; the effect happened, re-dispatch would be wrong).
      * strategy is safe-to-retry           → return the step to READY so the supervisor
        re-dispatches it (RECONCILING → the dedup guarantee makes a retry harmless).
      * strategy is not-safely-retryable    → record a durable UNKNOWN receipt and put the
        step in UNKNOWN (never a silent retry — a human/compensation decides).

    Either way the stranded ATTEMPT gets its own durable ``UNKNOWN`` receipt first (§8.4:
    "if the lease expires_at passes with no terminal receipt, a reconciler asserts a receipt
    with status = UNKNOWN"), so the multi-attempt history records that this physical try ended
    unobserved; a retry then runs as a NEW attempt under the SAME idempotency key (§8.5),
    never as an overwrite of the stranded one. A safe retry marks that receipt
    ``retry_authorized`` — the only thing that lets the supervisor re-dispatch past a resting
    UNKNOWN.

    An effect class of IRREVERSIBLE/FINANCIAL/COMMUNICATION is never auto-retried, whatever
    its idempotency strategy claims (§8.5).

    A step not in the crash window is returned with its classified state and no mutation.
    Composes only the kernel's content path + the cells helpers; dispatches nothing."""
    weave = Weave.fold(weft)
    step = weave.get(step_id)
    if step is None:
        raise ValueError(f"no such step {step_id}")

    if effect_status(weave, step_id) == ReceiptStatus.SUCCEEDED:
        if step.content.get("status") != StepStatus.SUCCEEDED:
            cells.set_status(weft, author, step, StepStatus.SUCCEEDED)
        return {
            "step": step_id,
            "state": EffectState.SUCCEEDED,
            "action": "already-succeeded",
            "retried": False,
            "cost": cost_of_step(weave, step_id),
        }

    if step.content.get("status") != StepStatus.RUNNING:
        # Not stranded — nothing to reconcile; report the classified state as-is.
        return {
            "step": step_id,
            "state": classify_effect(weave, step_id, now),
            "action": "noop",
            "retried": False,
        }

    # Crash window: RUNNING with no terminal receipt.
    strategy = strategy_of(step, default_strategy)
    effect_class = (effect_class_of(step) or "").upper()
    safe = strategy in _SAFE_TO_RETRY and effect_class not in NO_AUTO_RESOLVE
    out = record_transition(
        weft,
        author,
        step_id,
        status=ReceiptStatus.UNKNOWN,
        now=now,
        retry_authorized=safe,
        converge_step=not safe,
        diagnostics={
            "reconciled": True,
            "reason": (
                "lease lapsed with the outcome unobserved"
                if safe
                else "ambiguous interruption; effect not safely retryable"
            ),
            "strategy": strategy,
            "effect_class": effect_class or None,
        },
    )
    if not out.get("recorded"):
        # The §8.2 machine refused the UNKNOWN (e.g. the attempt already ended FAILED):
        # fail closed with the refusal recorded, never a retry on an unclear history.
        return {
            "step": step_id,
            "state": classify_effect(Weave.fold(weft), step_id, now),
            "action": "refused",
            "retried": False,
            "reason": out.get("reason"),
            "error_cell": out.get("error_cell"),
        }
    if safe:
        cells.set_status(weft, author, Weave.fold(weft).get(step_id), StepStatus.READY)
        return {
            "step": step_id,
            "state": EffectState.RECONCILING,
            "action": "safe-to-retry",
            "strategy": strategy,
            "retried": True,
            "attempt": out["attempt"],
            "receipt": out["receipt"],
        }
    # Not safely retryable: fail closed to UNKNOWN rather than gamble on a double-effect.
    return {
        "step": step_id,
        "state": EffectState.UNKNOWN,
        "action": "unknown",
        "strategy": strategy,
        "retried": False,
        "attempt": out["attempt"],
        "receipt": out["receipt"],
    }
