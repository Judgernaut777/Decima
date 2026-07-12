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
(step, lease, idempotency-key), so re-recording the same outcome lands on the SAME cell —
the fold's last-writer-wins yields one current state, never a duplicate.
"""

from __future__ import annotations

from decima.kernel.weave import Weave
from decima.runtime import cells
from decima.runtime.cells import StepStatus


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

    TERMINAL = frozenset({SUCCEEDED, FAILED, COMPENSATED})


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


def receipts_for_step(weave: object, step_id: str) -> list[object]:
    """All live receipt Cells recorded for a step (pure read)."""
    return [r for r in weave.of_type(cells.RECEIPT) if r.content.get("step_id") == step_id]


def _leases_for_step(weave: object, step_id: str) -> list[object]:
    return [c for c in weave.of_type(cells.LEASE) if c.content.get("step_id") == step_id]


def _terminal_receipt(weave: object, step_id: str, status: str) -> object | None:
    for r in receipts_for_step(weave, step_id):
        if r.content.get("status") == status:
            return r
    return None


def strategy_of(step_cell: object, default: str = IdempotencyStrategy.IDEMPOTENCY_KEY) -> str:
    """The declared idempotency strategy of a step's effect, defaulting to idempotency-key
    (the runtime already keys receipts). Set it on the step's content under
    ``idempotency_strategy`` to override per effect."""
    return step_cell.content.get("idempotency_strategy", default)


def classify_effect(weave: object, step_id: str, now: int) -> str:
    """Classify a step's effect into an :class:`EffectState` — a PURE read, no mutation.

    A SUCCEEDED/FAILED receipt is terminal ground truth. Otherwise the step's own status
    drives the answer, with the crash window the important case: a RUNNING step with no
    terminal receipt is DISPATCHED while its lease is still valid (``now`` < expiry) and
    UNKNOWN once the lease has lapsed with the outcome still unobserved."""
    step = weave.get(step_id)
    if step is None:
        raise ValueError(f"no such step {step_id}")
    if _terminal_receipt(weave, step_id, StepStatus.SUCCEEDED):
        return EffectState.SUCCEEDED
    if _terminal_receipt(weave, step_id, StepStatus.FAILED):
        return EffectState.FAILED
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


def reconcile_step(
    weft: object,
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

    A step not in the crash window is returned with its classified state and no mutation.
    Composes only the kernel's content path + the cells helpers; dispatches nothing."""
    weave = Weave.fold(weft)
    step = weave.get(step_id)
    if step is None:
        raise ValueError(f"no such step {step_id}")
    idem = step.content.get("idempotency_key", step_id)

    succeeded = _terminal_receipt(weave, step_id, StepStatus.SUCCEEDED)
    if succeeded is not None:
        if step.content.get("status") != StepStatus.SUCCEEDED:
            cells.set_status(weft, author, step, StepStatus.SUCCEEDED)
        return {
            "step": step_id,
            "state": EffectState.SUCCEEDED,
            "action": "already-succeeded",
            "retried": False,
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
    if strategy in _SAFE_TO_RETRY:
        cells.set_status(weft, author, step, StepStatus.READY)
        return {
            "step": step_id,
            "state": EffectState.RECONCILING,
            "action": "safe-to-retry",
            "strategy": strategy,
            "retried": True,
        }

    # Not safely retryable: fail closed to UNKNOWN rather than gamble on a double-effect.
    leases = _leases_for_step(weave, step_id)
    lease_id = leases[-1].id if leases else step_id
    cells.record_receipt(
        weft,
        author,
        step_id=step_id,
        lease_id=lease_id,
        idempotency_key=idem,
        status=StepStatus.UNKNOWN,
        diagnostics={
            "reconciled": True,
            "reason": "ambiguous interruption; effect not safely retryable",
            "strategy": strategy,
        },
    )
    cells.set_status(weft, author, Weave.fold(weft).get(step_id), StepStatus.UNKNOWN)
    return {
        "step": step_id,
        "state": EffectState.UNKNOWN,
        "action": "unknown",
        "strategy": strategy,
        "retried": False,
    }
