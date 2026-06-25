# Decima — Build Backlog

The shared board for multi-instance work. One source of truth for **what's next,
who can take it, and how not to collide.**

> Decima is built in the **Python reference** until the design stops moving; the
> single Rust port is the **last** step (see [`VISION.md`](../VISION.md)).

## Status

**Cycle 1 — ✅** A1/A2/F1 · B1/B2 · A3 · C1 · E1.
**Cycle 2 — ✅** D1 (CLI worker) · D2 (sessions) · D3 (org policy).
**Cycle 3 — ✅** M1 (merge layer) · B3 (memory maturation) · C2 (router engines) · S1 (`SYNC.md`) · S2 (`SNAPSHOTS.md`).
**Cycle 4 — ✅** **M2** (Sequence/Map/Counter/Append-log + adjudication) · **SN1** (snapshots: verifiable cache) · **SY1** (sync convergence sim).
**Cycle 5 — ✅** **R1** (REDACT → §11 8/8) · **SY2** (sync transport, two real Wefts) · **B4** (memory-as-governance).
**Cycle 6 — ✅** **DET1** (detection-as-code, security beachhead) · **INS1** (Capability Inspector + the Constellation) · **SH1** (agent shorthand).
**Cycle 7 — ✅** **SB1** (sandboxed-principal substrate) · **GX1** (sync at scale: Merkle-DAG + gossip) · **VOX1** (voice contract slice).
**Tooling — ✅** `heartbeat/checks/NN_*.py` auto-run by `smoke.py`; new lanes add a file there, never edit `smoke.py`.

