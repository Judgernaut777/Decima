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

Receipts are assertion values with a fixed schema:

```text
EffectReceipt {
  invocation:       EventId
  executor:         PrincipalId
  attempt:          uint
  status:           ACCEPTED | RUNNING | SUCCEEDED | FAILED |
                    UNKNOWN | COMPENSATED | CANCELLED
  provider_ref:     string?
  outputs:          [ValueRef]
  stdout:           BlobId?
  stderr:           BlobId?
  cost:             [CostItem]
  started_at:       int?
  finished_at:      int?
  environment:      CellId
  implementation:   BlobId
  error:            StructuredError?
}
```

`UNKNOWN` is mandatory: a network timeout after submission must not be rewritten as failure.

## 9. Ordering

The DAG defines causality. Lamport time accelerates ordering but does not establish causality. A deterministic total order for folding concurrent events is:

```text
(lamport, event_id bytes)
```

Type-specific merge semantics may preserve concurrency rather than selecting a winner.

## 10. Versioning

Unknown required fields or unknown verb versions fail closed. Optional extensions are namespaced Cells. Protocol upgrades are asserted as type/policy Cells and activated by attested realm policy; old events remain valid under their original protocol.
