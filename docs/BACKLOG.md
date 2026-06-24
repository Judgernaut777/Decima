# Decima — Build Backlog

The shared board for multi-instance work. One source of truth for **what's next,
who can take it, and how not to collide.**

> Decima is built in the **Python reference** until the design stops moving; the
> single Rust port is the **last** step (see [`VISION.md`](../VISION.md)).

## Status

**Cycle 1 — ✅** A1/A2/F1 · B1/B2 · A3 · C1 · E1.
**Cycle 2 — ✅** D1 (CLI worker) · D2 (sessions) · D3 (org policy). Zero `smoke.py` conflicts — the `checks/` harness held.
**Cycle 3 — ✅** **M1** (merge layer: concurrent fork + LWW/OR-set, MV heads preserved) · B3 (memory maturation) · C2 (router engines) · S1 (`SYNC.md`) · S2 (`SNAPSHOTS.md`).
**Tooling — ✅** `heartbeat/checks/NN_*.py` auto-run by `smoke.py`; new lanes add a file there, never edit `smoke.py`.

Oracle: **7/8 FOLD §11 hold, 1 partial** (RETRACT). M1 made arrival-order
independence **genuinely concurrent** (`checks/70`), not trivially linear.

## Coordination rules

1. **Core-kernel files serialize:** `weave.py`, `weft.py`, `kernel.py`, `executor.py`
   — **one owner per cycle.** Everyone else builds in new modules and *calls* the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, not `smoke.py`.** Own a free `NN`.
3. **`specs/` is collision-free** — one instance per file.
4. Keep the oracle green: `cd heartbeat && python3 smoke.py` → `alive. ✓`, exit 0.

## Cycle 4 — active (Claude instances only)

Building out the merge layer M1 started, and turning the `SNAPSHOTS.md` design into
running code.

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **M2** | **Remaining merge classes** — on M1's substrate, add **Sequence CRDT** (ordered text/blocks: stable element ids + tombstones), **Map CRDT** (per-key merge class), and the **semantic-adjudication** path (preserve branches; an `ATTEST` collapses them) — plus Counter / Append-log / State-machine as they fit. Assign classes to Cell types per `specs/MERGE_SEMANTICS.md`. **Also refresh the §11/PROFILE wording** (merge is no longer "deferred"). | `weave.py` (core, single-owner) + `checks/71_merge_advanced.py`; plus the `smoke.py` §11 line + `heartbeat/PROFILE.md` wording | **P0** | `checks/71`: a Sequence and a Map type converge under concurrent forks; an adjudication `ATTEST` collapses MV heads to one resolved value; §11/PROFILE wording matches landed merge |
| **SN1** | **Snapshots, first increment** — realize `specs/SNAPSHOTS.md`: a new module that builds a `SnapshotManifest` (`state_root` + chunked CellState), restores it with **hash + root verification**, and proves **fold-from-snapshot ≡ genesis fold** by re-folding and comparing roots. Uses the **public** `Weave.state_root()` / `fold` only. | `snapshot.py` (new) + `checks/76_snapshots.py` | P1 | `checks/76`: snapshot a frontier, restore + verify `state_root`, prove it equals a genesis fold to that frontier, and a corrupted chunk is rejected. (Incremental fold-from-base — the perf win — deferred to a core cycle.) |
| **SY1** *(optional, 3rd instance)* | **Sync convergence in the reference** — simulate two peers as event subsets of a forked Weft, **union** them, fold, and assert convergence + that a grant revoked in one branch can't re-authorize across the union. A reference exercise of `specs/SYNC.md` — no network. | `checks/78_sync_sim.py` (+ a tiny helper module if needed) | P2 | `checks/78`: union of two peers' events → identical `state_root` regardless of apply order; revoked authority stays revoked post-union |

**Collision note:** only **M2** touches core (`weave.py`) and the shared `smoke.py`
§11 line + `PROFILE.md`. **SN1** is a new module + `checks/76`; **SY1** is `checks/78`.
Disjoint files. SN1/SY1 *read* merge/fold behavior M2 is changing (logical coupling,
no file conflict) — **land M2 first**, or expect SN1/SY1 to re-verify on rebase.

