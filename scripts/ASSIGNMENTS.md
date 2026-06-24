# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle (5).** Tasks/lanes live in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); this is the operational layer.

**Two hard rules:**
1. `weave.py` / `weft.py` / `kernel.py` / `executor.py` are **owned by the retraction
   instance (R1)** this cycle. No one else edits them — post a request instead.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`.** Own a free
   `NN`. (Exception: R1 edits the `smoke.py` §11 *wording* line.) See `heartbeat/checks/README.md`.

Bootstrap any lane first: `scripts/kickoff.sh <dir> <branch>`

**Land R1 first** — SY2 reads the Weft and the §11 oracle; landing the REDACT core
first avoids a re-verify.

---

## Instance 1 — Claude · REDACT (core, the §11 closer)  (clone: `~/decima-claude`)

**Task:** R1. **Owns:** `heartbeat/decima/weave.py`, `weft.py`,
`heartbeat/checks/82_redaction.py` (new); the `smoke.py` §11 line; `heartbeat/PROFILE.md`.
**Must not touch:** `memory.py`, `retrieval.py`, `sync.py`, other `checks/` files.

```text
You are the Claude retraction instance for Decima, in ~/decima-claude. Read docs/BACKLOG.md
(brief R1), specs/FOLD_AND_LIFECYCLE.md §10, specs/WEFT_PROTOCOL.md §5, and
heartbeat/checks/README.md first.

Task R1 — branch claude/r1-redact — close the last partial §11 invariant:
  1. Add a retraction MODE to the RETRACT body: WITHDRAW (default = today's behavior) vs
     REDACT (withdraw AND erase payload). Per WEFT §5 / FOLD §10.
  2. On REDACT the fold removes the cell's content from EVERY projection (of_type,
     content/content_heads, why, and the state_root leaf becomes a tombstone), BUT
     weft.events() still yields the prior asserts + the redact event (skeletons) and
     tamper-evidence still holds. (Heartbeat erasure analog; full blob crypto-erasure noted.)
  3. Update the inline §11 #7 check to assert payload-absent + skeleton-present → flip it
     from "partial" to "holds" (oracle 8/8). Refresh the smoke.py §11 wording + PROFILE.md
     (retraction row + §11 table).
  Demo in a NEW file heartbeat/checks/82_redaction.py exposing run(k, line). Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude claude/r1-redact
You OWN weave.py/weft.py this cycle. Demo in checks/82; the only smoke.py edit allowed is
the §11 wording/section. Keep the oracle green (cd heartbeat && python3 smoke.py → "alive ✓",
exit 0). Commit small; git pull --rebase; push your branch; fast-forward to main when green.
```

---

## Instance 2 — Claude · sync transport  (worktree, e.g. `~/decima-claude-sync`)

`git worktree add ~/decima-claude-sync claude/sy2-sync-transport`.

**Task:** SY2. **Owns:** `heartbeat/decima/sync.py` (new),
`heartbeat/checks/80_sync_transport.py` (new).
**Must not touch:** `weave.py`, `weft.py`, `kernel.py`, `memory.py`, `smoke.py`.

```text
You are a Claude sync-transport instance for Decima, in a dedicated worktree (NOT the main
~/decima-claude tree). Read docs/BACKLOG.md (brief SY2), specs/SYNC.md, and
heartbeat/checks/README.md first.

Task SY2 — branch claude/sy2-sync-transport — realize SYNC.md between TWO real Weft instances
(offline, in-process, sharing the kernel keyring for the HMAC profile):
  New module heartbeat/decima/sync.py: given two Wefts, compute each side's missing events
  (frontier / causal-difference), transfer the event records, INGEST them into the target
  (insert the verified foreign rows — the existing events() read-verification checks id+sig;
  note a proper Weft.ingest() with full WEFT §2 validation is deferred), fold both, assert an
  identical state_root (convergence). Bidirectional. Demo in a NEW file
  heartbeat/checks/80_sync_transport.py exposing run(k, line): two Wefts with unique events
  sync → one state_root; a tampered foreign event is rejected on fold. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-sync claude/sy2-sync-transport
Stay in sync.py + checks/80. Use the public Weft/Weave API (+ .db for raw ingest); do NOT
edit weft.py or any core file or smoke.py. Keep the oracle green. Commit small; git pull
--rebase; push your branch; fast-forward to main when green.
```

---

## Instance 3 — Claude · memory governance  (worktree, e.g. `~/decima-claude-gov`)

`git worktree add ~/decima-claude-gov claude/b4-governance`.

**Task:** B4. **Owns:** `heartbeat/decima/memory.py`, `retrieval.py`,
`heartbeat/checks/84_governance.py` (new).
**Must not touch:** `weave.py`, `weft.py`, `kernel.py`, `sync.py`, `smoke.py`.

```text
You are a Claude memory-governance instance for Decima, in a dedicated worktree. Read
docs/BACKLOG.md (brief B4), specs/MEMORY_ARCHITECTURE.md, and heartbeat/checks/README.md first.

Task B4 — branch claude/b4-governance — give memory governance teeth:
  In memory.py, add functions to record governance claims (banned_action, fragile_file,
  failed_approach) and governance_check(target) -> {allow, reason, evidence} that queries
  memory (trusted, instruction-eligible) and returns a verdict with provenance. The kernel
  WIRING (Decima auto-consulting before it delegates) is a later core cycle — note it, don't
  build it. Demo in a NEW file heartbeat/checks/84_governance.py exposing run(k, line): a
  recorded banned action makes governance_check deny a repeat WITH the prior evidence; a
  fragile_file warning surfaces. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-gov claude/b4-governance
Stay in memory.py/retrieval.py + checks/84. No core edits, no smoke.py edit. Keep the oracle
green. Commit small; git pull --rebase; push your branch; fast-forward to main when green.
```

---

## Notes
- **Pushing:** SSH deploy keys push code (no token); fast-forward small green changes to `main`.
- **R1 is the cycle's milestone** — it takes the oracle to **8/8 FOLD §11**. Land it first;
  SY2/B4 are disjoint and can land in any order after.
- **Next cycle:** incremental fold-from-base (snapshot perf, core), REDACT cascade, and the
  real networked sync transport — see `docs/BACKLOG.md` "Backlog (future cycles)".
