# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle**. Tasks/lanes live in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); this is the operational layer: who runs
where, in what order, and the exact prompt to launch each instance.

**Two hard rules this cycle:**
1. `kernel.py` / `weft.py` / `weave.py` / `executor.py` are **owned by the Claude /
   kernel instance** (D3). No one else edits them — post a request instead.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`** (the
   harness auto-runs them). Pick a free `NN`. See `heartbeat/checks/README.md`.

Bootstrap any lane first:
```bash
scripts/kickoff.sh <clone-or-worktree-dir> <branch>
```

---

## Instance 1 — Claude · kernel  (clone: `~/decima-claude`)

**Task:** D3 (learned org policy). **Owns:** `heartbeat/decima/kernel.py`,
`heartbeat/checks/50_org_policy.py` (new).
**Must not touch:** `memory.py`, `retrieval.py`, `router.py`, `session.py`, `cli_worker.py`, `smoke.py`.

```text
You are the Claude kernel instance for Decima, in ~/decima-claude. Read docs/BACKLOG.md
and heartbeat/checks/README.md first.

Task D3 — branch claude/d3-org-policy:
  Make org_score DRIVE a decision. Add a thin, deterministic policy (folded from the
  Weave) that reads prior `task` outcomes (status/steps/denials) and changes a delegation
  choice — e.g. prefer a capability/topology that completed before, or refuse one that
  keeps getting denied. Demonstrate it in a NEW file heartbeat/checks/50_org_policy.py
  exposing run(k, line): build a history, then show the policy picks differently than the
  naive path. Fail loud (assert) on regression.

Bootstrap: scripts/kickoff.sh ~/decima-claude claude/d3-org-policy
You OWN kernel.py/weft.py/weave.py/executor.py this cycle. Put your demo in checks/50 —
do NOT edit smoke.py. Keep the oracle green (cd heartbeat && python3 smoke.py → "alive ✓",
exit 0) before every commit. Commit small; git pull --rebase; push your branch; then
fast-forward to main when green (or have the PR merged).
```

---

## Instance 2 — Codex · CLI worker  (clone: `~/decima-codex`)

**Task:** D1 (real CLI-agent worker). **Owns:** `heartbeat/decima/cli_worker.py` (new),
`heartbeat/checks/55_cli_worker.py` (new).
**Must not touch:** `kernel.py`, `weft.py`, `weave.py`, `executor.py`, `smoke.py`.

```text
You are the Codex instance for Decima, in ~/decima-codex. Read docs/BACKLOG.md and
heartbeat/checks/README.md first.

Task D1 — branch codex/d1-cli-worker:
  Run a REAL external CLI as a sandboxed worker. New module heartbeat/decima/cli_worker.py
  whose handler shells out to a real, safe, deterministic command (a local script or an
  echo-class stand-in for `codex`), wired via the PUBLIC API kernel.integrate_tool(name,
  handler) / executor.register. It must run as a DELEGATED worker with an attenuated grant
  and capture output as an EffectReceipt-shaped result. Demonstrate in a NEW file
  heartbeat/checks/55_cli_worker.py exposing run(k, line): integrate the tool at runtime,
  delegate it to a worker, show it in the task tree with a receipt. Note where real
  sandboxing (landlock/seccomp) would slot in. Fail loud on regression.

Bootstrap: scripts/kickoff.sh ~/decima-codex codex/d1-cli-worker
Use the PUBLIC kernel/executor API only — do NOT edit kernel.py/executor.py. Put your demo
in checks/55 — do NOT edit smoke.py. Keep the oracle green (cd heartbeat && python3 smoke.py
→ "alive ✓", exit 0). Commit small; git pull --rebase; push your branch; fast-forward to
main when green.
```

---

## Instance 3 — Claude · sessions  (worktree: `~/decima-claude-router`, or a fresh one)

Reuse the existing router worktree, or make a fresh one:
`git worktree add ~/decima-claude-sessions claude/d2-sessions`.

**Task:** D2 (session/process Cells). **Owns:** `heartbeat/decima/session.py` (new),
`heartbeat/checks/60_sessions.py` (new).
**Must not touch:** `kernel.py`, `weft.py`, `weave.py`, `smoke.py`.

```text
You are a Claude sessions instance for Decima, in a dedicated worktree (NOT the main
~/decima-claude tree). Read docs/BACKLOG.md and heartbeat/checks/README.md first.

Task D2 — branch claude/d2-sessions:
  Model a session/process as Cells. New module heartbeat/decima/session.py: a session is a
  Cell whose stream output is appended as events to the Weft; support attach/detach and
  REPLAY (fold the session's events to reconstruct its transcript). PTY can be stubbed —
  the Cell/fold model is the point. Demonstrate in a NEW file heartbeat/checks/60_sessions.py
  exposing run(k, line): create a session, append output, detach, then replay it from the
  fold to prove the transcript reconstructs. Fail loud on regression.

Bootstrap: scripts/kickoff.sh <your-worktree-dir> claude/d2-sessions
Do NOT edit kernel.py/weft.py/weave.py/smoke.py. Put your demo in checks/60. Keep the oracle
green (cd heartbeat && python3 smoke.py → "alive ✓", exit 0). Commit small; git pull
--rebase; push your branch; fast-forward to main when green.
```

---

## Notes
- **Pushing:** these clones push over SSH deploy keys (no token needed). PRs need the GitHub
  API (a token); without one, **fast-forward small green changes straight to `main`** — that's
  what worked in cycle 1.
- **Next cycle** starts the merge-class implementation (realizing `specs/MERGE_SEMANTICS.md`)
  and memory maturation — see `docs/BACKLOG.md` "Backlog (future cycles)".
