# Decima — Build Backlog

The shared board for multi-instance work (Claude, Codex, …). One source of truth
for **what's next, who can take it, and how not to collide.**

> Decima is built in the **Python reference** until the design stops moving; the
> single Rust port is the **last** step (see [`VISION.md`](../VISION.md) "How we
> build it"). The design has *not* stopped moving — almost the entire system is
> still to be built here first.

## Status

**Cycle 1 — ✅ complete** (all merged to `main`):
A1 merge-semantics spec · A2 EffectReceipt spec · **F1** receipts in the kernel
(closes FOLD §11 #8, `UNKNOWN`) · B1 memory taxonomy · B2 lexical retriever ·
A3 retraction-modes spec · C1 model router · E1 powerbox. The §11 oracle now reads
**7/8 invariants hold, 1 partial** (RETRACT; full REDACT deferred).

**Tooling — ✅** the `checks/` harness: feature checks live in
`heartbeat/checks/NN_*.py`, auto-run by `smoke.py` before tamper-evidence. **New
lanes add a file there and NEVER edit `smoke.py`** — this killed the cycle-1
collision (E1 vs C1 both appended to `smoke.py`).

## Coordination rules (the collision model)

1. **Core-kernel files serialize.** `heartbeat/decima/weave.py`, `weft.py`,
   `kernel.py`, `executor.py` have **one owner per cycle**. Everyone else builds
   in **new modules** and *calls* the kernel's public API; they don't edit it.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, not `smoke.py`.** Pick a free
   `NN` prefix (lanes own distinct numbers). See `heartbeat/checks/README.md`.
3. **`specs/` is collision-free** — one instance per file.
4. Keep the oracle green: `cd heartbeat && python3 smoke.py` → `alive. ✓`, exit 0.

## Cycle 2 — active

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **D3** | **Learned org policy** — turn `org_score` into a signal that *drives* a delegation/topology choice | `kernel.py` (single-owner) + `checks/50_org_policy.py` | **P0** | the score changes a delegation decision; `checks/50` demonstrates it |
| **D1** | **Real CLI-agent worker** — run an external CLI/agent (e.g. a `codex`/`claude-code` shim) as a **sandboxed subprocess principal** via the effect registry; capture its receipt | `cli_worker.py` (new) + `checks/55_cli_worker.py` | P1 | a real subprocess tool runs as an attenuated worker, recorded in the task tree with a receipt |
| **D2** | **Session / process Cells** — PTY-ish sessions with attach/detach/replay (tmux-native, not tmux) | `session.py` (new) + `checks/60_sessions.py` | P1 | session Cells fold; `checks/60` shows attach + replay |

**Collision note:** only D3 touches the kernel (its single owner). D1 and D2 are
new modules that *call* `executor.register` / `kernel.integrate_tool` — they must
not edit `kernel.py`/`executor.py`. All three add their own `checks/` file, so
nobody touches `smoke.py`. Zero shared-file overlap.

## Suggested allocation (Cycle 2, ≈3 instances)

- **Instance 1 — Claude / kernel** (`~/decima-claude`): **D3** — sole owner of `kernel.py` this cycle.
- **Instance 2 — Codex** (`~/decima-codex`): **D1** — its integration/registry wheelhouse.
- **Instance 3 — Claude / worktree** (`~/decima-claude-router` or a fresh worktree): **D2**.

## Backlog (future cycles)

- **Merge-class implementation** — start realizing `specs/MERGE_SEMANTICS.md` (MV registers, OR-sets…) in the reference; the real concurrency work.
- **Memory maturation** — consolidation, freshness/decay, heat/promotion, horizon-mediated recall (`memory.py`).
- **Router → real calls** — wire the model router to actual tiered model invocation + a judge/critic harness.
- **Snapshots** — `SnapshotManifest`, restore-verify, periodic replay (`state_root()` is seeded).
- **Scratch Weft + graduation**; **real sandboxing** (landlock/seccomp); **budget folded into the Weft**.

## Pick-up-cold briefs (Cycle 2)

### D3 — Learned org policy `kernel.py` + `checks/50_org_policy.py`
**Why:** `org_score` already folds the task tree into metrics; the next rung is
*using* them. Make the orchestrator's delegation choice depend on recorded outcomes.
**Deliverable:** a thin policy that reads prior `task` outcomes (status/steps/denials)
and changes a decision — e.g. prefer a capability/topology that completed before, or
refuse one that repeatedly got denied. Keep it deterministic and folded from the Weave.
**Acceptance:** `checks/50_org_policy.py` sets up a history where the policy demonstrably
picks differently than the naive path; oracle green.
**Lane:** you own `kernel.py` this cycle. Add your demo as `checks/50`, not in `smoke.py`.

### D1 — Real CLI-agent worker `cli_worker.py` + `checks/55_cli_worker.py`
**Why:** "integrate any CLI tool/agent" is a core promise; today `integrate_tool` exists
but the handlers are stubs. Run a *real* subprocess as an attenuated principal.
**Deliverable:** a new module whose handler shells out to a real command (start with
something safe + deterministic, e.g. a local script or `echo`-class tool standing in for
`codex`), wired via `kernel.integrate_tool(name, handler)`. It runs as a delegated worker
with an attenuated grant, and its output is captured as an `EffectReceipt`-shaped result.
**Acceptance:** `checks/55` integrates the tool at runtime, delegates it to a worker, and
shows it in the task tree with a receipt; oracle green.
**Lane:** new `cli_worker.py` + `checks/55`. Use the **public** API only — do NOT edit
`kernel.py` or `executor.py`. Sandbox seam: note where landlock/seccomp would slot in.

### D2 — Session / process Cells `session.py` + `checks/60_sessions.py`
**Why:** Decima must multiplex many agents/shells/logs; the native form is process/session
**Cells**, not terminal panes.
**Deliverable:** a new module modeling a session as Cells (stream events appended to the
Weft) supporting attach/detach and **replay** (fold the session's events to reconstruct its
transcript). PTY can be stubbed; the Cell/fold model is the point.
**Acceptance:** `checks/60` creates a session, appends output, detaches, and replays it from
the fold to prove the transcript reconstructs; oracle green.
**Lane:** new `session.py` + `checks/60`. Do NOT edit `kernel.py`/`smoke.py`.