Oracle: **all 8 FOLD §11 invariants hold.** Merge + snapshots + sync (sim/transport/scale) +
redaction + memory governance + detection-as-code + capability inspector + agent shorthand +
sandbox substrate + voice all real in the reference. The scope catalog of what's next (donor
adoptions + ecosystem capabilities + blue/red-team + ideas D1–D4) is
[`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md).

## Coordination rules

1. **Core-kernel files serialize:** `weave.py`, `weft.py`, `kernel.py`, `executor.py`
   — **one owner per cycle.** Everyone else builds in new modules and *calls* the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, not `smoke.py`.** Own a free `NN`.
3. **`specs/` is collision-free** — one instance per file.
4. Keep the oracle green: `cd heartbeat && python3 smoke.py` → `alive. ✓`, exit 0.

## Cycle 8 — active

Give the agent **judgment that compounds** — the net-new ideas from `CAPABILITY_MAP` D4 plus
the D3 auto-router: a **Wager/Verdict** learning loop (predict → act → measure → learn), an
explicit **Orientation** lens (act from the user's values, not fast noise), and an
**auto-router** (switch models on cost/privacy/capability). **No core (`weave`/`weft`/`kernel`/
`executor`) edits this cycle** — three single-owner-file lanes, disjoint.

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **WV1** | **Wager/Verdict loop** (`CAPABILITY_MAP` D4) — before a significant action, record a **`wager`** Cell (a probabilistic prediction + confidence); after, a **`verdict`** Cell measures the actual outcome; the hit/miss folds into a **calibration** signal that refines future confidence. Receipts say *what happened*; wager/verdict says *predicted vs. got* — the learning loop Nona doesn't cover. | `wager.py` (new) + `checks/98_wager.py` | **P1** | `checks/98`: a wager → action → verdict records hit/miss with provenance; a calibration aggregate over several wagers reflects accuracy; a significant wager is Morta-gateable |
| **OR1** | **Orientation lens — "the Big O"** (`CAPABILITY_MAP` D4) — assemble the user's profile/values + B4 governance rules + the agent `horizon` into an explicit **Orientation** consulted before `decide`, so the agent interprets data through "who you are and what you value." Non-linear OODA: a fast path for oriented/known patterns, deliberate for novel. | `orientation.py` (new) + `agent.py` + `checks/100_orientation.py` | P1 | `checks/100`: orientation built from profile + governance; a request conflicting with a rule is caught at orient-time (with the rule as evidence); a preference shapes the chosen action |
| **AR1** | **Auto-router** (`CAPABILITY_MAP` D3.1) — grow the router into automatic, per-task model switching on **cost / latency / privacy / context / capability / refusal**: sensitive work routes local (no egress), a refused-but-authorized task falls back to a capable engine, cheap models handle low-stakes. Token-aware (pairs with SH1). | `router.py` + `checks/102_autorouter.py` | P2 | `checks/102`: a private task routes to a local engine; a refused task falls back to a capable one; a low-stakes task picks a cheap model; choices are logged with the deciding factor |

**Collision note:** all three are **single-owner-file** lanes that touch **no core**: WV1 = new
`wager.py`; OR1 = new `orientation.py` + `agent.py` (the brain); AR1 = `router.py`. `agent.py` and
`router.py` are distinct non-core files — AR1 keeps `router.route()` back-compatible so OR1's call
site is untouched. Distinct `checks/` (98/100/102). Disjoint.

## Suggested allocation (Cycle 8 — all Claude, agent-agnostic)

- **Instance 1**: **WV1** — `wager.py` (the learning loop; headline).
- **Instance 2**: **OR1** — `orientation.py` + `agent.py` (the Orientation lens).
- **Instance 3**: **AR1** — `router.py` (the auto-router).

## Backlog (future cycles)

- **Snapshots, incremental fold-from-base** — the perf win (skip genesis); core change to `weave.py` fold.
- **Disposition routing** (D4.2) — an Intake Event resolves to a first-class disposition (INVOKE / memory / task / policy); generalizes browser→memory ingestion.
- **Sovereign access build-out** (D3) — credential/billing powerbox + self-hosted/private inference + Morta-gated payments rail (trading/ads); WV1 wagers verify the payments.
- **Real sandbox enforcement** (namespaces/seccomp/landlock + WASM-component runtime — needs deps); **real model engines** behind the auto-router; **real voice engines** behind VOX1.
- **The Constellation GUI** (Skyrim-style skill tree, post-port) over INS1's data model.
- **The Rust port** — last, once the reference is stable and complete.

## Pick-up-cold briefs (Cycle 8)

### WV1 — Wager/Verdict loop `wager.py` + `checks/98_wager.py`
**Why:** receipts record *what happened*; Decima has no first-class record of *what it predicted
vs. what it got*. The Wager/Verdict pair is the scientific method as Cells — the loop that lets
judgment compound (it complements Nona, which learns which *capabilities* work, by learning which
*decisions* work).
**Deliverable:** a new module: (1) `wager(k, action, prediction, confidence)` → a `wager` Cell
(the predicted outcome + a confidence as an int in millionths — no floats in signed content) before
a significant action; (2) `verdict(k, wager_id, observed)` → a `verdict` Cell comparing prediction
vs. observed (hit/miss + delta), with a `verdict_of` edge to the wager; (3) `calibration(k)` →
aggregate hit-rate over resolved wagers (the learned signal that refines future confidence). A
significant wager should be **Morta-gateable** (a big bet needs approval). Use `memory`/`model`/
`weave` public API.
**Acceptance:** `checks/98`: a wager → action → verdict records the hit/miss with provenance; a
calibration aggregate over several wagers reflects accuracy; show a significant wager gated by Morta.
Fail loud.
**Lane:** `wager.py` + `checks/98`. Public `memory`/`model`/`weave`/`kernel` API; no core edit.

### OR1 — Orientation lens `orientation.py` + `agent.py` + `checks/100_orientation.py`
**Why:** generic AI is strong at Observe/Decide/Act, weak at **Orientation** — the filter of the
user's values/context/constraints that interprets data before deciding. Decima has the ingredients
(profile memory, B4 governance, the agent `horizon`); make them an explicit lens.
**Deliverable:** `orientation.py` — `orient(k, agent, situation)` assembles the relevant profile/
values + governance rules (reuse B4's `governance_check`) + horizon into an **Orientation** object;
a brief hook in `agent.py` consults it before `decide` so a request conflicting with a rule is
caught at orient-time (with the rule as evidence) and a preference shapes the choice. Keep the
non-linear OODA in mind (a fast path for oriented/known patterns).
**Acceptance:** `checks/100`: orientation built from profile + governance; a banned/conflicting
request is caught at orient-time with the rule cited; a stated preference changes the chosen action.
Fail loud.
**Lane:** `orientation.py` + `agent.py` + `checks/100`. You own `agent.py` this cycle (non-core).
Do **not** edit `router.py` (AR1's) or `wager.py` (WV1's).

### AR1 — Auto-router `router.py` + `checks/102_autorouter.py`
**Why:** D3.1 — token optimization + intelligent model switching as a first-class objective.
**Deliverable:** grow `router.py` into automatic per-task selection on **cost / latency / privacy /
context size / capability / refusal**: a sensitive/private task routes to a **local** engine (no
egress); a refused-but-authorized task **falls back** to a capable engine; low-stakes work picks a
cheap model. Offline stubs for the engines; log each choice with the deciding factor. Keep
`router.route()` **back-compatible** (extend behavior, not the call site) so `agent.py` is untouched.
**Acceptance:** `checks/102`: a private task routes local; a refused task falls back to a capable
engine; a low-stakes task picks a cheap model; the deciding factor is logged. Fail loud.
**Lane:** `router.py` + `checks/102`. Do **not** edit `agent.py` (OR1's). No core edit.

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **SB1** | **Sandboxed-principal substrate** — `specs/SANDBOX.md` (the contract: a sandbox *profile* — allowed effects, network on/off, fs read/write scope, resource caveats; durable enforcement via namespaces/cgroups/seccomp/**landlock**, **WASM component model** as the swappable-engine form, Firecracker for heavy isolation) **+** an `executor` sandbox-**policy seam**: before dispatch, enforce the capability's profile so even a *held* effect runs only within its declared footprint (defense-in-depth beyond possession). | `executor.py` (core, single-owner) + `specs/SANDBOX.md` + `checks/92_sandbox.py` | **P0** | `checks/92`: an in-profile effect runs; an out-of-profile effect (network-denied, or fs path outside scope) is **refused before execution**; the spec defines the durable enforcement and the WASM-component target |
| **GX1** | **Networked sync at scale** — **Merkle-DAG diff** (find divergence in O(log n) by root-hash descent, transfer only the divergent events) + **gossip / anti-entropy** across **N peers** (generalize SY2 beyond two), offline/in-process. | `merkle.py` + `gossip.py` (new) + `checks/94_gossip.py` | P1 | `checks/94`: 3+ peers with divergent events gossip to one shared `state_root`; the Merkle diff moves only the divergent set; a revoked grant stays revoked post-merge |
| **VOX1** | **Voice contract slice (livability)** — a `voice` contract with a deterministic **stub engine** (like the browser stub): voice-in transcribes audio → an utterance/proposal Cell (a *user turn*, never a kernel verb; ambient/3rd-party audio is **untrusted data**); voice-out (speech) is a **Morta-gated** outward effect. | `voice.py` (new) + `checks/96_voice.py` | P2 | `checks/96`: voice-in yields a proposal Cell; speech-out is Morta-gated (denied → approve → allowed); transcribed untrusted audio is data, not an instruction |

**Collision note:** only **SB1** touches core (`executor.py`) + its own `specs/SANDBOX.md`.
**GX1** is new modules reading the `weft`/`sync` public API; **VOX1** is `voice.py` using the
**public** `executor.register`/`kernel.integrate_tool` (it must **not** edit `executor.py` —
that's SB1's). Distinct `checks/` files (92/94/96). Disjoint.

## Suggested allocation (Cycle 7)

- **Instance 1 — kernel** (`~/decima-claude`): **SB1** — sole owner of `executor.py`. The isolation linchpin.
- **Instance 2 — worktree**: **GX1** — `merkle.py`/`gossip.py`.
- **Instance 3 — worktree**: **VOX1** — `voice.py`.

*(Land SB1 first — VOX1 registers effects that run through the executor SB1 hardens; different
files so no merge conflict, but a quick re-verify on rebase is cleanest.)*

## Backlog (future cycles)

- **Snapshots, incremental fold-from-base** — the perf win (skip genesis); core change to `weave.py` fold.
- **Real sandbox enforcement** — wire the SB1 profile to actual namespaces/seccomp/landlock and a WASM-component runtime (needs deps → post-stdlib / Rust port).
- **Cascade / lease-tree retraction** (`REDACT` cascade); a proper `Weft.ingest()` with full WEFT §2 validation (pairs with GX1).
- **Wrap real security tools** as sandboxed external-engine workers (the blue/red flagship at scale); **real model engines** behind the router; **real voice engines** (whisper.cpp/Piper) behind the VOX1 contract.
- **The Constellation GUI** (Skyrim-style skill tree, post-port) over INS1's data model.
- **The Rust port** — last, once the reference is stable and complete.

## Pick-up-cold briefs (Cycle 7)

### SB1 — Sandboxed-principal substrate `executor.py` + `specs/SANDBOX.md` + `checks/92_sandbox.py`
**Why:** ocap says *what an agent may do* (which capabilities it holds); the sandbox says
*what an engine's effect handler may touch while doing it* (network, fs, resources) — defense
in depth so a compromised or buggy engine can't exceed its declared footprint even with a
valid capability.
**Deliverable:**
  1. **`specs/SANDBOX.md`** — the sandboxed-principal contract: a **sandbox profile** (allowed
     effects, `network` on/off, fs read/write path scope, resource/budget caveats), how the
     executor enforces it before/around dispatch, and the **durable enforcement** (Linux
     namespaces/cgroups v2/seccomp/landlock; the **WASM component model** as the swappable-engine
     form; Firecracker microVMs for heavy isolation).
  2. **`executor.py` seam** — before running an effect handler, read the capability's sandbox
     profile/caveats and **refuse out-of-profile effects** (e.g. a `network`-denied capability
     attempting a network effect; an fs effect outside its declared paths). Pure-stdlib =
     enforcement at the contract boundary; mark where real OS/WASM enforcement plugs in.
**Acceptance:** `checks/92`: an in-profile effect runs; an out-of-profile effect is refused
*before* execution; all prior checks green.
**Lane:** you own `executor.py` (+ `weave`/`weft`/`kernel` if needed) this cycle. Demo in
`checks/92`; only the §11 wording line in `smoke.py` may change if required.

### GX1 — Networked sync at scale `merkle.py`/`gossip.py` + `checks/94_gossip.py`
**Why:** SY2 syncs two Wefts by full frontier exchange; at scale you need O(log n) divergence
detection (Merkle) and N-peer convergence (gossip/anti-entropy).
**Deliverable:** `merkle.py` — a Merkle tree/DAG over a Weft's event ids (in `(lamport, id)`
order) so two peers diff by exchanging root hashes and descending only divergent subtrees,
transferring only missing events. `gossip.py` — simulate **N in-process Wefts** doing
epidemic/anti-entropy sync (pairwise rounds) to convergence; build on `sync.py` (SY2).
**Acceptance:** `checks/94`: 3+ peers with divergent events converge to one identical
`state_root`; the Merkle diff moves only the divergent events (not the whole log); a grant
revoked on one peer stays revoked across the union.
**Lane:** `merkle.py` + `gossip.py` + `checks/94`. Read `weft.events` + `sync`/`weave` public
API; no core edit.

### VOX1 — Voice contract slice `voice.py` + `checks/96_voice.py`
**Why:** voice is the core I/O channel that makes Decima livable; build the contract now with a
deterministic stub (real whisper.cpp/Piper engines wrap behind it later, like the browser worker).
**Deliverable:** `voice.py` — `transcribe(audio_ref)` → text → an **utterance/proposal Cell** (a
*user turn* the brain may act on; ambient/third-party audio is **untrusted data**,
`instruction_eligible=false`); `speak(text)` → an outward **speech effect that is Morta-gated**
(speech leaves the box). Deterministic stub engine (no real audio); register via the public
`executor.register` / `kernel.integrate_tool`.
**Acceptance:** `checks/96`: voice-in yields a proposal Cell; `speak` is denied without approval
and allowed after (Morta); untrusted transcribed audio is stored as data, never an instruction.
**Lane:** `voice.py` + `checks/96`. Public `executor.register`/`kernel` API; **do not edit
`executor.py`** (SB1's this cycle).
