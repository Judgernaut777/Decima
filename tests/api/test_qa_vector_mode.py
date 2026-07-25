"""Grounded Q&A through the API surface with VECTOR retrieval configured.

Driven through the same routes the Shell uses (login → ``ImportArtifact`` →
``AskGroundedQuestion`` → the question readers), with the local deterministic embedder on
the stack — no model, no network, no credential.

What is pinned here:

  * The run RECORDS how its evidence was ranked (``retrieval.mode`` + the embedder tag)
    and each citation's integer semantic score. Those are recorded FACTS on the Weft: a
    projection delete+rebuild reproduces the detail exactly, so the answer is REPLAYED,
    never recomputed by the fold.
  * With no embedder (the default stack) the recorded mode is ``lexical`` and every
    semantic score is 0 — an offline run is unchanged.
  * A configured-but-broken embedder records ``lexical_fallback`` with NO embedder tag —
    the run never claims a capability it did not have — and still answers, grounded.
  * Nothing recorded is a float, and citations still validate against real segments.
"""

from __future__ import annotations

import dataclasses

from decima.kernel.weave import Weave
from decima.projections.embedding import EmbeddingError, HashingEmbedder
from decima.services.api import qa_service
from decima.services.api.server import build_driver

PORT_DOC = "The Aurora relay listens on port 7712 for telemetry traffic."
RETENTION_DOC = "The Aurora relay keeps telemetry logs for ninety days of retention."
QUESTION = "What port does the Aurora relay listen on and how long is telemetry retention?"


class _BrokenEmbedder:
    """Configured, but its local endpoint is down."""

    def model(self) -> str:
        return "broken/v0"

    def dimensions(self) -> int:
        return 8

    def embed(self, texts):
        raise EmbeddingError("no endpoint")


def _seed(client) -> None:
    for name, body in (("aurora-port.md", PORT_DOC), ("aurora-retention.md", RETENTION_DOC)):
        r = client.request("POST", "/api/v1/artifacts/import", body={"name": name, "body": body})
        assert r.status == 201, r.json()


def _ask(client, question=QUESTION):
    r = client.request("POST", "/api/v1/questions/ask", body={"question": question})
    assert r.status == 201, r.json()
    return r.json()["data"]


def _with_embedder(env, embedder):
    env["app"].commands.models = dataclasses.replace(env["app"].commands.models, embedder=embedder)


def _run_cell(env, run_id):
    cell = Weave.fold(env["app"].weft).get(run_id)
    assert cell is not None
    return cell


def test_default_stack_records_lexical_retrieval_with_no_semantic_claim(client, env):
    _seed(client)
    run = _ask(client)
    assert run["grounded"] is True and run["citations"]
    content = _run_cell(env, run["id"]).content
    assert content["retrieval"] == {"mode": "lexical", "embedder": "", "dimensions": 0}
    assert all(c["relevance"]["semantic_score"] == 0 for c in content["citations"])


def test_configured_embedder_records_the_mode_and_an_integer_semantic_score(client, env):
    _with_embedder(env, HashingEmbedder(dims=64))
    _seed(client)
    run = _ask(client)
    assert run["grounded"] is True and run["citations"]
    content = _run_cell(env, run["id"]).content
    assert content["retrieval"] == {
        "mode": "semantic",
        "embedder": "hashing-ngram-v1/d64n3",
        "dimensions": 64,
    }
    for cit in content["citations"]:
        rel = cit["relevance"]
        # every recorded number is an INT (bool is not accepted as a stand-in for one)
        for key in ("score", "semantic_score"):
            assert isinstance(rel[key], int) and not isinstance(rel[key], bool)
        assert rel["matched_tokens"]  # the lexical citability gate still decided this
    # the detail reader surfaces the same recorded provenance (a pure fold read)
    detail = client.request(
        "GET", "/api/v1/questions/detail", csrf=False, query={"id": run["id"]}
    ).json()
    assert detail["retrieval"] == content["retrieval"]


def test_recorded_vector_signal_is_replayed_not_recomputed(client, env):
    """The vector score is a RECORDED FACT: rebuilding every projection from the Weft
    reproduces the run detail byte-for-byte, and dropping the embedder afterwards does
    NOT change the already-answered run (it is never re-derived from a model)."""
    _with_embedder(env, HashingEmbedder(dims=64))
    _seed(client)
    run = _ask(client)
    before = qa_service.get_question_run(env["app"], {"id": run["id"]})
    env["app"].driver = build_driver(env["app"].weft)
    _with_embedder(env, None)  # the embedder is gone; the recorded run must not move
    after = qa_service.get_question_run(env["app"], {"id": run["id"]})
    assert after == before
    assert after["retrieval"]["mode"] == "semantic"


def test_identical_questions_produce_identical_citations_under_the_vector_path(client, env):
    _with_embedder(env, HashingEmbedder(dims=64))
    _seed(client)
    first, second = _ask(client), _ask(client)
    assert first["id"] != second["id"]
    a = _run_cell(env, first["id"]).content["citations"]
    b = _run_cell(env, second["id"]).content["citations"]
    assert [c["segment_id"] for c in a] == [c["segment_id"] for c in b]
    assert [c["relevance"] for c in a] == [c["relevance"] for c in b]


def test_a_broken_embedder_still_answers_and_records_the_fallback_honestly(client, env):
    _with_embedder(env, _BrokenEmbedder())
    _seed(client)
    run = _ask(client)
    assert run["grounded"] is True and run["citations"]  # the question still gets answered
    content = _run_cell(env, run["id"]).content
    assert content["retrieval"] == {
        "mode": "lexical_fallback",
        "embedder": "",  # no capability is claimed that the run did not have
        "dimensions": 0,
    }


def test_an_ungrounded_run_records_how_it_looked_for_evidence(client, env):
    _with_embedder(env, HashingEmbedder(dims=64))
    _seed(client)
    run = _ask(client, "zzz unrelated quantum llama husbandry")
    assert run["grounded"] is False and run["citations"] == []
    content = _run_cell(env, run["id"]).content
    assert content["retrieval"]["mode"] in {"semantic", "lexical_fallback"}
    assert content["answer_text"] == qa_service.UNGROUNDED_ANSWER
