"""The Weave — the materialized graph, computed by folding the Weft.

Law 5: state is a fold; everything you see is a projection. A Cell is not
stored — it is the fold of every event that touched its id. `fold(weft, seq)`
rebuilds the entire world as of any point in history: that single function IS
time-travel, undo, and reproducibility.

Law 3: everything is a Cell — notes, agents, capabilities, results, views.
"""
from dataclasses import dataclass, field

from decima.weft import ASSERT, RETRACT, INVOKE, ATTEST
from decima.hashing import content_id

# Type merge classes (specs/MERGE_SEMANTICS.md §3). A Type Cell declares one;
# untagged/legacy types default to LWW (which, on a linear log, is exactly the
# old overwrite-by-order behavior — so nothing changes until events actually fork).
# Every reducer is a pure function of the event *set* and the deterministic total
# order (lamport, event_id): never of arrival order (§2.1) — that is what makes a
# forked fold converge regardless of how events were delivered (FOLD §11.2).
MERGE_LWW = "lww"            # register: highest (lamport, event_id) wins; resolved
MERGE_MV = "mv"             # register: concurrent heads preserved until adjudicated
MERGE_ORSET = "or-set"       # set: add/remove by observed event identity; add-wins
MERGE_SEQUENCE = "sequence"  # ordered text/blocks: stable element ids + tombstones (RGA-style)
MERGE_MAP = "map"           # record: each key merged by its own declared class
MERGE_COUNTER = "counter"    # PN-counter: concurrent deltas sum (commutative)
MERGE_APPEND = "append-log"  # accreted observations; union in causal order, no conflict
MERGE_ADJUDICATED = "adjudicated"  # like MV, but the resolving ATTEST is the contract (claims, schemas)
_DEFAULT_MERGE = MERGE_LWW
# Classes that preserve concurrent heads until an adjudication ATTEST collapses them.
_MULTIVALUE = (MERGE_MV, MERGE_ADJUDICATED)


@dataclass
class Cell:
    id: str
    type: str
    content: dict
    version: int = 0
    provenance: list = field(default_factory=list)   # event ids that built this cell
    attestations: list = field(default_factory=list)  # {by, claim, event}
    retracted: bool = False
    # Edges are first-class relations folded onto the cells they touch (WEFT §4
    # assertion kind EDGE; FOLD CellState edges_out/edges_in). Each record is
    # {rel, src, dst, event}: src.edges_out and dst.edges_in hold the same edge.
    edges_out: list = field(default_factory=list)
    edges_in: list = field(default_factory=list)
    # Concurrent-head representation (FOLD §3 / MERGE_SEMANTICS §2). `content` is the
    # materialized (reduced) value consumers read; `content_heads` is the ordered
    # list of live head values — [content] when resolved (LWW / single head), and
    # ALL concurrent branches when an MV type preserves them. `in_conflict` is true
    # iff an MV cell has >1 live head awaiting adjudication.
    content_heads: list = field(default_factory=list)
    in_conflict: bool = False
    # REDACT (WEFT §5 mode / FOLD §10): a redacted cell keeps its id + type as a
    # content-free tombstone — the payload is erased from every projection while the
    # event skeleton stays on the Log. `retracted` is set too (REDACT ⊃ WITHDRAW).
    redacted: bool = False


@dataclass
class Invocation:
    event: str
    by: str          # principal id
    cap: str         # capability cell id
    args: dict


