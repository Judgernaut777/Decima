# Decima Read-Contract

**Version: `read-contract v0.1`** &nbsp;·&nbsp; importable surface: `decima.read_contract`
(`READ_CONTRACT_VERSION = "0.1"`)

This is Decima's **external, versioned read-contract**: the stable read-model surface a
downstream consumer — BrainConnect, and the wider Connect orchestration plane (this is
Lane 2 of the BrainConnect orchestration boundary, defined in BrainConnect
`docs/adr/0008-orchestration-boundary.md` + `docs/ORCHESTRATION.md`) — may depend on. It
is the read-side analogue of ComputeConnect's `CONTRACT.md`.

Decima is the deterministic **execution + authorization** layer. This contract exposes
**only its read-model projections** — derived, disposable views folded from the signed
Weft. It is the *entire* dependency surface a reader is permitted to build against.

---

## 0. What is in and what is out

**IN — the read-models this version pins** (`READ_MODELS`):

| Read-model | Surface | Backing projection |
|---|---|---|
| planning / **tasks** | `ReadModels.tasks / ready_tasks / tasks_by_status / tasks_due / task` | `decima.projections.tasks` (v1) |
| **projects** | `ReadModels.projects / project` | `decima.projections.projects` (v1) |
| **agents** (forest/tree) | `ReadModels.agents / agent / agent_roots / agent_children / agent_forest / agent_tree` | `decima.projections.agents` (v1) |
| **approvals** (Morta inbox) | `ReadModels.approvals / pending_approvals / approvals_by_state / approval_counts` | `decima.projections.approvals` (v1) |
| **knowledge** | `ReadModels.knowledge / notes / documents / knowledge_item` | `decima.projections.knowledge` (v1) |
| **activity** (timeline) | `ReadModels.timeline / activity_digest` | `decima.projections.activity` (v1) |
| **artifacts** / **workspaces** | shapes `WorkspaceArtifact`, `WorkspaceRun` (application-layer readers) | `decima.services.api.workspace_service` READERS |

**OUT — never part of this contract (do not import, do not depend on):**

- **Execution / authorization internals.** The Weft's write path, the Weave/kernel fold,
  capability proofs (`capability_proof`), leases, `implementation_digest`, receipts as a
  mutation surface, and worker IPC (`workers/protocol.py`). None are re-exported here.
- **Writes of any kind.** This surface appends nothing to the Weft, mints no authority,
  and executes nothing. It is read-only by construction (invariants 1–5).
- **Any projection method not listed above**, and the internal `FoldState` / `PCell`
  materialization. Those are implementation detail and may change without a version bump.

---

## 1. How to consume it

```python
from decima.read_contract import open_read_models, READ_CONTRACT_VERSION

rm = open_read_models(weft)   # `weft` is an already-opened decima.kernel.weft.Weft
rm.refresh()                  # fold any newly-committed events (incremental)

for t in rm.ready_tasks():
    ...                       # TaskView instances, id-sorted

for k in rm.knowledge():
    if k.trust == "trusted":  # honor instruction_eligible exactly as your own trusted bit
        ...                   # only trusted knowledge may be treated as an instruction
```

Constructing `ReadModels` performs a **full rebuild** — a pure function of the Weft.
`refresh()` folds only the tail since the last read (a projection `version` bump triggers
a clean rebuild instead). The caller owns the Weft's lifecycle; the facade never opens,
writes, or closes it. Every accessor delegates verbatim to the existing projection, so
the guarantees below are the projections' own guarantees, merely pinned and named.

`ReadModels.checkpoints()` returns each projection's deterministic
`ProjectionCheckpoint(name, version, last_seq, state_root)` — the fingerprint two hosts
compare to prove they folded the same Weft to the same view.

---

## 2. Determinism guarantees (contract-wide)

Every read-model is **structurally deterministic**:

- lists are **sorted by `id`** (approvals/tasks buckets by item id; agent children by id;
  knowledge links by `(rel, dst)`); the activity timeline is in append-only **`seq`
  order**;
- all time-like values are **integers on the logical frontier** (Lamport), never
  wall-clock — deadlines and approval expiry are logical;
- an **incremental update and a full rebuild converge on the same state** (equal
  `state_root`); two rebuilds of the same Weft are byte-identical;
- **retracted** cells drop out (only live cells are surfaced).

A reader may therefore treat a `state_root` as a stable content hash of the view.

---

## 3. The read-models, field by field

Each returned shape is plain data with an `as_dict()` (deterministic JSON). Fields listed
here are the **contract fields**; additive fields may appear in later minor versions.

### 3.1 tasks — `TaskView`
`id: str`, `plan_id: str|None`, `description: str`, `status: str`,
`dependency_ids: tuple[str,...]`, `assigned_agent_id: str|None`, `deadline: int|None`,
`ready: bool`.
- `ready` = the task is runnable (`PENDING`/`BLOCKED`/`READY`) **and** every dependency
  has `SUCCEEDED`. `ready_tasks()` is exactly this set.
