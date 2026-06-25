# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle (8).** Tasks/lanes in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); rationale in
[`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md) (ideas D3–D4).

**No core edits this cycle.** Three single-owner-file lanes, disjoint. Two rules:
1. Don't touch core (`weave.py`/`weft.py`/`kernel.py`/`executor.py`); call the public API.
   Per-file ownership: WV1→`wager.py`, OR1→`orientation.py`+`agent.py`, AR1→`router.py`.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`.** Own a free `NN`
   (98/100/102 assigned). See `heartbeat/checks/README.md`.

Bootstrap any lane first: `scripts/kickoff.sh <dir> <branch>`.

---

## Instance 1 — Claude · Wager/Verdict loop (the learning loop)  (worktree `~/decima-claude-wv`)

`git worktree add ~/decima-claude-wv claude/wv1-wager`.

**Task:** WV1. **Owns:** `heartbeat/decima/wager.py` (new), `heartbeat/checks/98_wager.py` (new).
**Must not touch:** any core file, `orientation.py`, `agent.py`, `router.py`, `smoke.py`.

```text
You are a Claude wager/verdict instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md
(brief WV1), specs/CAPABILITY_MAP.md D4, specs/MORTA_CAPABILITIES.md, and heartbeat/checks/README.md.

Task WV1 — branch claude/wv1-wager — the scientific method as Cells:
  New module heartbeat/decima/wager.py: (1) wager(k, action, prediction, confidence) → a `wager`
  Cell (predicted outcome + confidence as an int in MILLIONTHS — no floats in signed content) before
  a significant action; (2) verdict(k, wager_id, observed) → a `verdict` Cell comparing prediction
  vs observed (hit/miss + delta) with a verdict_of edge to the wager; (3) calibration(k) → aggregate
  hit-rate over resolved wagers (the learned signal). A significant wager must be Morta-gateable.
  Receipts say what happened; wager/verdict says predicted-vs-got. Demo in a NEW file
  heartbeat/checks/98_wager.py exposing run(k, line): wager → action → verdict records hit/miss with
  provenance; a calibration aggregate over several wagers reflects accuracy; a significant wager is
  gated by Morta. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-wv claude/wv1-wager
Stay in wager.py + checks/98. Public memory/model/weave/kernel API; no core edit, no smoke.py edit.
Keep the oracle green (cd heartbeat && python3 smoke.py → "alive ✓", exit 0). Commit small;
git pull --rebase; push; fast-forward to main when green.
```

---

## Instance 2 — Claude · Orientation lens ("the Big O")  (worktree `~/decima-claude-or`)

`git worktree add ~/decima-claude-or claude/or1-orientation`.

**Task:** OR1. **Owns:** `heartbeat/decima/orientation.py` (new), `heartbeat/decima/agent.py`,
`heartbeat/checks/100_orientation.py` (new).
**Must not touch:** any core file, `wager.py`, `router.py`, `smoke.py`.

```text
You are a Claude orientation instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md
(brief OR1), specs/CAPABILITY_MAP.md D4, specs/MEMORY_ARCHITECTURE.md (B4 governance), and
heartbeat/checks/README.md first.

Task OR1 — branch claude/or1-orientation — make Orientation explicit:
  New module heartbeat/decima/orientation.py: orient(k, agent, situation) assembles the relevant
  profile/values + governance rules (reuse memory.governance_check, B4) + the agent horizon into an
  Orientation object — the lens that interprets data before decide. Add a brief hook in agent.py so
  decide consults orientation: a request conflicting with a rule is caught at orient-time (with the
  rule cited as evidence) and a stated preference shapes the choice. Keep the non-linear OODA in mind
  (fast path for oriented/known patterns, deliberate for novel). Demo in a NEW file
  heartbeat/checks/100_orientation.py exposing run(k, line): orientation built from profile +
  governance; a banned/conflicting request caught at orient-time with the rule cited; a preference
  changes the chosen action. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-or claude/or1-orientation
You own agent.py this cycle (non-core). Stay in orientation.py + agent.py + checks/100; do NOT edit
router.py or wager.py. Public memory/weave API; no core edit, no smoke.py edit. Keep the oracle
green. Commit small; git pull --rebase; push; fast-forward when green.
```

---

## Instance 3 — Claude · Auto-router  (worktree `~/decima-claude-ar`)

`git worktree add ~/decima-claude-ar claude/ar1-autorouter`.

**Task:** AR1. **Owns:** `heartbeat/decima/router.py`, `heartbeat/checks/102_autorouter.py` (new).
**Must not touch:** any core file, `agent.py` (OR1's), `wager.py`, `orientation.py`, `smoke.py`.

```text
You are a Claude auto-router instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md
(brief AR1), specs/CAPABILITY_MAP.md D3.1, heartbeat/decima/router.py (Router/route/select), and
heartbeat/checks/README.md first.

Task AR1 — branch claude/ar1-autorouter — automatic, intelligent model switching:
  Grow router.py into automatic per-task selection on cost / latency / privacy / context size /
  capability / refusal: a sensitive/private task routes to a LOCAL engine (no egress); a
  refused-but-authorized task FALLS BACK to a capable engine; low-stakes work picks a cheap model.
  Offline stubs for engines; log each choice with the deciding factor. Keep router.route()
  BACK-COMPATIBLE (extend behavior, not the call site) so agent.py is untouched. Demo in a NEW file
  heartbeat/checks/102_autorouter.py exposing run(k, line): a private task routes local; a refused
  task falls back to a capable engine; a low-stakes task picks a cheap model; the deciding factor is
  logged. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-ar claude/ar1-autorouter
Stay in router.py + checks/102; do NOT edit agent.py (OR1's). No core edit, no smoke.py edit. Keep
the oracle green. Commit small; git pull --rebase; push; fast-forward when green.
```

---

## Notes
- **No core owner this cycle** — three disjoint single-owner-file lanes (98/100/102); land in any order.
- **OR1 + AR1 coordination:** OR1 owns `agent.py`, AR1 owns `router.py`; AR1 keeps `route()`
  back-compatible so the two never touch the same file.
- **Pushing:** SSH deploy keys push code (no token); fast-forward small green changes to `main`.
- **Next:** Disposition routing (D4.2), sovereign-access build-out (D3), real engines, the
  Constellation GUI — see `docs/BACKLOG.md`.
