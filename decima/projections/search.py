"""The search read-model — a derived, disposable hybrid-lexical index over knowledge.

A lexical inverted index built by folding the knowledge read-model. It is a cache
in the Law-5 sense (invariant 2): DELETING the index does not touch knowledge — the
Cells stay on the Weft and ``rebuild`` reproduces a byte-identical index from the
current fold. Results carry provenance and the instruction-eligibility / trust flag,
so a RAG/UI layer treats an untrusted hit as DATA, never as an instruction
(invariant 5). Deterministic: tokenization, ranking, and tiebreaks are all fixed.

Ranking is HYBRID but stays pure-stdlib and integer-only (no numpy/vector/embedding
dependency — that would break the local-first, deterministic invariant): the exact
content-token overlap GATE decides citability (a segment sharing only stopwords with
the query is never evidence), and the survivors are then ordered by an IDF-style
weight (rarer corpus tokens weigh more), a bounded char-n-gram fuzzy bonus (so
"running" rewards "run"/"runs" as a SECONDARY signal), and a phrase/proximity bonus.
The bonuses are bounded BELOW a single exact overlap token, so no near-match can ever
promote a stopword-only overlap into a citation.

The index also folds INCREMENTALLY: ``add_item`` / ``remove_item`` mutate the inverted
index in place, and after any sequence of them ``fingerprint`` is byte-identical to a
full ``rebuild`` from the same knowledge fold (invariant 2 — the projection is a pure
function of the Weft, however it was folded).

Semantic / embedding search is a noted SEAM (``semantic_rank``): the stub here is a
REAL deterministic dependency-free proxy (char-n-gram similarity, NOT embeddings). A
true vector backend wraps in behind the same ``Hit`` list without changing callers,
and no vector dependency enters this package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from decima.kernel.hashing import content_id
from decima.projections.knowledge import KnowledgeProjection

_TOKEN = re.compile(r"[a-z0-9]+")
_SNIPPET = 160

# Ranking scale (all integers — invariant 6, no floats enter the score). The PRIMARY
# IDF-weighted overlap is multiplied by ``_PRIMARY`` and the two bonuses are capped so
# their sum (``_PROX_CAP * _PROX`` + ``_FUZZY_CAP`` = 85) stays strictly below one
# minimum-weight exact overlap token (``_PRIMARY`` × min IDF 1 = 100). Consequence:
# one more exact content-token match always outranks ANY accumulation of near-match /
# proximity bonus, so the fuzzy signal can never turn a weaker overlap — least of all a
# stopword-only overlap, which the gate already rejects — into the top citation.
_PRIMARY = 100
_PROX = 5
_PROX_CAP = 9
_FUZZY_CAP = 40
_NGRAM = 3

# High-frequency function words carry no evidentiary signal. A query that overlaps a
# segment ONLY on these must not qualify the segment as citable evidence (else an
# unrelated question earns a spurious "grounded" citation on shared "the/of/is"). The
# set is small, fixed, and lower-cased — determinism preserved.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "its",
        "made",
        "of",
        "on",
        "or",
        "that",
        "the",
        "then",
        "there",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    }
)


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def ordered_tokens(text: str) -> list[str]:
    """Tokens in document order — needed by the phrase/proximity signal (``tokens``
    loses order). Deterministic: a fixed regex over lower-cased text."""
    return _TOKEN.findall((text or "").lower())


def content_tokens(text: str) -> set[str]:
    """Query/segment tokens with function words removed — the evidentiary signal."""
    return tokens(text) - _STOPWORDS


def _ngrams(word: str, n: int = _NGRAM) -> frozenset[str]:
    """Character n-grams of a token (a token shorter than ``n`` is its own single
    gram). The fuzzy signal compares these — a pure-stdlib stand-in for stemming."""
    if len(word) < n:
        return frozenset((word,))
    return frozenset(word[i : i + n] for i in range(len(word) - n + 1))


def _fuzzy_match(token: str, others: set[str]) -> bool:
    """True when ``token`` is a char-n-gram near-match of any token in ``others``.

    Uses the OVERLAP COEFFICIENT (shared grams / smaller gram set) at threshold ½, so
    a stem and its inflections match despite the length gap ("run" ⊂ "running") while
    unrelated tokens do not. Integer comparison only — ``shared * 2 >= min_size``."""
    tg = _ngrams(token)
    for other in others:
        og = _ngrams(other)
        shared = len(tg & og)
        if shared and shared * 2 >= min(len(tg), len(og)):
            return True
    return False


def _phrase_hits(q_ordered: list[str], seg_ordered: list[str]) -> int:
    """How many adjacent query-token pairs survive as adjacent pairs in the segment —
    a deterministic proximity/phrase signal (contiguous query phrasing scores)."""
    if len(q_ordered) < 2 or len(seg_ordered) < 2:
        return 0
    q_bigrams = {(q_ordered[i], q_ordered[i + 1]) for i in range(len(q_ordered) - 1)}
    seg_bigrams = {(seg_ordered[i], seg_ordered[i + 1]) for i in range(len(seg_ordered) - 1)}
    return len(q_bigrams & seg_bigrams)


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
            self._index_item(item)

    def _index_item(self, item: object) -> None:
        """Fold ONE knowledge item into the inverted index. Assumes the item is not
        already indexed (``build`` walks a fresh fold; ``add_item`` unindexes first).
        An item with no tokens contributes nothing — exactly as ``build`` skips it."""
        toks = tokens(item.text)
        if not toks:
            return
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

    def _unindex(self, item_id: str) -> None:
        """Remove one item's postings, deleting any posting set it empties. ``build``
        never leaves an empty posting, so the cleanup is what keeps an incremental
        fold byte-identical to a full rebuild's ``fingerprint``."""
        rec = self.records.pop(item_id, None)
        if rec is None:
            return
        for t in rec["tokens"]:
            posting = self.postings.get(t)
            if posting is None:
                continue
            posting.discard(item_id)
            if not posting:
                del self.postings[t]

    def add_item(self, item: object) -> None:
        """Incrementally index (or REINDEX) a single knowledge item without a full
        rebuild. Idempotent-by-replacement: an already-present id is unindexed first,
        so re-asserting an item with new text lands the new tokens and drops the old —
        leaving the index identical to a rebuild that folded the same current item."""
        self._unindex(item.id)
        self._index_item(item)

    def remove_item(self, item_id: str) -> None:
        """Incrementally drop a single knowledge item (e.g. a retracted note) from the
        index without a full rebuild — the mirror of ``add_item``."""
        self._unindex(item_id)

    def rebuild(self) -> SearchIndex:
        """Reproduce the index from the current knowledge fold (Law-5 cache
        contract): same fold ⇒ identical ``fingerprint``."""
        self.build()
        return self

    def size(self) -> int:
        return len(self.records)

    def _idf(self, token: str) -> int:
        """IDF-style weight of a corpus token — an integer log2 proxy of N/df, NOT an
        embedding: a token in every record weighs 1, a rarer token weighs more. Pure
        ints (no float enters the score); computed from live postings so it never
        needs its own state kept consistent across incremental folds."""
        n = max(1, len(self.records))
        df = max(1, len(self.postings.get(token, ())))
        return max(1, (n // df)).bit_length()

    def _score(self, qtok: set[str], q_ordered: list[str], overlap: set[str], rec: dict) -> int:
        """Hybrid integer score for a survivor of the overlap gate. PRIMARY is the
        IDF-weighted exact overlap (dominant, ``_PRIMARY``-scaled); PROXIMITY rewards
        query phrasing kept contiguous in the segment; FUZZY adds a bounded char-n-gram
        bonus for query tokens with no exact hit. Bonuses are capped below one exact
        token (see ``_PRIMARY``), so they only re-order equally-grounded survivors."""
        primary = sum(self._idf(t) for t in overlap) * _PRIMARY
        seg_ordered = ordered_tokens(rec["text"])
        proximity = min(_phrase_hits(q_ordered, seg_ordered), _PROX_CAP) * _PROX
        seg_content = rec["tokens"] - _STOPWORDS
        fuzzy = sum(1 for t in qtok - overlap if _fuzzy_match(t, seg_content))
        return primary + proximity + min(fuzzy, _FUZZY_CAP)

    def query(self, query: str, *, limit: int = 10) -> list[Hit]:
        # Match on CONTENT tokens: a segment sharing only stopwords with the question
        # is not evidence. A query that is ALL stopwords falls back to its raw tokens so
        # it can still match (degenerate, but never silently returns nothing citable for
        # a legitimately stopword-only phrase). ``q_ordered`` keeps document order for
        # the phrase signal; ``qtok`` is the same tokens as a set for the gate.
        q_ordered = [t for t in ordered_tokens(query) if t not in _STOPWORDS]
        if not q_ordered:
            q_ordered = ordered_tokens(query)
        qtok = set(q_ordered)
        if not qtok:
            return []
        candidates: set[str] = set()
        for t in qtok:
            candidates |= self.postings.get(t, set())
        scored: list[tuple[tuple, int, str]] = []
        for cid in candidates:
            rec = self.records[cid]
            overlap = qtok & rec["tokens"]
            if not overlap:
                continue  # EXACT content overlap is the citability gate (kept from 0.3)
            score = self._score(qtok, q_ordered, overlap, rec)
            # Deterministic ranking: hybrid score first, then text, then id.
            sort_key = (score, rec["text"], cid)
            scored.append((sort_key, score, cid))
        scored.sort(key=lambda item: item[0], reverse=True)
        out: list[Hit] = []
        for _sort_key, score, cid in scored[: max(0, int(limit))]:
            rec = self.records[cid]
            out.append(
                Hit(
                    cell=cid,
                    type=rec["type"],
                    snippet=_snippet(rec["text"]),
                    score=int(score),
                    instruction_eligible=rec["instruction_eligible"],
                    trust=rec["trust"],
                    provenance=list(rec["provenance"]),
                )
            )
        return out

    def fingerprint(self) -> str:
        postings = {t: sorted(ids) for t, ids in self.postings.items()}
        records = {cid: {**r, "tokens": sorted(r["tokens"])} for cid, r in self.records.items()}
        return content_id(
            {
                "search_index": {
                    "postings": dict(sorted(postings.items())),
                    "records": dict(sorted(records.items())),
                }
            },
            kind="projection",
        )


def _text_ngrams(text: str, n: int = _NGRAM) -> set[str]:
    """Union of the char-n-grams of every token in ``text`` — the fingerprint of a
    string for the semantic proxy below."""
    grams: set[str] = set()
    for t in ordered_tokens(text):
        grams |= _ngrams(t, n)
    return grams


def semantic_rank(hits: list[Hit], query: str) -> list[Hit]:
    """SEAM for a semantic re-rank — a REAL deterministic implementation, but a
    dependency-free PROXY, NOT embeddings (no vector library enters this package). It
    re-scores each hit by char-n-gram similarity (integer Jaccard on a fixed 0..10000
    scale) between the query and the hit's snippet — a cheap lexical stand-in for
    semantic closeness — and breaks ties by the hit's existing lexical score then cell
    id, a TOTAL and STABLE order. A true vector backend re-ranks the SAME ``Hit`` list
    here later, behind this signature, without touching callers or adding a dependency."""
    qg = _text_ngrams(query)

    def key(h: Hit) -> tuple[int, int, str]:
        sg = _text_ngrams(h.snippet)
        shared = len(qg & sg)
        union = len(qg | sg) or 1
        sim = (shared * 10000) // union  # integer Jaccard — no float in the ordering
        return (sim, h.score, h.cell)

    return sorted(hits, key=key, reverse=True)
