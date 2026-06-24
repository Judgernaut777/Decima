# Weft Protocol v0.1

This document defines the target wire contract. The Heartbeat may implement a smaller profile, but it must not create incompatible meanings.

## 1. Identifiers and canonical bytes

- Hash algorithm: `BLAKE3-256`.
- Identifier text: multibase base32-lower, prefixed by kind: `evt_`, `bdy_`, `blob_`, `cell_`, `prn_`, `cap_`.
- Canonical structured encoding: deterministic CBOR (RFC 8949, Section 4.2).
- Integers use the shortest encoding. Floats, indefinite lengths, duplicate map keys, and Unicode normalization during encoding are forbidden.
- Human text must already be valid UTF-8 and NFC-normalized.
- Maps are keyed by integer field numbers in signed material. JSON is a diagnostic representation only.
- Domain-separated hash:

```text
digest(kind, bytes) = BLAKE3-256("decima:v0.1:" || kind || 0x00 || bytes)
```

The Heartbeat currently uses canonical JSON and 128-bit BLAKE2b. That is acceptable for a prototype but not the durable protocol.

## 2. Event envelope

The signature covers `event_unsigned_bytes`; `id` and `sig` are excluded from those bytes.

```text
Event {
  1: protocol       uint              // 1
  2: realm          bytes32           // workspace/security realm
  3: parents        [EventId]         // sorted lexicographically; causal frontier
  4: author         PrincipalId
  5: authorization  AuthorizationProof
  6: verb           uint              // 1 ASSERT, 2 RETRACT, 3 INVOKE, 4 ATTEST
  7: body           BodyId
  8: lamport        uint
  9: wall_time      int?              // Unix ns, informational and untrusted
 10: nonce          bytes16           // prevents accidental semantic collision
 11: extensions     {uint: any}?
}
EventRecord {
  1: event          Event
  2: id             EventId           // digest("event", canonical(Event))
  3: sig_alg        uint              // 1 Ed25519 initially
  4: signature      bytes
}
```

Validation:

1. Canonical-decode and reject noncanonical bytes.
2. Recompute body and event identifiers.
3. Verify author key validity for the event’s causal point.
4. Verify signature.
5. Verify every parent exists or quarantine as an orphan.
6. Require `lamport = 1 + max(parent.lamport)`, or `0` for genesis.
7. Verify authorization at the parent frontier, never against mutable “current” state.
8. Apply verb-specific validation.
9. Append atomically. Materialization happens after acceptance.

## 3. Authorization proof

Knowing a capability Cell ID is not possession. Authorization uses a graph-verifiable grant path and a request binding.

```text
AuthorizationProof {
  1: capability      CellId
  2: grant_event     EventId
  3: delegation_path [EventId]        // parent grant through attenuations
  4: holder          PrincipalId
  5: invocation_bind Hash             // digest of verb/body/nonce/parents
  6: holder_sig      bytes             // proof of key possession
  7: approvals       [EventId]?
}
```

The kernel verifies that the capability was live at the causal frontier, the holder was the grantee, every delegation attenuated authority, all caveats hold, and required approvals bind to this exact invocation.

Bootstrap/genesis events use a realm-creation capability established by the realm genesis record. There is no authorization null value after genesis.

## 4. ASSERT body

`ASSERT` introduces a version, relation, grant, lease, message, receipt, or other proposition.

```text
AssertBody {
  1: subject       CellId
  2: type          CellId
  3: assertion     uint              // 1 CONTENT, 2 EDGE, 3 GRANT, 4 LEASE,
                                    // 5 MESSAGE, 6 RECEIPT, 7 POLICY, 8 TYPE_DEF
  4: value         ValueRef          // inline canonical value or BlobId
  5: basis         [EventId]?        // evidence/source events
  6: valid_from    int?
  7: valid_until   int?
  8: conflict_key  bytes?            // type-defined logical register/key
  9: schema        CellId             // schema version used to validate value
 10: labels        [CellId]?
}
```

An assertion is not automatically “true.” It is a signed proposition whose effective status is computed from retractions, policies, attestations, and type semantics.

