# Merge Semantics — concurrent heads, merge classes, and adjudication

**Status:** design (A1). No code. This is the contract the Rust port must satisfy
and the rule the Heartbeat must not contradict before it gets there.

The Heartbeat is single-process and linear: every event has one parent, the head
is a chain, and "last write wins" *happens to be* deterministic because arrival
order equals the total order `(lamport, event_id)`. The durable system is a
**DAG**: peers sync by union (`FOLD §9`), so two principals can assert to the same
Cell concurrently, with neither causally before the other. At that point "last
write wins" is no longer defined — there is no last. This document says, for
**every Cell type the Heartbeat folds today**, how concurrency is resolved, how
concurrent heads are represented in `CellState`, and how an adjudication (an
`ATTEST`) collapses them.

It is the design half of the merge layer; it gates the Rust port and reshapes
nothing in the Python profile that is already linear. Read alongside
`FOLD_AND_LIFECYCLE.md` §3 (CellState), §4 (merge classes), §9 (sync), §11
(invariants), and `WEFT_PROTOCOL.md` §4 (`conflict_key`) and §9 (ordering).

---

## 1. The problem, precisely

`Weave._apply` (today) materializes an `ASSERT CONTENT` by overwriting:

```python
cell.content = b.get("content", {})   # last applied wins
cell.version += 1
```

This is correct **only** while the log is a chain. Three facts make it wrong for
the DAG, and §11 names all three:

- **§11.2 — arrival-order independence.** Two folds of the same event *set* must
  yield the same `state_root`, regardless of the order events were delivered.
  "Last applied wins" makes state a function of arrival order. The fix is to make
  the reducer a pure function of the event *set* + the deterministic total order
  `(lamport, event_id)` (`WEFT §9`), never of delivery sequence.
- **§3 — heads may be plural.** "A concurrent conflict is preserved until a type
  reducer merges it or an adjudication attestation chooses a resolution."
  Overwriting silently destroys the losing branch; the loser must remain
  *inspectable*, not gone.
- **§4 — no generic AI merge is authoritative.** Where a winner cannot be picked
  mechanically, the branches are preserved and a *policy or trusted principal*
  attests the resolution.

So the merge layer has two jobs: **(a)** decide, per Cell type, whether
concurrency resolves mechanically (and how), and **(b)** when it does not,
represent the live branches and define the `ATTEST` that resolves them.

---

## 2. Concurrent heads in CellState

`FOLD §3` already declares `content_heads`, `type_heads`, and `conflicts` as
plural. This section pins their meaning for the reducer. (Design only — the
Heartbeat's `Cell` keeps a scalar `content`; these are the durable-CellState
fields the linear profile collapses to one.)

### 2.1 Heads are computed by causal dominance, never by arrival

For a Cell and a **`conflict_key`** (`WEFT §4`, field 8 — the type-defined
logical register/slot an assertion targets; absent ⇒ the whole content is one
register):

```text
heads(cell, conflict_key) =
  { e : e is a live ASSERT to (cell, conflict_key)
        and no other live ASSERT to (cell, conflict_key) is a causal descendant of e }
```

A "live" assertion is one not withdrawn by an effective `RETRACT` (`WEFT §5`) and
not superseded by an adjudication (§4 below). An assertion `e2` *dominates* `e1`
when `e1 ∈ ancestors(e2)` — i.e. the author of `e2` had already observed `e1`.
Sequential writes therefore collapse to one head (each sees the last); only
**mutually concurrent** writes produce `|heads| > 1`.

This definition is pure over the event set: it never consults arrival order, the
clock, or mutable globals (`FOLD §2`). That is exactly what §11.2 demands.

### 2.2 The reducer projects heads → materialized content per merge class

`content` (the value consumers read) is the type reducer applied to
`heads(cell, conflict_key)`:

| `|heads|` | what the reducer does |
|---|---|
| 0 | Cell absent / fully retracted — not in `of_type`. |
| 1 | The single head's value (the common, sequential case). |
| >1 | **Resolve by merge class** (§3). Classes that merge mechanically return one value; classes that cannot record a `conflicts[]` entry and expose all heads until an `ATTEST` resolves them (§4). |

