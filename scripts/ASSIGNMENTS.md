# Cycle assignments + kickoff prompts

Per-instance briefs for the current cycle. Tasks/lanes are defined in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); this file is the **operational** layer:
who runs where, in what order, and the exact prompt to launch each instance.

**The one hard rule:** `heartbeat/decima/kernel.py`, `weft.py`, and `weave.py` are
**owned by the worker / kernel instance this cycle.** No one else edits them. If
another instance needs a kernel hook, it posts a request; it does not edit the file.

Bootstrap any lane first:
```bash
scripts/kickoff.sh <clone-dir> <branch>
```

---

## Instance 1 — worker · kernel & correctness  (clone: `~/decima-work`)

**Tasks, in order:** A1 → A2 → F1 → E1. One branch + PR **per task**.
**Owns (lane):** `specs/MERGE_SEMANTICS.md` (new), `specs/WEFT_PROTOCOL.md`,
`heartbeat/decima/kernel.py`, `executor.py`, `capability.py`,
`heartbeat/decima/powerbox.py` (new).
**Must not touch:** `memory.py`, `retrieval.py`, `router.py`, `agent.py`.

```text
You are the worker kernel/correctness instance for Decima, working in ~/decima-work.
Read docs/BACKLOG.md and scripts/ASSIGNMENTS.md first.

This cycle, do these tasks IN ORDER, each on its own branch with its own PR into main:
  1. A1  — branch work/a1-merge-design   — write specs/MERGE_SEMANTICS.md (design only, no code)
  2. A2  — branch work/a2-receipt-spec   — refine EffectReceipt in specs/WEFT_PROTOCOL.md §8
  3. F1  — branch work/f1-receipts       — implement receipts in kernel.py/executor.py;
                                              flip FOLD §11 #8 (UNKNOWN) from deferred → holds in smoke.py
  4. E1  — branch work/e1-powerbox       — new powerbox.py + capability.py: mediated attenuated grants

Start each task by running: scripts/kickoff.sh ~/decima-work <branch>
Build to the task's "Done when" in docs/BACKLOG.md. Keep heartbeat/smoke.py GREEN
(cd heartbeat && python3 smoke.py → "heartbeat: alive. ✓", exit 0) before every commit.
You OWN kernel.py/weft.py/weave.py/executor.py this cycle. Do NOT touch memory.py,
retrieval.py, router.py, or agent.py. Commit small; git pull --rebase; push; open a PR.
```

---

## Instance 2 — review worker · memory engine  (clone: `~/decima-review`)

**Tasks, in order:** B1 → B2 → A3. One branch + PR per task.
**Owns (lane):** `heartbeat/decima/memory.py`, `heartbeat/decima/retrieval.py` (new),
`specs/MEMORY_ARCHITECTURE.md`, `specs/FOLD_AND_LIFECYCLE.md` (§10 only).
**Must not touch:** `kernel.py`, `weft.py`, `weave.py`, `executor.py`.

```text
You are the review worker memory instance for Decima, working in ~/decima-review.
Read docs/BACKLOG.md and scripts/ASSIGNMENTS.md first.

This cycle, do these tasks IN ORDER, each on its own branch with its own PR into main:
  1. B1  — branch review/b1-memory-taxonomy — add episodic/semantic/procedural/decision/failure
                                              Cell types + helpers in memory.py (per MEMORY_ARCHITECTURE.md);
                                              keep recall-vs-instruct + the four permissions intact
  2. B2  — branch review/b2-retrieval       — new retrieval.py behind the Retriever seam: contradiction +
                                              duplicate detection, supersession (NO vector dependency)
  3. A3  — branch review/a3-retraction      — typed retraction modes (SUPERSEDE/REDACT/TERMINATE + cascade)
                                              in specs/FOLD_AND_LIFECYCLE.md §10 (design)

Start each task by running: scripts/kickoff.sh ~/decima-review <branch>
Build to the task's "Done when" in docs/BACKLOG.md. Add your demos as a NEW section in
heartbeat/smoke.py (append before the TAMPER-EVIDENCE block) and keep it GREEN
(cd heartbeat && python3 smoke.py → "heartbeat: alive. ✓", exit 0) before every commit.
You may NOT edit kernel.py/weft.py/weave.py/executor.py — those are the worker instance's
this cycle. If you need a kernel hook (e.g. memory routing), STOP and post the request.
Commit small; git pull --rebase; push; open a PR.
```

---

## Instance 3 (optional) — worker · model routing  (separate clone or `git worktree`)

Run this only if you want a third lane in parallel. Use a **separate clone or a git
worktree** (`git worktree add ../decima-work-router work/c1-router`) so it does
not share a working tree with Instance 1.

**Task:** C1. **Owns (lane):** `heartbeat/decima/router.py` (new), `agent.py`.
**Must not touch:** `kernel.py`, `weft.py`, `weave.py`, `memory.py`.

```text
You are a worker routing instance for Decima, in a dedicated worktree/clone (NOT the
main ~/decima-work tree). Read docs/BACKLOG.md and scripts/ASSIGNMENTS.md first.

Task C1 — branch work/c1-router:
  New router.py + agent.py: a Router that picks a model tier (local-small / retrieval-
  assisted / frontier / judge) from a task descriptor (kind, stakes, latency/cost/privacy,
  modality, whether deterministic verification exists). The brain consults it. Tiers are
  config, vendor-neutral. The router has ZERO authority — authorize() still gates every INVOKE.

Bootstrap: scripts/kickoff.sh <your-worktree-dir> work/c1-router
Build to C1's "Done when". Keep heartbeat/smoke.py GREEN before every commit. Do NOT touch
kernel.py/weft.py/weave.py/memory.py. Commit small; git pull --rebase; push; open a PR.
```

---

## Parked for next cycle
D1 (real CLI-agent worker), D2 (session Cells), D3 (learned org policy) — D1/D3 touch
`kernel.py`, so they wait until F1 frees the core lane. Re-assign when this cycle's PRs land.
