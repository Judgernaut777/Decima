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
**Tooling — ✅** `heartbeat/checks/NN_*.py` auto-run by `smoke.py`; new lanes add a file there, never edit `smoke.py`.

Oracle: **all 8 FOLD §11 invariants hold.** Merge + snapshots + sync (sim & transport) +
redaction + memory governance all real in the reference. The scope catalog of what's next
(donor adoptions + ecosystem capabilities + blue/red-team) is
[`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md).

## Coordination rules

1. **Core-kernel files serialize:** `weave.py`, `weft.py`, `kernel.py`, `executor.py`
   — **one owner per cycle.** Everyone else builds in new modules and *calls* the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, not `smoke.py`.** Own a free `NN`.
3. **`specs/` is collision-free** — one instance per file.
4. Keep the oracle green: `cd heartbeat && python3 smoke.py` → `alive. ✓`, exit 0.

## Cycle 6 — active

Build the highest-leverage **buildable-now** capabilities from the Capability Map: the
security beachhead (**detection-as-code**), the capability/skill projections (**Capability
Inspector + the Constellation**), and the first slice of the **agent shorthand**. All three
are **new-module lanes — zero core contention this cycle.**

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **DET1** | **Detection-as-code (security beachhead)** — a detection is a Nona-forged, **test-gated** rule (pattern/IOC matcher) with TP/FP fixtures; promotion is gated on passing; a promoted detection applied to data Cells emits `finding` Cells with provenance. The purple-team loop: a red-team evasion becomes a new FP fixture. (`CAPABILITY_MAP` Part C.) | `detection.py` (new) + `checks/86_detection.py` | P1 | `checks/86`: a benign-but-suspicious sample does **not** false-positive; a malicious pattern matches → `finding` with provenance; a rule that fails its fixtures is **not** promoted |
| **INS1** | **Capability Inspector + the Constellation** (`CAPABILITY_MAP` A2 + D1) — from any capability, list **every holder + the full delegation chain** (exact fold over the Weft, never heuristic); render the forged-skills/capabilities as a **Constellation** tree (lineage + promotion state, grouped by domain). | `inspector.py` (new) + `checks/88_constellation.py` | P1 | `checks/88`: inspector returns holders + downhill delegation chain of a granted cap (impostor excluded); constellation renders forged skills with lineage + promoted/quarantined state |
| **SH1** | **Agent shorthand (D2 first slice)** — a Cell-ref **pointer language** + a **signed symbol dictionary** (a Cell); `encode`/`decode` inter-agent messages **deterministically** (lossless round-trip), measure the token/byte saving. Inbound shorthand is decoded, **logged on the Weft, and treated as untrusted data** — never an opaque private language. | `shorthand.py` (new) + `checks/90_shorthand.py` | P2 | `checks/90`: a message referencing Cell IDs + dictionary round-trips losslessly; reports the saving; a forged inbound message decodes to a DATA claim (`instruction_eligible=false`), not an instruction |

**Collision note:** all three are **new modules** with distinct `checks/` files (86/88/90);
none touch core (`weave`/`weft`/`kernel`/`executor`) or each other — they call the public
API + `reckoner`/`memory`. `smoke.py` is untouched (the `checks/` harness runs them). **No
core owner is needed this cycle** — the cleanest fan-out yet.

## Suggested allocation (Cycle 6 — all Claude, agent-agnostic)

- **Instance 1**: **DET1** — `detection.py` (security beachhead).
- **Instance 2**: **INS1** — `inspector.py` (Capability Inspector + Constellation).
- **Instance 3**: **SH1** — `shorthand.py` (agent shorthand).

## Backlog (future cycles)

- **Snapshots, incremental fold-from-base** — the perf win (skip genesis); core change to `weave.py` fold.
- **Cascade / lease-tree retraction** — `REDACT` cascade to derived authority/leases (`WEFT §5`).
- **Sandboxed-principal substrate** — the no-ambient-authority linchpin: a `specs/SANDBOX.md` contract (namespaces/seccomp/landlock; **WASM component model** as the durable form) + an `executor` sandbox-policy seam. Needs deps → spec + seam first (core).
- **Networked sync at scale** — Merkle-DAG diff + gossip/anti-entropy (generalize SY2 beyond two peers); a proper `Weft.ingest()` with full WEFT §2 validation.
- **Voice runtime** (`[effect]` core I/O — whisper.cpp/Piper behind a contract) + the **Constellation GUI** (post-port) — what makes it feel like a livable OS.
- **Wrap real security tools** as sandboxed external-engine workers (the blue/red flagship at scale); **real model engines** behind the router.
- **The Rust port** — last, once the reference is stable and complete.

## Pick-up-cold briefs (Cycle 6)

### DET1 — Detection-as-code `detection.py` + `checks/86_detection.py`
**Why:** the cybersecurity flagship's cheapest win, built on existing primitives — **Nona's
forge + test-gate IS the detection's unit test**, and the signed Weft is the findings log.
**Deliverable:** a new module where a detection is forged like any capability: a rule
(regex/substring/IOC/YARA-lite matcher over text or structured Cells) carrying **TP fixtures
(must match)** and **FP fixtures (must NOT match)**; reuse `reckoner` to test-gate — a rule is
**promoted only if it matches every TP and no FP**, else it stays quarantined. A promoted
detection applied to data Cells (claims/results/observations) emits `finding` Cells (rule id,
matched source, severity) with provenance via `memory`/the Weft. Note the **purple loop**: a
red-team evasion becomes a new FP fixture that re-gates the rule.
**Acceptance:** `checks/86`: forge a detection with TP+FP fixtures; a benign-but-suspicious
sample does not false-positive; a malicious pattern → `finding` with provenance; a rule that
fails its fixtures is not promoted. Fail loud.
**Lane:** `detection.py` + `checks/86`. Public `reckoner`/`memory`/`weave` API; no core edit.

### INS1 — Capability Inspector + Constellation `inspector.py` + `checks/88_constellation.py`
**Why:** A2 (Fuchsia-validated capability inspection) + D1 (the Skyrim-style skill tree). Both
are **exact projections over the Weave/Weft** of "what authority exists / what Decima can do."
**Deliverable:** a new module: (1) `capability_holders(cap_id)` → every agent whose envelope
holds it + the **delegation chain** (walk parent grants to the root, showing attenuations) —
exact fold, never heuristic; (2) `constellation()` → the forged-skills/capabilities tree: each
capability a node with lineage (parent), promotion state (quarantined/promoted), grouped by
domain/effect; render as display lines (like `task_tree`/`workspace`). The Constellation is the
data model behind the eventual Skyrim-style GUI; text/graph now.
**Acceptance:** `checks/88`: grant a cap via delegation, inspector returns the holder(s) +
downhill chain (an impostor that doesn't hold it is excluded); constellation renders forged
skills with lineage + state. Fail loud.
**Lane:** `inspector.py` + `checks/88`. Reads `weave`/`weft` public API; no core edit.

### SH1 — Agent shorthand `shorthand.py` + `checks/90_shorthand.py`
**Why:** D2 — cut token cost / tighten agent↔agent comms, as a **reversible, auditable
transport over the canonical Weft**, never an opaque private language.
**Deliverable:** a new module: (1) a **signed symbol dictionary** stored as a versioned Cell
(frequent concepts/ops → short codes); (2) `encode(msg)` → a compact form that references
**Cell IDs** (pointer language) + dictionary codes; `decode(compact)` → the original, with a
**deterministic lossless round-trip**; (3) a reported token/byte saving. An inbound shorthand
message from another agent is **decoded, logged on the Weft, and stored as untrusted data**
(`instruction_eligible=false`) until authorized.
**Acceptance:** `checks/90`: a message referencing Cell IDs + dictionary round-trips losslessly;
report the saving; a forged inbound shorthand decodes to a DATA claim, not an instruction.
Fail loud.
**Lane:** `shorthand.py` + `checks/90`. Uses `content_id`/`weft`/`memory` public API; no core edit.