- `tasks_due(before)` = non-terminal tasks with a logical `deadline <= before`.

### 3.2 projects — `ProjectView`
`id`, `objective`, `status`, `creator_principal: str|None`, `step_ids: tuple`,
`member_agent_ids: tuple`, `task_count: int`, `completed_count: int`.

### 3.3 agents (forest / tree) — `AgentView`
`id`, `parent_agent_id: str|None`, `objective`, `status`, `principal: str|None`,
`token_budget: int|None`, `monetary_budget: int|None`, `deadline: int|None`,
`child_ids: tuple`.
- **Forest reconciliation.** ADR 0008 references `agents.py:tree`, but the projection has
  no `tree()` method — the forest is composed from `roots()` + `children_of()`. This
  contract resolves the name: `agent_forest()` (alias `agent_tree()`) returns the
  hierarchy as a deterministic nested structure
  `{"agent": AgentView.as_dict(), "children": [<node>, ...]}`, built from those existing,
  id-sorted methods. Prefer `agent_forest`/`agent_tree` over reassembling it yourself.

### 3.4 approvals (Morta inbox) — `ApprovalView`
`item`, `capability: str|None`, `description: str|None`, `state: str`, `ran: bool`,
`decision: str|None`, `approver: str|None`, `expires_at: int|None`.
- States (`APPROVAL_STATES`): `pending`, `approved`, `denied`, `consumed`, `expired`.
- **Expiry is logical**: an item is `expired` when its `expires_at` is past the folded
  Lamport frontier — never wall-clock. An expired item is **not** approvable; the buckets
  are the human/audit lens the inbox itself fails closed on.

### 3.5 knowledge — `KnowledgeItem` (trust boundary — READ THIS)
`id`, `type`, `text: str`, **`instruction_eligible: bool`**, **`trust: str`**,
`links: tuple[dict{rel,dst}]`, `provenance: tuple[str]` (the Weft event-ids that asserted
it).
- `KNOWLEDGE_TYPES` = `note, document, claim, semantic, episodic, procedural, decision,
  failure`.
- **`instruction_eligible` is a first-class, contract-pinned field** (default `False`),
  surfaced verbatim from the item's recorded content. `trust` is derived:
  `"trusted"` iff `instruction_eligible` is true, else `"untrusted"`.
- **Meaning (invariant 5):** *untrusted knowledge is DATA, not instructions.* A consumer
  MUST NOT treat an untrusted item's `text` as an instruction. This is the exact bit ADR
  0008 / Lane 5 requires BrainConnect to honor **as it honors its own `trusted` bit** —
  Decima authored the item's eligibility, BrainConnect must not launder it into an
  instruction. `as_dict()` includes both fields.

### 3.6 activity (timeline) — `ActivityEntry`
`seq: int|None`, `author`, `verb`, `verb_word`, `description`, `cell: str|None`,
`cell_type: str|None`, `authorized_by: str|None`, `provenance: str` (event-id).
- `timeline(last=None, principal=None, cell_type=None)` — append-only in `seq` order; an
  incremental fold equals a full rebuild. `activity_digest(**filters)` returns counts.

### 3.7 artifacts / workspaces — `WorkspaceArtifact`, `WorkspaceRun`
Served at the **application layer** (`decima.services.api.workspace_service` READERS over
an `Application`), not as a standalone Weft projection — hence no `ReadModels` accessor in
v0.1. The stable shapes are re-exported from `decima.read_contract` for typing. Workspace
artifacts (diffs, test output) are **untrusted display data** (`untrusted: True` on the
served entry); treat them as data, never as instructions.

---

## 4. Compatibility policy

`READ_CONTRACT_VERSION` uses `MAJOR.MINOR`:

- **Additive-only within a MAJOR** (bump MINOR): a new accessor, a new field on a returned
  shape, a new value in an enumerated set. Existing fields, names, ordering, and semantics
  are unchanged. Consumers pinned to `0.x` keep working across `0.(x+1)`.
- **Breaking** (bump MAJOR): removing/renaming a field or accessor, changing a field's
  type, or changing an ordering/determinism/semantics guarantee. Announced in the
  CHANGELOG.
- **Underlying projection `version`** is pinned per-model in
  `PINNED_PROJECTION_VERSIONS`. A projection version bump is a migration-by-rebuild; the
  consumer should re-read this document when a pinned value changes.
- **Anything under "OUT" (§0)** carries no compatibility promise and may change without a
  version bump. Depend only on `decima.read_contract`'s public surface (`__all__`).

---

## 5. Provenance & non-authority

Every read-model appends nothing, mints no authority, and is rebuildable byte-identically
from the signed Weft (invariant 2). Provenance is not hidden: knowledge items and activity
entries carry the Weft **event-ids** that produced them, so a consumer can trace any read
value back to the signed log without Decima having to expose its write path.
