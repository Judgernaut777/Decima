# Weave Fold, Snapshots, Merge, Scratch, and GC

## 1. Acceptance versus materialization

The Weft has two stages:

1. **Accepted event store** — immutable canonical event/body/blob records.
2. **Materialized projections** — replaceable indexes and Cell states.

An event is accepted only after cryptographic, causal, authorization, schema, and verb validation. A projection failure never rolls back accepted history; it marks the projection unhealthy and rebuildable.

## 2. Deterministic fold

For a realm and causal frontier:

```text
fold(frontier, projection_version):
  base = newest verified snapshot whose frontier is an ancestor of frontier
  events = causal_difference(base.frontier, frontier)
  order events by (lamport, event_id)
  for event in events:
      body = load_and_verify(event.body)
      apply_verb(base.state, event, body, projection_version)
  return base.state
```

Each projection reducer is:

- Pure over recorded bytes and reducer version.
- Total: malformed-but-accepted historical input produces a deterministic error Cell, not a crash.
- Idempotent by Event ID.
- Versioned by content hash.
- Forbidden from network, clock, randomness, environment variables, or mutable global state.

External effects are not replayed. Their recorded invocation and receipts are folded.

## 3. Cell state

A materialized Cell state contains:

```text
CellState {
  cell_id
  live_assertions: EventId[]
  active_retractions: EventId[]
  type_heads: EventId[]
  content_heads: EventId[]
  edges_out / edges_in
  grants / leases
  attestations
  conflicts
  provenance_frontier
  reducer_version
}
```

“Head” may be plural. A concurrent conflict is preserved until a type reducer merges it or an adjudication attestation chooses a resolution.

## 4. Type merge classes

Every Type Cell declares one merge class:

| Merge class | Use | Semantics |
|---|---|---|
| Immutable value | blobs, receipts, released artifacts | Same content deduplicates; differing content creates separate Cells |
| LWW register | explicitly low-value settings | Highest `(lamport,event_id)` wins; concurrency remains inspectable |
| MV register | titles, status where conflict matters | Preserve concurrent heads until adjudicated |
| OR-set | tags, membership, capability grants | Add/remove by observed event identity |
| Sequence CRDT | collaborative block text | Stable element IDs and tombstones |
| Map CRDT | structured documents/settings | Per-key declared merge class |
| Counter | quotas/telemetry | PN-counter or ledger, according to trust requirement |
| Append log | messages, observations | Causal ordered set; no overwrite |
| State machine | runs, approvals, promotions | Transition table; invalid transitions become rejected/error Cells |
| Semantic adjudication | plans, schemas, architecture | Preserve branches; require attested merge proposal |

No generic “AI merge” is authoritative. An AI may propose a merged assertion; policy or a trusted principal attests it.

## 5. Incremental materialization

- Partition event indexes by realm and touched Cell IDs.
- Store event-to-cell dependency edges when bodies are accepted.
- Reducers consume an ordered queue and commit projection cursor plus updates atomically.
- Retractions invalidate affected Cells and transitive derived projections.
- Capability revocation receives a high-priority invalidation lane.
- Search, embedding, graph analytics, UI views, and summaries subscribe to projection changes; none are canonical.

## 6. Snapshots

```text
SnapshotManifest {
  realm
  frontier: EventId[]
  event_count
  state_root                 // Merkle root over canonical CellState records
  reducer_set: [{name, hash}]
  schema_frontier
  created_by
  chunks: [{range, blob_id, hash}]
  signature
}
```

Rules:

- Snapshot creation is an invocation; its manifest/result is asserted and attested.
- Restore verifies every chunk and the state root.
- A random sample and periodic full replay compare rebuilt state roots.
- Snapshots never authorize events. Authorization is always evaluated from causal grant history.
- Keep multiple snapshot generations and at least one independently rebuilt checkpoint.
- Snapshot cadence is adaptive: event count, replay cost, revocation pressure, and shutdown checkpoints.

## 7. Scratch Weft

Scratch is a separate, encrypted, bounded event realm with the same four verbs but weaker retention guarantees.

Scratch includes:

- Hidden chain-of-thought and model internals only when provider policy permits storage.
- Candidate plans, transient retrieval results, failed drafts, temporary tool output.
- Raw audio buffers after transcription unless retention is requested.
- Secrets only as opaque broker handles; never secret values.

Scratch events carry TTL, sensitivity, owner, and promotion policy. They are excluded from normal sync and search.

## 8. Graduation to durable Weft

Scratch bytes never silently become durable. Graduation creates new durable assertions that reference permitted evidence hashes.

Required graduation predicates:

- The object has future operational, evidentiary, preference, or knowledge value.
- It is not merely hidden reasoning.
- Data policy permits retention in the target realm.
- Provenance is attached.
- Confidence and epistemic type are explicit: observation, user statement, inference, hypothesis, instruction, preference, or verified fact.
- “May recall” and “may act as instruction” are independently authorized.
- Secret and personal-data scanning passed.
- The author or configured memory curator approves automatic graduation.

Default durable outputs:

- User decisions and explicit preferences.
- Goals, plans, task transitions, artifact versions, invocation receipts.
- Verified claims and their evidence.
- Reusable skills/capabilities and evaluation results.
- Summaries needed to resume work.

