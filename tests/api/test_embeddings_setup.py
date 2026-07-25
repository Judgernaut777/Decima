"""The retrieval-embedding half of the model stack (``models_setup.build_embedder``).

The obligations, all offline:

  * FAIL CLOSED to lexical. No env, an unsupported kind, or an incompletely configured
    ``local`` embedder all yield ``None`` — retrieval keeps its deterministic lexical
    ranking rather than half-enabling a vector path.
  * LOOPBACK CONFINEMENT. Every embedding call sends imported document text, so a
    ``local`` base URL that is not loopback is REFUSED with a ``ValueError`` (never
    silently downgraded) — the same rule the ``local`` completion provider is held to.
  * THE FLOAT BOUNDARY IS THE TRANSPORT. ``LocalEmbedder`` hands its backend's floats to
    ``quantize`` and returns ints; a row or dimension mismatch, or a dead endpoint, raises
    ``EmbeddingError`` so retrieval can fall back instead of scoring nonsense.
  * NO CREDENTIAL is read, held, or sent anywhere in this path.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from decima.projections.embedding import SCALE, EmbeddingError, HashingEmbedder
from decima.services.api.models_setup import (
    ENV_EMBED_BASE_URL,
    ENV_EMBED_DIM,
    ENV_EMBED_MODEL,
    ENV_EMBED_PROVIDER,
    LocalEmbedder,
    build_embedder,
    build_model_stack,
)

LOOPBACK = "http://127.0.0.1:8081"


def _local_env(**extra: str) -> dict:
    env = {
        ENV_EMBED_PROVIDER: "local",
        ENV_EMBED_MODEL: "bge-small-local",
        ENV_EMBED_BASE_URL: LOOPBACK,
    }
    env.update(extra)
    return env


# ── fail closed to the deterministic lexical path ─────────────────────────────
def test_no_env_means_no_embedder():
    assert build_embedder(env={}) is None
    assert build_model_stack(env={}).embedder is None


def test_unsupported_kind_and_incomplete_local_config_fall_back_to_lexical():
    assert build_embedder(env={ENV_EMBED_PROVIDER: "pinecone"}) is None
    assert build_embedder(env={ENV_EMBED_PROVIDER: "local"}) is None  # no model, no url
    assert build_embedder(env={ENV_EMBED_PROVIDER: "local", ENV_EMBED_MODEL: "m"}) is None


# ── the local, dependency-free deterministic embedder ─────────────────────────
def test_hashing_kind_yields_the_local_deterministic_embedder():
    emb = build_embedder(env={ENV_EMBED_PROVIDER: "hashing"})
    assert isinstance(emb, HashingEmbedder)
    assert emb.dimensions() == 256
    stack = build_model_stack(env={ENV_EMBED_PROVIDER: "hashing"})
    assert isinstance(stack.embedder, HashingEmbedder)
    # ...and it is NOT a routable model: the catalogue is untouched by embedding config
    assert [e.model for e in stack.registry.enabled_entries()] == ["deterministic-offline"]


def test_hashing_dimensions_are_operator_tunable_but_bounded():
    def dims(value: str) -> int:
        emb = build_embedder(env={ENV_EMBED_PROVIDER: "hashing", ENV_EMBED_DIM: value})
        assert emb is not None
        return emb.dimensions()

    assert dims("64") == 64
    # a nonsense or too-small width falls back to the default instead of failing a boot
    assert dims("2") == 256
    assert dims("abc") == 256


# ── loopback confinement of a real embedding model ────────────────────────────
@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:8081", "http://localhost:8081", "http://[::1]:8081", "http://127.5.5.5:1"],
)
def test_loopback_local_embedder_is_accepted(base_url):
    emb = build_embedder(env=_local_env(**{ENV_EMBED_BASE_URL: base_url}))
    assert isinstance(emb, LocalEmbedder)
    assert emb.model() == "bge-small-local"


@pytest.mark.parametrize(
    "base_url",
    ["http://10.1.2.3:8081", "http://embeddings.example.com", "http://0.0.0.0:8081", "not a url"],
)
def test_non_loopback_local_embedder_is_refused_not_downgraded(base_url):
    with pytest.raises(ValueError, match="loopback"):
        build_embedder(env=_local_env(**{ENV_EMBED_BASE_URL: base_url}))


def test_a_non_loopback_embed_url_fails_the_whole_stack_build():
    with pytest.raises(ValueError, match="loopback"):
        build_model_stack(env=_local_env(**{ENV_EMBED_BASE_URL: "http://evil.example.com"}))


# ── the float→int boundary lives in the transport wrapper ─────────────────────
def _fake_backend(vectors: list[list[float]]):
    def backend(model: str, texts: Sequence[str]) -> list[list[float]]:
        assert model == "bge-small-local"
        assert list(texts)  # never called with an empty batch
        return vectors

    return backend


def test_local_embedder_quantizes_floats_to_ints():
    emb = LocalEmbedder(
        model_name="bge-small-local", dims=0, backend=_fake_backend([[0.6, 0.8], [-1.0, 0.0]])
    )
    out = emb.embed(["a", "b"])
    assert out == [((3 * SCALE) // 5, (4 * SCALE) // 5), (-SCALE, 0)]
    assert all(isinstance(v, int) for vec in out for v in vec)  # no float survives
    assert emb.embed([]) == []  # empty batch never reaches the transport
    assert emb.dimensions() == 0  # unpinned width


def test_local_embedder_enforces_a_pinned_dimension():
    emb = LocalEmbedder(model_name="bge-small-local", dims=4, backend=_fake_backend([[0.6, 0.8]]))
    with pytest.raises(EmbeddingError, match="dimensions"):
        emb.embed(["a"])


def test_local_embedder_refuses_a_row_mismatch():
    emb = LocalEmbedder(model_name="bge-small-local", dims=0, backend=_fake_backend([[0.6, 0.8]]))
    with pytest.raises(EmbeddingError, match="vectors"):
        emb.embed(["a", "b"])


def test_a_dead_endpoint_raises_embedding_error_so_retrieval_can_fall_back():
    def dead(model: str, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("embedding transport URLError")

    emb = LocalEmbedder(model_name="bge-small-local", dims=0, backend=dead)
    with pytest.raises(EmbeddingError):
        emb.embed(["a"])
