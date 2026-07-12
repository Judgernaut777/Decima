"""The activity read-model — a disposable human-readable timeline over the Weft.

An ordered feed of "what happened": one entry per event in seq order — who did
what to which cell — covering asserts, retracts, invokes, attests, receipts, and
status transitions (a transition is just a re-asserted status version). It is
append-only per event, so an incremental update and a full rebuild yield the
identical feed. It asserts nothing and is rebuildable from the Weft.
"""

from __future__ import annotations

from dataclasses import dataclass

from decima.projections.engine import BaseProjection
from decima.runtime.cells import RECEIPT

_VERB_WORD = {
    "ASSERT": "asserted",
    "RETRACT": "retracted",
    "INVOKE": "invoked",
    "ATTEST": "attested",
}


@dataclass(frozen=True)
class ActivityEntry:
    seq: int | None
    author: str
    verb: str
    verb_word: str
    description: str
    cell: str | None
    cell_type: str | None
    authorized_by: str | None
    provenance: str

    def as_dict(self) -> dict:
        return {
            "seq": self.seq,
            "author": self.author,
            "verb": self.verb,
            "verb_word": self.verb_word,
            "description": self.description,
            "cell": self.cell,
            "cell_type": self.cell_type,
            "authorized_by": self.authorized_by,
            "provenance": self.provenance,
        }


def _touched_cell(body: dict) -> str | None:
    for key in ("cell", "target_cell", "cap", "src"):
        v = body.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _describe(verb: str, cell_type: str | None) -> str:
    if verb == "INVOKE":
        return "invoked an effect through a capability"
    thing = cell_type or "cell"
    if verb == "ATTEST":
        return f"attested a {thing}"
    if verb == "RETRACT":
        return f"retracted a {thing}"
    if cell_type == RECEIPT:
        return "recorded a receipt"
    return f"asserted a {thing}"


class ActivityProjection(BaseProjection):
    name = "activity"
    version = 1

    def reset(self) -> None:
        super().reset()
        self.entries: list[ActivityEntry] = []

    def apply(self, event: object) -> None:
        super().apply(event)  # fold first, so cell types are known
        body = event.body if isinstance(event.body, dict) else {}
        cid = _touched_cell(body)
        ctype = self.fold.type_of(cid)
        self.entries.append(
            ActivityEntry(
                seq=getattr(event, "seq", None),
                author=event.author,
                verb=event.verb,
                verb_word=_VERB_WORD.get(event.verb, event.verb.lower()),
                description=_describe(event.verb, ctype),
                cell=cid,
                cell_type=ctype,
                authorized_by=getattr(event, "authorized", None),
                provenance=event.id,
            )
        )

    def timeline(
        self, *, last: int | None = None, principal: str | None = None, cell_type: str | None = None
    ) -> list[ActivityEntry]:
        out = [
            e
            for e in self.entries
            if (principal is None or e.author == principal)
            and (cell_type is None or e.cell_type == cell_type)
        ]
        if last is not None:
            out = out[-last:]
        return out

    def digest(self, **filters: object) -> dict:
        by_verb: dict[str, int] = {}
        by_cell_type: dict[str, int] = {}
        for e in self.timeline(**filters):  # type: ignore[arg-type]
            by_verb[e.verb_word] = by_verb.get(e.verb_word, 0) + 1
            bucket = e.cell_type or ("effect" if e.verb == "INVOKE" else "—")
            by_cell_type[bucket] = by_cell_type.get(bucket, 0) + 1
        return {"by_verb": by_verb, "by_cell_type": by_cell_type}

    def view(self) -> object:
        return [e.as_dict() for e in self.entries]