## 5. RETRACT body

`RETRACT` withdraws the effect of identified assertions or attestations from the retractor’s authority domain. It never erases history.

```text
RetractBody {
  1: targets       [EventId]         // sorted, nonempty
  2: mode          uint              // 1 WITHDRAW, 2 SUPERSEDE, 3 REVOKE,
                                    // 4 REDACT, 5 TERMINATE
  3: reason        ValueRef?
  4: replacement   EventId?
  5: effective_at  int?
  6: cascade       uint              // 1 NONE, 2 DERIVED_AUTHORITY, 3 LEASE_TREE
}
```

- `REVOKE` targets grants/capabilities.
- `REDACT` removes payload availability from normal projections and starts cryptographic-erasure/GC policy; the event skeleton remains.
- Retraction is authorized only if policy permits the principal to withdraw the target. It is not “last writer wins.”

## 6. INVOKE body

`INVOKE` records effect intent. Completion is a separate `ASSERT RECEIPT`, causally descending from the invocation.

```text
InvokeBody {
  1: capability     CellId
  2: operation      Symbol
  3: target         ResourceRef
  4: arguments      ValueRef
  5: idempotency    bytes32
  6: execution      ExecutionPolicy
  7: expected       OutputSchema
  8: budget         BudgetReservation?
  9: deadline       int?
 10: privacy        DataHandlingPolicy?
}
ExecutionPolicy {
  1: effect_class   uint              // PURE, READ, REVERSIBLE_WRITE,
                                    // IRREVERSIBLE, COMMUNICATION, FINANCIAL
  2: retry          RetryPolicy
  3: compensation   CellId?
  4: sandbox        CellId?
}
```

Rules:

- Kernel acceptance means “authorized intent recorded,” not “effect succeeded.”
- Executors claim invocations with lease assertions.
- At-most-once cannot be guaranteed across arbitrary external systems. Decima provides idempotency keys, durable receipts, reconciliation, and compensation.
- A retry reuses the same idempotency key unless policy explicitly creates a new logical operation.
- External results include provider request IDs, timestamps, hashes, cost, and observed status.

## 7. ATTEST body

`ATTEST` expresses a signed judgment over events, Cells, artifacts, evaluations, or policy transitions.

```text
AttestBody {
  1: subjects      [SubjectRef]
  2: predicate     CellId             // e.g. tests_pass, provenance_verified
  3: verdict       uint               // PASS, FAIL, ABSTAIN, QUALIFIED
  4: claims        ValueRef?
  5: evidence      [SubjectRef]
  6: method        CellId
  7: evaluator     CellId?
  8: confidence    uint?              // integer millionths, never float
  9: scope         Selector?
 10: expires_at    int?
}
```

Attestation never mutates its subject. Policies decide what combinations of attestations activate trust, promotion, approval, or conflict resolution.

## 8. Effect receipts

An `INVOKE` records *authorized intent* (§6); a receipt records an *observed
outcome*. The two are separate events: a receipt is an `ASSERT` whose assertion
kind is `6 RECEIPT` (§4), causally descending from the invocation it reports on.
Acceptance of the `INVOKE` never implies the effect happened — only a receipt
carries outcome, and only the executor that holds the invocation's lease (§8.4)
may assert one.

### 8.1 Schema

```text
EffectReceipt {
  1:  invocation     EventId            // the INVOKE this reports on
  2:  executor       PrincipalId        // who ran it; must hold the live lease
  3:  attempt        uint               // 0-based physical try under one idempotency key
  4:  status         Status             // see §8.2
  5:  idempotency    bytes32            // copied from the INVOKE; stable across attempts
  6:  effect_class   uint               // copied from ExecutionPolicy (§6); governs §8.3/§8.5
  7:  provider_ref   string?            // external request/transaction id, for reconciliation
  8:  outputs        [ValueRef]         // present only for SUCCEEDED
  9:  stdout         BlobId?
  10: stderr         BlobId?
  11: cost           [CostItem]
  12: started_at     int?               // Unix ns, informational and untrusted
  13: finished_at    int?
  14: environment    CellId             // sandbox / host descriptor Cell
  15: implementation BlobId             // hash of the realized effect handler
  16: error          StructuredError?   // present for FAILED, and may annotate UNKNOWN
  17: lease          EventId            // the lease assertion this receipt was produced under
}

CostItem {
  1: resource  Symbol                   // e.g. "tokens", "usd_micros", "wall_ms"
  2: amount    int                      // integer in the resource's smallest unit; never float (§1)
  3: unit      Symbol
  4: provider_ref string?
}

StructuredError {
  1: code         Symbol                // stable, machine-routable
  2: retryable    bool                  // executor's classification, not a license to auto-retry (§8.5)
  3: provider_code string?
  4: message      string?
  5: at           int?
}
```

