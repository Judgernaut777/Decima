# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle (3)**. Tasks/lanes live in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); this is the operational layer.

**Two hard rules:**
1. `weave.py` / `weft.py` / `kernel.py` / `executor.py` are **owned by the merge
   instance (M1)** this cycle. No one else edits them — post a request instead.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`.** Own a
   free `NN`. See `heartbeat/checks/README.md`.

Bootstrap any lane first: `scripts/kickoff.sh <dir> <branch>`

---

## Instance 1 — Claude · merge layer (the hard one)  (clone: `~/decima-claude`)

**Task:** M1. **Owns:** `heartbeat/decima/weave.py`, `weft.py`, `model.py`,
`heartbeat/checks/70_merge.py` (new).
**Must not touch:** `memory.py`, `retrieval.py`, `router.py`, `agent.py`, `verifier.py`, `smoke.py`.

```text
You are the Claude merge-layer instance for Decima, in ~/decima-claude. Read
docs/BACKLOG.md (brief M1), specs/MERGE_SEMANTICS.md, and heartbeat/checks/README.md first.

Task M1 — branch claude/m1-merge-impl — implement the FIRST increment of the merge layer:
  1. Let the Weft append a CONCURRENT event — two events sharing the same parent set (a
     fork). The fold orders by (lamport, event_id) and must MERGE, not last-writer-clobber.
  2. Represent PLURAL heads in the Cell (FOLD §3) so MV types keep concurrent branches.
  3. Tag each Type Cell with its MERGE CLASS (via model.py / TYPE_DEF) and apply the reducer.
     Implement LWW register and OR-set now; preserve heads for MV register. Defer Sequence/
     Map CRDT and semantic adjudication (say so in comments).
  Demo in a NEW file heartbeat/checks/70_merge.py exposing run(k, line): fork the Weft, fold
  both arrival orders, assert identical state_root(); show LWW + OR-set resolving and MV heads
  preserved. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude claude/m1-merge-impl
You OWN weave.py/weft.py/model.py this cycle. Demo in checks/70 — do NOT edit smoke.py. Keep
the oracle green (cd heartbeat && python3 smoke.py → "alive ✓", exit 0) before every commit.
Commit small; git pull --rebase; push your branch; fast-forward to main when green.
```

---

## Instance 2 — Codex · memory maturation  (clone: `~/decima-codex`)

**Task:** B3. **Owns:** `heartbeat/decima/memory.py`, `retrieval.py`,
`heartbeat/checks/72_memory_maturation.py` (new).
**Must not touch:** `weave.py`, `weft.py`, `kernel.py`, `executor.py`, `router.py`, `smoke.py`.

```text
You are the Codex memory instance for Decima, in ~/decima-codex. Read docs/BACKLOG.md
(brief B3) and heartbeat/checks/README.md first.

Task B3 — branch codex/b3-memory-maturation:
  Mature memory recall: (a) DECAY — recall scoring weights recency/heat, computed from the
  claim's events/access (not a mutable field outside the log); (b) CONSOLIDATION — detect
  near-duplicate/related claims (reuse B2's dup/contradiction logic) and SUPERSEDE them into
  one, preserving provenance (no destructive overwrite); (c) HEAT — recall bumps an access
  signal that lifts ranking. Demo in a NEW file heartbeat/checks/72_memory_maturation.py
  exposing run(k, line): a stale claim ranks below a fresh one, duplicates consolidate while
  why() still walks the original evidence, heat rises on repeat recall. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-codex codex/b3-memory-maturation
Stay in memory.py/retrieval.py. Demo in checks/72 — do NOT edit smoke.py or any core file.
Keep the oracle green. Commit small; git pull --rebase; push your branch; fast-forward to
main when green.
```

---

## Instance 3 — Claude · router engines  (worktree, e.g. `~/decima-claude-router`)

Reuse the router worktree or make a fresh one:
`git worktree add ~/decima-claude-engines claude/c2-router-engines`.

**Task:** C2. **Owns:** `heartbeat/decima/router.py`, `agent.py`, `verifier.py` (new),
`heartbeat/checks/74_router_engines.py` (new).
**Must not touch:** `weave.py`, `weft.py`, `kernel.py`, `memory.py`, `smoke.py`.

```text
You are a Claude router-engines instance for Decima, in a dedicated worktree (NOT the main
~/decima-claude tree). Read docs/BACKLOG.md (brief C2) and heartbeat/checks/README.md first.

Task C2 — branch claude/c2-router-engines:
  Make the router's tier choice DO something, vendor-neutrally. Add an engine registry (tier
  → engine, config/env-overridable, like make_brain's seam); the chosen tier selects an engine
  to invoke. For deterministic-verification tasks run a verifier (new verifier.py); else a
  judge/critic fallback. Keep it OFFLINE-SAFE — engines are deterministic stubs in the test,
  real model call is the documented seam. The router still confers ZERO authority — authorize()
  gates every effect unchanged. Demo in a NEW file heartbeat/checks/74_router_engines.py
  exposing run(k, line). Fail loud.

Bootstrap: scripts/kickoff.sh <your-worktree-dir> claude/c2-router-engines
Stay in router.py/agent.py/verifier.py. Demo in checks/74 — do NOT edit weave.py/weft.py/
kernel.py/memory.py/smoke.py. Keep the oracle green. Commit small; git pull --rebase; push
your branch; fast-forward to main when green.
```

---

## Notes
- **Pushing:** SSH deploy keys push code (no token); PRs need a token. Without one,
  **fast-forward small green changes to `main`** — what's worked all along.
- **M1 is the cycle's critical path and risk.** If the merge increment proves bigger than a
  cycle, land the concurrent-fork + LWW slice first and carry OR-set/MV to a follow-up — don't
  block the others on it (they're disjoint and can land independently).
- **Next cycle:** the remaining merge classes (Sequence/Map CRDT, semantic adjudication),
  snapshots, and scratch-graduation — see `docs/BACKLOG.md` "Backlog (future cycles)".
