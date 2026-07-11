"""The search read-model — a derived, disposable exact-text index over knowledge.

A lexical inverted index built by folding the knowledge read-model. It is a cache
in the Law-5 sense (invariant 2): DELETING the index does not touch knowledge — the
Cells stay on the Weft and ``rebuild`` reproduces a byte-identical index from the
current fold. Results carry provenance and the instruction-eligibility / trust flag,
so a RAG/UI layer treats an untrusted hit as DATA, never as an instruction
(invariant 5). Deterministic: tokenization, ranking, and tiebreaks are all fixed.

Semantic / embedding search is a noted SEAM (``semantic_rank``) — a real vector
backend wraps in behind the same ``Hit`` list without changing callers, and no
vector dependency enters this package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from decima.kernel.hashing import content_id
from decima.projections.knowledge import KnowledgeProjection

_TOKEN = re.compile(r"[a-z0-9]+")
_SNIPPET = 160


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def _snippet(text: str, width: int = _SNIPPET) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


@dataclass(frozen=True)
class Hit:
    cell: str
    type: str
    snippet: str
    score: int
    instruction_eligible: bool
    trust: str
    provenance: list[str] = field(default_factory=list)


class SearchIndex:
    """A derived inverted index over a ``KnowledgeProjection``. Disposable: it holds
    no authority and its whole contents are a pure function of the knowledge fold, so
    ``rebuild`` reproduces it and discarding it loses nothing canonical."""

    def __init__(self, knowledge: KnowledgeProjection) -> None:
        self.knowledge = knowledge
        self.postings: dict[str, set[str]] = {}
        self.records: dict[str, dict] = {}
        self.build()

    def build(self) -> None:
        self.postings = {}
        self.records = {}
        for item in self.knowledge.items():
            toks = tokens(item.text)
            if not toks:
                continue
            for t in toks:
                self.postings.setdefault(t, set()).add(item.id)
            self.records[item.id] = {
                "type": item.type,
                "tokens": toks,
                "text": item.text,
                "instruction_eligible": item.instruction_eligible,
                "trust": item.trust,
                "provenance": list(item.provenance),
            }

    def rebuild(self) -> SearchIndex:
        """Reproduce the index from the current knowledge fold (Law-5 cache
        contract): same fold ⇒ identical ``fingerprint``."""
        self.build()
        return self

    def size(self) -> int:
        return len(self.records)

    def query(self, query: str, *, limit: int = 10) -> list[Hit]:
        qtok = tokens(query)
        if not qtok:
            return []
        candidates: set[str] = set()
        for t in qtok:
            candidates |= self.postings.get(t, set())
        scored: list[tuple[tuple, str]] = []
        for cid in candidates:
            rec = self.records[cid]
            overlap = qtok & rec["tokens"]
            if not overlap:
                continue
            # Deterministic ranking: more overlap first, then text, then id.
            sort_key = (len(overlap), rec["text"], cid)
            scored.append((sort_key, cid))
        scored.sort(key=lambda item: item[0], reverse=True)
        out: list[Hit] = []
        for sort_key, cid in scored[: max(0, int(limit))]:
            rec = self.records[cid]
            out.append(Hit(
                cell=cid,
                type=rec["type"],
                snippet=_snippet(rec["text"]),
                score=int(sort_key[0]),
                instruction_eligible=rec["instruction_eligible"],
                trust=rec["trust"],
                provenance=list(rec["provenance"]),
            ))
        return out

    def fingerprint(self) -> str:
        postings = {t: sorted(ids) for t, ids in self.postings.items()}
        records = {cid: {**r, "tokens": sorted(r["tokens"])}
                   for cid, r in self.records.items()}
        return content_id({"search_index": {
            "postings": dict(sorted(postings.items())),
            "records": dict(sorted(records.items())),
        }}, kind="projection")


def semantic_rank(hits: list[Hit], query: str) -> list[Hit]:
    """SEAM for a future semantic re-rank. The stub is the identity (lexical order),
    so search stays deterministic until a vector backend is plugged in behind the
    same ``Hit`` list — no dependency enters this package."""
    return list(hits)
