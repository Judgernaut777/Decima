## Runtime completion — budgets, cancellation, reconciliation (DEC-046/047/048)

Three modules were added on top of `decima/runtime/{cells,scheduler,supervisor}.py`; none of the existing three were edited. Each composes the kernel's public content path (`assert_content`, `set_status`, `Weave.fold`) and `decima.kernel.lifecycle` — no second canonical store, no ambient authority.

### `budgets.py` (DEC-046)
- `check_budget(weave, agent_id, cost, now) -> (ok, reason)` — pure pre-dispatch gate over the six named bounds (`token_budget`, `monetary_budget`, `deadline`, `max_attempts`, `max_child_agents`, `max_concurrent`). Every bound optional; fails closed on unknown/blocked/terminal agent or any crossed bound. Ints only.
- `spend_ledger(weave, agent_id) -> Spend` — the spend side folded from RECEIPTS (token/monetary cost in diagnostics), LEASES (attempts), Agent cells (child count), and RUNNING steps (concurrency). Pure, deterministic.
- `guarded_dispatch_step(...)` — runs the gate BEFORE the effect; on exhaustion it transitions the agent to a durable `BUDGET_BLOCKED` Cell, marks the step `BLOCKED`, and returns a refusal without ever calling the runner. `set_limits` / `block_agent` are the durable writers.

### `cancellation.py` (DEC-047)
- `cancel_plan(weft, author, plan_id)` — plan -> CANCELLED; each non-terminal step CANCELLED with its active leases TERMINATEd (`lifecycle.terminate`, LEASE_TREE cascade).
- `cancel_agent(weft, author, agent_id)` — depth-first: child agents first, then TERMINATE the agent, cancel its steps+leases, and `revoke_capability` its grants (`lifecycle.revoke`, DERIVED_AUTHORITY cascade fails closed descendant grants). Never dispatches new work.
- Honesty: already-committed effects (steps with a receipt) and receiptless invocations are surfaced in `committed_effects` / `pending_invocations` — recorded, not reversed.

### `reconciliation.py` (DEC-048)
- `EffectState` (PROPOSED/AUTHORIZED/DISPATCHED/SUCCEEDED/FAILED/UNKNOWN/RECONCILING/SUPERSEDED/COMPENSATED) and `IdempotencyStrategy` (naturally-idempotent / idempotency-key / read-before-write / write-once / not-safely-retryable).
- `classify_effect(...)` — pure classification; a RUNNING step with no terminal receipt is DISPATCHED while its lease is live and UNKNOWN once lapsed.
- `reconcile_step(...)` — recovers the crash window: already-succeeded converges the step; safe-to-retry strategies return it to READY; `not-safely-retryable` fails closed to UNKNOWN (durable UNKNOWN receipt + step status), never a silent retry. Duplicate receipts are idempotent by content-addressing (same step/lease/idempotency-key -> one cell).

Verification: `tests/runtime` 22 passed, `tests/architecture` 19 passed (import boundary intact), `import decima.kernel, decima.runtime` ok, full suite 96 passed, ruff clean at line-length 100.