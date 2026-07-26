"""Nona N3 part 2: the evaluation stages that feed the gate.

The stage worth the most scrutiny is the RUG-PULL scan, because it is the only one that can
refuse a candidate whose behaviour is flawless. Cases show what code did on inputs someone
chose; imports show what it is ABLE to do. A loop that only tested behaviour would happily
promote a "pure" function that imports `socket`.
"""

from __future__ import annotations

from typing import Any

from decima.services.nona import stages
from decima.services.nona.reckoner import HIGH, Metrics, gate

_PURE_SRC = "def main(a, b):\n    return a + b\n"


def _rules(findings: list[dict[str, Any]]) -> set[str]:
    return {f["rule"] for f in findings}


# ── stage 6: the rug-pull check ──────────────────────────────────────────────
def test_a_genuinely_pure_candidate_scans_clean():
    assert stages.scan_source(_PURE_SRC, "pure") == []


def test_a_pure_candidate_importing_socket_is_a_high_rug_pull_finding():
    """The headline property: behaviour can be perfect and the candidate still refused,
    because it asked for reach it did not declare."""
    src = "import socket\n\ndef main(a, b):\n    return a + b\n"
    found = stages.scan_source(src, "pure")
    assert "scan.rug_pull" in _rules(found)
    assert all(f["severity"] == HIGH for f in found)


def test_the_same_import_is_acceptable_when_the_tier_declares_it():
    """The scan compares reach against the DECLARED tier — it is not a blanket import ban.
    A candidate that admits it needs the network is judged on that basis."""
    src = "import socket\n\ndef main(a, b):\n    return a + b\n"
    assert stages.scan_source(src, "network") == []


def test_subprocess_in_a_read_only_candidate_is_a_rug_pull():
    src = "import subprocess\n\ndef main(x):\n    return x\n"
    assert "scan.rug_pull" in _rules(stages.scan_source(src, "read_only"))


def test_dynamic_execution_is_high_at_every_tier():
    """`eval`/`exec` defeat the scan itself: code assembled at run time cannot be
    characterised by reading it, so it is refused even for the most permissive tier."""
    src = "def main(s):\n    return eval(s)\n"
    for tier in ("pure", "read_only", "network", "financial"):
        assert "scan.dynamic_execution" in _rules(stages.scan_source(src, tier)), tier


def test_unparseable_source_is_high():
    """What cannot be read cannot be cleared."""
    assert "scan.unparseable" in _rules(stages.scan_source("def main(:\n", "pure"))


def test_a_from_import_is_caught_too():
    src = "from socket import socket as s\n\ndef main(a):\n    return a\n"
    assert "scan.rug_pull" in _rules(stages.scan_source(src, "pure"))


# ── stage 5: seeded, replayable property inputs ──────────────────────────────
def test_property_inputs_are_seeded_from_cell_data_and_replay_identically():
    """Unseeded fuzzing would make every evaluation unreplayable and quietly break Law 5
    for the whole promotion path."""
    suite = {"reproducibility": {"seed": 42}}
    assert stages.property_inputs(suite) == stages.property_inputs(suite)


def test_a_different_seed_explores_different_inputs():
    a = stages.property_inputs({"reproducibility": {"seed": 1}})
    b = stages.property_inputs({"reproducibility": {"seed": 2}})
    assert a != b


# ── stage 1: contract ────────────────────────────────────────────────────────
def test_a_candidate_without_an_output_schema_is_flagged():
    """ "It returned something" is not a pass."""
    found = stages.validate_contract({"declared_effect_class": "pure"}, {"cases": []})
    assert "contract.no_output_schema" in _rules(found)


def test_an_unknown_tier_is_a_high_contract_finding():
    found = stages.validate_contract({"declared_effect_class": "root"}, {"cases": []})
    assert "contract.unknown_effect_class" in _rules(found)
    assert any(f["severity"] == HIGH for f in found)


