"""Document ingestion + source-grounded Q&A.

Load-bearing properties pinned here:
  * an answer's citations RESOLVE to imported source segments;
  * imported content is DATA (instruction_eligible=False) — never an instruction;
  * private-project knowledge is horizon-scoped away from an unrelated agent;
  * deleting the search index does not delete knowledge (invariant 2);
  * retracted material stops appearing (fold yields only live cells).
"""

from __future__ import annotations

import zlib

from decima.capabilities import documents, qa
from decima.capabilities.documents import build_index, import_document, knowledge_projection
from decima.kernel.lifecycle import redact
from decima.kernel.weave import Weave


def _make_pdf(text: str) -> bytes:
    """A tiny, uncompressed PDF whose content stream shows one literal string. Safe to
    parse (no actions, no scripts)."""
    stream = b"BT /F1 12 Tf 72 720 Td (" + text.encode("latin-1") + b") Tj ET"
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog>>endobj\n"
        b"stream\n" + stream + b"\nendstream\n"
        b"%%EOF"
    )


ALPHA = (
    "The Nimbus deployment runs on port 8443. "
    "Rollback is triggered by the sentinel flag in the manifest. "
    "The retention window for logs is ninety days."
)
BETA = (
    "The Vega billing pipeline reconciles invoices nightly. "
    "A failed reconciliation pages the on-call owner immediately."
)


def test_import_classifies_and_source_links_segments(weft, author):
    imported = import_document(
        weft, author, source="alpha.md", data=ALPHA.encode("utf-8"), project="ops"
    )
    assert imported.doc_type == documents.MARKDOWN
    assert imported.segment_ids  # at least one segment
    weave = Weave.fold(weft)
    for sid in imported.segment_ids:
        cell = weave.get(sid)
        # every segment keeps its claim → source relationship (id + offset)
        assert cell.content["source_document"] == imported.document_id
        assert isinstance(cell.content["offset"], int)
        # and a typed edge is a second, index-independent witness of the link
        assert any(e["dst"] == imported.document_id for e in cell.edges_out)
        # imported content is DATA, never an instruction (invariant 5)
        assert cell.content["instruction_eligible"] is False


def test_import_is_idempotent_by_content_address(weft, author):
    a = import_document(weft, author, source="alpha.md", data=ALPHA.encode("utf-8"))
    b = import_document(weft, author, source="alpha.md", data=ALPHA.encode("utf-8"))
    assert a.document_id == b.document_id
    assert a.segment_ids == b.segment_ids
    # the same segment ids re-assert — the knowledge count does not grow
    kp = knowledge_projection(weft)
    seg_ids = {i.id for i in kp.items() if i.id in set(a.segment_ids)}
    assert seg_ids == set(a.segment_ids)


def test_pdf_text_is_extracted_safely_and_answerable(weft, author, provider):
    pdf = _make_pdf("Aurora uses a quorum of five replicas")
    imported = import_document(weft, author, source="spec.pdf", data=pdf, project="ops")
    assert imported.doc_type == documents.PDF
    assert imported.segment_ids, "PDF text should extract into at least one segment"
    ans = qa.answer_question(weft, "How many replicas does Aurora use?",
                             provider=provider, horizon={"ops"})
    assert ans.grounded
    assert ans.citations


def test_answer_citations_resolve_to_imported_segments(weft, author, provider):
    imported = import_document(
        weft, author, source="alpha.md", data=ALPHA.encode("utf-8"), project="ops"
    )
    kp = knowledge_projection(weft)
    ans = qa.answer_question(
        weft, "What port does the Nimbus deployment run on?",
        provider=provider, horizon={"ops"},
    )
    assert ans.grounded and ans.citations
    imported_segments = set(imported.segment_ids)
    for cite in ans.citations:
        # a citation resolves to an imported segment Cell...
        assert kp.get(cite.segment_id) is not None
        assert cite.segment_id in imported_segments
        # ...and back to the source document it came from
        assert cite.source_document == imported.document_id