A receipt value is **immutable** (`FOLD §4`; `specs/MERGE_SEMANTICS.md` §3): it is
never edited in place. Progress is expressed by asserting a *new* receipt for the
same `invocation` with a later `(attempt, lamport, event_id)`. The current status
of an invocation is the fold of its receipt append-log through the state machine
below — an Immutable-value series reduced by a State machine, exactly as A1 maps
`result`/`receipt`.

### 8.2 Status state machine

`status` is one of:

```text
ACCEPTED | RUNNING | SUCCEEDED | FAILED | UNKNOWN | COMPENSATED | CANCELLED
```

Allowed transitions (each transition is a new receipt assertion; the reducer
rejects any out-of-table transition as an error Cell, never silent state):

| from | to | when |
|---|---|---|
| _(INVOKE)_ | `ACCEPTED` | executor claims the invocation (lease held); nothing submitted yet |
| _(INVOKE)_ | `RUNNING` | executor submits immediately, skipping an explicit ACCEPTED |
| `ACCEPTED` | `RUNNING` | effect submitted to the external system |
| `ACCEPTED` | `CANCELLED` | withdrawn before any side effect |
| `ACCEPTED` | `FAILED` | refused before submission (e.g. precondition failed) |
| `RUNNING` | `SUCCEEDED` | completion observed; `outputs` present |
| `RUNNING` | `FAILED` | positive evidence the effect did not take effect |
| `RUNNING` | `UNKNOWN` | timeout / lease expiry / crash after possible submission (§8.3) |
| `RUNNING` | `CANCELLED` | provider-acknowledged cancel before any irreversible effect |
| `UNKNOWN` | `SUCCEEDED` / `FAILED` | reconciliation *observed* the true outcome (§8.6) |
| `UNKNOWN` | `UNKNOWN` | reconciliation attempted; provider still indeterminate |
| `SUCCEEDED` | `COMPENSATED` | a compensation invocation reversed the effect |

- **ACCEPTED** — an executor claimed the invocation (holds a live lease) and
  accepted responsibility; no external side effect attempted yet.
- **RUNNING** — the effect has been submitted to / is in flight at the external
  system. After this point a side effect *may already exist*.
- **SUCCEEDED** — the executor observed completion; `outputs` are present.
- **FAILED** — the effect definitively did not take effect, *or* the provider
  reported a definite failure. `error` is present. A retry is a **new attempt**
  (`attempt + 1`), not an edit of this receipt.
- **UNKNOWN** — outcome indeterminate *after* possible submission. The mandatory
  resting state (§8.3).
- **CANCELLED** — the invocation was withdrawn before any irreversible side effect
  (e.g. `RETRACT`/`TERMINATE` while ACCEPTED, or a provider-acknowledged cancel).
- **COMPENSATED** — a prior `SUCCEEDED` effect's consequences were reversed by the
  compensation invocation named in `ExecutionPolicy.compensation` (§6); the
  compensation has its own receipt chain, and this status records the link.

`SUCCEEDED`, `FAILED`, `CANCELLED`, and `COMPENSATED` are final for that
*attempt*; `UNKNOWN` is *resting, not terminal* — it must be reconciled (§8.6).

### 8.3 The UNKNOWN rule (mandatory)

> A network timeout, lease expiry, or executor crash **after submission** resolves
> to `UNKNOWN`, never to a fabricated `SUCCEEDED` or `FAILED`.