# ── stages 2/3/4: tallying cases honestly ────────────────────────────────────
def _suite(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {"cases": cases}


def test_a_clean_run_tallies_passes():
    suite = _suite(
        [
            {"in": [1, 2], "out": 3},
            {"in": [2, 2], "out": 4},
            {"origin": "baseline", "adversarial": True, "in": ["../etc/passwd"]},
        ]
    )

    def runner(case: dict[str, Any]) -> dict[str, Any]:
        if case.get("adversarial"):
            return {"status": "FAILED", "contained": True}
        return {"status": "SUCCEEDED", "output": case["out"]}

    metrics, findings = stages.run_cases(suite, runner=runner)
    assert (metrics.deterministic_cases, metrics.deterministic_pass) == (2, 2)
    assert (metrics.hostile_cases, metrics.hostile_contained) == (1, 1)
    assert findings == []
    assert gate(metrics).eligible is True


def test_an_unknown_case_is_counted_and_never_passes():
    """A backstop-killed run means we do not know; the gate must refuse."""
    suite = _suite([{"in": [1], "out": 1}, {"origin": "baseline", "adversarial": True, "in": []}])

    def runner(case: dict[str, Any]) -> dict[str, Any]:
        return {"status": "UNKNOWN"}

    metrics, _ = stages.run_cases(suite, runner=runner)
    assert metrics.unknown_outcomes == 2
    assert gate(metrics).eligible is False


def test_a_runner_exception_is_an_errored_case_not_a_crash():
    suite = _suite([{"in": [1], "out": 1}])

    def runner(case: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("effect blew up")

    metrics, findings = stages.run_cases(suite, runner=runner)
    assert metrics.errored_cases == 1
    assert "case.errored" in _rules(findings)
    assert gate(metrics).eligible is False


def test_an_adversarial_case_that_SUCCEEDS_is_a_containment_failure():
    """Inverted on purpose: for an attack, "it worked" is the bad outcome."""
    suite = _suite([{"in": [1], "out": 1}, {"origin": "baseline", "adversarial": True, "in": []}])

    def runner(case: dict[str, Any]) -> dict[str, Any]:
        if case.get("adversarial"):
            return {"status": "SUCCEEDED", "output": "escaped"}
        return {"status": "SUCCEEDED", "output": case["out"]}

    metrics, findings = stages.run_cases(suite, runner=runner)
    assert metrics.hostile_contained == 0
    assert "containment.escaped" in _rules(findings)
    assert gate(metrics).eligible is False


def test_a_candidate_authored_attack_is_recorded_but_never_counted():
    suite = _suite(
        [
            {"in": [1], "out": 1},
            {"origin": "candidate", "adversarial": True, "in": []},
        ]
    )

    def runner(case: dict[str, Any]) -> dict[str, Any]:
        return {"status": "SUCCEEDED", "output": case.get("out")}

    metrics, findings = stages.run_cases(suite, runner=runner)
    assert metrics.hostile_cases == 0, "a self-authored attack must not count as evaluation"
    assert "suite.candidate_authored_attack" in _rules(findings)


def test_the_default_runner_refuses_rather_than_pretending():
    """No accidental no-op evaluation: without an injected runner nothing is 'evaluated'."""
    suite = _suite([{"in": [1], "out": 1}])
    metrics, findings = stages.run_cases(suite)
    assert metrics.errored_cases == 1
    assert gate(metrics).eligible is False


# ── findings are counted once, over every stage ──────────────────────────────
def test_high_findings_are_retallied_over_the_full_evidence_set():
    """A HIGH from the SCAN must not be lost behind a clean case run — the gate reads one
    number for "did anything serious turn up", so it is counted once over everything."""
    clean = Metrics(
        deterministic_cases=1,
        deterministic_pass=1,
        hostile_cases=1,
        hostile_contained=1,
    )
    assert gate(clean).eligible is True

    scan = stages.scan_source("import socket\ndef main(a):\n    return a\n", "pure")
    combined = stages.with_findings(clean, stages.merge_findings(scan))
    assert combined.high_findings == 1
    assert gate(combined).eligible is False
