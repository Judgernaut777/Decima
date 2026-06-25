# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle (7).** Tasks/lanes in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); rationale in
[`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md).

**One core lane this cycle: SB1 owns `executor.py`.** Two rules:
1. Only SB1 touches core (`weave.py`/`weft.py`/`kernel.py`/`executor.py`); others call the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`.** Own a free `NN`
   (92/94/96 assigned). See `heartbeat/checks/README.md`.

Bootstrap any lane first: `scripts/kickoff.sh <dir> <branch>`. **Land SB1 first.**

---

## Instance 1 — kernel · sandboxed-principal substrate  (clone `~/decima-claude`)

**Task:** SB1 (core). **Owns:** `heartbeat/decima/executor.py`, `specs/SANDBOX.md` (new),
`heartbeat/checks/92_sandbox.py` (new).
**Must not touch:** `merkle.py`, `gossip.py`, `voice.py`, other `checks/` files.

```text
You are the Claude kernel/isolation instance for Decima, in ~/decima-claude. Read
docs/BACKLOG.md (brief SB1), specs/MORTA_CAPABILITIES.md, and heartbeat/checks/README.md first.

Task SB1 — branch claude/sb1-sandbox — the no-ambient-authority linchpin:
  1. Write specs/SANDBOX.md — the sandboxed-principal contract: a sandbox PROFILE (allowed
     effects, network on/off, fs read/write path scope, resource/budget caveats), how the
     executor enforces it around dispatch, and the durable enforcement (namespaces/cgroups/
     seccomp/landlock; WASM component model as the swappable-engine form; Firecracker for heavy
     isolation).
  2. Add an executor sandbox-POLICY seam: before running an effect handler, read the capability's
     sandbox profile/caveats and REFUSE out-of-profile effects (e.g. a network-denied capability
     attempting a network effect; an fs effect outside its declared paths). Pure-stdlib =
     enforcement at the contract boundary; mark where real OS/WASM enforcement plugs in.
  Demo in a NEW file heartbeat/checks/92_sandbox.py exposing run(k, line): an in-profile effect
  runs; an out-of-profile effect is refused BEFORE execution. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude claude/sb1-sandbox
You OWN executor.py this cycle. Demo in checks/92; only the §11 wording line in smoke.py may
change if needed. Keep the oracle green (cd heartbeat && python3 smoke.py → "alive ✓", exit 0).
Commit small; git pull --rebase; push; fast-forward to main when green.
```

---

## Instance 2 — Claude · networked sync at scale  (worktree `~/decima-claude-gx`)

`git worktree add ~/decima-claude-gx claude/gx1-gossip`.

**Task:** GX1. **Owns:** `heartbeat/decima/merkle.py` (new), `heartbeat/decima/gossip.py` (new),
`heartbeat/checks/94_gossip.py` (new).
**Must not touch:** any core file, `executor.py`, `voice.py`, `smoke.py`.

```text
You are a Claude sync-at-scale instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md
(brief GX1), specs/SYNC.md, heartbeat/decima/sync.py (SY2), and heartbeat/checks/README.md first.

Task GX1 — branch claude/gx1-gossip — generalize SY2 to N peers, efficiently:
  merkle.py — a Merkle tree/DAG over a Weft's event ids (in (lamport, id) order) so two peers diff
  by exchanging root hashes and descending only divergent subtrees, transferring only missing
  events. gossip.py — simulate N in-process Wefts doing epidemic/anti-entropy sync (pairwise
  rounds) to convergence; build on sync.py. Demo in a NEW file heartbeat/checks/94_gossip.py
  exposing run(k, line): 3+ peers with divergent events converge to ONE state_root; the Merkle
  diff moves only the divergent set; a grant revoked on one peer stays revoked across the union.
  Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-gx claude/gx1-gossip
Stay in merkle.py/gossip.py + checks/94. Read weft/sync/weave public API; no core edit, no
smoke.py edit. Keep the oracle green. Commit small; git pull --rebase; push; fast-forward when green.
```

---

## Instance 3 — Claude · voice contract slice  (worktree `~/decima-claude-vox`)

`git worktree add ~/decima-claude-vox claude/vox1-voice`.

**Task:** VOX1. **Owns:** `heartbeat/decima/voice.py` (new), `heartbeat/checks/96_voice.py` (new).
**Must not touch:** any core file, `executor.py` (SB1's), `merkle.py`, `gossip.py`, `smoke.py`.

```text
You are a Claude voice instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md (brief
VOX1), specs/BROWSER_WORKER.md (the stub-engine + untrusted-data pattern), and
heartbeat/checks/README.md first.

Task VOX1 — branch claude/vox1-voice — the voice contract with a deterministic stub:
  voice.py — transcribe(audio_ref) → text → an utterance/proposal Cell (a USER turn the brain may
  act on; ambient/third-party audio is UNTRUSTED data, instruction_eligible=false); speak(text) →
  an outward speech effect that is MORTA-GATED (speech leaves the box). Deterministic stub engine
  (no real audio); register via the PUBLIC executor.register / kernel.integrate_tool. Demo in a NEW
  file heartbeat/checks/96_voice.py exposing run(k, line): voice-in yields a proposal Cell; speak is
  denied without approval and allowed after (Morta); untrusted transcribed audio is stored as data,
  not an instruction. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-vox claude/vox1-voice
Stay in voice.py + checks/96. Use public executor.register/kernel API; do NOT edit executor.py
(SB1's) or smoke.py. Keep the oracle green. Commit small; git pull --rebase; push; fast-forward
when green.
```

---

## Notes
- **Land SB1 first** (core); GX1 and VOX1 are disjoint new-module lanes that land in any order after.
- **Pushing:** SSH deploy keys push code (no token); fast-forward small green changes to `main`.
- **Next:** real sandbox enforcement (WASM/namespaces — needs deps), incremental fold-from-base,
  wrapping real security tools + real model/voice engines, the Constellation GUI — see `docs/BACKLOG.md`.