Default non-durable outputs:

- Token-by-token reasoning.
- Duplicate retrieval passages.
- Rejected candidates without diagnostic value.
- Raw credentials.
- Incidental sensitive data.

## 9. Synchronization

- Peers exchange realm identity, frontier, reducer compatibility, and event availability summaries.
- Sync transfers immutable event/body/blob records, then verifies locally.
- Merge is DAG union; no peer can overwrite another peer’s history.
- Capability and policy events may be realm-local and encrypted for designated recipients.
- A peer lacking a body may retain an event skeleton and encrypted blob reference.
- Conflicts are surfaced by Cell reducers, not hidden by transport.

## 10. Redaction and garbage collection

`RETRACT` is a typed lifecycle event, not a delete button. Its first effect is
logical: reducers compute effective Cell state from the target event, the
retraction mode, policy, type semantics, and frontier. Physical deletion is a
later garbage-collection act and is permitted only for redaction-covered bytes.

Mode semantics:

| Mode | Meaning | Projection effect | History/bytes |
|---|---|---|---|
| `WITHDRAW` | The author or authorized curator withdraws an assertion or attestation from their authority domain. | The target stops contributing to live Cell state where the retractor has authority. Other independent assertions remain live. | Target event and payload remain available. |
| `SUPERSEDE` | The target is obsolete because a replacement event carries the current assertion. | Current-state projections prefer `replacement`; audit and temporal projections retain both and expose the supersession edge. | Target event and payload remain available unless also redacted by a separate policy. |
| `REVOKE` | A grant, lease, or capability is no longer effective. | Authority fails closed at and after the effective frontier; descendants covered by cascade are invalidated. | Grant history remains available for audit. |
| `REDACT` | Payload availability must be removed from normal projections and deletion policy starts. | Normal projections expose only allowed skeleton metadata; search, graph, summaries, caches, and exports must drop payload-derived material. | Event skeleton remains unless stronger law/policy requires removal; payload bytes become GC candidates after eligibility checks. |
| `TERMINATE` | A state-machine object is ended: session, run, lease tree, task, or long-lived process. | Reducers move the target to a terminal state and reject further non-compensating transitions after the effective frontier. | History remains available; payloads are not erased unless separately redacted. |

### 10.1 SUPERSEDE versus REDACT

`SUPERSEDE` is about correctness and currency. It says "do not use this target as
the current answer; use the replacement." It does not hide the old payload and
does not satisfy deletion requests.

`REDACT` is about availability and erasure. It says "this payload must leave
normal projections and may be physically erased when policy allows." A redacted
event may still be superseded for semantic clarity, but redaction must not depend
on supersession.

Plain `WITHDRAW`/`REVOKE`/`TERMINATE` are also not erasure. They change effective
state; they do not remove accepted bytes.

### 10.2 Cascade

Cascade is explicit in the `RETRACT` body (`WEFT §5`) so reducers do not guess.

| Cascade | Scope | Required behavior |
|---|---|---|
| `NONE` | Only listed targets. | Apply the mode to exactly the targets. Derived projections invalidate only because their inputs changed. |
| `DERIVED_AUTHORITY` | Grants, leases, invocations, and assertions whose authority descends from the target. | Used for capability revocation and authority withdrawal. Reducers mark covered descendants ineffective after the frontier and fail closed on ambiguous ancestry. |
| `LEASE_TREE` | A lease/session/process subtree rooted at the target. | Used for `TERMINATE` and time-bounded authority. Reducers end the subtree, reject later work under it, and preserve receipts/results already completed before the effective frontier. |

For `REDACT`, cascade controls payload-derived material, not unrelated history.
If a redacted source was summarized, embedded, indexed, quoted, or copied into a
derived Cell, that derived payload is invalidated and must be rebuilt without the
redacted material or separately redacted. Independent evidence for the same
claim may remain.

### 10.3 Erasure and garbage collection

GC eligibility requires all:

1. Payload is covered by an effective `REDACT`.
2. Retention/legal-hold policies allow deletion.
3. No live assertion, receipt, snapshot, export pin, or audit policy requires the bytes.
4. Required replicas acknowledge the redaction frontier or their keys are revoked.
5. Grace period elapsed.

Preferred deletion mechanism:

- Encrypt blobs with per-object or per-erasure-domain data keys.
- Destroy the data key first (cryptographic erasure).
- Sweep physical bytes and derived indexes.
- Preserve a minimal signed event skeleton unless law/policy requires its removal.

Content addressing creates a privacy trap if raw hashes are globally guessable. Blob IDs are realm-domain-separated; sensitive blobs are encrypted and access-controlled. Deduplication across security realms is forbidden by default.

## 11. Invariants to test

- Replay from genesis and replay from any valid snapshot produce the same state root.
- Event arrival order does not change a frontier’s state.
- Duplicate delivery is harmless.
- Revoked authority cannot authorize descendants after its effective frontier.
- Derived capability scope is never broader than its parent.
- External effects are never repeated by projection replay.
- Redacted payloads disappear from every derivative projection.
- Unknown/ambiguous external execution resolves to `UNKNOWN`, never fabricated success or failure.