def test_ungrounded_when_nothing_in_horizon(weft, author, provider):
    import_document(weft, author, source="alpha.md", data=ALPHA.encode("utf-8"), project="ops")
    # A question about content that exists but a horizon that excludes its project.
    ans = qa.answer_question(weft, "What port does Nimbus use?",
                             provider=provider, horizon={"unrelated"})
    assert not ans.grounded
    assert ans.citations == ()


def test_private_project_not_exposed_to_unrelated_agent(weft, author, provider):
    # A PUBLIC doc and a PRIVATE doc, each the lexical match for its own query.
    import_document(weft, author, source="public.md", data=ALPHA.encode("utf-8"),
                    project="public")
    private = import_document(weft, author, source="secret.md",
                              data=BETA.encode("utf-8"), project="private")

    # An agent whose horizon is only {public} asks the private question directly.
    ans = qa.answer_question(
        weft, "Who does a failed Vega reconciliation page?",
        provider=provider, horizon={"public"},
    )
    private_ids = set(private.segment_ids)
    cited = {c.segment_id for c in ans.citations}
    # NONE of the private segments leak into the unrelated agent's citations.
    assert not (cited & private_ids)

    # The owning agent (horizon includes private) DOES see it — scoping is a gate,
    # not a deletion.
    owner = qa.answer_question(
        weft, "Who does a failed Vega reconciliation page?",
        provider=provider, horizon={"public", "private"},
    )
    assert owner.grounded
    assert {c.segment_id for c in owner.citations} & private_ids


def test_deleting_the_search_index_does_not_delete_knowledge(weft, author, provider):
    imported = import_document(
        weft, author, source="alpha.md", data=ALPHA.encode("utf-8"), project="ops"
    )
    index = build_index(weft)
    assert index.query("Nimbus port")  # index has the knowledge

    # "Delete" the index — drop the whole disposable read-model.
    del index

    # Knowledge is untouched on the Weft: a fresh projection still has every segment...
    kp = knowledge_projection(weft)
    live = {i.id for i in kp.items()}
    assert set(imported.segment_ids) <= live
    # ...and a rebuilt index reproduces the same hits, and Q&A still answers.
    rebuilt = build_index(weft)
    assert rebuilt.query("Nimbus port")
    ans = qa.answer_question(weft, "What port does Nimbus run on?",
                             provider=provider, horizon={"ops"})
    assert ans.grounded and ans.citations


def test_retracted_material_stops_appearing(weft, author, provider):
    imported = import_document(
        weft, author, source="alpha.md", data=ALPHA.encode("utf-8"), project="ops"
    )
    before = qa.answer_question(weft, "What port does Nimbus run on?",
                                provider=provider, horizon={"ops"})
    assert before.grounded and before.citations

    # Retract (redact) every segment of the document.
    for sid in imported.segment_ids:
        redact(weft, author, sid)

    # The knowledge fold now yields only live cells — the segments are gone...
    kp = knowledge_projection(weft)
    live = {i.id for i in kp.items()}
    assert not (set(imported.segment_ids) & live)
    # ...so a rebuilt index no longer surfaces them and the answer is ungrounded.
    assert not build_index(weft).query("Nimbus port")
    after = qa.answer_question(weft, "What port does Nimbus run on?",
                               provider=provider, horizon={"ops"})
    assert not after.grounded


def test_pdf_helper_roundtrips_uncompressed_and_compressed():
    # Sanity: the extractor pulls literal strings from an uncompressed stream...
    pdf = _make_pdf("Hello workspace reviewer")
    assert "Hello workspace reviewer" in documents._extract_pdf_text(pdf)
    # ...and tolerates a FlateDecode-compressed stream without executing anything.
    inner = b"BT (Compressed secret token) Tj ET"
    compressed = b"%PDF-1.4\nstream\n" + zlib.compress(inner) + b"\nendstream\n%%EOF"
    assert "Compressed secret token" in documents._extract_pdf_text(compressed)
