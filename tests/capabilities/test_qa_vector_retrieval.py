"""qa lane: the OPTIONAL vector re-rank inside ``capabilities.qa.retrieve``.

Load-bearing properties, all proven with the local deterministic embedder (no model, no
network, no credential — an offline run exercises the real code path):

  * LEXICAL IS THE DEFAULT AND THE FALLBACK. With no embedder the mode is ``lexical`` and
    the citations are exactly what retrieval always produced. With a BROKEN embedder the
    mode is ``lexical_fallback`` and the citations are the same lexical ones — a missing
    local endpoint can never fail a question.
  * THE CITABILITY GATE STAYS LEXICAL. A vector score can only RE-ORDER segments that
    already share a content token with the question; it can never manufacture evidence,
    and a stopword-only question still earns nothing.
  * HORIZON SCOPING IS UNAFFECTED. An out-of-scope document is invisible to the vector
    path too, even when it is the closest match in the vector space.
  * DETERMINISM. Repeated identical questions produce byte-identical citations, every
    score is an ``int``, and no float appears anywhere in a citation's recorded shape.
"""

from __future__ import annotations

from collections.abc import Sequence

from decima.capabilities import qa
from decima.capabilities.documents import import_document, knowledge_projection
from decima.projections.embedding import EmbeddingError, EmbeddingIndex, HashingEmbedder

PORT_DOC = "The Aurora relay listens on port 7712 for telemetry traffic."
RETENTION_DOC = "The Aurora relay keeps telemetry logs for ninety days of retention."
SECRET_DOC = "The Vega treasury relay key rotates on the first Monday of each quarter."
QUESTION = "What port does the Aurora relay listen on for telemetry?"


class _BrokenEmbedder:
    """A configured embedder whose endpoint is unreachable."""

    def model(self) -> str:
        return "broken/v0"

    def dimensions(self) -> int:
        return 8

    def embed(self, texts: Sequence[str]) -> list[tuple[int, ...]]:
        raise EmbeddingError("no endpoint")


def _seed(weft, author) -> None:
    for name, body in (
        ("aurora-port.md", PORT_DOC),
        ("aurora-retention.md", RETENTION_DOC),
        ("vega-secret.md", SECRET_DOC),
    ):
        import_document(weft, author, source=name, data=body.encode("utf-8"), project=name)


def test_default_retrieval_is_lexical_and_byte_identical(weft, author):
    _seed(weft, author)
    lexical, mode = qa.retrieve_with_mode(weft, QUESTION, limit=3)
    assert mode == qa.LEXICAL
    assert lexical
    assert [c.as_dict() for c in lexical] == [
        c.as_dict() for c in qa.retrieve(weft, QUESTION, limit=3)
    ]
    assert all(c.semantic_score == 0 for c in lexical)  # no vector signal was claimed


def test_vector_rerank_keeps_the_lexical_citability_gate(weft, author):
    _seed(weft, author)
    emb = HashingEmbedder()
    lexical, _ = qa.retrieve_with_mode(weft, QUESTION, limit=3)
    semantic, mode = qa.retrieve_with_mode(weft, QUESTION, limit=3, embedder=emb)
    assert mode == qa.SEMANTIC
    assert semantic
    # every semantically-ranked citation was ALREADY lexically citable: same candidate set
    assert {c.segment_id for c in semantic} <= {c.segment_id for c in lexical} | {
        c.segment_id for c in qa.retrieve_with_mode(weft, QUESTION, limit=32)[0]
    }
    for cit in semantic:
        assert cit.matched_tokens  # the exact content-token overlap gate still decides
        assert isinstance(cit.score, int) and cit.score > 0
        assert isinstance(cit.semantic_score, int)
        assert isinstance(cit.as_dict()["semantic_score"], int)


def test_vector_rerank_is_deterministic_across_calls(weft, author):
    _seed(weft, author)
    emb = HashingEmbedder()
    first = qa.retrieve(weft, QUESTION, limit=3, embedder=emb)
    second = qa.retrieve(weft, QUESTION, limit=3, embedder=emb)
    assert [c.as_dict() for c in first] == [c.as_dict() for c in second]


def test_a_prebuilt_vector_index_is_a_cache_not_a_different_answer(weft, author):
    _seed(weft, author)
    emb = HashingEmbedder()
    index = EmbeddingIndex(knowledge_projection(weft), emb)
    inline = qa.retrieve(weft, QUESTION, limit=3, embedder=emb)
    cached = qa.retrieve(weft, QUESTION, limit=3, embedder=emb, vectors=index)
    assert [c.as_dict() for c in cached] == [c.as_dict() for c in inline]


def test_a_broken_embedder_degrades_to_the_lexical_order_and_says_so(weft, author):
    _seed(weft, author)
    lexical, _ = qa.retrieve_with_mode(weft, QUESTION, limit=3)
    degraded, mode = qa.retrieve_with_mode(weft, QUESTION, limit=3, embedder=_BrokenEmbedder())
    assert mode == qa.LEXICAL_FALLBACK
    assert [c.as_dict() for c in degraded] == [c.as_dict() for c in lexical]


def test_horizon_scoping_still_bounds_the_vector_path(weft, author):
    _seed(weft, author)
    emb = HashingEmbedder()
    scoped = qa.retrieve(
        weft, "relay key rotation", horizon={"aurora-port.md"}, limit=5, embedder=emb
    )
    assert all(c.source_document for c in scoped)
    assert {c.source for c in scoped} <= {"aurora-port.md"}
    # an empty horizon fails CLOSED even with a vector embedder configured
    assert qa.retrieve(weft, QUESTION, horizon=[], limit=5, embedder=emb) == []


def test_a_stopword_only_question_earns_nothing_from_the_vector_path(weft, author):
    _seed(weft, author)
    assert qa.retrieve(weft, "what is the of and to", limit=3, embedder=HashingEmbedder()) == []
