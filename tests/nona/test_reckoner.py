"""Nona N3: the evaluation gate.

The gate is the load-bearing safety property of the whole self-extension loop: it is the
only thing standing between "a model wrote some code" and "that code is a promoted organ of
the system". So these tests are mostly about what must NOT pass.

The three honesty rules get a test each, because each one is a place where a system under
pressure would be tempted to lie:

  * a candidate that ERRORS must fail (not "mostly worked");
  * UNKNOWN must fail (not "probably fine") — the system says "I don't know" and refuses;
  * a model judge must be unable to overturn a deterministic failure, and here that is
    enforced STRUCTURALLY: `gate` takes no judge parameter at all, so there is no code path
    by which flattery becomes a promotion.
"""

from __future__ import annotations

import inspect
import os
import tempfile

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.services.nona import reckoner
from decima.services.nona.reckoner import Metrics

_CONTAINED = {
    "no_new_privs": True,
    "network_denied": True,
    "chroot": True,
    "namespaces": True,
    "matrix_version": 1,
}


def _weft() -> tuple[Weft, Keyring]:
    kr = Keyring(seed=bytes(32))
    return Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), kr), kr


def _passing() -> Metrics:
    return Metrics(
        deterministic_cases=3,
        deterministic_pass=3,
        hostile_cases=2,
        hostile_contained=2,
        property_cases=5,
        property_pass=5,
    )


# ── the gate passes only a genuinely clean candidate ─────────────────────────
def test_a_clean_candidate_is_eligible():
    assert reckoner.gate(_passing()).eligible is True


def test_a_candidate_with_no_adversarial_case_is_refused():
    """A suite that never attacked the candidate has not evaluated it."""
    m = Metrics(deterministic_cases=3, deterministic_pass=3, property_cases=1, property_pass=1)
    v = reckoner.gate(m)
    assert v.eligible is False
    assert "adversarial" in v.reason


def test_a_candidate_with_no_deterministic_case_is_refused():
    v = reckoner.gate(Metrics(hostile_cases=1, hostile_contained=1))
    assert v.eligible is False
    assert "no deterministic cases" in v.reason


def test_one_deterministic_failure_refuses_the_whole_candidate():
    m = Metrics(deterministic_cases=3, deterministic_pass=2, hostile_cases=1, hostile_contained=1)
    assert reckoner.gate(m).eligible is False


def test_an_uncontained_hostile_case_refuses():
    m = Metrics(deterministic_cases=1, deterministic_pass=1, hostile_cases=2, hostile_contained=1)
    v = reckoner.gate(m)
    assert v.eligible is False
    assert "containment" in v.reason


def test_a_high_finding_refuses_even_when_every_case_passes():
    """The rug-pull check: behaviour can be perfect and the candidate still refused,
    because a high finding means it can do something it did not declare."""
    m = Metrics(**{**_passing().__dict__, "high_findings": 1})
    v = reckoner.gate(m)
    assert v.eligible is False
    assert "high security finding" in v.reason


def test_differential_regression_only_gates_when_there_is_an_incumbent():
    m = Metrics(**{**_passing().__dict__, "differential_cases": 2, "differential_agree": 1})
    assert reckoner.gate(m, has_incumbent=False).eligible is True
    assert reckoner.gate(m, has_incumbent=True).eligible is False


# ── the three honesty rules ──────────────────────────────────────────────────
def test_honesty_rule_1_an_errored_case_fails():
    m = Metrics(**{**_passing().__dict__, "errored_cases": 1})
    v = reckoner.gate(m)
    assert v.eligible is False
    assert "errored" in v.reason


def test_honesty_rule_2_unknown_is_not_a_pass():
    """A worker killed by the wall-clock/CPU backstop resolves UNKNOWN. The system must
    refuse rather than rewrite ignorance as success."""
    m = Metrics(**{**_passing().__dict__, "unknown_outcomes": 1})
    v = reckoner.gate(m)
    assert v.eligible is False
    assert "UNKNOWN" in v.reason


