# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle (6).** Tasks/lanes live in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); deeper rationale in
[`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md). This is the operational layer.

**This cycle is all new-module lanes — there is NO core owner and zero cross-lane overlap.**
Two rules still hold:
1. Don't touch core (`weave.py`/`weft.py`/`kernel.py`/`executor.py`) — call the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`.** Own a free `NN`
   (86/88/90 assigned below). See `heartbeat/checks/README.md`.

Bootstrap any lane first: `scripts/kickoff.sh <dir> <branch>`

---

## Instance 1 — Claude · detection-as-code (security beachhead)  (worktree `~/decima-claude-det`)

`git worktree add ~/decima-claude-det claude/det1-detection`.

**Task:** DET1. **Owns:** `heartbeat/decima/detection.py` (new), `heartbeat/checks/86_detection.py` (new).
**Must not touch:** any core file, `inspector.py`, `shorthand.py`, `smoke.py`.

```text
You are a Claude detection-engineering instance for Decima, in a dedicated worktree. Read
docs/BACKLOG.md (brief DET1), specs/CAPABILITY_MAP.md Part C, and heartbeat/checks/README.md first.

Task DET1 — branch claude/det1-detection — detection-as-code on Decima's own primitives:
  New module heartbeat/decima/detection.py. A detection is a forged, TEST-GATED rule (a
  regex/substring/IOC/YARA-lite matcher over text or structured Cells) carrying TP fixtures
  (must match) and FP fixtures (must NOT match). Reuse reckoner (Nona) to gate: promote ONLY if
  it matches every TP and no FP, else it stays quarantined. A promoted detection applied to data
  Cells (claims/results/observations) emits `finding` Cells (rule id, matched source, severity)
  with provenance via memory/the Weft. Note the purple loop: a red-team evasion becomes a new FP
  fixture. Demo in a NEW file heartbeat/checks/86_detection.py exposing run(k, line): benign
  sample no-false-positive; malicious pattern → finding w/ provenance; a rule failing its
  fixtures is NOT promoted. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-det claude/det1-detection
Stay in detection.py + checks/86. Public reckoner/memory/weave API only; no core edit, no
smoke.py edit. Keep the oracle green (cd heartbeat && python3 smoke.py → "alive ✓", exit 0).
Commit small; git pull --rebase; push; fast-forward to main when green.
```

---

## Instance 2 — Claude · capability inspector + constellation  (worktree `~/decima-claude-ins`)

`git worktree add ~/decima-claude-ins claude/ins1-inspector`.

**Task:** INS1. **Owns:** `heartbeat/decima/inspector.py` (new), `heartbeat/checks/88_constellation.py` (new).
**Must not touch:** any core file, `detection.py`, `shorthand.py`, `smoke.py`.

```text
You are a Claude inspector/constellation instance for Decima, in a dedicated worktree. Read
docs/BACKLOG.md (brief INS1), specs/CAPABILITY_MAP.md (A2 + D1), specs/MORTA_CAPABILITIES.md,
and heartbeat/checks/README.md first.

Task INS1 — branch claude/ins1-inspector — exact projections over the Weave/Weft:
  New module heartbeat/decima/inspector.py: (1) capability_holders(cap_id) → every agent whose
  envelope holds it + the delegation chain (walk parent grants to root, showing attenuations) —
  EXACT fold, never heuristic; (2) constellation() → the forged-skills/capabilities tree: each
  capability a node with lineage (parent), promotion state (quarantined/promoted), grouped by
  domain/effect; render as display lines (like task_tree/workspace). This is the data model behind
  the eventual Skyrim-style skill-tree GUI — text/graph now. Demo in a NEW file
  heartbeat/checks/88_constellation.py exposing run(k, line): grant a cap via delegation →
  inspector returns holder(s)+downhill chain (impostor excluded); constellation renders forged
  skills with lineage + state. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-ins claude/ins1-inspector
Stay in inspector.py + checks/88. Read weave/weft public API; no core edit, no smoke.py edit.
Keep the oracle green. Commit small; git pull --rebase; push; fast-forward to main when green.
```

---

## Instance 3 — Claude · agent shorthand  (worktree `~/decima-claude-sh`)

`git worktree add ~/decima-claude-sh claude/sh1-shorthand`.

**Task:** SH1. **Owns:** `heartbeat/decima/shorthand.py` (new), `heartbeat/checks/90_shorthand.py` (new).
**Must not touch:** any core file, `detection.py`, `inspector.py`, `smoke.py`.

```text
You are a Claude shorthand instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md
(brief SH1), specs/CAPABILITY_MAP.md (D2), and heartbeat/checks/README.md first.

Task SH1 — branch claude/sh1-shorthand — an auditable, reversible token-compression transport:
  New module heartbeat/decima/shorthand.py: (1) a signed symbol dictionary stored as a VERSIONED
  Cell (frequent concepts/ops → short codes); (2) encode(msg) → a compact form referencing Cell
  IDs (pointer language) + dictionary codes; decode(compact) → the original, DETERMINISTIC lossless
  round-trip; (3) report the token/byte saving. An inbound shorthand message from another agent is
  decoded, LOGGED on the Weft, and stored as UNTRUSTED data (instruction_eligible=false) until
  authorized — never an opaque private language. Demo in a NEW file heartbeat/checks/90_shorthand.py
  exposing run(k, line): a message referencing Cell IDs + dictionary round-trips losslessly; report
  the saving; a forged inbound shorthand decodes to a DATA claim, not an instruction. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-sh claude/sh1-shorthand
Stay in shorthand.py + checks/90. Use content_id/weft/memory public API; no core edit, no smoke.py
edit. Keep the oracle green. Commit small; git pull --rebase; push; fast-forward to main when green.
```

---

## Notes
- **No core owner this cycle** — three disjoint new-module lanes (86/88/90), so they land in any
  order with no rebase contention. Cleanest fan-out so far.
- **Pushing:** SSH deploy keys push code (no token); fast-forward small green changes to `main`.
- **Next cycle (core):** incremental fold-from-base, the sandboxed-principal substrate (SANDBOX.md +
  executor seam, WASM-component model), networked sync at scale — see `docs/BACKLOG.md`.