`conflicts[]` (an entry per unresolved `(conflict_key, head_set)`) is what makes a
conflict *loud* (`FOLD §9`: "conflicts are surfaced by Cell reducers, not hidden
by transport"). A red conflict is a first-class, queryable state — not a crash
and not a silent overwrite.

---

## 3. Merge class per Cell type

Every Type Cell declares one merge class (`FOLD §4`). The table below assigns one
to **every Cell type the Heartbeat folds today** — `capability, agent, task,
result, utterance, speech, claim, entity, type, note`, plus the generic `thing`
fallback. Several types are *structured records* whose fields differ in
volatility; for those the cell-level class is **Map CRDT** and the per-field
classes are pinned in §3.1 (a Map CRDT's value is "per-key declared merge class",
`FOLD §4`). No type is left unmapped — that is A1's acceptance bar.

| Cell type | Merge class | One-line rationale |
|---|---|---|
| `utterance` | **Append log** | A human turn is an observation; never overwritten, only accreted. Concurrency is union in causal order — no conflict is possible. (`FOLD §4`: messages/observations.) |
| `speech` | **Append log** | Decima's reply is an emitted message; same as `utterance`. |
| `result` / `receipt` | **Immutable value** | A receipt records one observed outcome of one `(invocation, attempt)`; identical content dedupes, differing content is a *different* receipt — never a merge. Status *progression* across attempts is the receipt **State machine** of `WEFT §8` (see A2), folded over the receipt append-log keyed by `(invocation, attempt)`. |
| `entity` | **Immutable value** | An entity is an identity anchor; `entity_id = hash(name)`, so a different name is a different Cell and two asserts of the same name dedupe. Future per-attribute fields graduate it to **Map CRDT**. |
| `type` | **Immutable value** (binding) / **Semantic adjudication** (schema) | The name→Cell binding is content-addressed and dedupes. A *schema evolution* is a plan-shaped change: branches preserved, a trusted principal attests the migration (`FOLD §4`: schemas). |
| `note` | **Sequence CRDT** | Workspace block text is the canonical collaborative-edit case (`FOLD §4`). The profile asserts a whole `{text}` today (so it behaves as LWW); the durable class is a sequence with stable element ids + tombstones. |
| `capability` | **State machine** | A grant's *definition* (`name/effect/impl/parent`) is immutable by content-address; its *lifecycle* — `QUARANTINED → PROMOTED → REVOKED` — is a transition table gated by `ATTEST promote` and `RETRACT` (`FOLD §4`: promotions). Envelope *membership* (which agents hold it) is the **OR-set** carried on `agent` (§3.1). Invalid transitions become error Cells, never silent state. |
| `task` | **State machine** | A delegation is a *run*: `assigned → done | denied | refused | ungranted` (`FOLD §4`: runs). The leaf metrics (`steps`, `denials`, `latency_ms`) are **Counter** sub-fields; `result` is write-once. Concurrent transitions out of the same state from different principals are a conflict the orchestrator policy adjudicates. |
| `agent` | **Map CRDT** | A structured record; merge is per key (§3.1). The load-bearing field, `envelope`, is an **OR-set** of grants — the one place §4 names capability grants explicitly. |
| `claim` | **Semantic adjudication** | Knowledge. Two contradictory claims are concurrent *heads of belief*; neither is mechanically "later-is-truer". Branches are preserved and an `ATTEST` from a principal authorized to adjudicate selects/merges them. No AI auto-merge is authoritative (`FOLD §4`, closing rule) — this is the same trust boundary as the recall-vs-instruct law. |
| `thing` (generic) | **LWW register** | The untyped fallback `_ensure` mints. Lowest-value: deterministic winner by `(lamport, event_id)`, losing head kept inspectable. A Cell should acquire a real type (and class) before it carries value. |

### 3.1 Per-field classes for the Map-CRDT records

`agent` and (record-wise) `task` carry fields of differing volatility. The Map
CRDT's per-key declaration:

**`agent`** `{principal, objective, brain, envelope, budget, sandbox, lineage}`

| field | class | why |
|---|---|---|
| `principal`, `lineage` | Immutable value | Set at mint; identity. Re-binding is a new agent, not a merge. |
| `envelope` | **OR-set** | Grants are added (allotment) and removed (`RETRACT`) by observed event identity — the canonical OR-set use (`FOLD §4`). Add-wins on concurrent add/remove of *different* grants; a concurrent grant-add vs revoke of the *same* grant resolves remove-wins (fail-closed, matching `MORTA` revocation priority and §11's "revoked authority fails closed"). |
| `objective` | MV register | A purpose change where divergence matters; preserve heads until adjudicated. |
| `budget` | Counter / LWW | A quota; a PN-counter under the trust model, or LWW for a coarse reset. |
| `brain`, `sandbox` | LWW register | Low-value config knobs. |

**`task`** — class **State machine** over `status`; `objective`/`delegator`/
`worker`/`grant`/`capability`/`parent`/`depth` are write-once (Immutable);
`steps`/`denials`/`latency_ms` are **Counter**; `result` is write-once.

---

## 4. Adjudication is an ATTEST

When a class cannot pick a winner mechanically (`claim`, the `objective` MV
register, `type` schema evolution, divergent `task`/state-machine transitions),
the branches stay live and resolution is an **`ATTEST`** — the verb that already
"expresses a signed judgment over Cells … or policy transitions" (`WEFT §7`) and
that the Heartbeat already uses to lift a capability's quarantine. Reusing it
keeps the resolution itself on the Log, signed, provenance-bearing, and
time-travelable — an adjudication is never an out-of-band mutation.

### 4.1 Shape

An adjudication `ATTEST` (mapping onto `WEFT §7 AttestBody`):

```text
ATTEST {
  subjects:   [ the Cell being adjudicated ]
  predicate:  adjudicates                  // a well-known predicate Cell
  evidence:   [ the head EventIds being resolved ]   // the conflict's head_set
  verdict:    PASS
  claims:     { resolution: SELECT  | MERGE,
                winner:     EventId? ,       // SELECT: the head that holds
                merged:     EventId? }       // MERGE: a fresh ASSERT to graft in
  evaluator:  principal / policy Cell
}
```

- **SELECT** — one existing head holds; the others become *dominated* (recorded in
  the Cell's `superseded_by` set), so they drop out of `heads()` while remaining
  in history (`FOLD §10`: the skeleton stays; this is logical, not erasure).
- **MERGE** — the evaluator authors a *new* `ASSERT` that reconciles the branches
  and attests it as the resolution; the new assertion causally descends from all
  resolved heads, so by §2.1 it dominates every one of them and becomes the lone
  head. This is the "AI may *propose* a merge; a trusted principal *attests* it"
  rule (`FOLD §4`) made concrete: the proposal is an ordinary `ASSERT`; the
  authority is the `ATTEST` over it.

### 4.2 Who may adjudicate

The `ATTEST` is authorized like any event: the evaluator principal must hold a
capability whose policy permits resolving *this* Cell/type (a `MORTA` selector,
e.g. "memory-curator may adjudicate `claim`s in realm R"). An unauthorized
adjudication is simply an unauthorized `ATTEST` — rejected at acceptance
(`WEFT §2`, step 7). There is no privileged merge path; adjudication authority is
itself a capability.

### 4.3 Convergence and idempotence

- An adjudication references its `head_set` as `evidence`. If a *new* concurrent
  head arrives after the adjudication (a head it did not observe), the conflict
  re-opens with the unresolved head(s) — resolution binds only the heads it named,
  so it can never silently bless a branch it never saw.
- Re-delivering the same adjudication `ATTEST` is idempotent by Event ID
  (`FOLD §2`) — it dominates the same heads to the same result.
- Two *concurrent* adjudications of the same conflict are themselves concurrent
  heads (of the adjudication), resolved one level up by the same rule, or by a
  policy tie-break declared on the predicate. The regress terminates because
  policy ultimately names a single authoritative evaluator per realm.

---

## 5. Review against the invariants

How each relevant invariant is honored by the above (the acceptance check A1 owes
`FOLD §4`/§11):

- **§11.1 replay determinism** — `content` is a pure reduction of `heads()` over
  the `(lamport, event_id)` total order; two folds of one event set give one
  `state_root`. ✓ (already asserted in the Heartbeat oracle).
- **§11.2 arrival-order independence** — heads are computed by causal dominance,
  not by which event was applied last; the reducer never reads arrival order.
  This is the invariant "last applied wins" violated and this design fixes; the
  linear profile already passes it only because chain order *is* the total order.
  ✓ by construction.
- **§3 / §4 concurrent heads preserved until resolved** — classes that cannot
  merge mechanically keep every branch in `content_heads` and a loud
  `conflicts[]` entry; only an authorized `ATTEST` (§4) collapses them, and it
  stays on the Log. Nothing is silently overwritten. ✓
- **§11.4 revoked authority fails closed** — the `envelope` OR-set resolves
  same-grant add-vs-revoke as **remove-wins**, so a concurrent revoke can never
  lose to a concurrent re-grant. ✓ consistent with `MORTA`.
- **§11.5 derived scope never broader than parent** — orthogonal to merge: a
  capability's downhill attenuation is enforced at `authorize`/`attenuate` time,
  not by the reducer. A merge never *widens* a grant because capability
  definitions are immutable-by-content (a wider grant is a different Cell needing
  its own authorized grant event). ✓
- **§11.8 ambiguous execution → UNKNOWN** — receipts are Immutable value; an
  ambiguous outcome is recorded as a receipt with `status = UNKNOWN`, never
  merged or rewritten into success/failure (A2/F1). The merge layer cannot
  fabricate a terminal status it never observed. ✓

---

## 6. What changes in the Heartbeat (nothing, yet — and that is the point)

The Python profile stays linear, so every merge class degenerates to its
single-head case and `_apply`'s overwrite remains observably correct. This spec
exists so that:

1. when the DAG arrives (sync, multi-principal), `_apply` is replaced by
   *compute-heads-then-reduce*, type by type, with the classes fixed here — not
   re-litigated under pressure;
2. the `conflict_key` field (`WEFT §4`) and the adjudication `ATTEST` predicate
   have agreed meanings *before* any durable data is written under them
   (the compatibility rule, `specs/README.md`);
3. `PROFILE.md`'s "ordering: linear, single parent" row has a precise forward
   contract: the DAG reducer this document specifies.

No Cell type is unmapped; every mapping is justified against `FOLD §4`; the
concurrent-head representation and its adjudication are defined and checked
against §11. That is the whole of A1.
