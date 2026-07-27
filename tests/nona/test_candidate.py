"""Nona N2: candidates and evaluation suites — the half that cannot do anything.

The properties worth pinning here are all about RESTRAINT: proposing a candidate executes
nothing, generation never happens implicitly, the implementation is DATA bound by a digest,
and the DRAFT→QUARANTINED transition is provenance rather than an edited row.

This file also closes the loop N1 opened but did not prove: `capability.py` denies a
quarantined capability to any agent whose Cell lacks `sandbox: True`, and until N1 no
shipping path wrote that field — so quarantine meant "unrunnable, full stop" and evaluation
was impossible in-band. The last two tests assert both directions of that gate.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from decima.kernel import capability, model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.services.nona import anchors, candidate

_SRC = "def main(a, b):\n    return a + b\n"


def _weft() -> tuple[Weft, Keyring]:
    kr = Keyring(seed=bytes(32))
    return Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), kr), kr


# ── proposing executes nothing ───────────────────────────────────────────────
def test_codegen_is_not_available_by_default():
    """There is deliberately NO default path from an intent to generated source: an
    offline install fails closed instead of quietly acquiring a model dependency."""
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    with pytest.raises(candidate.CodegenUnavailable):
        candidate.propose_candidate(weft, nona, intent="add two ints", declared_effect_class="pure")


def test_codegen_is_an_injected_seam():
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    out = candidate.propose_candidate(
        weft,
        nona,
        intent="add two ints",
        declared_effect_class="pure",
        codegen=lambda intent: _SRC,
    )
    assert out["implementation_digest"] == candidate.implementation_digest(_SRC)


def test_proposing_does_not_execute_the_source():
    """The candidate's source is never imported, compiled or exec'd — source that would
    raise on import is still perfectly proposable, because it is only ever DATA."""
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    hostile = "raise SystemExit('this must never run')\n"
    out = candidate.propose_candidate(
        weft, nona, intent="hostile", declared_effect_class="pure", source=hostile
    )
    cell = Weave.fold(weft).get(out["cell"])
    assert cell is not None
    assert cell.content["source"] == hostile
    assert cell.content["source_is_data"] is True


def test_proposing_mints_no_capability():
    """Proposal grants nothing: the only cell written is the candidate itself."""
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    candidate.propose_candidate(weft, nona, intent="add", declared_effect_class="pure", source=_SRC)
    types = {c.type for c in Weave.fold(weft).cells.values()}
    assert types == {candidate.CANDIDATE}


# ── born quarantined, as provenance ──────────────────────────────────────────
def test_a_candidate_is_born_quarantined_with_the_baseline():
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    out = candidate.propose_candidate(
        weft, nona, intent="add", declared_effect_class="pure", source=_SRC
    )
    cell = Weave.fold(weft).get(out["cell"])
    assert cell is not None
    assert cell.content["lifecycle"] == candidate.QUARANTINED
    assert cell.content["quarantine"] == candidate.QUARANTINE_BASELINE


def test_the_draft_to_quarantined_transition_is_provenance_not_an_edit():
    """TWO signed events land on ONE content-addressed cell, so "was this ever a draft,
    and who moved it?" is answerable from the log rather than lost to an overwrite."""
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    out = candidate.propose_candidate(
        weft, nona, intent="add", declared_effect_class="pure", source=_SRC
    )
    cell = Weave.fold(weft).get(out["cell"])
    assert cell is not None
    assert cell.content["states"] == [candidate.DRAFT, candidate.QUARANTINED]
    assert len(cell.provenance) == 2, "the transition must be two events, not one edited row"


def test_identical_source_and_intent_is_the_same_candidate():
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    a = candidate.propose_candidate(
        weft, nona, intent="add", declared_effect_class="pure", source=_SRC
    )
    b = candidate.propose_candidate(
        weft, nona, intent="add", declared_effect_class="pure", source=_SRC
    )
    assert a["cell"] == b["cell"]


def test_an_unknown_effect_class_is_refused():
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    with pytest.raises(ValueError, match="unknown effect class"):
        candidate.propose_candidate(
            weft, nona, intent="x", declared_effect_class="root", source=_SRC
        )


def test_the_digest_changes_with_the_source():
    """The binding every later stage relies on: evaluate a digest, promote a digest, refuse
    a mismatch — so the code that was tested is the code that runs."""
    assert candidate.implementation_digest(_SRC) != candidate.implementation_digest(_SRC + "#x\n")


