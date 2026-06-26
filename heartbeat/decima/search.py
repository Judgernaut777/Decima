"""Search — a derived, re-foldable lexical index over ALL Cells (B1).

`memory.recall` answers "which CLAIMS mention this text"; `knowledge` answers the
graph-shape questions. This module answers the corpus-wide one: a single full-text
index over every searchable Cell in the Weave, returning ranked hits that each
carry provenance and the trust boundary so a UI / RAG layer can treat an untrusted
hit as DATA, never as instruction.

LAWS this module obeys (Cycle 19 SEARCH1):

  * The index is a DERIVED PROJECTION — it is *always* folded from the Weave and
    never written to the signed Log (`index`/`reindex` assert NOTHING). It is a
    cache in the Law-5 sense (specs/CAPABILITY_MAP B3 "caching — derived
    projections, always re-foldable from the Weft"): the authoritative state stays
    the Weft, and `reindex()` reproduces a byte-identical index from the current
    fold. A cache you cannot reproduce would be a second source of truth, which
    Law 5 forbids.

  * Results carry PROVENANCE and a TRUST flag (the RAG / injection boundary). A
    hit is DATA: `instruction_eligible` is taken from the Cell's own permission
    (False for anything observed from an untrusted source — `memory.remember*`
    writes it so), and any Cell that does not explicitly opt in is treated as
    NOT instruction-eligible. Search results are never instructions; the flag lets
    the caller enforce that. `provenance` is the event ids that asserted the Cell
    (already in the Weft) plus its `supported_by` evidence sources.

  * DETERMINISTIC, ints not floats. Tokenization, ranking, and tiebreaks are all
    fixed and sorted, so two folds of the same Weft yield identical results.

It composes the PUBLIC APIs of `weave` (read), `memory` (permissions / recall
taxonomy), and `retrieval` (`tokens`, `text_of`, `normalized_text`) — no new
tokenizer, no vector dependency pulled into the Heartbeat. A semantic re-rank is a
noted SEAM (`semantic_rank`): swap it in behind `search` later without changing
callers, exactly as `memory.Retriever` is a seam for recall.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from decima import memory, retrieval
from decima.hashing import content_id

# The default corpus: every recall-taxonomy memory type, claims, governance, and
# the workhorse content types a search-over-all-Cells should reach. A caller can
# narrow with `types=`; `None` means "this default corpus".
DEFAULT_TYPES = (
    memory.CLAIM,
    *(_t for _t in memory.MEMORY_TYPES if _t != memory.CLAIM),
    memory.GOVERNANCE,
    "topic",
    "entity",
    "result",
    "document",
)

# Snippet width (characters of source text carried back with each hit).
SNIPPET = 160


def _searchable_text(cell) -> str:
    """The text a Cell exposes to search. Reuses memory/retrieval's notion of a
    Cell's text (`proposition`/`text`), and falls back to a governance `target` or
    an entity `name` so those Cells are reachable too."""
    return (
        retrieval.text_of(cell)
        or cell.content.get("target")
        or cell.content.get("name")
        or ""
    )


def _instruction_eligible(cell) -> bool:
    """The trust flag for a hit. A Cell is instruction-eligible ONLY if it
    explicitly opted in (`instruction_eligible=True`). Anything else — including a
    Cell that never declared the permission — is DATA: the RAG boundary fails
    closed, never open."""
    return bool(cell.content.get("instruction_eligible", False))


def _snippet(text: str, width: int = SNIPPET) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


@dataclass(frozen=True)
class Hit:
    """A ranked search result. DATA, never an instruction: read `instruction_eligible`
    before letting any hit text steer behavior."""
    cell: str                       # cell id
    type: str
    snippet: str
    score: int                      # token-overlap count (int, never a float)
    scope: str | None
    instruction_eligible: bool      # the RAG / injection trust flag
    trust: str                      # "trusted" | "untrusted" (a human-readable mirror)
    provenance: list = field(default_factory=list)   # asserting event ids
    supported_by: list = field(default_factory=list)  # evidence source cell ids


class Index:
    """A derived inverted/lexical index over the Weave. Built by folding, never on
    the Log. Holds an inverted token→cell-ids map plus a per-cell record; both are
    pure functions of the Weave it was built from, so `reindex()` reproduces it."""

    def __init__(self, weave, types: tuple[str, ...]):
        self._weave = weave
        self.types = tuple(types)
        self.postings: dict[str, set[str]] = {}   # token -> {cell id}
        self.records: dict[str, dict] = {}        # cell id -> folded record
        self._build()

    # -- construction (pure fold; asserts nothing) --------------------------
    def _build(self):
        for memory_type in self.types:
            for cell in self._weave.of_type(memory_type):
                # may-recall-as-data gate: an unrecallable Cell is not indexed.
                if not cell.content.get("recallable", True):
                    continue
                text = _searchable_text(cell)
                toks = retrieval.tokens(text)
                if not toks:
                    continue
                for t in toks:
                    self.postings.setdefault(t, set()).add(cell.id)
                self.records[cell.id] = {
                    "type": cell.type,
                    "tokens": toks,
                    "text": text,
                    "scope": cell.content.get("scope"),
                    "instruction_eligible": _instruction_eligible(cell),
                    "confidence": int(cell.content.get("confidence", 0)),
                    "provenance": list(cell.provenance),
                    "supported_by": [e["dst"] for e in
                                     self._weave.edges_from(cell.id, "supported_by")],
                }

    # -- identity (re-foldability is checkable) -----------------------------
    def fingerprint(self) -> str:
        """A deterministic digest of the index contents. Two indexes folded from
        the same Weave (same `types`) share a fingerprint — that is what makes
        `reindex()` provably reproduce the same projection (not a new authority)."""
        postings = {t: sorted(ids) for t, ids in self.postings.items()}
        records = {
            cid: {**r, "tokens": sorted(r["tokens"])}
            for cid, r in self.records.items()
        }
        return content_id({"search_index": {
            "types": list(self.types),
            "postings": dict(sorted(postings.items())),
            "records": dict(sorted(records.items())),
        }})

    def size(self) -> int:
        return len(self.records)

    # -- query --------------------------------------------------------------
    def query(self, query: str, *, scope: str | None = None,
              limit: int = 10) -> list[Hit]:
        qtok = retrieval.tokens(query)
        if not qtok:
            return []
        # Candidate cells: the union of the postings for the query tokens — an
        # inverted-index lookup, not a full scan.
        candidates: set[str] = set()
        for t in qtok:
            candidates |= self.postings.get(t, set())

        scored = []
        for cid in candidates:
            rec = self.records[cid]
            if scope is not None and rec["scope"] != scope:
                continue
            overlap = qtok & rec["tokens"]
            if not overlap:
                continue
            # Deterministic ranking key (all ints / stable strings): more overlap
            # first, then confidence, then more provenance depth, then text, then id.
            score = len(overlap)
            sort_key = (
                score,
                rec["confidence"],
                len(rec["provenance"]),
                rec["text"],
                cid,
            )
            scored.append((sort_key, cid, score, rec))
        scored.sort(key=lambda item: item[0], reverse=True)

        out = []
        for _, cid, score, rec in scored[: max(0, int(limit))]:
            eligible = rec["instruction_eligible"]
            out.append(Hit(
                cell=cid,
                type=rec["type"],
                snippet=_snippet(rec["text"]),
                score=int(score),
                scope=rec["scope"],
                instruction_eligible=eligible,
                trust="trusted" if eligible else "untrusted",
                provenance=list(rec["provenance"]),
                supported_by=list(rec["supported_by"]),
            ))
        return out

    # -- re-foldability (the Law-5 contract) --------------------------------
    def reindex(self, weave=None) -> "Index":
        """Reproduce the index from the current Weave. Always re-foldable: given the
        same Weave + `types`, the rebuilt index is byte-identical (same
        `fingerprint`). Pass a fresher `weave` to fold the index forward; omit it to
        rebuild from the same fold (the determinism / cache-validity check)."""
        return Index(weave if weave is not None else self._weave, self.types)


def _weave(k):
    """Read the Weave. `k` may already be a Weave (so the helpers can be driven
    directly in a check) or a Kernel exposing `.weave()` — the same affordance the
    rest of the Heartbeat uses."""
    return k if hasattr(k, "cells") else k.weave()


def index(k, *, types: tuple[str, ...] | None = None) -> Index:
    """Build a DERIVED lexical/inverted index over all (searchable) Cells. Folds the
    Weave and indexes it; asserts NOTHING to the Log (the index is a re-foldable
    projection, never authoritative state — CAPABILITY_MAP B3)."""
    return Index(_weave(k), tuple(types) if types is not None else DEFAULT_TYPES)


def search(k, query: str, *, types: tuple[str, ...] | None = None,
           scope: str | None = None, limit: int = 10) -> list[Hit]:
    """Search across all Cells. Returns ranked `Hit`s, each carrying the cell id, a
    snippet, provenance, and an `instruction_eligible`/`trust` flag — the RAG
    boundary: an untrusted-source hit comes back as DATA (`instruction_eligible`
    False), never as an instruction. Honors `recallable` (unrecallable Cells are
    not indexed) and an optional `scope` filter. Builds a fresh index per call;
    callers that query repeatedly should `index()` once and reuse `Index.query`."""
    return index(k, types=types).query(query, scope=scope, limit=limit)


# -- semantic re-rank SEAM ---------------------------------------------------
def semantic_rank(hits: list[Hit], query: str) -> list[Hit]:
    """A stub seam for a future semantic re-rank. A real implementation (an
    embedding model / vector store) wraps in HERE, behind the same `Hit` list, so
    no vector dependency enters the Heartbeat and callers are unchanged — exactly as
    `memory.Retriever` is the recall seam. The stub is the identity (lexical order),
    so search stays deterministic until a semantic backend is plugged in."""
    return list(hits)