class Weave:
    def __init__(self):
        self.cells: dict[str, Cell] = {}
        self.invocations: list[Invocation] = []
        self.types: dict[str, str] = {}   # type name -> TYPE_DEF cell id (Law 3)
        self.merge_classes: dict[str, str] = {}   # type name -> merge class (§3)
        self.last_seq: int = 0
        self._applied: set[str] = set()   # event ids folded so far (idempotency)
        # Merge substrate (MERGE_SEMANTICS §2). Computed during the fold. Each is
        # keyed by a NAMESPACE: a plain cell uses its id; a Map-CRDT field uses
        # `f"{cell_id}\x00{key}"` (a `conflict_key`, §2.1), so the same reducers
        # serve both whole-cell types and individual map fields.
        self._ancestors: dict[str, set] = {}      # event id -> its causal ancestor ids
        self._reg_heads: dict[str, dict] = {}     # ns -> {assert_eid: (lamport, content)}
        self._reg_superseded: dict[str, set] = {}  # ns -> {eid} dominated by an adjudication (§4)
        self._orset: dict[str, dict] = {}         # ns -> {"adds": {eid: elem}, "removes": [(elem, anc)]}
        self._counter: dict[str, dict] = {}       # ns -> {eid: int delta}  (PN-counter)
        self._appendlog: dict[str, dict] = {}     # ns -> {eid: (lamport, entry)}
        self._seq: dict[str, dict] = {}           # cell id -> {elem_id: {after,lamport,eid,value,deleted}}
        self._map_keys: dict[str, set] = {}       # cell id -> set of field keys seen

    @classmethod
    def fold(cls, weft, upto_seq: int | None = None) -> "Weave":
        """Fold the Weft into the Weave. Events are applied in the deterministic
        total order `(lamport, event_id)` (FOLD §2 / WEFT §9), NOT storage/arrival
        order — that ordering is what makes the result independent of how events
        arrived, and it guarantees every event's causal ancestors are applied
        before it (a parent's lamport is always strictly smaller). On a linear log
        this order equals seq order, so nothing changes until events fork."""
        w = cls()
        evs = list(weft.events(upto_seq))
        for ev in sorted(evs, key=lambda e: (e.lamport, e.id)):
            w._apply(ev)
        w.last_seq = max((e.seq for e in evs), default=0)
        return w

    def _ensure(self, cid: str, type: str = "thing") -> Cell:
        cell = self.cells.get(cid)
        if cell is None:
            cell = Cell(id=cid, type=type, content={})
            self.cells[cid] = cell
        return cell

    def _redact(self, cell):
        """Erase a cell's payload from every projection (FOLD §10). The materialized
        content and ALL merge substrate keyed to the cell — including its Map-field
        conflict_keys (`cell\\x00key`) — are purged; a content-free tombstone
        (`redacted=True`) remains, and the cell drops out of `of_type` via `retracted`.
        The event skeleton stays on the Log; physical byte-erasure of the payload is a
        separate GC step that needs encrypted blobs (not in this profile)."""
        cell.content = {}
        cell.content_heads = []
        cell.in_conflict = False
        cell.redacted = True
        pfx = cell.id + "\x00"
        for d in (self._reg_heads, self._reg_superseded, self._orset, self._counter,
                  self._appendlog, self._seq, self._map_keys):
            for ns in [n for n in d if n == cell.id or n.startswith(pfx)]:
                del d[ns]

    def _apply(self, ev):
        # Idempotent by Event ID (FOLD §2): duplicate delivery of the same event
        # — e.g. via sync or a re-fed queue — must not change state. A second
        # apply of an id already folded is a no-op.
        if ev.id in self._applied:
            return
        self._applied.add(ev.id)

        # Causal ancestors (MERGE_SEMANTICS §2.1). We fold in (lamport, event_id)
        # order, so every parent — and thus its ancestor set — is already applied.
        # Domination is decided against this: assertion e supersedes a head h iff
        # h ∈ ancestors(e) (e's author had already observed h).
        anc = set(ev.parents)
        for p in ev.parents:
            anc |= self._ancestors.get(p, set())
        self._ancestors[ev.id] = anc

        b = ev.body
        if ev.verb == ASSERT:
            # ASSERT carries an assertion kind (WEFT §4): CONTENT upserts a Cell's
            # content (today's path, the default), EDGE records a typed relation,
            # TYPE_DEF registers a type as a Cell (Law 3). Read kind BEFORE b["cell"]
            # — EDGE bodies have no "cell" key.
            kind = b.get("kind", "CONTENT")

            if kind == "EDGE":
                src, rel, dst = b["src"], b["rel"], b["dst"]
                s, d = self._ensure(src), self._ensure(dst)
                key = (rel, src, dst)
                if not any((e["rel"], e["src"], e["dst"]) == key for e in s.edges_out):
                    edge = {"rel": rel, "src": src, "dst": dst, "event": ev.id}
                    s.edges_out.append(edge)
                    d.edges_in.append(edge)
                return

            cid = b["cell"]
            cell = self._ensure(cid, type=b.get("type", "thing"))
            cell.type = b.get("type", cell.type)
            content = b.get("content", {})
            cell.version += 1
            cell.retracted = False
            cell.provenance.append(ev.id)

            # Materialize by the Type Cell's merge class (MERGE_SEMANTICS §3).
            mc = self._merge_class_of(cell.type)
            if mc == MERGE_ORSET:
                self._apply_orset(cell.id, cell, ev, anc, content)
            elif mc == MERGE_COUNTER:
                self._apply_counter(cell.id, cell, ev, content)
            elif mc == MERGE_APPEND:
                self._apply_append(cell.id, cell, ev, content)
            elif mc == MERGE_SEQUENCE:
                self._apply_sequence(cell, ev, content)
            elif mc == MERGE_MAP:
                self._apply_map(cell, ev, anc, content)
            else:  # LWW / MV / adjudicated / generic 'thing' → register
                self._apply_register(cell.id, cell, ev, anc, content, mc)

            if kind == "TYPE_DEF":
                # A type is itself a Cell; index its name AND its declared merge
                # class, so both "what types exist" and "how they merge" are data.
                name = cell.content.get("name", cid)
                self.types[name] = cid
                self.merge_classes[name] = cell.content.get("merge_class") or _DEFAULT_MERGE

        elif ev.verb == RETRACT:
            cell = self.cells.get(b["cell"])
            if cell:
                cell.retracted = True
                cell.provenance.append(ev.id)
                # Retraction MODE (WEFT §5): WITHDRAW (default) tombstones the cell;
                # REDACT additionally ERASES the payload from every projection while
                # the event skeleton stays on the Log (FOLD §10 / §11 #7).
                if b.get("mode") == "REDACT":
                    self._redact(cell)

        elif ev.verb == INVOKE:
            self.invocations.append(
                Invocation(event=ev.id, by=ev.author, cap=b["cap"], args=b.get("args", {}))
            )

        elif ev.verb == ATTEST:
            target = self.cells.get(b.get("target_cell"))
            if target:
                target.attestations.append(
                    {"by": ev.author, "claim": b.get("claim", ""), "event": ev.id}
                )
                # Adjudication (MERGE_SEMANTICS §4): an ATTEST with predicate
                # 'adjudicates' collapses preserved heads (MV / adjudicated classes).
                # SELECT supersedes the non-winner heads it names as `evidence`, so
                # they drop out of heads() while staying in history (§4.1, logical not
                # erasure). MERGE needs no special case: the evaluator authors a fresh
                # ASSERT whose parents are all the heads, so by causal dominance (§2.1)
                # it becomes the lone head on its own. Resolution binds only the named
                # evidence — a later, unobserved concurrent head re-opens the conflict
                # (§4.3). No silent AI merge: the authority is the signed ATTEST.
                if b.get("predicate") == "adjudicates":
                    ns = target.id
                    if b.get("resolution", "select") == "select":
                        sup = self._reg_superseded.setdefault(ns, set())
                        winner = b.get("winner")
                        for eid in b.get("evidence", []):
                            if eid != winner:
                                sup.add(eid)
                    self._materialize_register(target, ns, self._merge_class_of(target.type))
                    target.version += 1
                    target.provenance.append(ev.id)
                # Promotion: a trusted attestation lifts a capability's quarantine
                # entirely — clearing both the flag and the sandbox_only caveat.
                if b.get("promote") and target.type == "capability":
                    caveats = {k: v for k, v in target.content.get("caveats", {}).items()
                               if k != "sandbox_only"}
                    target.content = {**target.content, "quarantined": False, "caveats": caveats}
                    target.content_heads = [target.content]   # keep the resolved head in sync
                    target.version += 1
                    target.provenance.append(ev.id)

    # -- merge reducers (MERGE_SEMANTICS §3) --------------------------------
    # Every reducer below operates over a NAMESPACE `ns` (a cell id, or a
    # `cell\x00key` conflict_key for a Map field) and recomputes the materialized
    # value purely from the accumulated ops + the (lamport, event_id) order. None
    # reads arrival order, so a forked fold converges either way (FOLD §11.2).
    def _merge_class_of(self, type_name: str) -> str:
        return self.merge_classes.get(type_name, _DEFAULT_MERGE)

    def _field_class_of(self, type_name: str, key: str) -> str:
        """A Map type declares per-key classes in its TYPE_DEF `field_classes`;
        unlisted keys default to LWW (MERGE_SEMANTICS §3.1)."""
        cid = self.types.get(type_name)
        if cid and cid in self.cells:
            return (self.cells[cid].content.get("field_classes") or {}).get(key, MERGE_LWW)
        return MERGE_LWW

    # ----- register (LWW / MV / adjudicated) -------------------------------
    def _reg_push(self, ns, ev, anc, content):
        """Add an assertion's head, dropping any existing head it dominates (a head
        in its causal ancestors — its author had already observed it). Sequential
        writes each see the last, so heads collapse to one; only mutually concurrent
        writes leave |heads| > 1."""
        heads = self._reg_heads.setdefault(ns, {})
        for h in [h for h in heads if h in anc]:
            del heads[h]
        heads[ev.id] = (ev.lamport, content)

    def _reg_live(self, ns):
        """Live heads for `ns`, in (lamport, event_id) order, minus any superseded
        by an adjudication. Returns [(eid, lamport, value), …]."""
        heads = self._reg_heads.get(ns, {})
        sup = self._reg_superseded.get(ns, set())
        live = [(eid, lam, val) for eid, (lam, val) in heads.items() if eid not in sup]
        live.sort(key=lambda t: (t[1], t[0]))
        return live

    def _materialize_register(self, cell, ns, mc):
        """Project heads → content. LWW resolves to the (lamport, eid) winner; MV /
        adjudicated preserve every concurrent head and flag the conflict until an
        adjudication ATTEST (§4) collapses them. The losing branch stays in history."""
        live = self._reg_live(ns)
        if not live:
            return
        winner = live[-1][2]
        cell.content = winner
        if mc in _MULTIVALUE:
            cell.content_heads = [v for (_, _, v) in live]
            cell.in_conflict = len(live) > 1
        else:
            cell.content_heads = [winner]
            cell.in_conflict = False

    def _apply_register(self, ns, cell, ev, anc, content, mc):
        self._reg_push(ns, ev, anc, content)
        self._materialize_register(cell, ns, mc)

    # ----- OR-set (sets; capability grants / tags) -------------------------
    def _orset_live(self, ns):
        """Elements with ≥1 live add. A `remove` tombstones only the adds it OBSERVED
        (in its ancestors); an add concurrent with a remove is unobserved and
        survives — add-wins (MERGE_SEMANTICS §3)."""
        st = self._orset.get(ns, {"adds": {}, "removes": []})
        return sorted({
            e for aeid, e in st["adds"].items()
            if not any(e == relem and aeid in ranc for (relem, ranc) in st["removes"])
        })

    def _orset_op(self, ns, ev, anc, content):
        st = self._orset.setdefault(ns, {"adds": {}, "removes": []})
        op, elem = content.get("op"), content.get("element")
        if op == "add":
            st["adds"][ev.id] = elem
        elif op == "remove":
            st["removes"].append((elem, anc))

    def _apply_orset(self, ns, cell, ev, anc, content):
        self._orset_op(ns, ev, anc, content)
        cell.content = {"elements": self._orset_live(ns)}
        cell.content_heads = [cell.content]
        cell.in_conflict = False

    # ----- Counter (PN-counter; commutative deltas) ------------------------
    def _counter_value(self, ns):
        return sum(self._counter.get(ns, {}).values())

    def _apply_counter(self, ns, cell, ev, content):
        """Concurrent increments commute: each delta is keyed by its event id (so
        re-delivery is idempotent) and the value is their sum. No conflict possible."""
        delta = content.get("delta", content.get("value", 0))
        self._counter.setdefault(ns, {})[ev.id] = int(delta or 0)
        cell.content = {"value": self._counter_value(ns)}
        cell.content_heads = [cell.content]
        cell.in_conflict = False

    # ----- Append-log (accreted observations; never overwritten) -----------
    def _append_entries(self, ns):
        items = sorted(self._appendlog.get(ns, {}).items(), key=lambda kv: (kv[1][0], kv[0]))
        return [v for _, (_, v) in items]

    def _apply_append(self, ns, cell, ev, content):
        """Messages/observations (utterance, speech): union in (lamport, eid) order.
        Concurrency is accretion, not conflict — nothing is ever overwritten."""
        self._appendlog.setdefault(ns, {})[ev.id] = (ev.lamport, content)
        cell.content = {"entries": self._append_entries(ns)}
        cell.content_heads = [cell.content]
        cell.in_conflict = False

    # ----- Sequence CRDT (ordered text/blocks; RGA-style) ------------------
    def _seq_order(self, cid):
        """Walk the insert tree to a total order. Each element is inserted `after`
        an anchor element (None = list head); concurrent inserts after the same
        anchor are ordered by (lamport, event_id) DESCENDING — a deterministic
        tiebreak (RGA: the more recent concurrent insert sits closer to its anchor).
        Tombstoned elements keep their place in the tree (so later inserts that
        referenced them still position correctly) but are dropped from the output."""
        seq = self._seq.get(cid, {})
        children = {}
        for elem_id, r in seq.items():
            children.setdefault(r["after"], []).append(elem_id)
        for anchor in children:
            children[anchor].sort(key=lambda e: (seq[e]["lamport"], seq[e]["eid"]), reverse=True)
        out = []
        def walk(anchor):
            for elem_id in children.get(anchor, []):
                out.append((elem_id, seq[elem_id]))
                walk(elem_id)
        walk(None)
        return out

    def _apply_sequence(self, cell, ev, content):
        seq = self._seq.setdefault(cell.id, {})
        op = content.get("op", "insert")
        elem_id = content.get("elem_id") or ev.id
        if op == "delete":
            rec = seq.get(content.get("elem_id"))
            if rec is not None:
                rec["deleted"] = True
        else:  # insert — stable id; on a duplicate id the (lamport, eid) winner holds
            cand = (ev.lamport, ev.id)
            rec = seq.get(elem_id)
            if rec is None or cand > (rec["lamport"], rec["eid"]):
                seq[elem_id] = {"after": content.get("after"), "lamport": ev.lamport,
                                "eid": ev.id, "value": content.get("value"),
                                "deleted": rec["deleted"] if rec else False}
        ordered = self._seq_order(cell.id)
        cell.content = {"elements": [r["value"] for _, r in ordered if not r["deleted"]],
                        "ids": [eid for eid, r in ordered if not r["deleted"]]}
        cell.content_heads = [cell.content]
        cell.in_conflict = False

    # ----- Map CRDT (record; each key merged by its own class) -------------
    def _map_value(self, cell):
        out = {}
        for key in sorted(self._map_keys.get(cell.id, ())):
            fclass = self._field_class_of(cell.type, key)
            ns = f"{cell.id}\x00{key}"
            if fclass == MERGE_ORSET:
                out[key] = self._orset_live(ns)
            elif fclass == MERGE_COUNTER:
                out[key] = self._counter_value(ns)
            else:  # lww (and, this increment, mv fields resolve to their winner)
                live = self._reg_live(ns)
                out[key] = live[-1][2] if live else None
        return out

    def _apply_map(self, cell, ev, anc, content):
        """A structured record (agent, …): each KEY is its own register/set/counter,
        merged independently and namespaced by `cell\x00key`. The cell value is the
        per-key projection — keys never interfere (MERGE_SEMANTICS §3.1)."""
        key = content.get("key")
        if key is None:                       # whole-record assert → LWW fallback
            self._apply_register(cell.id, cell, ev, anc, content, MERGE_LWW)
            return
        self._map_keys.setdefault(cell.id, set()).add(key)
        ns = f"{cell.id}\x00{key}"
        fclass = self._field_class_of(cell.type, key)
        if fclass == MERGE_ORSET:
            self._orset_op(ns, ev, anc, content)
        elif fclass == MERGE_COUNTER:
            delta = content.get("delta", content.get("value", 0))
            self._counter.setdefault(ns, {})[ev.id] = int(delta or 0)
        else:
            self._reg_push(ns, ev, anc, content.get("value"))
        cell.content = self._map_value(cell)
        cell.content_heads = [cell.content]
        cell.in_conflict = False

    # -- projections -------------------------------------------------------
    def state_root(self) -> str:
        """A deterministic digest over the folded logical state (FOLD §6
        `state_root` — a content-addressed root over canonical CellState records).
        Two folds of the same Weft yield the same root; it is the comparable
        identity of a projection, independent of *how* it was replayed. Covers
        logical state (type, content, version, retraction, edges, attestations),
        not history (provenance/application order), so reordered-but-equivalent
        replays match."""
        records = []
        for cid in sorted(self.cells):
            c = self.cells[cid]
            records.append([
                c.id, c.type, c.content, c.version, c.retracted,
                sorted([e["rel"], e["src"], e["dst"]] for e in c.edges_out),
                sorted([a["by"], a.get("claim", "")] for a in c.attestations),
                # Concurrent heads are part of comparable state (MERGE_SEMANTICS §5):
                # `content_heads` is in deterministic (lamport, event_id) order, so a
                # preserved MV conflict folds the same regardless of arrival order.
                c.content_heads, c.in_conflict,
                # A REDACTed cell's leaf is a content-free tombstone (FOLD §10): the
                # flag is part of comparable state, the erased payload is not.
                c.redacted,
            ])
        return content_id({"state_root": records}, kind="snapshot")

    def of_type(self, t: str) -> list[Cell]:
        return [c for c in self.cells.values() if c.type == t and not c.retracted]

    def get(self, cid_or_prefix: str) -> Cell | None:
        if cid_or_prefix in self.cells:
            return self.cells[cid_or_prefix]
        matches = [c for c in self.cells.values() if c.id.startswith(cid_or_prefix)]
        return matches[0] if len(matches) == 1 else None

    def edges_from(self, cid: str, rel: str | None = None) -> list[dict]:
        """Typed relations leaving a cell (its edges_out), optionally by rel."""
        c = self.cells.get(cid)
        if not c:
            return []
        return [e for e in c.edges_out if rel is None or e["rel"] == rel]

    def edges_to(self, cid: str, rel: str | None = None) -> list[dict]:
        """Typed relations entering a cell (its edges_in), optionally by rel."""
        c = self.cells.get(cid)
        if not c:
            return []
        return [e for e in c.edges_in if rel is None or e["rel"] == rel]