# ── suites are integer-gated data ────────────────────────────────────────────
def test_a_suite_is_versioned_content_addressed_data():
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    out = candidate.declare_suite(
        weft,
        nona,
        subject_schema={"args": ["int", "int"]},
        cases=[{"in": [1, 2], "out": 3}],
        thresholds={"pass_rate_pct": 100},
        version=2,
    )
    cell = Weave.fold(weft).get(out["cell"])
    assert cell is not None
    assert cell.type == candidate.EVALUATION_SUITE
    assert cell.content["version"] == 2
    assert cell.content["thresholds"] == {"pass_rate_pct": 100}


def test_a_float_threshold_is_refused():
    """Determinism is load-bearing: thresholds ride in signed content, so a gate is an
    integer comparison and never a floating-point tolerance."""
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    with pytest.raises(ValueError, match="plain ints"):
        candidate.declare_suite(
            weft,
            nona,
            subject_schema={},
            cases=[],
            # Deliberately ill-typed: the point is that the RUNTIME check refuses a float
            # even when a caller bypasses the int-typed contract, so the annotation is not
            # the only thing standing between a float and signed content.
            thresholds={"pass_rate_pct": 99.5},  # type: ignore[dict-item]
        )


# ── the gate N1 unlocked: quarantine is runnable ONLY in a sandbox ───────────
_CAP = "cap:candidate-organ"


def _quarantined_cap(weft: Weft, root: str, grantee: str) -> str:
    """A quarantined candidate capability, exactly as Nona will mint one in N4 — including
    the `grantee` it is issued TO. That field is not decoration in a fixture: a grant naming
    nobody is now refused for everybody (`DenialCode.NO_GRANTEE`), so a helper that omitted it
    would make the sandbox test below pass on a denial that has nothing to do with the sandbox
    and everything to do with a malformed fixture."""
    model.assert_content(
        weft,
        root,
        _CAP,
        "capability",
        {
            "effect": "generated_code",
            "declared_effect_class": anchors.PURE,
            "quarantined": True,
            "parent": None,
            "grantee": grantee,
            "granter": root,
            "caveats": dict(candidate.QUARANTINE_BASELINE),
        },
    )
    return _CAP


def test_a_quarantined_capability_is_denied_to_an_ordinary_agent():
    """The cap IS in the agent's envelope, so the refusal can only be about quarantine —
    which is what makes this assertion about the gate rather than about scoping."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    cap = _quarantined_cap(weft, root, grantee=root)
    aid = cells.create_agent(
        weft, root, objective="real work", principal=root, capability_grant_ids=[cap]
    )

    weave = Weave.fold(weft)
    agent = weave.get(aid)
    assert agent is not None
    allowed, _reason, code = capability.authorize_detail(weave, agent, cap, {}, root)
    assert allowed is False
    assert code == capability.DenialCode.QUARANTINED


def test_a_quarantined_capability_is_reachable_by_a_sandbox_agent():
    """The positive control for N1's `sandbox` field: it is exactly the flag
    `capability.py:197` reads, so a candidate can finally be evaluated IN-BAND instead of
    being unrunnable full stop. Whatever else may gate this invocation, quarantine no
    longer does."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    sandbox_pid = kr.mint(anchors.SANDBOX_NAME, "reckoner").id
    cap = _quarantined_cap(weft, root, grantee=sandbox_pid)
    aid = cells.create_agent(
        weft,
        root,
        objective="evaluate",
        principal=sandbox_pid,
        capability_grant_ids=[cap],
        sandbox=True,
    )

    weave = Weave.fold(weft)
    agent = weave.get(aid)
    assert agent is not None
    allowed, reason, code = capability.authorize_detail(weave, agent, cap, {}, sandbox_pid)
    # Asserted POSITIVELY rather than as `code != QUARANTINED`. The inequality is the vacuous
    # shape this repo has been bitten by: it is satisfied by ANY other denial, so it kept
    # passing while the fixture quietly stopped authorizing for an unrelated reason.
    assert (allowed, reason, code) == (True, "ok", capability.DenialCode.OK), (
        "a sandbox agent holding the grant must be authorized outright, not merely refused "
        "for some reason other than quarantine"
    )
