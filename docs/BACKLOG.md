# Decima — Build Backlog

The shared board for multi-instance work (Claude, Codex, …). One source of truth
for **what's next, who can take it, and how not to collide.**

> Decima is built in the **Python reference** until the design stops moving; the
> single Rust port is the **last** step (see [`VISION.md`](../VISION.md) "How we
> build it").

## Status

**Cycle 1 — ✅** A1/A2/F1 (receipts; closed §11 #8) · B1/B2 (memory taxonomy + retriever) · A3 · C1 (router) · E1 (powerbox).
**Cycle 2 — ✅** D1 (real CLI worker) · D2 (session Cells) · D3 (learned org policy). Landed with **zero `smoke.py` conflicts** — the `checks/` harness held.
**Tooling — ✅** `heartbeat/checks/NN_*.py` auto-run by `smoke.py`. New lanes add a file there; **never edit `smoke.py`.**

Oracle: **7/8 FOLD §11 invariants hold, 1 partial** (RETRACT).

## Coordination rules (the collision model)

1. **Core-kernel files serialize:** `weave.py`, `weft.py`, `kernel.py`, `executor.py`
   — **one owner per cycle.** Everyone else builds in new modules and *calls* the
   public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, not `smoke.py`.** Own a free `NN`.
3. **`specs/` is collision-free** — one instance per file.
4. Keep the oracle green: `cd heartbeat && python3 smoke.py` → `alive. ✓`, exit 0.

## Cycle 3 — active

This cycle the **core lane gets the hard, design-defining task** (the merge layer),
while two new-module lanes run in parallel.

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **M1** | **Merge layer, first increment** — make the Weft support **concurrent events** (a fork: two events sharing a parent), fold them deterministically, and apply per-type **merge classes** from `specs/MERGE_SEMANTICS.md`: implement **LWW register** + **OR-set** first; **preserve concurrent heads** for MV types | `weave.py`, `weft.py` (+ `model.py` for the type's merge-class tag) — **core, single-owner** + `checks/70_merge.py` | **P0** | a forked Weft folds to the same `state_root()` in either arrival order; LWW & OR-set resolve correctly; MV heads preserved; `checks/70` upgrades §11 #2 from *linear* to *genuinely concurrent* |
| **B3** | **Memory maturation** — recall **decay** (recency/heat weighting), **consolidation** (supersede near-duplicates, provenance preserved), **heat/promotion** signal | `memory.py`, `retrieval.py` + `checks/72_memory_maturation.py` | P1 | `checks/72`: a decayed claim ranks lower, dupes consolidate with kept provenance, heat rises on recall |
| **C2** | **Router → engines** — tiers map to **vendor-neutral engines** (config/env); the chosen tier selects an engine to invoke; run the **deterministic verifier** when one exists, else a **judge/critic** fallback. Offline-safe stubs; real model call is the seam | `router.py`, `agent.py` (+ `verifier.py` new) + `checks/74_router_engines.py` | P1 | `checks/74`: tasks route to engines; a verifiable task runs its verifier; non-verifiable falls back to judge; router still confers **zero authority** |

**Collision note:** only **M1** touches core (`weave.py`/`weft.py`) — its single owner.
B3 stays in `memory.py`/`retrieval.py`; C2 in `router.py`/`agent.py`/`verifier.py`.
Each adds its own `checks/` file. Disjoint — no shared file across lanes.

## Suggested allocation (Cycle 3, ≈3 instances)

- **Instance 1 — Claude / kernel** (`~/decima-claude`): **M1** — the merge layer; sole owner of `weave.py`/`weft.py` this cycle. The hard one.
- **Instance 2 — Codex** (`~/decima-codex`): **B3** — memory maturation, its domain.
- **Instance 3 — Claude / worktree**: **C2** — router engines + verifier/judge.

## Backlog (future cycles)

- **Merge layer, later increments** — Sequence CRDT (collaborative text), Map CRDT, Counter, **semantic adjudication** (attested merge of plans/schemas). The remaining classes from `MERGE_SEMANTICS.md`.
- **Snapshots** — `SnapshotManifest`, restore-verify, periodic full-replay (`state_root()` is the seed).
- **Scratch Weft + graduation**; **typed retraction modes** in the reference (REDACT → closes §11 #7 fully); **real sandboxing** (landlock/seccomp/Firecracker); **budget folded into the Weft**.
- **The Rust port** — last, once the reference is stable and complete.

## Pick-up-cold briefs (Cycle 3)

### M1 — Merge layer, first increment `weave.py`/`weft.py` + `checks/70_merge.py`
**Why:** the Weft is **linear today** (one head); the durable system is a DAG where
concurrent events merge deterministically. This is the genuinely unsolved core, and
it's what makes §11 arrival-order independence *real* instead of trivially-true. The
design is already on paper in `specs/MERGE_SEMANTICS.md` — implement the first slice.
**Deliverable:**
  1. Let the Weft append a **concurrent** event — two events sharing the same parent
     set (a fork). The fold orders by `(lamport, event_id)` (already the rule) and must
     **merge**, not last-writer-clobber.
  2. Represent **plural heads** in the Cell (FOLD §3) so MV types keep concurrent branches.
  3. Read each Type Cell's **merge class** (tag it on `TYPE_DEF` via `model.py`) and apply
     the reducer. Implement **LWW register** (resolve by `(lamport, event_id)`) and
     **OR-set** (capability grants / tags) now; preserve heads for **MV register**.
  4. Defer Sequence CRDT, Map CRDT, semantic adjudication to later increments — say so.
**Acceptance:** `checks/70_merge.py` forks the Weft, folds the two branches in **both
arrival orders**, asserts identical `state_root()`; shows LWW and OR-set resolving
correctly and an MV type preserving both heads. Keep all prior demos green.
**Lane:** you own `weave.py`/`weft.py` this cycle. Demo in `checks/70`, not `smoke.py`.

### B3 — Memory maturation `memory.py`/`retrieval.py` + `checks/72_memory_maturation.py`
**Why:** "better the longer it runs" needs recall that ages and consolidates.
**Deliverable:** (a) **decay** — recall scoring weights recency/heat (a signal computed
from the claim's events/access, not a mutable field outside the log); (b) **consolidation**
— a pass that detects near-duplicate/related claims (reuse B2's dup/contradiction logic)
and **supersedes** them into one, with provenance preserved (no destructive overwrite);
(c) **heat/promotion** — recall bumps an access signal that lifts ranking.
**Acceptance:** `checks/72` shows a stale claim ranked below a fresh one, a consolidation
merging duplicates while `why()` still walks the original evidence, and heat rising on repeat recall.
**Lane:** `memory.py` + `retrieval.py` only. No core edits.

### C2 — Router → engines `router.py`/`agent.py`/`verifier.py` + `checks/74_router_engines.py`
**Why:** C1 picks a *tier*; C2 makes the tier actually **do** something, vendor-neutrally.
**Deliverable:** an **engine registry** (tier → engine, config/env-overridable, like
`make_brain`'s seam); the router's chosen tier selects an engine to invoke. For
deterministic-verification tasks, run a **verifier** (`verifier.py`); when none exists,
a **judge/critic** fallback. Keep it **offline-safe** — engines are deterministic stubs in
the test, with the real model call as the documented seam. The router still **confers no
authority** — `authorize()` gates every effect unchanged.
**Acceptance:** `checks/74` routes a set of tasks to engines, runs a verifier on a
verifiable one, falls back to judge otherwise, and re-asserts the zero-authority property.
**Lane:** `router.py` + `agent.py` + new `verifier.py`. No core edits.
