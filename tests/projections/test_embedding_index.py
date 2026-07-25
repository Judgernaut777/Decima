"""qa-retrieval lane: the VECTOR half of retrieval (``projections.embedding``).

Everything proven here is deterministic and needs no model, no network and no credential:

  * INTEGER-ONLY math. Vectors are ints on a fixed-point grid, similarity is an exact
    integer cosine (``cosine_score(v, v) == SIM_SCALE`` exactly), and :func:`quantize` is
    the ONE float→int boundary — so no float can reach a score, an ordering, or (via
    ``qa_service``) any recorded content.
  * The Law-5 CACHE contract, same as ``SearchIndex``: an incremental
    ``add_item``/``remove_item`` sequence leaves a ``fingerprint`` byte-identical to a
    full ``rebuild`` over the same knowledge fold, and deleting the index touches no
    knowledge.
  * A TOTAL, STABLE re-rank: ``rank``/``rank_hits`` are permutations of their input,
    ordered by (similarity, lexical score, cell id), reproducible across calls.
  * FAIL-NEUTRAL degradation: a broken embedder raises ``EmbeddingError`` rather than
    inventing a score, which is what lets retrieval fall back to lexical.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from decima.kernel.model import assert_content
from decima.kernel.weft import RETRACT
from decima.projections.embedding import (
    SCALE,
    SIM_SCALE,
    EmbeddingError,
    EmbeddingIndex,
    HashingEmbedder,
    cosine_score,
    normalize,
    quantize,
    rank_hits,
    rank_texts,
)
from decima.projections.engine import ProjectionDriver
from decima.projections.knowledge import KnowledgeProjection
from decima.projections.search import SearchIndex, semantic_rank
from tests.projections.conftest import new_weft


def _knowledge(weft):
    driver = ProjectionDriver(weft)
    driver.register(KnowledgeProjection())
    return driver, driver.get("knowledge")


def _note(weft, author, note_id, text):
    assert_content(weft, author, note_id, "note", {"text": text, "instruction_eligible": False})


class _BrokenEmbedder:
    """An embedder whose backend is down — the offline / misconfigured case."""

    def model(self) -> str:
        return "broken/v0"

    def dimensions(self) -> int:
        return 8

    def embed(self, texts: Sequence[str]) -> list[tuple[int, ...]]:
        raise EmbeddingError("no endpoint")


class _ShortEmbedder:
    """An embedder that returns fewer rows than texts — a row mismatch cannot be aligned."""

    def model(self) -> str:
        return "short/v0"

    def dimensions(self) -> int:
        return 4

    def embed(self, texts: Sequence[str]) -> list[tuple[int, ...]]:
        items = list(texts)
        if len(items) <= 1:
            return [(1, 0, 0, 0)] * len(items)
        return [(1, 0, 0, 0)] * (len(items) - 1)


# ── integer-only vector math ───────────────────────────────────────────────────
def test_vectors_are_ints_bounded_and_deterministic():
    emb = HashingEmbedder()
    (a,) = emb.embed(["the aurora relay listens on port 7712"])
    (b,) = emb.embed(["the aurora relay listens on port 7712"])
    assert a == b  # same text ⇒ byte-identical vector, no clock, no randomness
    assert len(a) == emb.dimensions() == 256
    assert all(isinstance(v, int) for v in a)
    assert all(abs(v) <= SCALE for v in a)  # normalized onto the fixed-point grid
    assert emb.model() == "hashing-ngram-v1/d256n3"


def test_cosine_is_exact_integer_and_self_similarity_is_the_full_scale():
    emb = HashingEmbedder(dims=64)
    (v,) = emb.embed(["telemetry retention is ninety days"])
    assert cosine_score(v, v) == SIM_SCALE  # exact, not 999_9xx — integer isqrt identity
    assert isinstance(cosine_score(v, v), int)
    # fail-neutral, never raising into an ordering
    assert cosine_score(v, (1, 2, 3)) == 0
    assert cosine_score((), ()) == 0
    assert cosine_score(v, tuple(0 for _ in v)) == 0
    # an opposed vector scores the negative full scale (sign is preserved)
    assert cosine_score(v, tuple(-x for x in v)) == -SIM_SCALE


def test_related_text_scores_above_unrelated_text():
    emb = HashingEmbedder()
    query, near, far = emb.embed(
        [
            "relay port configuration",
            "configuring the relay ports",
            "quarterly budget approval workflow",
        ]
    )
    assert cosine_score(query, near) > cosine_score(query, far)


def test_quantize_is_the_only_float_boundary_and_rounds_half_up():
    assert all(isinstance(x, int) for x in quantize([0.5, -0.5, 0.0]))
    # Round-half-up (floor(x + 0.5)) — a fixed total function of the float, NOT Python's
    # bankers' rounding: exactly +0.5 of a grid step rounds away from zero, -0.5 to zero.
    half = 0.5 / SCALE
    assert quantize([half]) == (SCALE,)
    assert quantize([-half]) == (0,)
    assert quantize([]) == ()
    assert quantize([0.0, 0.0]) == (0, 0)  # a zero vector normalizes to itself
    assert normalize([3, 4]) == ((3 * SCALE) // 5, (4 * SCALE) // 5)


# ── the Law-5 cache contract: incremental == full rebuild ──────────────────────
def test_incremental_fold_matches_full_rebuild_fingerprint():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "the roadmap for the alpha release")
    _note(weft, author, "note:b", "beta feedback with a zzunique marker token")
    _note(weft, author, "note:c", "gamma channel notes about running relays")
    driver, know = _knowledge(weft)
    emb = HashingEmbedder(dims=32)

    index = EmbeddingIndex(know, emb)
    assert index.size() >= 3
    assert index.vector("note:a") is not None

    _note(weft, author, "note:d", "delta report on relay ports and running latency")
    driver.update()
    index.add_item(know.get("note:d"))  # ADD

    weft.append(author, RETRACT, {"cell": "note:b"})
    driver.update()
    index.remove_item("note:b")  # REMOVE

    _note(weft, author, "note:a", "the REVISED roadmap for the beta release")
    driver.update()
    index.add_item(know.get("note:a"))  # REINDEX in place

    incremental = index.fingerprint()
    fresh = EmbeddingIndex(know, emb).fingerprint()
    assert incremental == fresh  # byte-identical: pure function of (fold, embedder)
    assert index.vector("note:b") is None
    # rebuild() is idempotent over an unchanged fold
    assert index.rebuild().fingerprint() == fresh


def test_fingerprint_tracks_the_embedder_identity():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "the roadmap for the alpha release")
    _driver, know = _knowledge(weft)
    a = EmbeddingIndex(know, HashingEmbedder(dims=32)).fingerprint()
    b = EmbeddingIndex(know, HashingEmbedder(dims=64)).fingerprint()
    assert a != b  # a different vector space is a different cache, honestly addressed


def test_deleting_the_vector_index_loses_no_knowledge():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "the roadmap for the alpha release")
    _driver, know = _knowledge(weft)
    index = EmbeddingIndex(know, HashingEmbedder(dims=32))
    before = index.fingerprint()
    del index
    assert know.get("note:a") is not None  # the Cells are on the Weft, untouched
    assert EmbeddingIndex(know, HashingEmbedder(dims=32)).fingerprint() == before


# ── re-ranking: total, stable, and a permutation of its input ──────────────────
def test_rank_is_deterministic_total_and_a_permutation():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:near", "relay port configuration and relay tuning")
    _note(weft, author, "note:far", "relay port")
    _driver, know = _knowledge(weft)
    search = SearchIndex(know)
    hits = search.query("relay port", limit=10)
    index = EmbeddingIndex(know, HashingEmbedder(dims=128))

    ranked = index.rank("relay port relay tuning configuration", hits)
    assert [pair[0].cell for pair in ranked] == [
        pair[0].cell for pair in index.rank("relay port relay tuning configuration", hits)
    ]
    assert {pair[0].cell for pair in ranked} == {h.cell for h in hits}
    assert all(isinstance(pair[1], int) for pair in ranked)
    assert ranked[0][0].cell == "note:near"
    # the lexical Hit is returned untouched — the vector score never overwrites it
    assert all(pair[0].score > 0 for pair in ranked)


def test_score_texts_uses_the_cache_and_still_scores_unseen_ids():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "relay port configuration")
    _driver, know = _knowledge(weft)
    index = EmbeddingIndex(know, HashingEmbedder(dims=64))
    scores = index.score_texts(
        "relay port", [("note:a", "relay port configuration"), ("unseen", "relay port")]
    )
    assert set(scores) == {"note:a", "unseen"}
    assert all(isinstance(v, int) for v in scores.values())
    assert scores["unseen"] > 0
    assert index.score_texts("relay port", []) == {}


def test_rank_hits_and_rank_texts_agree_on_the_seam_shape():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:near", "relay port configuration and relay tuning")
    _note(weft, author, "note:far", "relay port")
    _driver, know = _knowledge(weft)
    hits = SearchIndex(know).query("relay port", limit=10)
    emb = HashingEmbedder(dims=128)
    ranked = rank_hits(hits, "relay port relay tuning configuration", emb)
    assert [pair[0].cell for pair in ranked][0] == "note:near"
    assert rank_hits([], "anything", emb) == []
    scored = rank_texts("relay port", [(h.cell, h.snippet) for h in hits], emb)
    assert set(scored) == {h.cell for h in hits}
    assert rank_texts("q", [], emb) == {}


def test_semantic_rank_seam_accepts_an_embedder_and_stays_a_permutation():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:near", "relay port configuration and relay tuning")
    _note(weft, author, "note:far", "relay port")
    _driver, know = _knowledge(weft)
    hits = SearchIndex(know).query("relay port", limit=10)
    emb = HashingEmbedder(dims=128)
    ranked = semantic_rank(hits, "relay port relay tuning configuration", embedder=emb)
    assert {h.cell for h in ranked} == {h.cell for h in hits}
    assert ranked == semantic_rank(
        list(hits), "relay port relay tuning configuration", embedder=emb
    )
    # the dependency-free proxy remains the default path (no embedder ⇒ unchanged)
    assert semantic_rank(hits, "relay port relay tuning configuration")[0].cell == "note:near"


# ── failure is loud, never a fabricated score ─────────────────────────────────
def test_a_broken_embedder_raises_rather_than_inventing_a_score():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "relay port configuration")
    _driver, know = _knowledge(weft)
    with pytest.raises(EmbeddingError):
        EmbeddingIndex(know, _BrokenEmbedder())
    with pytest.raises(EmbeddingError):
        rank_texts("relay port", [("note:a", "relay port")], _BrokenEmbedder())


def test_a_row_mismatch_is_refused():
    with pytest.raises(EmbeddingError):
        rank_texts("q", [("a", "one"), ("b", "two")], _ShortEmbedder())
