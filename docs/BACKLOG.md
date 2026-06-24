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
**Tooling — ✅** `heartbeat/checks/NN_*.py` auto-run by `smoke.py`; new lanes add a file there, never edit `smoke.py`.

Oracle: **7/8 FOLD §11 hold, 1 partial** (REDACT — Cycle 5 closes it). Merge layer
+ snapshots + sync-convergence all real in the reference.

## Coordination rules

1. **Core-kernel files serialize:** `weave.py`, `weft.py`, `kernel.py`, `executor.py`
   — **one owner per cycle.** Everyone else builds in new modules and *calls* the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, not `smoke.py`.** Own a free `NN`.
3. **`specs/` is collision-free** — one instance per file.
4. Keep the oracle green: `cd heartbeat && python3 smoke.py` → `alive. ✓`, exit 0.

## Cycle 5 — active

Close the **last partial §11 invariant** (REDACT → 8/8), turn the sync *simulation*
into a real two-Weft *transport*, and give memory governance teeth.

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **R1** | **Typed retraction → REDACT** — add a retraction **mode** (`WITHDRAW` vs `REDACT`, per `WEFT §5` / `A3` / `FOLD §10`). `REDACT` **erases the payload** from every projection (content gone, a tombstone remains) while the **event skeleton stays on the Log** (tamper-evidence intact) — the heartbeat's cryptographic-erasure analog. **Flip §11 #7 from partial → holds (8/8)** and refresh `PROFILE`. | `weave.py`, `weft.py` (core, single-owner) + `checks/82_redaction.py`; the `smoke.py` §11 line + `PROFILE.md` | **P0** | `checks/82`: a `REDACT`ed cell's payload is absent from `of_type`/`state_root`/`why`, its prior-assert + redact events remain in `weft.events()`; the inline §11 #7 invariant now asserts **holds** → 8/8 |
| **SY2** | **Sync transport (reference, offline)** — realize `SYNC.md` between **two real Weft instances** (sharing the kernel keyring for the HMAC profile): frontier/causal-difference → transfer event records → local verification → DAG union → converge. | `sync.py` (new) + `checks/80_sync_transport.py` | P1 | `checks/80`: two Wefts each with unique events sync bidirectionally to one identical `state_root`; a tampered foreign event is rejected on fold |
| **B4** | **Memory-as-governance** — record governance claims (`banned_action`, `fragile_file`, `failed_approach`) and a `governance_check(target)` that queries memory and returns allow/deny + reason + provenance. (Decima auto-consulting it = a later **core** cycle.) | `memory.py`, `retrieval.py` + `checks/84_governance.py` | P1 | `checks/84`: a recorded banned action makes `governance_check` deny a repeat **with the prior evidence**; a fragile-file warning surfaces |

**Collision note:** only **R1** touches core (`weave.py`/`weft.py`) and the shared
`smoke.py` §11 line + `PROFILE.md`. **SY2** is a new module (`sync.py`) that *reads*
the Weft's public API (and `.db` for raw foreign-event ingest — a proper
`Weft.ingest()` is deferred to avoid colliding with R1's core lane); **B4** is
`memory.py`/`retrieval.py`. Distinct `checks/` files (80/82/84). Disjoint.

## Suggested allocation (Cycle 5)

- **Instance 1 — Claude / kernel** (`~/decima-claude`): **R1** — sole owner of `weave.py`/`weft.py`. The §11 closer.
- **Instance 2 — Claude / worktree**: **SY2** — `sync.py`.
- **Instance 3 — Claude / worktree**: **B4** — `memory.py`.

*(Lanes are agent-agnostic — bring a Codex instance onto any of them if it's working.)*

## Backlog (future cycles)

- **Snapshots, incremental fold-from-base** — the perf win (skip genesis); core change to `weave.py` fold (collides with R1, hence next cycle).
- **Cascade / lease-tree retraction** — `REDACT` cascade to derived authority/leases (`WEFT §5`).
- **Real sync transport over a network** (behind `SYNC.md` §3–4) + a proper `Weft.ingest()` with full WEFT §2 validation.
- **Real model engines** behind the router; **real sandboxing** (landlock/seccomp); **budget folded into the Weft**.
- **The Rust port** — last, once the reference is stable and complete.

## Pick-up-cold briefs (Cycle 5)

### R1 — Typed retraction → REDACT `weave.py`/`weft.py` + `checks/82_redaction.py`
**Why:** §11 #7 is the **last partial invariant**. The heartbeat has `RETRACT`
(logical withdrawal — the cell leaves projections, payload still in the events).
`REDACT` must additionally **erase the payload** while keeping the event skeleton.
**Deliverable:**
  1. A retraction **mode** on the `RETRACT` body (`WITHDRAW` default = today's behavior;
     `REDACT` = withdraw **and** erase payload). Per `WEFT §5` / `specs/FOLD §10`.
  2. On `REDACT`, the fold removes the cell's content from every projection (`of_type`,
     `content`/`content_heads`, `why`, and the `state_root` leaf becomes a tombstone),
     **but** `weft.events()` still yields the prior asserts + the redact event (skeletons),
     and tamper-evidence over the log still holds.
  3. Heartbeat erasure analog: the content bytes are gone from the materialized state;
     full cryptographic blob erasure is noted as the durable form.
  4. **Update the inline §11 #7 check** (currently "partial") to assert payload-absent +
     skeleton-present, flipping it to **holds** → 8/8. Refresh the `smoke.py` §11 wording
     and `PROFILE.md` (retraction row + the §11 table).
**Acceptance:** `checks/82` demonstrates REDACT; the §11 section reads **8/8 hold**; all
prior checks green.
**Lane:** you own `weave.py`/`weft.py` this cycle. Demo in `checks/82`; only the §11
wording line in `smoke.py` may change.

### SY2 — Sync transport `sync.py` + `checks/80_sync_transport.py`
**Why:** SY1 simulated peers as forks in one Weft. SY2 is the real protocol between
**two Weft instances** — still offline (in-process, shared keyring for the HMAC profile).
**Deliverable:** a new module that, given two `Weft`s, computes each side's missing
events (frontier / causal-difference), transfers the event records, **ingests** them into
the target (insert the verified foreign rows; the existing `events()` read-verification
checks id+sig — note a proper `Weft.ingest()` with full `WEFT §2` validation is deferred),
folds both, and asserts an identical `state_root` (convergence). Bidirectional.
**Acceptance:** `checks/80`: peer A and peer B each hold unique events, sync, converge to
one `state_root`; a tampered foreign event is rejected on fold.
**Lane:** `sync.py` + `checks/80`. Public Weft/Weave API + `.db` for raw ingest; **no
`weft.py` source edit** (that's R1's).

### B4 — Memory-as-governance `memory.py`/`retrieval.py` + `checks/84_governance.py`
**Why:** VISION's "memory prevents repeated bad actions — what's banned, what's fragile,
what failed." B3 did decay/consolidation/heat; this adds governance.
**Deliverable:** functions to record governance claims (`banned_action`, `fragile_file`,
`failed_approach`) and `governance_check(target) -> {allow, reason, evidence}` that queries
memory (trusted, instruction-eligible) and returns a verdict with provenance. The kernel
**wiring** (Decima auto-consulting before it delegates) is a later core cycle — note it.
**Acceptance:** `checks/84`: a recorded banned action makes `governance_check` deny a repeat
**with the prior evidence**; a `fragile_file` warning surfaces.
**Lane:** `memory.py` + `retrieval.py` + `checks/84`. No core edits.
