"""qa-retrieval lane: the hybrid ranking and INCREMENTAL folding of ``SearchIndex``.

Two obligations proven here:

  * HYBRID ranking stays behind the same ``query``/``Hit`` interface — the exact
    content-token overlap GATE still decides citability (a stopword-only or fuzzy-only
    resemblance is never citable), and survivors are ordered by an IDF-style weight,
    a bounded char-n-gram fuzzy bonus, and a phrase/proximity bonus. Scores stay
    integers; provenance/trust/instruction_eligible ride on every Hit.
  * INCREMENTAL folding (``add_item``/``remove_item``) reproduces a full ``rebuild``
    BYTE-for-BYTE: after any sequence of updates ``fingerprint`` equals a fresh index
    over the same fold (invariant 2 — the projection is a pure function of the Weft).
"""

from __future__ import annotations

from decima.kernel.model import assert_content
from decima.kernel.weft import RETRACT
from decima.projections.engine import ProjectionDriver
from decima.projections.knowledge import KnowledgeProjection
from decima.projections.search import SearchIndex, semantic_rank
from tests.projections.conftest import new_weft


def _knowledge(weft):
    driver = ProjectionDriver(weft)
    driver.register(KnowledgeProjection())
    return driver, driver.get("knowledge")


def _note(weft, author, note_id, text, *, trusted=False):
    assert_content(
        weft, author, note_id, "note", {"text": text, "instruction_eligible": bool(trusted)}
    )


# ── (b) incremental == full rebuild, byte-identical fingerprint ────────────────
def test_incremental_fold_matches_full_rebuild_fingerprint():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "the roadmap for the alpha release")
    _note(weft, author, "note:b", "beta feedback with a zzunique marker token")
    _note(weft, author, "note:c", "gamma channel notes about running relays")
    driver, know = _knowledge(weft)

    index = SearchIndex(know)  # full build over the base fold

    # A sequence of incremental mutations, each mirrored into the index by hand.
    _note(weft, author, "note:d", "delta report on relay ports and running latency")
    driver.update()
    index.add_item(know.get("note:d"))  # ADD

    weft.append(author, RETRACT, {"cell": "note:b"})
    driver.update()
    index.remove_item("note:b")  # REMOVE (and its unique token must vanish)

    _note(weft, author, "note:a", "the REVISED roadmap for the beta release")
    driver.update()
    index.add_item(know.get("note:a"))  # REINDEX in place (text changed)

    # The token that only note:b carried is gone — an emptied posting is deleted, which
    # is exactly what keeps the incremental fold byte-identical to a rebuild.
    assert "zzunique" not in index.postings

    full = SearchIndex(know)  # brand-new index folded from the whole current fold
    assert index.fingerprint() == full.fingerprint()
    assert index.records.keys() == full.records.keys()


def test_incremental_add_then_remove_returns_to_original_fingerprint():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "the canonical spec document")
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    before = index.fingerprint()

    _note(weft, author, "note:x", "an ephemeral transient scratch note")
    driver.update()
    index.add_item(know.get("note:x"))
    assert index.fingerprint() != before

    weft.append(author, RETRACT, {"cell": "note:x"})
    driver.update()
    index.remove_item("note:x")
    assert index.fingerprint() == before  # round-trip is exact


def test_empty_text_item_is_indexed_as_nothing_like_a_rebuild():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "a real note with words")
    _note(weft, author, "note:empty", "   ")  # whitespace only ⇒ no tokens
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    # build() skips a token-less item; add_item must agree so the fingerprint matches.
    index.add_item(know.get("note:empty"))
    assert "note:empty" not in index.records
    assert index.fingerprint() == SearchIndex(know).fingerprint()


# ── (a) hybrid ranking: gate kept, richer deterministic order ──────────────────
def test_stopword_only_overlap_is_never_citable():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "the relay port is on the internal network")
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    # Query shares ONLY stopwords ("the", "is", "on") with the note — content tokens
    # {database, schema} have no exact overlap ⇒ not citable (load-bearing gate).
    assert index.query("is the database schema on") == []