The reducer may only leave `UNKNOWN` by *observing* the real outcome (§8.6) — never
by assuming one. `RUNNING → FAILED` is permitted only when the executor has
positive evidence the effect did not take effect; absent that evidence the only
honest transition out of `RUNNING` on a timeout is `RUNNING → UNKNOWN`. This is the
representable form of `FOLD §11`'s eighth invariant ("ambiguous external execution
resolves to `UNKNOWN`"): the status set *contains* `UNKNOWN` and the machine has
*no* edge that invents a terminal outcome.

### 8.4 Leases (at-most-once claim)

An executor claims an invocation before running it by asserting a lease (assertion
kind `4 LEASE`, §4):

```text
Lease { invocation: EventId, holder: PrincipalId, expires_at: int, attempt: uint }
```

- A live lease makes the executor the sole party permitted to assert receipts for
  that `(invocation, attempt)` — preventing two executors from double-firing.
- If the lease `expires_at` passes with no terminal receipt, a reconciler asserts
  a receipt with `status = UNKNOWN` (the holder may have died mid-flight) and a new
  attempt may be leased. The expired lease never licenses a fabricated outcome.
- Each receipt names the `lease` it was produced under (field 17), so the chain
  from claim → outcome is on the Log.

### 8.5 Idempotency, attempts, and retry

- `idempotency` (field 5) is copied verbatim from the `INVOKE` (`InvokeBody.idempotency`,
  §6). It is the identity of the **logical operation** and is stable across every
  attempt. The provider dedupes on it, so a retry after `UNKNOWN` does not
  double-fire.
- `attempt` distinguishes **physical executions** of that one logical operation. A
  retry reuses the idempotency key and increments `attempt` (`§6`: "a retry reuses
  the same idempotency key unless policy explicitly creates a new logical
  operation").
- `effect_class` (field 6) governs what automation is *allowed* on `UNKNOWN`/`FAILED`,
  regardless of `error.retryable`:
  - `PURE` / `READ` — naturally idempotent; safe to re-execute to resolve `UNKNOWN`.
  - `REVERSIBLE_WRITE` — retry under the same idempotency key, or compensate.
  - `IRREVERSIBLE` / `FINANCIAL` / `COMMUNICATION` — **must not** be auto-retried or
    auto-failed out of `UNKNOWN`; resolution requires reconciliation (§8.6) or a
    `MORTA` approval / human adjudication. This is where fabricating an outcome
    would do real-world harm, so the machine forbids it.

### 8.6 Reconciliation

`UNKNOWN` is resolved by *observing*, not deciding. A reconcile is itself a `READ`
invocation against the provider, keyed by `provider_ref` and/or `idempotency`,
whose receipt asserts the true terminal status (`UNKNOWN → SUCCEEDED | FAILED`),
or `UNKNOWN` again if the provider is still indeterminate. Because each step is an
ordinary authorized event, reconciliation is auditable and time-travelable, and an
unresolved `UNKNOWN` is a loud, queryable state (a dependent may block on it per
policy) — not a silent gap.

### 8.7 Heartbeat profile

The Heartbeat (F1) implements the minimal slice that closes `FOLD §11` #8: the
`result` cell `kernel.invoke` asserts becomes an `EffectReceipt`-shaped cell
carrying `status`, and a deliberately ambiguous effect resolves to `UNKNOWN`
(never a fabricated success/failure), with a smoke assertion flipping the oracle
row from *deferred* to *holds*. Leases, multi-attempt reconciliation, and
`COMPENSATED` are recorded here but remain Rust-port work; see
`heartbeat/PROFILE.md`.

## 9. Ordering

The DAG defines causality. Lamport time accelerates ordering but does not establish causality. A deterministic total order for folding concurrent events is:

```text
(lamport, event_id bytes)
```

Type-specific merge semantics may preserve concurrency rather than selecting a winner.

## 10. Versioning

Unknown required fields or unknown verb versions fail closed. Optional extensions are namespaced Cells. Protocol upgrades are asserted as type/policy Cells and activated by attested realm policy; old events remain valid under their original protocol.
