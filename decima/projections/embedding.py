"""Vector retrieval — a DISPOSABLE embedding projection with INTEGER-ONLY scores.

This is the vector half of retrieval, and it is deliberately built so that NO float and
NO nondeterministic model output can ever reach signed content:

  * An :class:`Embedder` returns vectors of **ints** (fixed-point, ``SCALE``), never
    floats. The single float→int boundary in the whole system is :func:`quantize`, which
    a real model-backed embedder calls on the transport's reply the moment it arrives —
    everything downstream (similarity, ranking, fingerprints) is integer arithmetic.
  * Similarity is :func:`cosine_score`: an exact integer cosine on a fixed
    ``0..SIM_SCALE`` scale using :func:`math.isqrt` (exact integer square root), so the
    same vectors give the same score on every host — no float rounding in an ordering.
  * :class:`EmbeddingIndex` is a CACHE in the Law-5 sense, exactly like
    ``projections.search``: it holds no authority, deleting it touches no knowledge, and
    ``rebuild`` reproduces a byte-identical ``fingerprint`` from the same knowledge fold
    (given the same embedder). Vectors are NEVER asserted onto the Weft.
  * :class:`HashingEmbedder` is a REAL, local, dependency-free, fully deterministic
    embedder (the signed hashing trick over tokens + char n-grams). It needs no model, no
    network and no credential, so an offline run gets genuine vector retrieval with the
    project's determinism intact. A model-backed embedder (loopback-confined, see
    ``services.api.models_setup``) slots in behind the same protocol.

Ordering contract: every ranking here breaks ties by the caller's existing integer
lexical score and then the cell id, so the order is TOTAL and STABLE — a re-rank is a
permutation of its input, never a source of nondeterminism.

An embedder that cannot produce vectors raises :class:`EmbeddingError`; callers are
expected to degrade to the deterministic lexical path rather than fail the query.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable

from decima.kernel.hashing import content_id
from decima.projections.knowledge import KnowledgeItem, KnowledgeProjection
from decima.projections.search import ordered_tokens

# Fixed-point scale of a stored vector component, and the scale of a similarity score.
# Both are powers/round numbers chosen once: changing them changes every fingerprint, so
# they are part of the (disposable) index identity, never of any signed content.
SCALE = 1 << 14
SIM_SCALE = 1_000_000

HASHING_MODEL = "hashing-ngram-v1"
HASHING_DIMENSIONS = 256
_HASHING_NGRAM = 3
# Personalization string: keeps this feature hash in its own domain, so it can never be
# confused with (or collide meaningfully against) the kernel's content-address hashes.
_PERSON = b"decima-emb"


class EmbeddingError(RuntimeError):
    """An embedder could not produce vectors (transport failure, malformed reply, or a
    row/dimension mismatch). Retrieval catches this and falls back to the deterministic
    lexical path — a missing model never fails a query and never fabricates a score."""


@runtime_checkable
class Embedder(Protocol):
    """A source of INTEGER vectors. ``embed`` returns one vector per input text, in
    input order; ``model`` is a stable tag recorded as provenance; ``dimensions`` is the
    pinned width (``0`` = unpinned, i.e. whatever the backend returns)."""

    def model(self) -> str: ...

    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[tuple[int, ...]]: ...


class HitLike(Protocol):
    """The read-only shape :func:`rank_hits` needs — satisfied by
    ``projections.search.Hit`` without this module importing it (which would close an
    import cycle: ``search`` reaches this module lazily, inside ``semantic_rank``)."""

    @property
    def cell(self) -> str: ...

    @property
    def snippet(self) -> str: ...

    @property
    def score(self) -> int: ...


H = TypeVar("H", bound=HitLike)


def normalize(raw: Sequence[int]) -> tuple[int, ...]:
    """Integer L2 normalization onto the ``SCALE`` fixed-point grid. Pure ints: the
    magnitude is an exact :func:`math.isqrt`, the division is floor division, so the
    result is identical on every host. A zero vector normalizes to itself."""
    norm = math.isqrt(sum(v * v for v in raw))
    if norm == 0:
        return tuple(0 for _ in raw)
    return tuple((int(v) * SCALE) // norm for v in raw)


def quantize(vector: Sequence[float]) -> tuple[int, ...]:
    """THE float→int boundary. A model-backed embedder calls this on the floats its
    transport returned and passes on ints only. Rounding is explicit round-half-up
    (``floor(x + 0.5)``), not Python's bankers' rounding, so the mapping is a fixed
    total function of the input floats rather than a mode-dependent one."""
    scaled = [int(math.floor(float(v) * SCALE + 0.5)) for v in vector]
    return normalize(scaled)


def cosine_score(a: Sequence[int], b: Sequence[int]) -> int:
    """Exact integer cosine similarity of two int vectors on the ``-SIM_SCALE..SIM_SCALE``
    scale. Mismatched widths or an empty/zero vector score 0 (fail neutral, never raise
    into a ranking). ``cosine_score(v, v) == SIM_SCALE`` exactly for any non-zero v."""
    if not a or len(a) != len(b):
        return 0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag = math.isqrt(sum(x * x for x in a) * sum(y * y for y in b))
    if mag == 0:
        return 0
    score = min((abs(dot) * SIM_SCALE) // mag, SIM_SCALE)
    return -score if dot < 0 else score


@dataclass(frozen=True)
class HashingEmbedder:
    """A LOCAL, dependency-free, fully deterministic embedder: the signed hashing trick
    over each text's tokens plus their boundary-marked character n-grams, hashed with
    stdlib BLAKE2b into ``dims`` buckets and integer-normalized.

    It is a real embedding (a dense fixed-width vector whose cosine tracks shared
    sub-word structure), not a model: no network, no credential, no float, no clock. So
    it is the DEFAULT vector path — an offline replay reproduces every score exactly."""

    dims: int = HASHING_DIMENSIONS
    ngram: int = _HASHING_NGRAM

    def model(self) -> str:
        return f"{HASHING_MODEL}/d{self.dimensions()}n{max(2, int(self.ngram))}"

    def dimensions(self) -> int:
        return max(8, int(self.dims))

    def embed(self, texts: Sequence[str]) -> list[tuple[int, ...]]:
        return [self._one(text) for text in texts]

    def _features(self, text: str) -> list[str]:
        """Whole tokens (the exact-match signal) plus boundary-marked char n-grams (the
        sub-word signal that makes 'running' close to 'run'). Deterministic order is
        irrelevant — the features are accumulated into buckets — but it is fixed."""
        toks = ordered_tokens(text)
        n = max(2, int(self.ngram))
        feats: list[str] = list(toks)
        for tok in toks:
            padded = f"^{tok}$"
            if len(padded) <= n:
                feats.append(padded)
                continue
            feats.extend(padded[i : i + n] for i in range(len(padded) - n + 1))
        return feats

    def _one(self, text: str) -> tuple[int, ...]:
        dims = self.dimensions()
        raw = [0] * dims
        for feat in self._features(text):
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8, person=_PERSON).digest()
            h = int.from_bytes(digest, "big")
            raw[(h >> 1) % dims] += 1 if h & 1 else -1
        return normalize(raw)


def _embed_one(embedder: Embedder, text: str) -> tuple[int, ...]:
    vectors = embedder.embed([text])
    if len(vectors) != 1:
        raise EmbeddingError("embedder returned no vector for a single input text")
    return tuple(int(v) for v in vectors[0])


def _embed_many(embedder: Embedder, texts: Sequence[str]) -> list[tuple[int, ...]]:
    vectors = embedder.embed(list(texts))
    if len(vectors) != len(texts):
        raise EmbeddingError(
            f"embedder returned {len(vectors)} vectors for {len(texts)} texts — "
            "a row mismatch cannot be aligned, so retrieval must fall back"
        )
    return [tuple(int(v) for v in vec) for vec in vectors]


def rank_hits(hits: Sequence[H], query: str, embedder: Embedder) -> list[tuple[H, int]]:
    """Re-rank search hits by integer cosine similarity of query↔snippet, returning
    ``(hit, similarity)`` pairs in descending order. Ties fall back to the hit's own
    integer lexical score and then its cell id, so the order is total and stable and the
    result is always a permutation of ``hits``."""
    if not hits:
        return []
    qv = _embed_one(embedder, query)
    vectors = _embed_many(embedder, [hit.snippet for hit in hits])
    scored = [(hit, cosine_score(qv, vec)) for hit, vec in zip(hits, vectors, strict=True)]
    scored.sort(key=lambda pair: (pair[1], pair[0].score, pair[0].cell), reverse=True)
    return scored


def rank_texts(query: str, items: Sequence[tuple[str, str]], embedder: Embedder) -> dict[str, int]:
    """Integer similarity of ``query`` to each ``(id, text)`` pair — the stateless helper
    the Q&A path uses over its already-resolved candidate segments (full passage text,
    not a truncated snippet). Returns a plain ``{id: score}`` map; the caller owns the
    ordering, so this adds no hidden policy."""
    if not items:
        return {}
    qv = _embed_one(embedder, query)
    vectors = _embed_many(embedder, [text for _id, text in items])
    return {
        item_id: cosine_score(qv, vec) for (item_id, _text), vec in zip(items, vectors, strict=True)
    }


class EmbeddingIndex:
    """A derived VECTOR index over a ``KnowledgeProjection`` — the embedding sibling of
    ``projections.search.SearchIndex``. Disposable: it holds no authority, its whole
    contents are a pure function of (knowledge fold, embedder), and discarding it loses
    nothing canonical. Nothing here is ever asserted onto the Weft."""

    def __init__(self, knowledge: KnowledgeProjection, embedder: Embedder) -> None:
        self.knowledge = knowledge
        self.embedder = embedder
        self.vectors: dict[str, tuple[int, ...]] = {}
        self.build()

    def build(self) -> None:
        self.vectors = {}
        items = [item for item in self.knowledge.items() if (item.text or "").strip()]
        if not items:
            return
        vectors = _embed_many(self.embedder, [item.text for item in items])
        for item, vec in zip(items, vectors, strict=True):
            self.vectors[item.id] = vec

    def add_item(self, item: KnowledgeItem) -> None:
        """Incrementally (re)embed ONE knowledge item. Idempotent by replacement, so a
        sequence of updates leaves the same ``fingerprint`` as a full ``rebuild``."""
        if not (item.text or "").strip():
            self.vectors.pop(item.id, None)
            return
        self.vectors[item.id] = _embed_many(self.embedder, [item.text])[0]

    def remove_item(self, item_id: str) -> None:
        """Incrementally drop one item's vector (e.g. a retracted note)."""
        self.vectors.pop(item_id, None)

    def rebuild(self) -> EmbeddingIndex:
        """Reproduce the index from the current knowledge fold (Law-5 cache contract):
        same fold + same embedder ⇒ identical ``fingerprint``."""
        self.build()
        return self

    def size(self) -> int:
        return len(self.vectors)

    def vector(self, item_id: str) -> tuple[int, ...] | None:
        return self.vectors.get(item_id)

    def score_texts(self, query: str, items: Sequence[tuple[str, str]]) -> dict[str, int]:
        """Integer similarity of ``query`` to each ``(id, text)`` pair, reusing the
        CACHED vector for every id this index already holds and embedding only the
        unseen ones — the point of keeping the index around."""
        if not items:
            return {}
        qv = _embed_one(self.embedder, query)
        missing = [(item_id, text) for item_id, text in items if item_id not in self.vectors]
        fresh: dict[str, tuple[int, ...]] = {}
        if missing:
            vectors = _embed_many(self.embedder, [text for _id, text in missing])
            for (item_id, _text), vec in zip(missing, vectors, strict=True):
                fresh[item_id] = vec
        out: dict[str, int] = {}
        for item_id, _text in items:
            known = self.vectors.get(item_id)
            out[item_id] = cosine_score(qv, known if known is not None else fresh.get(item_id, ()))
        return out

    def rank(self, query: str, hits: Sequence[H]) -> list[tuple[H, int]]:
        """Re-rank hits using the cached vectors (falling back to the hit's snippet for
        an unindexed cell). Same total, stable ordering contract as :func:`rank_hits`."""
        scores = self.score_texts(query, [(hit.cell, hit.snippet) for hit in hits])
        scored = [(hit, scores.get(hit.cell, 0)) for hit in hits]
        scored.sort(key=lambda pair: (pair[1], pair[0].score, pair[0].cell), reverse=True)
        return scored

    def fingerprint(self) -> str:
        """A content address of the whole index — the Law-5 cache equality witness."""
        return content_id(
            {
                "embedding_index": {
                    "model": str(self.embedder.model()),
                    "dimensions": int(self.embedder.dimensions()),
                    "vectors": {cid: list(vec) for cid, vec in sorted(self.vectors.items())},
                }
            },
            kind="projection",
        )
