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
# This increment implements LWW + OR-set and PRESERVES heads for MV. Sequence CRDT,
# Map CRDT, and semantic adjudication are deferred to later increments (§6).
MERGE_LWW = "lww"      # register: highest (lamport, event_id) wins; resolved
MERGE_MV = "mv"        # register: concurrent heads preserved until adjudicated
MERGE_ORSET = "or-set"  # set: add/remove by observed event identity; add-wins
_DEFAULT_MERGE = MERGE_LWW


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
        # Merge substrate (MERGE_SEMANTICS §2). Computed during the fold:
        self._ancestors: dict[str, set] = {}      # event id -> its causal ancestor ids
        self._reg_heads: dict[str, dict] = {}     # cell id -> {assert_eid: (lamport, content)}
        self._orset: dict[str, dict] = {}         # cell id -> {"adds": {eid: elem}, "removes": [(elem, anc)]}

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

            # Materialize by the Type Cell's merge class (MERGE_SEMANTICS §3): the
            # OR-set folds element ops; every other class is a register (LWW / MV).
            mc = self._merge_class_of(cell.type)
            if mc == MERGE_ORSET:
                self._apply_orset(cell, ev, anc, content)
            else:
                self._apply_register(cell, ev, anc, content, mc)

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
    def _merge_class_of(self, type_name: str) -> str:
        return self.merge_classes.get(type_name, _DEFAULT_MERGE)

    def _apply_register(self, cell, ev, anc, content, mc):
        """Register reducer (LWW / MV). Track live heads per cell: a new assertion
        DOMINATES every existing head that is its causal ancestor (its author saw
        it), leaving only heads concurrent with it. Then materialize:
          - LWW: highest (lamport, event_id) wins; the cell is RESOLVED to it (the
                 superseded branch stays in history/provenance, still inspectable).
          - MV : all concurrent heads preserved; the cell is IN CONFLICT until an
                 adjudication ATTEST collapses them (that step is a later increment).
        On a linear log every prior assertion is an ancestor, so heads collapse to
        one and this reduces to the old overwrite-by-order — by design."""
        heads = self._reg_heads.setdefault(cell.id, {})
        for h in [h for h in heads if h in anc]:
            del heads[h]                       # this assertion supersedes h
        heads[ev.id] = (ev.lamport, content)
        ordered = sorted(heads.items(), key=lambda kv: (kv[1][0], kv[0]))  # by (lamport, eid)
        winner = ordered[-1][1][1]
        cell.content = winner
        if mc == MERGE_MV:
            cell.content_heads = [it[1][1] for it in ordered]
            cell.in_conflict = len(ordered) > 1
        else:  # LWW and the default: resolved to the deterministic winner
            cell.content_heads = [winner]
            cell.in_conflict = False

    def _apply_orset(self, cell, ev, anc, content):
        """OR-set reducer (capability grants / tags). Each `add` carries an element
        and is identified by its own event id; a `remove` tombstones the element's
        adds it OBSERVED (the adds in its causal ancestors). An add concurrent with
        a remove is not observed, so it survives — add-wins. Materialized content is
        the set of elements with at least one live add.

        Modeled here as a standalone set Cell. Applying OR-set to the live
        capability-grant case (an agent's `envelope`) is a Map-CRDT *field*, which
        this increment defers (MERGE_SEMANTICS §3.1)."""
        st = self._orset.setdefault(cell.id, {"adds": {}, "removes": []})
        op, elem = content.get("op"), content.get("element")
        if op == "add":
            st["adds"][ev.id] = elem
        elif op == "remove":
            st["removes"].append((elem, anc))
        live = sorted({
            e for aeid, e in st["adds"].items()
            if not any(e == relem and aeid in ranc for (relem, ranc) in st["removes"])
        })
        cell.content = {"elements": live}
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
