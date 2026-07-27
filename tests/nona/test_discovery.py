"""Nona N6: plug-in-or-forge — the order, the integers, and what must NOT happen.

Three properties carry this module, and each has a way of failing silently:

  * ORDER. Forging is the last resort, so a goal an existing organ already serves must
    resolve to `use` and must not consult research or forge. A spy research seam proves the
    seam was not even reached, which is stronger than checking the returned action.
  * DETERMINISM. Ranking reuses `projections.search` — integer IDF, exact-content-token
    gate, total ordering — so two runs over the same fold must produce byte-identical
    scores. A ranking that drifts makes "why did it pick that?" unanswerable.
  * NO AUTO-ACTIVATION. Discovery reads the fold and writes NOTHING: not a grant, not an
    inbox item, not a stub handler. Every test that exercises a decision also asserts the
    event count did not move, because "it suggested, it did not install" is exactly the kind
    of claim that rots without a test.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from decima.kernel import capability, lifecycle, model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.services.nona import anchors, candidate, discovery, executor

SUMMARISE = "def main(text):\n    return str(text)[:10]\n"


def _world() -> tuple[Weft, Keyring, str]:
    keyring = Keyring(seed=bytes(32))
    weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), keyring)
    author = keyring.mint("root", "root").id
    return weft, keyring, author


def _organ(
    weft: Weft,
    author: str,
    *,
    intent: str,
    name: str,
    tier: str = anchors.PURE,
    source: str = SUMMARISE,
) -> str:
    """A candidate + its capability, through the shipping build path."""
    proposed = candidate.propose_candidate(
        weft,
        author,
        intent=intent,
        declared_effect_class=tier,
        source=source,
        output_schema={"type": "str"},
    )
    built = executor.build_capability(
        weft,
        Weave.fold(weft),
        author,
        candidate=proposed["cell"],
        tier=tier,
        name=name,
        grantee=author,
        granter=author,
    )
    return str(built["capability"])


# ── order: the catalogue first, research second, forge last ──────────────────
def test_an_existing_organ_is_found_before_anything_is_forged():
    weft, _kr, author = _world()
    cap = _organ(
        weft, author, intent="summarise a document into a short abstract", name="summarise"
    )
    calls: list[str] = []

    def research(goal: str) -> list[dict[str, object]]:
        calls.append(goal)
        return [{"name": "should never be reached"}]

    out = discovery.discover(
        Weave.fold(weft), "summarise a document", threshold=100, research=research
    )

    assert out["action"] == discovery.USE
    assert out["capability"] == cap
    assert out["mode"] == discovery.LEXICAL
    assert out["matched_tokens"], "a match must carry the evidence for its score"
    assert set(out["matched_tokens"]) <= {"summarise", "document"}
    assert calls == [], "the research seam must not be consulted when the catalogue answers"


def test_a_use_suggestion_activates_nothing_and_writes_nothing():
    """Design §5.8, closing paragraph: auto-activation is the forbidden shortcut."""
    weft, _kr, author = _world()
    _organ(weft, author, intent="summarise a document into a short abstract", name="summarise")
    before = weft.count()

    out = discovery.discover(Weave.fold(weft), "summarise a document", threshold=100)

    assert out["action"] == discovery.USE
    assert out["grant_required"] is True  # using it means holding a grant, not being ranked
    assert weft.count() == before, "discovery must not write to the log"


def test_the_research_seam_is_consulted_only_after_the_catalogue_misses():
    weft, _kr, author = _world()
    _organ(weft, author, intent="summarise a document into a short abstract", name="summarise")
    calls: list[str] = []

    def research(goal: str) -> list[dict[str, object]]:
        calls.append(goal)
        return [{"name": "ledger.transfer", "source": "an external registry"}]

    before = weft.count()
    out = discovery.discover(
        Weave.fold(weft), "transfer money between two ledgers", threshold=100, research=research
    )

    assert calls == ["transfer money between two ledgers"]
    assert out["action"] == discovery.PLUG_IN
    assert out["candidates"][0]["name"] == "ledger.transfer"
    # A descriptor someone else wrote is DATA, in both directions.
    assert out["candidates"][0]["instruction_eligible"] is False
    assert out["candidates"][0]["trust"] == "untrusted"
    assert weft.count() == before


def test_forging_is_the_last_resort_and_is_only_ever_a_recommendation():
    weft, _kr, author = _world()
    _organ(weft, author, intent="summarise a document into a short abstract", name="summarise")
    before = weft.count()

    out = discovery.discover(Weave.fold(weft), "transfer money between ledgers", threshold=100)

    assert out["action"] == discovery.FORGE
    assert out["next_step"] == "ProposeCapability"
    assert out["researched"] is False and "no research seam" in out["reason"]
    # No source, no candidate, no stub organ — the refusal to invent is the point (§5.9.1).
    assert "source" not in out
    assert weft.count() == before


def test_an_empty_catalogue_says_so_rather_than_pretending_to_have_looked():
    weft, _kr, _author = _world()
    out = discovery.discover(Weave.fold(weft), "summarise a document", threshold=100)
    assert out["action"] == discovery.FORGE
    assert out["mode"] == discovery.EMPTY
    assert out["matches"] == []


# ── the gate: an exact content-token overlap, not vibes ──────────────────────
def test_a_stopword_only_overlap_never_clears_the_bar():
    weft, _kr, author = _world()
    _organ(weft, author, intent="summarise a document into a short abstract", name="summarise")

    out = discovery.discover(Weave.fold(weft), "what is the of and to", threshold=100)

    assert out["action"] == discovery.FORGE
    assert out["matches"] == [], "sharing only function words is not a match"


def test_the_threshold_is_a_bar_and_raising_it_refuses_a_weak_match():
    weft, _kr, author = _world()
    _organ(weft, author, intent="summarise a document into a short abstract", name="summarise")
    weave = Weave.fold(weft)

    low = discovery.discover(weave, "summarise", threshold=100)
    high = discovery.discover(weave, "summarise", threshold=10**6)
    assert low["action"] == discovery.USE
    assert high["action"] == discovery.FORGE
    assert high["matches"], "the match is still REPORTED; it just did not clear the bar"


@pytest.mark.parametrize("bad", [1.5, True, "100", None])
def test_a_non_integer_threshold_is_refused(bad):
    weft, _kr, _author = _world()
    with pytest.raises(ValueError, match="plain int"):
        discovery.discover(Weave.fold(weft), "anything", threshold=bad)


# ── determinism: the same fold ranks identically, forever ────────────────────
def test_ranking_is_byte_identical_across_two_runs_over_the_same_fold():
    weft, _kr, author = _world()
    _organ(weft, author, intent="summarise a document into a short abstract", name="summarise")
    _organ(
        weft,
        author,
        intent="count the words in a document",
        name="wordcount",
        source="def main(text):\n    return len(str(text).split())\n",
    )
    weave = Weave.fold(weft)

    first = [m.as_dict() for m in discovery.rank(weave, "document summary", limit=5)]
    second = [m.as_dict() for m in discovery.rank(Weave.fold(weft), "document summary", limit=5)]

    assert first == second
    assert first, "the fixture must actually rank something (else this passes vacuously)"
    assert all(isinstance(m["score"], int) for m in first)
    assert all(not isinstance(m["score"], bool) for m in first)


def test_a_retracted_capability_leaves_the_catalogue():
    weft, _kr, author = _world()
    cap = _organ(
        weft, author, intent="summarise a document into a short abstract", name="summarise"
    )
    assert discovery.rank(Weave.fold(weft), "summarise a document")

    lifecycle.revoke(weft, author, cap)

    assert discovery.rank(Weave.fold(weft), "summarise a document") == []
    assert (
        discovery.discover(Weave.fold(weft), "summarise a document", threshold=100)["action"]
        == discovery.FORGE
    )


# ── an unbound executor fails closed and installs no stub ────────────────────
def test_a_network_organ_is_reported_not_executable_rather_than_awaiting_approval():
    """ "Requires approval" would be a lie: there is no egress-mediating worker profile, so
    nothing an operator can click makes this run. Design Decision 2."""
    weft, _kr, author = _world()
    cap = _organ(
        weft,
        author,
        intent="fetch a page from the network and summarise it",
        name="fetch_and_summarise",
        tier=anchors.NETWORK,
        source="def main(url):\n    return str(url)\n",
    )
    before = weft.count()

    out = discovery.discover(Weave.fold(weft), "fetch a page from the network", threshold=100)

    assert out["action"] == discovery.USE and out["capability"] == cap
    assert out["executable"] is False
    assert "no executor" in out["note"] and "approve" in out["note"]
    # Nothing was bound to make it runnable: Nona declares exactly one effect handler, and
    # the tier has no worker profile at all.
    assert anchors.NETWORK not in executor.TIER_PROFILES
    assert set(executor.organ_effects(weft, Weave.fold(weft))) == {executor.GENERATED_CODE}
    assert weft.count() == before


def test_a_pure_organ_is_executable_and_says_nothing_alarming():
    weft, _kr, author = _world()
    _organ(weft, author, intent="summarise a document into a short abstract", name="summarise")
    out = discovery.discover(Weave.fold(weft), "summarise a document", threshold=100)
    assert out["executable"] is True
    assert out["note"] == ""


def test_a_quarantined_organ_is_reported_as_quarantined_not_hidden():
    """A quarantined organ is still in the catalogue — hiding it would make the operator
    forge a duplicate of something that merely needs promoting."""
    weft, _kr, author = _world()
    _organ(weft, author, intent="summarise a document into a short abstract", name="summarise")
    out = discovery.discover(Weave.fold(weft), "summarise a document", threshold=100)
    assert out["quarantined"] is True


# ── the catalogue text: what actually gets ranked ────────────────────────────
def test_a_capability_is_ranked_by_its_intent_not_only_by_its_opaque_name():
    """Goals are phrased in intent language; organ names are content-addressed noise. If the
    candidate's intent were not in the indexed text, nothing would ever be found."""
    weft, _kr, author = _world()
    cap = _organ(weft, author, intent="deduplicate rows in a csv export", name="organ:opaque")
    weave = Weave.fold(weft)
    cell = weave.get(cap)
    assert cell is not None
    text = discovery.catalogue_text(weave, cell)
    assert "deduplicate" in text and "csv" in text

    out = discovery.discover(weave, "deduplicate csv rows", threshold=100)
    assert out["action"] == discovery.USE and out["capability"] == cap


def test_a_capability_with_no_candidate_still_ranks_on_its_own_fields():
    weft, kr, author = _world()
    model.assert_content(
        weft,
        author,
        "capability:handwritten",
        "capability",
        capability.capability_content(
            name="ledger_transfer",
            effect="transform",
            grantee=kr.mint("bookkeeper", "operator").id,
            granter=author,
        ),
    )
    out = discovery.discover(Weave.fold(weft), "ledger_transfer", threshold=100)
    assert out["action"] == discovery.USE
    assert out["capability"] == "capability:handwritten"
    # Not a generated organ, so Nona claims nothing about how it runs.
    assert out["executable"] is True
