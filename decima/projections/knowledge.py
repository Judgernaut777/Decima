"""The knowledge read-model — a disposable notes/documents/links/provenance view.

Reads the corpus Cells (notes, documents, claims, typed memories, entities) and
their typed EDGE relations from the fold, presenting each knowledge item with its
text, its outgoing links, and its provenance (the event ids that asserted it —
already on the signed Weft). A RETRACTED note stops appearing (of_type yields only
live cells). It asserts nothing and is rebuildable from the Weft; deterministic
(sorted by id, links sorted).

Trust boundary: an item carries its own ``instruction_eligible`` permission
(default False). Knowledge is DATA — a reader must not treat an untrusted item's
text as an instruction (invariant 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from decima.projections.engine import BaseProjection

NOTE = "note"
DOCUMENT = "document"

# The default knowledge corpus: notes/documents plus the memory taxonomy and claims.
KNOWLEDGE_TYPES = (
    NOTE,
    DOCUMENT,
    "claim",
    "semantic",
    "episodic",
    "procedural",
    "decision",
    "failure",
)


@dataclass(frozen=True)
class KnowledgeItem:
    id: str
    type: str
    text: str
    instruction_eligible: bool
    trust: str
    links: tuple[dict, ...] = field(default_factory=tuple)
    provenance: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "text": self.text,
            "instruction_eligible": self.instruction_eligible,
            "trust": self.trust,
            "links": [dict(link) for link in self.links],
            "provenance": list(self.provenance),
        }


def _text_of(cell: object) -> str:
    c = cell.content if isinstance(cell.content, dict) else {}
    for key in ("text", "proposition", "body", "title", "name"):
        v = c.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


class KnowledgeProjection(BaseProjection):
    name = "knowledge"
    version = 1

    def __init__(self, types: tuple[str, ...] = KNOWLEDGE_TYPES) -> None:
        super().__init__()
        self.types = tuple(types)

    def items(self) -> list[KnowledgeItem]:
        out: list[KnowledgeItem] = []
        for type_ in self.types:
            for c in self.fold.of_type(type_):
                eligible = bool(c.content.get("instruction_eligible", False))
                links = tuple(
                    sorted(
                        ({"rel": e["rel"], "dst": e["dst"]} for e in c.edges_out),
                        key=lambda link: (link["rel"] or "", link["dst"] or ""),
                    )
                )
                out.append(
                    KnowledgeItem(
                        id=c.id,
                        type=c.type,
                        text=_text_of(c),
                        instruction_eligible=eligible,
                        trust="trusted" if eligible else "untrusted",
                        links=links,
                        provenance=tuple(c.provenance),
                    )
                )
        return sorted(out, key=lambda k: k.id)

    def notes(self) -> list[KnowledgeItem]:
        return [k for k in self.items() if k.type == NOTE]

    def documents(self) -> list[KnowledgeItem]:
        return [k for k in self.items() if k.type == DOCUMENT]

    def get(self, item_id: str) -> KnowledgeItem | None:
        for k in self.items():
            if k.id == item_id:
                return k
        return None

    def view(self) -> object:
        return [k.as_dict() for k in self.items()]
