# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle (4) — Claude instances only.** Tasks/lanes
live in [`../docs/BACKLOG.md`](../docs/BACKLOG.md); this is the operational layer.

**Two hard rules:**
1. `weave.py` / `weft.py` / `kernel.py` / `executor.py` are **owned by the merge
   instance (M2)** this cycle. No one else edits them — post a request instead.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`.** Own a free
   `NN`. (The one exception: M2 may edit the `smoke.py` §11 *wording* line.) See
   `heartbeat/checks/README.md`.

Bootstrap any lane first: `scripts/kickoff.sh <dir> <branch>`

**Land M2 first** where possible — SN1/SY1 read merge/fold behavior it changes
(different files, so no merge conflict, but a rebase + re-verify is cleanest).

---

## Instance 1 — Claude · merge classes (core)  (clone: `~/decima-claude`)

**Task:** M2. **Owns:** `heartbeat/decima/weave.py`, `model.py`,
`heartbeat/checks/71_merge_advanced.py` (new); the `smoke.py` §11 wording line; `heartbeat/PROFILE.md`.
**Must not touch:** `memory.py`, `retrieval.py`, `router.py`, `agent.py`, `snapshot.py`, other `checks/` files.

```text
You are the Claude merge instance for Decima, in ~/decima-claude. Read docs/BACKLOG.md
(brief M2), specs/MERGE_SEMANTICS.md, and heartbeat/checks/README.md first.

Task M2 — branch claude/m2-merge-classes — build the remaining merge classes on M1's
substrate (M1 already did LWW + OR-set + MV-preserve in weave.py):
  1. Sequence CRDT — ordered elements, stable ids + tombstones; concurrent inserts converge.
  2. Map CRDT — per-key declared merge class; keys merge independently.
  3. Semantic adjudication — preserve branches (like MV); an ATTEST (e.g. predicate
     merge_resolved) collapses the heads to one recorded resolution (no silent AI merge).
  4. Counter / Append-log / State-machine per MERGE_SEMANTICS.md as they fit.
  5. Tag relevant Cell types with their class via model.py / TYPE_DEF.
  6. Refresh the stale wording: the smoke.py §11 arrival-order line and PROFILE.md no longer
     say merge is "deferred" — it's implemented (concurrency proven in checks/70 & 71).
  Demo in a NEW file heartbeat/checks/71_merge_advanced.py exposing run(k, line): fork a
  Sequence type and a Map type, show convergence; show an adjudication ATTEST collapsing MV
  heads. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude claude/m2-merge-classes
You OWN weave.py/weft.py/model.py this cycle. Demo in checks/71; the ONLY smoke.py edit
allowed is the §11 wording line. Keep the oracle green (cd heartbeat && python3 smoke.py →
"alive ✓", exit 0; checks/70 must stay green). Commit small; git pull --rebase; push your
branch; fast-forward to main when green.
```

---

## Instance 2 — Claude · snapshots  (worktree, e.g. `~/decima-claude-snap`)

`git worktree add ~/decima-claude-snap claude/sn1-snapshots`.

**Task:** SN1. **Owns:** `heartbeat/decima/snapshot.py` (new),
`heartbeat/checks/76_snapshots.py` (new).
**Must not touch:** `weave.py`, `weft.py`, `kernel.py`, `model.py`, `smoke.py`.

```text
You are a Claude snapshots instance for Decima, in a dedicated worktree (NOT the main
~/decima-claude tree). Read docs/BACKLOG.md (brief SN1), specs/SNAPSHOTS.md, and
heartbeat/checks/README.md first.

Task SN1 — branch claude/sn1-snapshots — realize SNAPSHOTS.md's first increment:
  New module heartbeat/decima/snapshot.py: given a frontier (upto_seq), fold, build a
  SnapshotManifest (state_root + chunked CellState as content-addressed blobs), and restore()
  by fetching chunks, verifying each hash, reassembling, and checking recomputed root ==
  manifest root. Prove fold-from-snapshot ≡ genesis by re-folding to the frontier and
  comparing roots. Use ONLY the public Weave.state_root()/fold API. Demo in a NEW file
  heartbeat/checks/76_snapshots.py exposing run(k, line): snapshot a frontier, restore +
  verify, equality to a genesis fold, and a corrupted chunk is REJECTED. Note in comments
  that incremental fold-from-base (skipping genesis) is deferred to a core cycle. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-snap claude/sn1-snapshots
Stay in snapshot.py + checks/76. Do NOT edit any core file or smoke.py — call the public API.
Keep the oracle green. Commit small; git pull --rebase; push your branch; fast-forward to
main when green.
```

---

## Instance 3 — Claude · sync convergence  (optional; worktree `~/decima-claude-sync`)

`git worktree add ~/decima-claude-sync claude/sy1-sync-sim`.

**Task:** SY1. **Owns:** `heartbeat/checks/78_sync_sim.py` (new) (+ a tiny helper module if needed).
**Must not touch:** any core file, `snapshot.py`, `smoke.py`.

```text
You are a Claude sync-sim instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md
(brief SY1), specs/SYNC.md, and heartbeat/checks/README.md first.

Task SY1 — branch claude/sy1-sync-sim — exercise SYNC.md in the reference (no network):
  Simulate two peers as event subsets of a FORKED Weft; union them; fold; assert identical
  state_root regardless of apply order; and assert a grant revoked in one branch does not
  re-authorize across the union (authorization at the parent frontier). Demo in a NEW file
  heartbeat/checks/78_sync_sim.py exposing run(k, line). Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-sync claude/sy1-sync-sim
Stay in checks/78 (+ a tiny helper if needed). No core edits, no smoke.py edit. Keep the
oracle green. Commit small; git pull --rebase; push your branch; fast-forward to main when green.
```

---

## Notes
- **Claude only this cycle** (by request). All three lanes push over the `~/decima-claude` SSH
  deploy key; fast-forward small green changes to `main` (no token needed for PRs).
- **M2 is the cycle's critical path** (it's the core lane and SN1/SY1 build on its behavior).
  If it overruns, land Sequence-CRDT first and carry Map/adjudication to a follow-up.
- **Next cycle:** incremental fold-from-base (snapshot perf), typed retraction/REDACT, and the
  real sync transport — see `docs/BACKLOG.md` "Backlog (future cycles)".
