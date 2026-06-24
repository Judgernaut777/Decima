# Snapshots — manifest, restore, and replay verification

**Status:** design (S2). No code. Expands `FOLD_AND_LIFECYCLE.md` §6 into an
implementation-ready contract, and names `Weave.state_root()` (heartbeat
`weave.py`) as its linear-profile seed. Read alongside `FOLD §2` (deterministic
fold), §3 (CellState), §5 (incremental materialization), §10 (GC/redaction), §11
(invariants), and `SYNC.md` (cold-peer bootstrap).

A snapshot is a **verifiable cache of materialized state at a frontier — never
truth.** Law 5 stands: the Weft is the only truth; a snapshot just lets the fold
start from a checkpoint instead of genesis. Everything here is built so a snapshot
can be *cheaply distrusted* — recomputed and compared — because a cache that can't
be verified is a second source of truth, which the system forbids.

---

## 1. Why

Folding from genesis is O(history) and unbounded. A snapshot records the folded
`CellState` at a causal frontier so restore + a **delta fold** of only the events
above that frontier reconstructs the present. The discipline: a snapshot is
**content-addressed, signed, reducer-versioned, and independently re-derivable**,
so it is provably equal to the genesis fold or it is rejected.

## 2. `SnapshotManifest`

```text
SnapshotManifest {
  1: protocol        uint
  2: realm           RealmId
  3: frontier        [EventId]            // sorted; the causal point this captures
  4: event_count     uint                 // events folded into it (sanity / monitoring)
  5: state_root      Hash                 // Merkle root over canonical CellState leaves (§3)
  6: reducer_set     [{name, version, hash}]   // the reducers that produced it
  7: schema_frontier [EventId]            // active schema/type-policy events at capture
  8: chunks          [{ cell_range, blob_id, hash, count }]   // §4
  9: created_by      PrincipalId
 10: created_at      int?                 // wall time — informational, untrusted
 11: sig_alg         uint
 12: signature       bytes                // over manifest-unsigned-bytes
}
```

The manifest is small and self-verifying: `state_root` commits to the full state,
`reducer_set` says *which code* produced it, `frontier` says *as of when*, and each
chunk is independently hash-checkable.

## 3. The `state_root` (what is hashed)

`state_root` is a Merkle root over **canonical `CellState` leaves**, one per live
Cell, ordered by `cell_id`:

- A leaf is the canonical encoding of the durable `CellState` (`FOLD §3`):
  `cell_id, type_heads, content_heads, edges_out/in, grants, leases, attestations,
  conflicts, reducer_version` — domain-separated (`WEFT §1`), no floats, NFC text.
- The tree is a binary Merkle over the cell-id-sorted leaves, so a restore can prove
  any single cell against the root, and two snapshots diff cheaply.

The heartbeat's `Weave.state_root()` is the **linear-profile seed**: today it digests
the folded cells (id, type, content, version, retraction, edges, attestations) into
one root and is already used by the `§11` replay-determinism check. The durable form
generalizes it to plural-head `CellState` and Merkle chunking — same role, richer leaf.

## 4. Chunking

`CellState` is partitioned by **cell-id ranges** (aligns with `FOLD §5` partitioning).
Each chunk is a content-addressed blob `{cell_range, blob_id, hash, count}`. Chunking
enables partial restore (fetch only the ranges you need), dedup across snapshot
generations (unchanged ranges share a blob_id), and parallel verification.

## 5. Creation is on the Log

A snapshot is **an effect, not a side door**:

1. `INVOKE` a `snapshot` capability (effect_class `READ`) over a frontier.
2. The executor folds to that frontier, emits the chunks (as blobs) and the manifest.
3. The manifest is **`ASSERT`ed as a Cell** and **`ATTEST`ed** (`predicate:
   snapshot_verified`) by the creator — and, ideally, by an *independent rebuilder*
   (§7). Creation, like everything, is recorded with provenance.

Snapshots are thus themselves foldable history: "which snapshots exist, who built and
verified them" is a projection of the Weft.

## 6. Restore

