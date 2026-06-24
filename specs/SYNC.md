# Synchronization — peer sync, DAG union, conflict surfacing

**Status:** design (S1). No code. Expands `FOLD_AND_LIFECYCLE.md` §9 into an
implementation-ready protocol. Read alongside `WEFT_PROTOCOL.md` §2 (validation),
§9 (ordering), `FOLD_AND_LIFECYCLE.md` §3/§5/§10, `MERGE_SEMANTICS.md` (how
concurrent assertions resolve once synced), and `SNAPSHOTS.md` (bootstrap).

Sync is the reason the merge layer exists: peers can assert to the same Cell
concurrently, so reconciliation is **union of immutable, signed events**, never a
transfer of "current state." No peer can overwrite another's history; conflicts
are *surfaced* by the Cell reducers, never resolved by transport.

---

## 1. Model

- A **realm** is the unit of sync (workspace/security boundary, `WEFT §2` field 2).
  Peers sync one realm at a time; capability/policy events may be realm-local.
- A peer's knowledge of a realm is its **frontier**: the set of events with no
  known causal descendant (the DAG's leaves). The frontier names the whole history
  below it.
- **Sync = make two peers' event sets converge.** Merge is **DAG union**; the fold
  (`MERGE_SEMANTICS`) materializes the union. Because identity is content+cause
  (`WEFT §1`) and the fold is idempotent by Event ID (`FOLD §2`), re-delivering an
  event is harmless and order does not matter.

Sync never moves Cells, snapshots, or "latest values" as authority. It moves
**event/body/blob records**; everything else is recomputed locally.

## 2. Session handshake

```text
Hello {
  protocol      uint
  realm         RealmId
  reducer_set   [{name, version, hash}]   // see FOLD §6 / SNAPSHOTS — must agree to materialize
  frontier_digest  bytes                  // compact summary of the local frontier (§3)
  capabilities  { wants_bodies, wants_blobs, encrypted_only, ... }
}
```

1. **Realm + protocol match** or abort (`WEFT §10`: unknown protocol fails closed).
2. **Reducer compatibility.** If `reducer_set` hashes differ, the peers may still
   exchange and verify **events** (events are reducer-independent), but **must not**
   trust each other's *materialized* state; each folds with its own reducers. A peer
   on an older reducer quarantines projections it can't reproduce rather than
   guessing. This keeps §11.1 (replay determinism) honest across versions.
3. Exchange **frontier digests**, not full histories.

## 3. Set reconciliation (who has what)

The goal is to compute `causal_difference` in both directions cheaply.

- **Frontier exchange + walk.** Send frontier event ids; the receiver requests
  ancestors it lacks, walking down until it reaches events it already has. Simple,
  exact, O(difference).
- **Have/want with digests.** For wide divergence, exchange a compact set summary
  (e.g. an IBLT or range-fingerprint over event ids in lamport buckets) to find the
  symmetric difference without a full frontier walk. Optional optimization; the
  frontier walk is the baseline and is always correct.
- **Partial sync.** A peer may request only a Cell subtree, a label, or a lamport
  range — useful for mobile/thin peers. Partial sync still verifies every event it
  accepts; it just bounds *which* events it asks for, and records the bound
  (so a thin peer never mistakes "didn't fetch" for "doesn't exist").

## 4. Transfer and local verification (trust nothing from the wire)

Records move as immutable, content-addressed bytes: `EventRecord`, `Body`, `Blob`.
For **every received event**, the receiver runs the full `WEFT §2` acceptance
pipeline before it counts as known:

1. Canonical-decode; reject noncanonical bytes.
2. Recompute body id and event id; reject on mismatch (tamper-evident).
3. Verify the author key was valid **at the event's causal point**, then verify the
   signature.
4. Every parent must exist, or the event is **quarantined as an orphan** until its
   parents arrive (withholding a parent stalls a branch; it cannot forge one).
5. `lamport = 1 + max(parent.lamport)` (or 0 at genesis).
6. **Authorization is verified at the parent frontier, never against mutable
   "current" state** (`WEFT §2.7`). This is what stops a revoked grant from
   re-authorizing via sync (§11: revoked authority cannot authorize descendants
   after its effective frontier).
7. Verb-specific validation, then append atomically.

Transport is untrusted: a peer can withhold or reorder, but cannot forge
(content-addressed + signed) or overwrite (union, no UPDATE). Bad bytes, bad
signatures, and impossible lamports are dropped, not quarantined.

## 5. Bodies and blobs (skeletons)

An event references its body and blobs by content id. A peer may hold the **event
skeleton** without the body — e.g. it relays for others, or lacks decryption keys.

- Bodies/blobs are fetched by content id with separate have/want; verified by hash
  on receipt (`WEFT §1` domain-separated digest).
- **Blob ids are realm-domain-separated** (`FOLD §10`): dedup across security realms
  is forbidden by default, so a content hash from realm A is not guessable/fetchable
  in realm B.
- A skeleton-only peer can still sync the DAG shape and relay; it just can't
  materialize Cells whose bodies it lacks (they appear as "present but opaque").

## 6. Encrypted, realm-local capability/policy events

Capability and policy events may be **encrypted for designated recipients**
(`FOLD §9`). A peer without the key keeps the skeleton + an encrypted blob
reference: it participates in causal ordering and relay, but the grant/policy is
opaque to it. Authorization that depends on an opaque event is **undecidable for
that peer**, not "allowed" — fail closed.

## 7. Conflicts are surfaced, never hidden

Transport **must not** resolve concurrency. After union, the Cell reducers compute
`heads(cell, conflict_key)` by causal dominance (`MERGE_SEMANTICS §2`):

- mechanically-resolvable classes (LWW, OR-set, counter…) collapse to one value;
- MV / semantic-adjudication classes **preserve plural heads** and mark the Cell
  in conflict until an `ATTEST` adjudicates (`MERGE_SEMANTICS §4`).

A synced peer therefore *sees* a conflict as inspectable state, with both branches
and their provenance — it is never silently overwritten by "whoever synced last."

## 8. Anti-entropy and liveness

- Periodic background sync; gossip frontiers opportunistically.
- Sync is **resumable and idempotent** — interrupt at any point, re-run, converge;
  duplicates are no-ops (`FOLD §2`).
- Bootstrap a cold peer from a **verified snapshot** (`SNAPSHOTS.md`): restore at a
  frontier (verifying `state_root`), then sync only the delta above it. A snapshot
  **never authorizes** events — the restored peer still validates and authorizes
  every event from the Weft.

## 9. Security properties (what an adversarial peer can and cannot do)

| can | cannot |
|---|---|
| withhold events/bodies (stalls a branch — visible as a frontier gap) | forge an event (content-addressed + signed) |
| reorder / duplicate delivery | overwrite another peer's history (union, no UPDATE) |
| send garbage (dropped at §4 validation) | re-authorize a revoked grant (auth at parent frontier) |
| relay opaque encrypted events | read realm-local encrypted events without keys |
| eclipse a peer (partition) | make a peer accept invalid causal/lamport structure |

Eclipse/withholding is *surfaced* (frontier gaps, stalled orphans), not silently
tolerated; recovery is sync with any honest peer.

## 10. Invariants to test (with `FOLD §11`)

- **Convergence:** any two peers that exchange to a common frontier compute the same
  `state_root` (arrival-order independence, §11.2; duplicate delivery harmless).
- **No overwrite:** merge is union; no peer's accepted event is ever dropped by sync.
- **Revocation respected:** an event authorized by a grant revoked before that
  event's causal point is rejected on receipt, on every peer.
- **Orphan safety:** an event whose parents never arrive stays quarantined forever,
  never materializes.
- **No effect replay:** syncing an `INVOKE` + its receipt never re-executes the
  effect (`§11`); only the recorded receipt is folded.
- **Redaction propagates:** a `REDACT` (`FOLD §10`) synced to a peer removes the
  payload from that peer's projections too.