## Suggested allocation (Cycle 4 — all Claude)

- **Instance 1 — Claude / kernel** (`~/decima-claude`): **M2** — sole owner of `weave.py` this cycle.
- **Instance 2 — Claude / worktree**: **SN1** — `snapshot.py`.
- **Instance 3 — Claude / worktree** *(optional)*: **SY1** — `checks/78`.

*(No Codex lanes this cycle, by request.)*

## Backlog (future cycles)

- **Snapshots, incremental fold-from-base** — the performance win (skip genesis); needs a core change to fold onto a restored base state.
- **Typed retraction in the reference** — `REDACT` + cryptographic erasure → closes §11 #7 fully (core).
- **Scratch Weft + graduation**; **real sandboxing** (landlock/seccomp); **budget folded into the Weft**.
- **Real sync transport** (the network layer behind `SYNC.md`); **real engines** behind the router.
- **The Rust port** — last, once the reference is stable and complete.

## Pick-up-cold briefs (Cycle 4)

### M2 — Remaining merge classes `weave.py` + `checks/71_merge_advanced.py`
**Why:** M1 landed LWW + OR-set + MV-preserve. The remaining `MERGE_SEMANTICS.md`
classes are what collaborative text, structured docs, and plan/schema merges need.
**Deliverable:**
  1. **Sequence CRDT** — ordered elements with stable ids + tombstones; concurrent
     inserts converge by a deterministic order (e.g. id tiebreak), deletes are tombstones.
  2. **Map CRDT** — per-key declared merge class; keys merge independently.
  3. **Semantic adjudication** — branches preserved (like MV); an `ATTEST`
     (predicate e.g. `merge_resolved`) collapses the heads to one chosen/derived value,
     recorded as the resolution (no silent AI merge — policy/trusted principal attests).
  4. Counter / Append-log / State-machine per `MERGE_SEMANTICS.md` as they fit.
  5. Tag the relevant Cell types with their class (via `model.py`/`TYPE_DEF`).
  6. **Refresh wording:** the `smoke.py` §11 arrival-order line and `PROFILE.md` no longer
     say merge is "deferred" — it's implemented (genuine concurrency proven in `checks/70`/`71`).
**Acceptance:** `checks/71` forks a Sequence type and a Map type and shows convergence;
shows an adjudication `ATTEST` collapsing MV heads; all prior demos + `checks/70` green.
**Lane:** you own `weave.py` this cycle. Demo in `checks/71`; the only `smoke.py` edit
allowed is the §11 wording line.

### SN1 — Snapshots, first increment `snapshot.py` + `checks/76_snapshots.py`
**Why:** `SNAPSHOTS.md` is designed and `Weave.state_root()` exists — make a snapshot
a real, verifiable cache.
**Deliverable:** a new module that, given a frontier (`upto_seq`), folds, builds a
`SnapshotManifest` (`state_root` + chunked CellState as content-addressed blobs),
and `restore()`s by fetching chunks, verifying each hash, reassembling, and checking
the recomputed root == manifest root. Prove **fold-from-snapshot ≡ genesis** by
re-folding to the frontier and comparing roots. Use only the **public** Weave/Weft API.
**Acceptance:** `checks/76`: snapshot at a frontier, restore + verify, equality to a
genesis fold, and a corrupted chunk is rejected. Note in comments that incremental
fold-from-base (skipping genesis) is deferred to a core cycle.
**Lane:** `snapshot.py` + `checks/76`. No core edits — call `Weave.state_root()`/`fold`.

### SY1 — Sync convergence (optional) `checks/78_sync_sim.py`
**Why:** exercise `SYNC.md`'s convergence + no-re-authorization invariants in the
reference without a network.
**Deliverable:** simulate two peers as disjoint-ish subsets of a **forked** Weft's
events; union them; fold; assert identical `state_root` independent of apply order;
and assert a grant revoked in one branch does not re-authorize across the union
(authorization at the parent frontier).
**Acceptance:** `checks/78` proves union convergence + revocation-respected.
**Lane:** `checks/78` (+ a tiny helper if needed). No core edits, no `smoke.py` edit.
