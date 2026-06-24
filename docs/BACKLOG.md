# Decima — Build Backlog

The shared board for multi-instance work (Claude, Codex, …). One source of truth
for **what's next, who can take it, and how not to collide.** Created 2026-06-24.

> Decima is built in the **Python reference** until the design stops moving; the
> single Rust port is the **last** step (see [`VISION.md`](../VISION.md) "How we
> build it"). The design has *not* stopped moving — almost the entire system is
> still to be built here first. This backlog is that work, not the port.

## How to use this board

- Pick a task whose **Lane** doesn't overlap an in-flight task. Branch
  (`claude/<topic>` / `codex/<topic>`), do the work, keep `heartbeat/smoke.py`
  green, PR into `main`. (See [`CONTRIBUTING.md`](../CONTRIBUTING.md).)
- When you take a task, note it (PR title or a line here). When it lands, check it off.
- A reference change that diverges from a spec must update the spec and say which
  in the commit. **`specs/` is the contract.**

## Coordination rules (the collision model)

The heartbeat's core is the conflict hotspot. To let many instances run at once:

1. **Core-kernel files serialize.** `heartbeat/decima/weave.py`, `weft.py`, and
   `kernel.py` have **one owner per cycle**. Everyone else builds in **new
   modules**.
2. **Features land as a new module + their own `smoke.py` section**, appended at
   the end of `main()` *before* the `TAMPER-EVIDENCE` block (that block corrupts
   the DB and must stay last).
3. **`specs/` is collision-free** — one instance per file. Highest parallelism,
   lowest risk, and it's design-first, so it de-risks the most.
4. Keep the **FOLD §11 oracle** green (`smoke.py` → `FOLD §11 INVARIANTS`); it
   fails loud on regression.

## Priorities

`P0` = on the critical path / unblocks the most. `P1` = high value, parallel.
`P2` = real, can wait a cycle.

## Track A — Protocol design (`specs/`, fully parallel)

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| A1 | **Merge-layer design** — assign each Cell type one FOLD §4 merge class; define concurrent-head representation + adjudication | `specs/MERGE_SEMANTICS.md` (new) | **P0** | Every current Cell type mapped to a merge class with rationale; reviewed vs FOLD §4/§11 |
| A2 | **EffectReceipt design** — receipt shape with `status` incl. `UNKNOWN`, idempotency keys, leases, `effect_class` | `specs/WEFT_PROTOCOL.md` §8 | P1 | Concrete schema + state machine; maps to §11 #8 |
| A3 | **Typed retraction modes** — SUPERSEDE/REDACT/TERMINATE + cascade + erasure | `specs/FOLD_AND_LIFECYCLE.md` §10 | P2 | Modes specified; REDACT-vs-RETRACT boundary clear |

## Track B — Memory engine (`memory.py` + new modules)

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| B1 | **Taxonomy** — add episodic/semantic/procedural/decision/failure Cell types + helpers atop claims | `heartbeat/decima/memory.py` | **P0** | Types + `remember`/`recall` variants; new smoke subsection green |
| B2 | **Retrieval engine** behind the `Retriever` seam — contradiction + duplicate detection, supersession (no vector dep yet) | `heartbeat/decima/retrieval.py` (new) | P1 | Pluggable retriever beats substring; smoke shows contradiction handling |

## Track C — Model routing (new module + `agent.py`)

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| C1 | **Model router** — task→model selection (cost/latency/privacy/reasoning), compose-not-replace, as a seam over the brain | `heartbeat/decima/router.py` (new), `agent.py` | **P0** | Router picks among tiers; brain routes through it; `authorize` gating unchanged |

## Track D — Orchestration & workers (new modules; coordinate on `kernel.py`)

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| D1 | **Real CLI-agent worker** — run claude-code/codex as a sandboxed subprocess principal via the effect registry; collect receipts | new handler module; `integrate_tool` | P1 | A real tool runs as an attenuated worker, recorded in the task tree |
| D2 | **Session/process Cells** — PTY, attach/detach/replay (tmux-native, not tmux) | `heartbeat/decima/session.py` (new) | P2 | Session Cells fold; smoke shows attach/replay |
| D3 | **Learned org policy** — extend `org_score` into a policy signal | `kernel.py` (single-owner) | P2 | Score drives a delegation choice; demonstrated |

## Track E — Security depth (new module + `capability.py`)

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| E1 | **Powerbox / capability-broker** — mediated attenuated grants under policy | `heartbeat/decima/powerbox.py` (new), `capability.py` | P1 | Broker hands out scoped caps; smoke shows policy-gated grant |

## Track F — Receipts in the reference (single-owner core; sequence after A2)

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| F1 | Implement EffectReceipts (from A2) in the heartbeat | `kernel.py`, `executor.py` (single-owner) | P1 | `result` cells become receipts w/ status; closes §11 #8 in smoke |

## Suggested allocation (≈4 instances, no collisions)

- **Instance 1 — design:** A1 → A2 → A3 (pure `specs/`, unblocks others).
- **Instance 2 — memory:** B1 → B2 (owns `memory.py` + `retrieval.py`).
- **Instance 3 — routing:** C1 (owns `router.py` + `agent.py`).
- **Instance 4 — kernel/core:** the **single owner** of `weave.py`/`weft.py`/`kernel.py`
  this cycle → F1 (after A2) and D3; coordinates anyone needing core edits.
- **Park** E1 / D1 / D2 for next cycle (new-module-heavy, collision-light while core is frozen).

**Critical path:** A1 (merge design) and A2 → F1 (receipts) unblock the most.
Start A1 first — it's the decision most likely to reshape everything else, and it
costs nothing to settle on paper now.

---

## Pick-up-cold briefs (P0 + critical path)

### A1 — Merge-layer design `specs/MERGE_SEMANTICS.md`
**Why:** the heartbeat is single-process LWW; the durable system is a DAG where
concurrent events must merge deterministically. This is the genuinely unsolved
piece, and it gates the Rust port. **Do it on paper first.**
**Deliverable:** a spec that, for **every Cell type currently in the heartbeat**
(capability, agent, task, result, utterance, speech, claim, entity, type, note,
session…), assigns one merge class from FOLD §4 (Immutable value, LWW register,
MV register, OR-set, Sequence CRDT, Map CRDT, Counter, Append log, State machine,
Semantic adjudication) with a one-line rationale. Define how concurrent heads are
**represented** in CellState and how adjudication (an ATTEST) resolves them.
**Acceptance:** reviewed against FOLD §4 and §11 (esp. arrival-order independence
and "concurrent heads preserved until resolved"); no Cell type left unmapped.
**Don't:** write code. This is design.

### A2 → F1 — EffectReceipts (design then reference)
**Why:** closes FOLD §11 #8 (`UNKNOWN` resolution) — the one invariant the oracle
can't yet represent — and the receipt shape is load-bearing for retries, idempotency,
and cost tracking everywhere downstream.
**A2 deliverable (`specs/WEFT_PROTOCOL.md` §8):** finalize `EffectReceipt` —
`status` ∈ {ACCEPTED, RUNNING, SUCCEEDED, FAILED, UNKNOWN, COMPENSATED, CANCELLED},
idempotency key, provider_ref, cost, the rule that a post-submission timeout is
`UNKNOWN` (never fabricated success/failure).
**F1 deliverable (`kernel.py`/`executor.py`, single-owner):** the `result` cell
`kernel.invoke` asserts becomes a receipt carrying `status`; a deliberately
ambiguous effect resolves to `UNKNOWN`. Add a smoke check so §11 #8 flips from
*deferred* to *holds*.
**Acceptance:** `smoke.py` FOLD §11 section asserts UNKNOWN-not-fabricated; all
prior demos still green.

### B1 — Memory taxonomy `heartbeat/decima/memory.py`
**Why:** memory is "better the longer it runs," but only `claim`/`entity` exist.
**Deliverable:** add episodic / semantic / procedural / decision / failure as Cell
types (per `specs/MEMORY_ARCHITECTURE.md`), with `remember`/`recall` aware of type;
keep the recall-vs-instruct law and the four permissions intact.
**Acceptance:** a new `smoke.py` subsection stores and recalls each type; the
existing MEMORY section still green.
**Lane note:** `memory.py` only; if a kernel hook is needed, hand it to Instance 4.

### C1 — Model router `heartbeat/decima/router.py`
**Why:** "enhance whatever model is plugged in," compose-not-replace. Today there's
one brain seam and no routing.
**Deliverable:** a `Router` that, given a task descriptor (kind, stakes, latency/cost/
privacy needs, modality, whether deterministic verification exists), picks a tier
(local-small / retrieval-assisted / frontier / judge). `agent.py`'s brain consults
it. Vendor-neutral: tiers are config, not hardcoded providers.
**Acceptance:** smoke shows the router choosing different tiers for different tasks;
`authorize` still gates every INVOKE (the router has zero authority).
**Lane note:** `router.py` (new) + `agent.py`. Coordinate with anyone else in `agent.py`.
