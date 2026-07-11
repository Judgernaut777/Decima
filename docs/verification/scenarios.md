## Verification lane (merge order 9) — tests only, no source

Independent property, fault-injection, and end-to-end tests over the INTEGRATED durable stack (decima.kernel + runtime + projections + services). Every test builds a real Weft + runtime + projections in-process and asserts a load-bearing invariant; none touches source.

### tests/e2e/ — release scenarios end to end
- **Scenario E — crash recovery** (`test_crash_recovery.py`): plan A→B; A completes to a terminal receipt; B stranded in the dispatch crash window; DROP the process (fresh Weft over the same db); reconcile B from its lease+receipt; resume → A is NOT repeated, only B runs; the interruption is visible in the activity projection, which agrees with a from-scratch rebuild.
- **Scenario F — revocation** (`test_revocation.py`): agent holds a shell/filesystem capability and begins a plan; `lifecycle.revoke` (one RETRACT); pending invocation → `REVOKED`; new authorization excludes it; the attenuated DESCENDANT grant fails closed via the DERIVED_AUTHORITY cascade; a receipt committed before revocation survives; the revocation shows as a RETRACT in the activity projection.
- **Scenario G — backup + restore** (`test_backup_restore.py`): notes/document/plan/tasks/artifact; `backup_create` → `backup_verify` → `restore_apply` into a fresh base with a seed-equal keyring; folded `state_root` and every per-projection `state_root` match the original, artifact restores byte-identically; a tampered event log is refused before it can produce a world.
- **Scenario D — approval gating** (`test_approval_gating.py`): a gated (`requires_approval`) capability → `authorize_decision` returns `APPROVAL_REQUIRED`; DENY lands a durable decision and confers no authority (effect never runs, shown DENIED, durable across restart); a capability-scoped APPROVAL cell clears the exact gate (shown CONSUMED); consuming (retracting) it reverts the gate to `APPROVAL_REQUIRED` — single-use, no replay. Plus: a pending item is not authority.

### tests/verification/ — property + fault injection (`test_properties.py`, hypothesis)
- Shuffled + duplicated event delivery folds to ONE `state_root` (order-independent, `_apply` idempotent).
- Duplicate worker responses (duplicate receipts) fold to one current state.
- A kill between dispatch and receipt NEVER silently retries a not-safely-retryable effect; the plan stalls fail-closed and a durable UNKNOWN receipt records the ambiguity.
- Over-budget dispatch is strictly blocked (dispatch iff cost ≤ budget), the runner never runs past exhaustion, the block is durable.
- Projection rebuild == incremental after ARBITRARY interleavings (random prefix/suffix action sequences).

### Run
```
PYTHONPATH=<testenv>:$PWD python3 -m pytest tests/e2e tests/verification -q
```
36 tests pass; `tests/architecture` stays green; kernel import boundary intact.

### No source bugs found
All five integrated subsystems behaved correctly under fault injection and property fuzzing.