def test_fuzzy_near_match_alone_never_makes_a_citation():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "the system will run nightly")
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    # "running" fuzzily resembles "run" but shares no EXACT content token: the fuzzy
    # signal is secondary and can never introduce a non-overlapping candidate.
    assert index.query("running") == []


def test_fuzzy_bonus_reranks_only_equally_grounded_survivors():
    weft, author, _db, _kr = new_weft()
    # All three share the exact token "relays"; ranking among them is the question.
    _note(weft, author, "note:exact", "running relays here")  # exact on both tokens
    _note(weft, author, "note:fuzzy", "run the relays")  # exact relays + fuzzy running
    _note(weft, author, "note:plain", "the relays alone")  # exact relays, no fuzzy
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    hits = index.query("running relays")
    order = [h.cell for h in hits]
    # Full exact overlap wins; among the single-overlap survivors the fuzzy near-match
    # outranks the plain one — but strictly below the exact hit (bonuses are capped).
    assert order == ["note:exact", "note:fuzzy", "note:plain"]
    assert hits[0].score > hits[1].score > hits[2].score


def test_idf_weighting_ranks_the_rarer_token_higher():
    weft, author, _db, _kr = new_weft()
    # "common" appears everywhere (low IDF); "sphinx" appears once (high IDF).
    for i in range(6):
        _note(weft, author, f"note:c{i}", "common shared filler common words")
    _note(weft, author, "note:rare", "common text mentioning sphinx exactly once")
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    hits = index.query("common sphinx")
    # The note carrying the rare, high-IDF token outranks the many common-only notes.
    assert hits[0].cell == "note:rare"
    assert hits[0].score > hits[1].score


def test_phrase_proximity_bonus_breaks_ties_toward_contiguous_phrasing():
    weft, author, _db, _kr = new_weft()
    # Identical content-token multiset & identical exact overlap; the difference is
    # whether the query phrase "relay port" survives as an adjacent pair.
    _note(weft, author, "note:phrase", "the relay port failed")  # "relay port" adjacent
    _note(weft, author, "note:split", "the port and the relay")  # same tokens, not adjacent
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    hits = index.query("relay port")
    assert [h.cell for h in hits] == ["note:phrase", "note:split"]
    assert hits[0].score > hits[1].score


def test_hits_are_positive_integers_with_a_total_stable_order():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:a", "roadmap alpha release plan")
    _note(weft, author, "note:b", "roadmap alpha release plan")  # identical text, different id
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    hits = index.query("roadmap alpha")
    assert all(isinstance(h.score, int) and h.score >= 1 for h in hits)
    # Equal score + equal text ⇒ id is the final, total tie-break. The sort is reverse
    # (descending), so the order is deterministic and stable across runs.
    assert [h.cell for h in hits] == sorted((h.cell for h in hits), reverse=True)
    assert index.query("roadmap alpha") == hits


# ── (c) provenance / trust / instruction-eligibility preserved on every Hit ────
def test_hits_carry_provenance_trust_and_instruction_eligibility():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:trusted", "the trusted roadmap alpha", trusted=True)
    _note(weft, author, "note:untrusted", "an untrusted roadmap alpha rumor")
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    by_id = {h.cell: h for h in index.query("roadmap alpha")}
    t = by_id["note:trusted"]
    assert t.instruction_eligible is True and t.trust == "trusted" and t.provenance
    u = by_id["note:untrusted"]
    assert u.instruction_eligible is False and u.trust == "untrusted" and u.provenance


# ── semantic_rank seam: a real, deterministic, dependency-free re-rank ──────────
def test_semantic_rank_is_deterministic_total_and_stable():
    weft, author, _db, _kr = new_weft()
    _note(weft, author, "note:near", "relay port configuration and relay tuning")
    _note(weft, author, "note:far", "relay port")
    driver, know = _knowledge(weft)
    index = SearchIndex(know)
    hits = index.query("relay port", limit=10)
    ranked = semantic_rank(hits, "relay port relay tuning configuration")
    # Deterministic: same inputs ⇒ same order, and a permutation of the same hits.
    assert ranked == semantic_rank(list(hits), "relay port relay tuning configuration")
    assert {h.cell for h in ranked} == {h.cell for h in hits}
    # The snippet richer in query n-grams ranks first.
    assert ranked[0].cell == "note:near"
