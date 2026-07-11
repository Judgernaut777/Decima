## decima/projections — disposable read-models over the Weft (Phase 7)

`decima/projections/` is a NEW package of DISPOSABLE read-models folded from the signed Weft. It appends nothing, mints no authority, executes nothing, and treats every item's content as DATA (invariants 2/3/5). The Weft stays the sole canonical store; every view here is rebuildable byte-identically at any time.

### engine.py — the projection substrate
- `Projection` protocol: `name`, `version:int`, `reset()`, `apply(event)`, `checkpoint() -> ProjectionCheckpoint`.
- `FoldState` — a small shared reducer that folds the linear log into live Cells (LWW content, retraction/REDACT tombstones, edges, attestations, invocations, logical frontier). It is a projection, never a second store; for the runtime's linear domain logs its per-cell result equals the kernel `Weave` fold (a fidelity test asserts this).
- `BaseProjection` — common machinery + a deterministic `state_root()` = `content_id({name, version, view})`.
- `ProjectionDriver` — pumps `weft.events()` into projections and provides: INCREMENTAL update (`weft.events(from_seq=last_seq)`), FULL REBUILD (`reset()` + replay), MIGRATION-BY-REBUILD (a `version` mismatch forces a clean rebuild, never in-place), and LAG reporting (`frontier - last_seq`). Reads only the public `events`/`count` seams, so tamper-evidence (signature check on read) rides along.

### read-models
- `tasks` — plan-step list / status / deps / due + derived `ready`.
- `projects` — plan objective / status / member agents / progress counts.
- `agents` — agent hierarchy (parent/children), status, token/monetary/deadline budgets.
- `approvals` — Morta inbox buckets: pending / approved / denied / consumed / expired (expiry judged at the logical frontier, never wall-clock).
- `activity` — an append-per-event human timeline over asserts/retracts/invokes/attests/receipts/transitions, filterable + a digest.
- `knowledge` — notes / documents / links (typed edges) / provenance, carrying each item's `instruction_eligible`/`trust` flag; a retracted note stops appearing.
- `search` — a derived, disposable exact-text inverted index over knowledge; deleting it loses nothing canonical, and `rebuild()` reproduces an identical `fingerprint`. `semantic_rank` is a noted seam (no vector dep).

### acceptance
`tests/projections/` builds a Weft with plans/tasks/agents/notes/approvals, projects it incrementally, then rebuilds every projection from scratch and asserts equality (state_root + field-by-field view + checkpoint). It also pins: retracted note disappears, deleting the search index does not delete knowledge, a version bump triggers a clean rebuild, and the task/agent views match the canonical kernel `Weave` fold.

Self-verified: `tests/projections` (13) + `tests/architecture` (19) green, full suite 97 green, `import decima.kernel, decima.runtime, decima.projections` ok, ruff clean.