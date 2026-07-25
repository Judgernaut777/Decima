"""Source-grounded question answering — cited, horizon-scoped, model-proposed.

The workflow:

  1. RETRIEVE relevant segments through ``projections.search`` (a disposable lexical
     index over the knowledge fold), then resolve each hit's provenance from the fold
     (its ``source_document`` + ``offset``). Ranking is lexical by DEFAULT; an optional
     ``embedder`` adds a vector RE-RANK over the same lexically-gated candidates
     (``retrieve_with_mode`` reports which ranking actually ran), and its scores are
     integers that never enter signed content.
  2. HORIZON-SCOPE the results: an agent sees ONLY the projects it was explicitly
     given. A segment in a project outside the horizon is dropped BEFORE it can enter
     either the citations or the model context — private-project knowledge is never
     exposed to an unrelated agent, even when it is the single best lexical match.
  3. ANSWER via a ``decima.models`` provider. The provider PROPOSES (invariant 4): the
     retrieved segments are passed as ``context`` with ``instruction_eligible=False``
     (invariant 5 — the source text is DATA, never an instruction the model must
     obey), and the returned text is inert DATA, authorizing nothing.
  4. Return the answer WITH citations. Every citation carries the segment Cell id it
     came from, its source document, and its offset — so a citation always RESOLVES
     back to an imported source segment on the Weft.

This module mints no authority and writes nothing to the Weft: Q&A is a pure read
over the fold plus a proposal from a model.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from decima.capabilities.documents import build_index
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.models.providers import ModelRequest
from decima.projections.embedding import Embedder, EmbeddingError, EmbeddingIndex, rank_texts
from decima.projections.search import content_tokens

# The retrieval modes :func:`retrieve_with_mode` reports (recorded as run provenance).
LEXICAL = "lexical"
SEMANTIC = "semantic"
LEXICAL_FALLBACK = "lexical_fallback"

# How many lexical candidates a VECTOR re-rank considers per requested citation. A
# re-rank can only re-order segments that already passed the lexical citability gate, so
# a wider pool is the only way the semantic signal can change which evidence surfaces.
_SEMANTIC_POOL = 32


class QAError(Exception):
    """The question could not be answered (no provider / malformed horizon)."""


@dataclass(frozen=True)
class Citation:
    """A pointer that RESOLVES to an imported source segment on the Weft.

    ``score`` and ``matched_tokens`` are the deterministic RELEVANCE SIGNAL for this
    citation: the integer hybrid retrieval score and the sorted set of CONTENT tokens
    the question and the segment actually share (function words already removed). Both
    default to their empty value so an older caller that constructs a ``Citation`` by
    hand stays valid — a purely additive, backward-compatible extension. A citation is
    only ever surfaced when ``matched_tokens`` is non-empty (an exact content-token
    overlap is the citability gate; see :func:`retrieve`), so the signal is honest
    evidence, never a fabricated relevance claim.

    ``semantic_score`` is the OPTIONAL vector signal: the integer cosine similarity
    (``projections.embedding``, fixed ``0..SIM_SCALE`` grid) a configured embedder gave
    this segment, or ``0`` when retrieval was purely lexical. An INT by construction — a
    float could not be recorded and replayed faithfully (invariant 6)."""

    segment_id: str
    source_document: str
    source: str
    offset: int
    snippet: str
    score: int = 0
    matched_tokens: tuple[str, ...] = ()
    semantic_score: int = 0

    def as_dict(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "source_document": self.source_document,
            "source": self.source,
            "offset": self.offset,
            "snippet": self.snippet,
            "score": int(self.score),
            "matched_tokens": list(self.matched_tokens),
            "semantic_score": int(self.semantic_score),
        }


@dataclass(frozen=True)
class Answer:
    """A model-proposed answer plus the citations grounding it. Inert DATA — it
    authorizes nothing (invariant 4)."""

    text: str
    model: str
    citations: tuple[Citation, ...] = field(default_factory=tuple)
    grounded: bool = False

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "model": self.model,
            "grounded": self.grounded,
            "citations": [c.as_dict() for c in self.citations],
        }


def _horizon_set(horizon: str | Iterable[object] | None) -> frozenset[str] | None:
    """Normalize a horizon into a set of allowed project ids, or ``None`` (= all).

    A horizon is the EXPLICIT selection of projects an agent may see. ``None`` means
    unrestricted; an empty collection means the agent sees NOTHING (fail closed — an
    unscoped agent gets no private knowledge by accident)."""
    if horizon is None:
        return None
    if isinstance(horizon, str):
        return frozenset({horizon})
    try:
        return frozenset(str(p) for p in horizon)
    except TypeError as exc:
        raise QAError(f"horizon must be a string, an iterable of strings, or None: {exc}") from exc


def retrieve_with_mode(
    weft: Weft,
    question: str,
    *,
    horizon: str | Iterable[object] | None = None,
    limit: int = 5,
    embedder: Embedder | None = None,
    vectors: EmbeddingIndex | None = None,
) -> tuple[list[Citation], str]:
    """:func:`retrieve` plus the retrieval MODE that actually ran — ``LEXICAL``,
    ``SEMANTIC``, or ``LEXICAL_FALLBACK`` — so a caller that records the run can state
    honestly how the evidence was ranked instead of claiming a capability it lacked.

    With no ``embedder`` this is EXACTLY the deterministic lexical path (same pool, same
    order, same citations). With one, the candidate pool widens by ``_SEMANTIC_POOL`` and
    the survivors are RE-ORDERED by integer vector similarity — the citability gate below
    stays lexical, so embeddings can never promote a segment that shares no content token
    with the question into a citation. If the embedder fails (no local endpoint, malformed
    reply) the mode is ``LEXICAL_FALLBACK`` and the lexical order stands: an offline run
    always answers.

    ``vectors`` is an optional caller-owned :class:`EmbeddingIndex` consulted as a vector
    CACHE for segments it already holds (disposable, authority-free); without it each
    candidate passage is embedded inline."""
    allowed = _horizon_set(horizon)
    q_content = content_tokens(question)
    index = build_index(weft)
    weave = Weave.fold(weft)
    # Pull extra candidates so horizon filtering + de-dup still leave up to `limit`;
    # a vector re-rank pulls a far wider pool (it can only RE-ORDER — see above).
    over = _SEMANTIC_POOL if embedder is not None else 4
    hits = index.query(question, limit=max(1, int(limit)) * over)

    candidates: list[tuple[Citation, str]] = []
    seen_text: set[str] = set()
    for hit in hits:
        cell = weave.get(hit.cell)
        if cell is None or cell.retracted:
            continue
        content = cell.content or {}
        source_document = content.get("source_document")
        if not source_document:
            continue  # not a source-linked segment — never citable as evidence
        project = content.get("project")
        if allowed is not None and project not in allowed:
            continue  # HORIZON SCOPING: outside the agent's selection ⇒ invisible
        text = str(content.get("text", ""))
        matched = q_content & content_tokens(text)
        if not matched:
            continue  # CITABILITY GATE: no shared content token ⇒ not evidence
        norm = " ".join(text.split())
        if norm in seen_text:
            continue  # DE-DUP: an identical passage is already cited (best-ranked wins)
        seen_text.add(norm)
        candidates.append(
            (
                Citation(
                    segment_id=hit.cell,
                    source_document=source_document,
                    source=content.get("source") or "",
                    offset=int(content.get("offset", 0)),
                    snippet=hit.snippet,
                    score=int(hit.score),
                    matched_tokens=tuple(sorted(matched)),
                ),
                norm,
            )
        )
        if embedder is None and len(candidates) >= int(limit):
            break

    if embedder is None:
        return [citation for citation, _text in candidates], LEXICAL

    keep = max(1, int(limit))
    pairs = [(citation.segment_id, text) for citation, text in candidates]
    try:
        scores = (
            vectors.score_texts(question, pairs)
            if vectors is not None
            else rank_texts(question, pairs, embedder)
        )
    except EmbeddingError:
        # No usable vectors ⇒ keep the deterministic lexical order and SAY so.
        return [citation for citation, _text in candidates][:keep], LEXICAL_FALLBACK

    ordered = sorted(
        candidates,
        key=lambda pair: (scores.get(pair[0].segment_id, 0), pair[0].score, pair[0].segment_id),
        reverse=True,
    )
    return [
        replace(citation, semantic_score=int(scores.get(citation.segment_id, 0)))
        for citation, _text in ordered[:keep]
    ], SEMANTIC


def retrieve(
    weft: Weft,
    question: str,
    *,
    horizon: str | Iterable[object] | None = None,
    limit: int = 5,
    embedder: Embedder | None = None,
    vectors: EmbeddingIndex | None = None,
) -> list[Citation]:
    """Retrieve the top source segments for a question, HORIZON-SCOPED, with a
    per-citation relevance signal and deterministic de-duplication.

    Ranks with the search read-model, resolves provenance from the fold, and returns
    ONLY segments whose project is inside ``horizon`` (or all, when ``horizon`` is
    ``None``). A hit that is not a source-linked segment (e.g. a bare document
    metadata cell) is skipped — a citation must resolve to a segment.

    Two evidentiary guards ride on top of the search ranking, both deterministic:

      * CITABILITY GATE. A hit is kept only when the question and the segment share at
        least one CONTENT token (function words removed). This re-asserts the Wave-2
        not-citable gate at the Q&A layer, so the search read-model's degenerate
        all-stopword fallback can never leak a spurious "grounded" citation — a
        stopword-only or fuzzy-only question yields NO citation here.
      * DE-DUPLICATION. Segments whose normalized passage text is identical (e.g. the
        same document imported under two names) collapse to their single best-ranked
        occurrence. The search order is a total, stable function of the fold
        (score, then text, then id), so which occurrence survives is deterministic and
        repeated identical questions produce identical citation ordering.

    ``score`` carries the integer hybrid retrieval score and ``matched_tokens`` the
    sorted shared content tokens — the relevance signal a UI surfaces per citation.
    ``embedder`` / ``vectors`` are the OPTIONAL vector path (see
    :func:`retrieve_with_mode`, which this delegates to); with neither, retrieval is the
    deterministic lexical ranking it has always been."""
    citations, _mode = retrieve_with_mode(
        weft,
        question,
        horizon=horizon,
        limit=limit,
        embedder=embedder,
        vectors=vectors,
    )
    return citations


def grounding_context(weft: Weft, citations: list[Citation] | tuple[Citation, ...]) -> str:
    """The retrieved segments' text, joined as one UNTRUSTED context block.

    Pure read over the fold. The result is DATA for a model request's ``context``
    (``instruction_eligible=False`` — invariant 5); it never becomes a prompt."""
    weave = Weave.fold(weft)
    parts: list[str] = []
    for cite in citations:
        cell = weave.get(cite.segment_id)
        if cell is not None and not cell.retracted:
            parts.append(str(cell.content.get("text", "")))
    return "\n\n".join(parts)


def grounding_request(
    weft: Weft,
    question: str,
    citations: list[Citation] | tuple[Citation, ...],
    *,
    prompt: str | None = None,
    max_output_tokens: int = 512,
) -> ModelRequest:
    """Build the ``ModelRequest`` for a grounded answer over ``citations``.

    ``prompt`` is the caller's TRUSTED framing (defaults to the bare question);
    the retrieved source text rides in ``context`` with ``instruction_eligible=False``
    (invariant 5 — a source that says "ignore all instructions" is quoted data, never
    an instruction). ``context_tokens`` is the honest deterministic estimate, so a
    routing layer can size the task truthfully. Pure; asserts nothing."""
    from decima.models.providers import estimate_tokens

    context = grounding_context(weft, citations)
    return ModelRequest(
        prompt=prompt if prompt is not None else question,
        purpose="qa",
        context=context,
        context_tokens=estimate_tokens(context),
        instruction_eligible=False,  # retrieved source text is DATA, never instruction
        max_output_tokens=int(max_output_tokens),
    )


def answer_question(
    weft: Weft,
    question: str,
    *,
    provider: object,
    horizon: str | Iterable[object] | None = None,
    limit: int = 5,
    max_output_tokens: int = 512,
) -> Answer:
    """Answer a question from imported sources, with resolving citations.

    Retrieval is horizon-scoped (``retrieve``); the model is a PROPOSAL engine
    (invariant 4) handed the retrieved segments as ``instruction_eligible=False``
    context (invariant 5). The returned ``Answer`` carries citations that each resolve
    to an imported segment Cell. When retrieval finds nothing inside the horizon the
    answer is UNGROUNDED (no citations) and says so — it never fabricates a source."""
    if provider is None or not hasattr(provider, "complete"):
        raise QAError("a model provider with .complete() is required (models PROPOSE)")
    if not isinstance(question, str) or not question.strip():
        raise QAError("question must be a non-empty string")

    citations = retrieve(weft, question, horizon=horizon, limit=limit)
    if not citations:
        return Answer(
            text="No source in the current horizon supports an answer.",
            model=getattr(getattr(provider, "capabilities", lambda: None)(), "model", "unknown"),
            citations=(),
            grounded=False,
        )

    request = grounding_request(weft, question, citations, max_output_tokens=max_output_tokens)
    response = provider.complete(request)

    return Answer(
        text=response.text,
        model=response.model,
        citations=tuple(citations),
        grounded=True,
    )