```text
restore(manifest):
  verify manifest.signature and protocol/realm
  require manifest.reducer_set == my reducer_set   // else REBUILD (§8) — never fold with mismatched reducers
  for chunk in manifest.chunks:
      bytes = fetch(chunk.blob_id); require hash(bytes) == chunk.hash
      load CellState leaves from chunk
  require merkle_root(all leaves, sorted by cell_id) == manifest.state_root
  base_state := assembled CellState at manifest.frontier
  return base_state            // ready for the delta fold (§7)
```

Any failure — bad signature, reducer mismatch, chunk hash, or root mismatch —
**rejects the snapshot**; fall back to an older verified snapshot or to genesis.
A snapshot is never partially trusted.

## 7. Fold-from-snapshot (equivalent to genesis)

Per `FOLD §2`:

```text
fold(target_frontier):
  base   = newest verified snapshot whose frontier is an ancestor of target_frontier
  events = causal_difference(base.frontier, target_frontier)
  apply events in (lamport, event_id) order onto base.state
```

This must produce the **same `state_root` as a genesis fold** to `target_frontier`
(`§11.1`). That equality is the whole contract: the snapshot is only ever a cache.

## 8. Verification regime (cheaply distrust the cache)

1. **Restore-time:** every chunk hash + the `state_root` are checked before the base
   state is used (§6).
2. **Sampled cross-check:** on restore, re-fold a **random sample** of cells from
   genesis (or from an older independent snapshot) and compare to the restored
   leaves.
3. **Periodic full replay:** on a schedule, replay from genesis, compute `state_root`,
   and compare to the snapshot's — this *is* the `§11.1` invariant test. A mismatch
   marks the snapshot **and the projection** unhealthy and rebuildable; it is never
   silently served.
4. **Independent rebuild:** keep at least one checkpoint rebuilt by a **different
   reducer build** (§ `reducer_set`) to catch reducer bugs a self-consistent snapshot
   would hide.

## 9. Reducer-version coupling

`state_root` is a function of `(CellState, reducer_version)`. A snapshot is valid
**only** for the `reducer_set` that built it; a reducer upgrade invalidates existing
snapshots, which must be rebuilt. This is why `reducer_set` is in the manifest and
why restore refuses a reducer mismatch (§6). Reducers are versioned by content hash
(`FOLD §2`), so "valid for this code" is a precise, checkable claim.

## 10. Snapshots never authorize

A restored snapshot is a **materialization cache**; authority is always evaluated
from causal grant history (`FOLD §6`), never from a snapshot. Restoring cannot grant,
revoke, or approve anything — every event above the frontier is still validated and
authorized from the Weft on the way in. A forged or stale snapshot can at worst make
the fold *unhealthy* (caught by §8), never *over-authorized*.

## 11. Cadence (adaptive)

Trigger snapshotting on, in combination:

- **event-count delta** since the last snapshot (replay-cost proxy);
- **measured replay cost** (time to rebuild) crossing a budget;
- **revocation pressure** — revocations get a high-priority invalidation lane
  (`FOLD §5`); snapshot after a burst so revoked authority drops out of the cache fast;
- **schema/reducer change** — a new reducer set invalidates old snapshots (§9);
- **graceful shutdown** — checkpoint so restart is fast.

Keep **multiple generations** plus **≥1 independently rebuilt** checkpoint (§8.4).

## 12. GC and redaction interaction

A snapshot **pins** the bytes it references, so it participates in GC eligibility
(`FOLD §10`): a `REDACT` must invalidate or rebuild every snapshot covering the
redacted payload, or the cache would resurrect erased data. Cryptographic erasure
(destroy the per-object data key) plus snapshot rebuild is the path; the manifest's
chunk list makes "which snapshots touch this cell" answerable.

## 13. Invariants to test (with `FOLD §11`)

- **Replay equality:** fold-from-snapshot to a frontier == genesis fold to that
  frontier, byte-for-byte `state_root` (§11.1).
- **Tamper-evidence:** a corrupted chunk fails its hash; a corrupted leaf set fails
  the `state_root`; restore rejects both.
- **No over-authorization:** restoring a snapshot grants nothing; a revoked grant
  stays revoked after restore + delta fold.
- **Redaction propagation:** a redacted payload is absent from every snapshot's
  projection after rebuild.
- **Reducer safety:** a snapshot built by reducer vN is refused (not silently folded)
  by reducer vM≠N.
