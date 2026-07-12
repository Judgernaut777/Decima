"""Non-live equivalent of the WS3 bounded provider qualification.

Runs in normal CI with NO credential and NO network. It drives the SAME harness
functions the live suite uses (`tests/live/harness.py`) against the reproducible
`DeterministicProvider` plus a synthetic `CloudProvider` whose transport is a local
recording stub. Proving these here proves the SHAPE of the logic the live suite runs;
only the transport differs.

It also emits a machine-readable evidence summary under
`docs/release-evidence/models/` so the release auditor can inspect what was checked.
"""

from __future__ import annotations

import json
import pathlib

from tests.live import harness

_EVIDENCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs"
    / "release-evidence"
    / "models"
    / "offline-qualification.json"
)


def _fresh_offline():
    broker = harness.EnvSecretBroker(store={harness.ENV_API_KEY: "OFFLINE-TEST-SECRET"})
    backend = harness.RecordingBackend(model="frontier-x", mode="ok", log=harness.CaptureLog())
    reg = harness.build_offline_registry(backend, broker)
    return reg, backend, broker


# ── connectivity / routing ─────────────────────────────────────────────────────
def test_connectivity_and_routing_records_full_decision():
    reg, backend, _ = _fresh_offline()
    out = harness.check_connectivity_and_routing(reg, model="on-host-7b")
    q = out["qualification"]
    # every field the charter requires a routing decision to make auditable:
    assert q["provider"] and q["model"]
    assert q["reason_codes"]
    assert isinstance(q["estimated_cost_microcents"], int)
    assert q["sensitivity_class"] == "public"
    assert q["routed"] is True


def test_cloud_task_routes_to_cloud_and_returns_through_abstraction():
    reg, backend, _ = _fresh_offline()
    reg.set_enabled("on-host-7b", False)  # force the cloud lane for this probe
    out = harness.check_connectivity_and_routing(reg, model="frontier-x")
    assert out["answered_by"] == "frontier-x"
    assert out["qualification"]["provider"] == "cloud"


# ── structured proposal ────────────────────────────────────────────────────────
def test_structured_proposal_valid_is_inert():
    reg, _, _ = _fresh_offline()
    out = harness.check_structured_proposal(reg, model="on-host-7b", expect_valid=True)
    assert out["ok"] is True


def test_malformed_structured_output_never_auto_invoked():
    # a cloud backend that always returns a schema-invalid proposal
    broker = harness.EnvSecretBroker(store={harness.ENV_API_KEY: "x"})
    backend = harness.RecordingBackend(model="frontier-x", mode="malformed")
    reg = harness.build_offline_registry(backend, broker)
    out = harness.check_structured_proposal(reg, model="frontier-x", expect_valid=False)
    assert out["ok"] is False
    assert out["attempts"] == 3, "bounded correction path, never an auto-invocation"


# ── budget enforcement ─────────────────────────────────────────────────────────
def test_small_budget_allows_one_then_blocks():
    reg, _, _ = _fresh_offline()
    inspector = harness.check_budget_enforcement(reg, model="on-host-7b")
    assert inspector["calls_made"] == 1
    assert inspector["exhausted"] is True
    assert inspector["remaining_tokens"] == 0


# ── privacy ────────────────────────────────────────────────────────────────────
def test_local_only_never_reaches_cloud_and_only_synthetic_transmits():
    reg, backend, _ = _fresh_offline()
    out = harness.check_privacy_local_only(reg, backend)
    assert out["local_only_reached_cloud"] is False
    assert out["synthetic_payloads"] >= 1


# ── failure / fallback (every mode surfaced, bounded, no secret leak) ───────────
# transport-level failures route through the bounded fallback; a malformed *structured*
# proposal is NOT a transport failure — it is handled by validation (tested above).
_TRANSPORT_FAILURES = (
    "invalid_credential",
    "timeout",
    "rate_limit",
    "unavailable",
    "malformed_transport",
)


def test_every_failure_mode_surfaces_and_falls_back_bounded():
    for mode in _TRANSPORT_FAILURES:
        out = harness.check_failure_fallback(mode)
        assert out["answered_by"] == "on-host-7b", f"{mode} must fall back to local"
        assert len(out["attempts"]) <= 3


# ── secret handling / redaction ────────────────────────────────────────────────
def test_secret_never_leaks_and_product_redactor_scrubs_logs():
    ev = harness.secret_redaction_evidence()
    assert ev["redacted_leaks"] == 0
    assert ev["secret_in_repr"] is False
    assert ev["broker_secret_names"] == [harness.ENV_API_KEY]


# ── emit the consolidated evidence summary (small JSON, committed) ──────────────
def test_emit_offline_evidence_summary():
    reg, backend, _ = _fresh_offline()
    routing_out = harness.check_connectivity_and_routing(reg, model="on-host-7b")
    reg2, backend2, _ = _fresh_offline()
    budget_out = harness.check_budget_enforcement(reg2, model="on-host-7b")
    reg3, backend3, _ = _fresh_offline()
    privacy_out = harness.check_privacy_local_only(reg3, backend3)
    failures = {m: harness.check_failure_fallback(m) for m in _TRANSPORT_FAILURES}
    redaction = harness.secret_redaction_evidence()

    summary = {
        "lane": "WS3 live model-provider bounded qualification",
        "path": "offline (deterministic + synthetic cloud stub; no network, no credential)",
        "live_call_status": "BLOCKED-pending-operator-credential",
        "env_var_names": harness.ENV_VARS,  # NAMES ONLY — never values
        "checks": {
            "connectivity_routing": routing_out["qualification"],
            "budget_enforcement": budget_out,
            "privacy": privacy_out,
            "failure_fallback": {
                m: [a["outcome"] for a in o["attempts"]] for m, o in failures.items()
            },
            "secret_redaction": redaction,
        },
    }
    _EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    _EVIDENCE.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    # sanity: no secret material of any kind is present in the emitted evidence.
    blob = _EVIDENCE.read_text()
    for needle in ("OFFLINE-TEST-SECRET", "TEST-SECRET-VALUE", "sk-live", "Bearer "):
        assert needle not in blob, f"evidence must not contain {needle!r}"
