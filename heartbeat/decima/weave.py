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
        self.last_seq: int = 0

    @classmethod
    def fold(cls, weft, upto_seq: int | None = None) -> "Weave":
        w = cls()
        for ev in weft.events(upto_seq):
            w._apply(ev)
            w.last_seq = ev.seq
        return w

    def _apply(self, ev):
        b = ev.body
        if ev.verb == ASSERT:
            cid = b["cell"]
            cell = self.cells.get(cid)
            if cell is None:
                cell = Cell(id=cid, type=b.get("type", "thing"), content={})
                self.cells[cid] = cell
            cell.type = b.get("type", cell.type)
            cell.content = b.get("content", {})
            cell.version += 1
            cell.retracted = False
            cell.provenance.append(ev.id)

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
