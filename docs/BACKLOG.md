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
**Cycle 8 — ✅** **WV1** (Wager/Verdict learning loop) · **OR1** (Orientation lens) · **AR1** (auto-router).
**Cycle 9 — ✅** **DISP1** (disposition routing) · **PAY1** (Morta-gated payments rail) · **IFB1** (incremental fold-from-base).
**Cycle 10 — ✅** **CRED1** (secrets broker) · **INF1** (self-hosted/private inference) · **LOOP1** (live governance gate).
**Tooling — ✅** `heartbeat/checks/NN_*.py` auto-run by `smoke.py`; new lanes add a file there, never edit `smoke.py`.

Oracle: **all 8 FOLD §11 invariants hold.** Merge + snapshots (incl. incremental fold) + sync
(sim/transport/scale) + redaction + memory + **live** governance gate + detection-as-code +
capability inspector + agent shorthand + sandbox + voice + wager/verdict + orientation + auto-router
+ disposition + payments + secrets broker + private inference all real in the reference. The scope
catalog of what's next (donor adoptions + ecosystem capabilities + blue/red-team + ideas D1–D4) is
[`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md).

## Coordination rules

1. **Core-kernel files serialize:** `weave.py`, `weft.py`, `kernel.py`, `executor.py`
   — **one owner per cycle.** Everyone else builds in new modules and *calls* the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, not `smoke.py`.** Own a free `NN`.
3. **`specs/` is collision-free** — one instance per file.
4. Keep the oracle green: `cd heartbeat && python3 smoke.py` → `alive. ✓`, exit 0.

## Cycle 11 — active

**Decima acts on the world — autonomously, defensively, and financially.** Wire DISP1 into the
live inbound path (the "go live" move LOOP1 made for governance), deepen the blue-team flagship
with triage/SIEM over the signed Weft, and add **trading** on the payments rail. Only **INTAKE1**
touches core.

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **INTAKE1** | **Live disposition loop** (core) — DISP1 built `dispose()` as a library; INTAKE1 makes it **live**: `kernel.ingest_observation` (and a general `ingest(source, text, trusted)` entry) routes inbound through `disposition.dispose()`, so an observed page / inbound message is **auto-routed** (noise archived, fact remembered, injection kept as flagged DATA) — the same wiring LOOP1 did for governance. | `kernel.py` (core, single-owner) + `checks/116_live_intake.py` | P1 | `checks/116`: an inbound observation auto-disposes via the kernel (noise → archive, fact → memory, injection → DATA) with a `disposed_as` edge; nothing elevates an untrusted intake to an action |
| **TRIAGE1** | **Blue-team triage / SIEM over the Weft** (`CAPABILITY_MAP` Part C) — the signed Weft *is* a tamper-evident SIEM. Correlate DET1 `finding` Cells into **`incident`** Cells (group by rule / severity / source, within a window), score severity, and propose a response (a disposition or a Morta-gated action proposal). Deepens the DET1 beachhead toward a real blue team. | `triage.py` (new) + `checks/118_triage.py` | P1 | `checks/118`: several findings correlate into an incident with cited findings + a severity; a single benign finding doesn't escalate; the incident proposes a response; all on the Weft |
| **TRADE1** | **Trading on the payments rail** (`CAPABILITY_MAP` D3.4) — "trade stocks": a trade **is** a Morta-gated payment with a price wager. `buy`/`sell` reuse `payments.pay` (FINANCIAL, spend cap, idempotency, **WV1 wager → verdict** on the predicted return) and update a **`portfolio`** Cell (positions) on the Weft; credentials via the CRED1 broker. | `trading.py` (new) + `checks/120_trading.py` | P2 | `checks/120`: a buy is Morta-gated + idempotent (no double-fill), updates the portfolio, binds a price wager + records a verdict; an over-cap trade is refused; a sell closes the position; all on the Weft |

**Collision note:** only **INTAKE1** touches core (`kernel.py`). **TRIAGE1** = new `triage.py` (reads
DET1 `finding` cells via the `weave`/`memory` public API). **TRADE1** = new `trading.py` (composes
`payments`/`wager`/`secrets` public APIs — edits none of them). Distinct `checks/` (116/118/120). Disjoint.

## Suggested allocation (Cycle 11)

- **Instance 1 — kernel** (`~/decima-claude`): **INTAKE1** — sole owner of `kernel.py`. The live intake loop.
- **Instance 2 — worktree**: **TRIAGE1** — `triage.py` (blue-team SIEM).
- **Instance 3 — worktree**: **TRADE1** — `trading.py` (trading on the rail).

## Backlog (future cycles)

- **Cascade / lease-tree retraction** (`REDACT` cascade to derived authority/leases, WEFT §5) — core.
- **A proper `Weft.ingest()`** with full WEFT §2 validation (pairs with GX1 networked sync) — core.
- **Red-team capability depth** (`CAPABILITY_MAP` Part C) — authorized-only, scoped, sandboxed, audited (the offensive half of the flagship).
- **Real sandbox enforcement** (namespaces/seccomp/landlock + WASM-component runtime — needs deps); **real model engines** behind the auto-router; **real voice engines** behind VOX1.
- **The Constellation GUI** (Skyrim-style skill tree, post-port) over INS1's data model.
- **The Rust port** — last, once the reference is stable and complete.

## Pick-up-cold briefs (Cycle 11)

### INTAKE1 — Live disposition loop `kernel.py` + `checks/116_live_intake.py`
**Why:** DISP1 built `dispose()` as a library; nothing calls it automatically. INTAKE1 is the
"go live" wiring (the same move LOOP1 made for governance): inbound data is auto-routed through the
disposition router, so the web/inbound messages become captured-and-routed rather than ad-hoc.
**Deliverable:** in `kernel.py`, route inbound through `disposition.dispose()`: make
`ingest_observation` (after it observes) pass the observed text through `dispose()` (untrusted →
DATA), and add a general `ingest(source, text, *, trusted=False)` entry that auto-disposes any
inbound (messages, tool output). Keep the existing observation receipt + provenance; the disposition
is recorded with its `disposed_as` edge. Untrusted inbound must never elevate to a task/invoke/policy
(DISP1's law holds end-to-end through the kernel).
**Acceptance:** `checks/116`: an inbound observation auto-disposes via the kernel — noise → archive,
a fact → memory, an injection-laced page → flagged DATA (never invoke) — each with a `disposed_as`
edge; an untrusted intake never elevates. Fail loud.
**Lane:** `kernel.py` (core, single-owner) + `checks/116`. Uses `disposition` (public); only the
relevant wording in `smoke.py` may change if required.

### TRIAGE1 — Blue-team triage / SIEM `triage.py` + `checks/118_triage.py`
**Why:** the signed Weft is a tamper-evident SIEM, and DET1 already emits `finding` Cells. TRIAGE1
is the correlation/response layer that turns raw detections into actionable incidents — the blue
team's next step past detection.
**Deliverable:** `triage.py` — read `finding` Cells (DET1) from the Weave; **correlate** them into
`incident` Cells (group by rule / severity / source within a window), compute an incident severity,
and link each incident to its findings (`includes` edges) with provenance. Propose a **response** —
a disposition (e.g. open a remediation task) or a Morta-gated action proposal — recorded on the Weft.
A single benign/low finding must not escalate to an incident.
**Acceptance:** `checks/118`: several related findings correlate into one incident citing them with a
computed severity; a lone benign finding does not escalate; the incident proposes a response; all on
the Weft. Fail loud.
**Lane:** `triage.py` + `checks/118`. Public `weave`/`memory`/`model` API; reads DET1 findings; no
core edit, no `detection.py` edit.

### TRADE1 — Trading on the payments rail `trading.py` + `checks/120_trading.py`
**Why:** D3.4 — "trade stocks". A trade is a Morta-gated payment with a price prediction, so it
composes PAY1 + WV1 + CRED1 directly rather than re-inventing money movement.
**Deliverable:** `trading.py` — `buy(k, …)` / `sell(k, …)` that reuse `payments.pay` for the
Morta-gated, spend-capped, **idempotent** money movement (no double-fill), bind a **WV1 wager** on
the predicted return and settle a **verdict** on the realized outcome, and update a **`portfolio`**
Cell (positions: symbol → qty/cost) on the Weft. Broker credentials come from the **CRED1** secrets
broker (a handle, never the raw key). An over-cap trade is refused; a sell closes/reduces the
position.
**Acceptance:** `checks/120`: a buy is Morta-gated + idempotent (a duplicate doesn't double-fill),
updates the portfolio, binds a price wager + records a verdict; an over-cap trade is refused; a sell
reduces the position; all on the Weft. Fail loud.
**Lane:** `trading.py` + `checks/120`. Composes `payments`/`wager`/`secrets`/`kernel` public APIs;
edits none of them; no core edit.