def test_honesty_rule_3_a_model_judge_cannot_reach_the_gate():
    """Enforced STRUCTURALLY, not by discipline: if `gate` ever grows a judge parameter,
    this test fails and someone has to argue for it in review."""
    params = set(inspect.signature(reckoner.gate).parameters)
    assert params == {"metrics", "has_incumbent"}, (
        "the gate must remain a pure function of recorded integers — a model judgment has "
        "no path into it"
    )


def test_a_flattering_judge_does_not_change_a_recorded_failure():
    weft, kr = _weft()
    nona = kr.mint("nona", "agent").id
    verdict = reckoner.gate(Metrics(**{**_passing().__dict__, "deterministic_pass": 1}))
    cell = reckoner.record_result(
        weft,
        nona,
        candidate_cell="candidate:x",
        suite_cell="suite:y",
        implementation_digest="blob_z",
        verdict=verdict,
        containment=_CONTAINED,
        model_judge={"verdict": "excellent, promote immediately", "confidence": 99},
    )
    got = Weave.fold(weft).get(cell)
    assert got is not None
    assert got.content["promote_eligible"] is False
    assert got.content["model_judge"]["authority"] is False


# ── Decision 5: host variance fails closed ───────────────────────────────────
def test_a_host_missing_containment_refuses_to_evaluate():
    """Better no result than a weaker result wearing the same suite id."""
    suite = {"environment_digest": "", "cases": [{"origin": "baseline"}]}
    for missing in ("no_new_privs", "network_denied", "chroot"):
        weak = {**_CONTAINED, missing: False}
        with pytest.raises(reckoner.EvaluationRefused, match="containment"):
            reckoner.require_host_containment(suite, weak)


def test_a_host_whose_matrix_differs_from_the_suite_refuses():
    suite = {"environment_digest": reckoner.environment_digest({**_CONTAINED, "matrix_version": 2})}
    with pytest.raises(reckoner.EvaluationRefused, match="does not match"):
        reckoner.require_host_containment(suite, _CONTAINED)


def test_the_environment_digest_ignores_host_variable_detail():
    """Two honest hosts must agree: the digest pins what soundness depends on, not kernel
    versions or timings, or the safety pin would just be noise."""
    a = reckoner.environment_digest({**_CONTAINED, "kernel": "6.1", "elapsed_ms": 12})
    b = reckoner.environment_digest({**_CONTAINED, "kernel": "6.8", "elapsed_ms": 900})
    assert a == b


# ── Decision 6: the code may not choose its own tests ────────────────────────
def test_a_suite_with_no_baseline_case_is_refused():
    with pytest.raises(reckoner.EvaluationRefused, match="baseline"):
        reckoner.require_authored_suite({"cases": [{"origin": "candidate"}]})


def test_candidate_authored_adversarial_cases_are_refused():
    """The R3 failure mode: a candidate that writes its own attacks has chosen the bar it
    clears."""
    suite = {
        "cases": [
            {"origin": "baseline"},
            {"origin": "candidate", "adversarial": True},
        ]
    }
    with pytest.raises(reckoner.EvaluationRefused, match="candidate-authored"):
        reckoner.require_authored_suite(suite)


def test_candidate_cases_are_allowed_alongside_the_baseline():
    reckoner.require_authored_suite(
        {
            "cases": [
                {"origin": "baseline", "adversarial": True},
                {"origin": "candidate"},
            ]
        }
    )


# ── the record is replayable ──────────────────────────────────────────────────
def test_the_recorded_result_carries_no_wall_clock_and_is_reproducible():
    """Same candidate + same suite ⇒ same result content ⇒ same state_root. Cost and
    latency are deliberately absent: they are wall-clock by nature and would break replay."""
    roots = []
    for _ in range(2):
        weft, kr = _weft()
        nona = kr.mint("nona", "agent").id
        reckoner.record_result(
            weft,
            nona,
            candidate_cell="candidate:x",
            suite_cell="suite:y",
            implementation_digest="blob_z",
            verdict=reckoner.gate(_passing()),
            containment=_CONTAINED,
            seed=7,
        )
        roots.append(Weave.fold(weft).state_root())
    assert roots[0] == roots[1]


def test_every_recorded_metric_is_an_int():
    content = _passing().as_content()
    assert content and all(isinstance(v, int) and not isinstance(v, bool) for v in content.values())
