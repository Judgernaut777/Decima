"""The Weave — the materialized graph, computed by folding the Weft.

Law 5: state is a fold; everything you see is a projection. A Cell is not
stored — it is the fold of every event that touched its id. `fold(weft, seq)`
rebuilds the entire world as of any point in history: that single function IS
time-travel, undo, and reproducibility.

Law 3: everything is a Cell — notes, agents, capabilities, results, views.
"""
from dataclasses import dataclass, field

from decima.weft import ASSERT, RETRACT, INVOKE, ATTEST


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
        self.last_seq: int = 0

    @classmethod
    def fold(cls, weft, upto_seq: int | None = None) -> "Weave":
        w = cls()
        for ev in weft.events(upto_seq):
            w._apply(ev)
            w.last_seq = ev.seq
        return w

    def _ensure(self, cid: str, type: str = "thing") -> Cell:
        cell = self.cells.get(cid)
        if cell is None:
            cell = Cell(id=cid, type=type, content={})
            self.cells[cid] = cell
        return cell

    def _apply(self, ev):
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
            cell.content = b.get("content", {})
            cell.version += 1
            cell.retracted = False
            cell.provenance.append(ev.id)

            if kind == "TYPE_DEF":
                # A type is itself a Cell; index its name so adding a type is data.
                self.types[cell.content.get("name", cid)] = cid

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
                    target.version += 1
                    target.provenance.append(ev.id)

    # -- projections -------------------------------------------------------
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
