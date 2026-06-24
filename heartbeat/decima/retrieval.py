"""Retrieval engine for Decima memory.

This module plugs in behind `memory.Retriever`. It deliberately stays lexical:
token overlap, duplicate collapse, explicit supersession links, and explicit
contradiction links. No vector store or model call belongs in the Heartbeat.
"""
from dataclasses import dataclass
import re

from decima import memory
from decima.hashing import nfc


STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is", "of",
    "on", "or", "the", "to", "with",
}


@dataclass(frozen=True)
class Contradiction:
    left: str
    right: str
    relation: str


def text_of(cell) -> str:
    return cell.content.get("proposition") or cell.content.get("text") or ""


def tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", nfc(text).lower())
        if t and t not in STOPWORDS
    }


def normalized_text(cell) -> str:
    return " ".join(sorted(tokens(text_of(cell))))


def _ids(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return {str(v) for v in value}


def _candidate_cells(weave, memory_types: tuple[str, ...] | None):
    for memory_type in memory_types or (memory.CLAIM,):
        for cell in weave.of_type(memory_type):
            yield cell


class LexicalRetriever(memory.Retriever):
    """Lexical retriever that beats substring without adding a vector dependency.

    Query matching uses token overlap, so "budget owner" can retrieve "Alice owns
    the budget" even though the phrase does not appear as a substring. It also
    collapses duplicate text and suppresses explicitly superseded Cells by
    default, while keeping contradiction links inspectable.
    """

    def __init__(self, include_superseded: bool = False, include_duplicates: bool = False,
                 heat_weight: int = 1, recency_weight: int = 1):
        self.include_superseded = include_superseded
        self.include_duplicates = include_duplicates
        self.heat_weight = int(heat_weight)
        self.recency_weight = int(recency_weight)

    def search(self, weave, query: str, scope: str | None = None,
               memory_types: tuple[str, ...] | None = None) -> list:
        query_tokens = tokens(query)
        if not query_tokens:
            return []
        candidates = [
            c for c in _candidate_cells(weave, memory_types)
            if c.content.get("recallable", True)
            and (scope is None or c.content.get("scope") == scope)
        ]
        superseded = self.superseded_ids(candidates)
        scored = []
        for cell in candidates:
            cell_tokens = tokens(text_of(cell))
            overlap = query_tokens & cell_tokens
            if not overlap:
                continue
            if not self.include_superseded and cell.id in superseded:
                continue
            heat_score = memory.heat(weave, cell.id)
            recency_score = memory.recency(weave, cell)
            score = (
                len(overlap),
                recency_score * self.recency_weight + heat_score * self.heat_weight,
                heat_score,
                recency_score,
                cell.content.get("confidence", 0),
                len(cell.provenance),
                text_of(cell),
                cell.id,
            )
            scored.append((score, cell))
        scored.sort(key=lambda item: item[0], reverse=True)
        cells = [cell for _, cell in scored]
        if not self.include_duplicates:
            cells = self.dedupe(cells)
        return cells

    def superseded_ids(self, cells: list) -> set[str]:
        out = set()
        for cell in cells:
            out.update(_ids(cell.content.get("supersedes")))
        return out

    def dedupe(self, cells: list) -> list:
        seen = set()
        out = []
        for cell in cells:
            key = (cell.type, cell.content.get("scope"), normalized_text(cell))
            if key in seen:
                continue
            seen.add(key)
            out.append(cell)
        return out

    def contradictions(self, cells: list) -> list[Contradiction]:
        ids = {c.id for c in cells}
        out = []
        for cell in cells:
            for other in _ids(cell.content.get("contradicts")):
                if other in ids:
                    out.append(Contradiction(cell.id, other, "contradicts"))
            for other in _ids(cell.content.get("contradicted_by")):
                if other in ids:
                    out.append(Contradiction(cell.id, other, "contradicted_by"))
        return out
