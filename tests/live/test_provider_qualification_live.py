"""LIVE bounded model-provider qualification — operator-gated (marker: live_provider).

This suite is SKIPPED unless the operator supplies a real provider configuration via
environment variables (NAMES documented in docs/operations/model-configuration.md;
values NEVER committed). Normal CI never runs it: collection succeeds with no key
(the module imports cleanly and every test is skipped at setup), so the standard
gate needs no live credential.

It drives the IDENTICAL harness functions the offline suite proves
(`tests/live/harness.py`) against ONE already-supported provider through a real
OpenAI-compatible transport, with the credential applied by a broker at call time.

Reproduce (names only — supply your own values):

    DECIMA_LIVE_PROVIDER=cloud \\
    DECIMA_LIVE_MODEL=<model-id> \\
    DECIMA_LIVE_BASE_URL=<https://endpoint> \\
    DECIMA_LIVE_API_KEY=<the-secret-value> \\
    PYTHONPATH="$TESTENV:$PWD" python3 -m pytest -m live_provider tests/live -v

For a purely local endpoint (no credential leaves the box):

    DECIMA_LIVE_PROVIDER=local DECIMA_LIVE_MODEL=<model> \\
    DECIMA_LIVE_BASE_URL=http://127.0.0.1:8080 \\
    PYTHONPATH="$TESTENV:$PWD" python3 -m pytest -m live_provider tests/live -v
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from tests.live import harness

pytestmark = pytest.mark.live_provider

_EVIDENCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs"
    / "release-evidence"
    / "models"
    / "live-qualification.json"
)


def _config_or_skip() -> dict:
    kind = os.environ.get(harness.ENV_PROVIDER)
    model = os.environ.get(harness.ENV_MODEL)
    base = os.environ.get(harness.ENV_BASE_URL)
    if not (kind and model and base):
        pytest.skip(
            "no live provider configured — set "
            f"{harness.ENV_PROVIDER}/{harness.ENV_MODEL}/{harness.ENV_BASE_URL} "
            f"(and {harness.ENV_API_KEY} for a cloud provider) to run"
        )
    if kind == "cloud" and not os.environ.get(harness.ENV_API_KEY):
        pytest.skip(f"cloud provider requires {harness.ENV_API_KEY} (a secret NAME→value)")
    timeout = int(os.environ.get(harness.ENV_TIMEOUT, "30"))
    return {"kind": kind, "model": model, "base": base, "timeout": timeout}


@pytest.fixture()
def live(tmp_path):
    cfg = _config_or_skip()
    log = harness.CaptureLog()
    reg, broker = harness.build_live_registry(
        cfg["kind"], cfg["model"], cfg["base"], log, cfg["timeout"]
    )
    return {"cfg": cfg, "reg": reg, "broker": broker, "log": log}


def test_live_connectivity_and_routing(live):
    out = harness.check_connectivity_and_routing(live["reg"], model=live["cfg"]["model"])
    assert out["qualification"]["model"] == live["cfg"]["model"]
    # the credential (if any) never appears in the transport log.
    key = os.environ.get(harness.ENV_API_KEY)
    if key:
        assert not live["log"].contains_secret(key)
        assert not any(key in ln for ln in live["log"].redacted())


def test_live_structured_proposal_is_validated_not_invoked(live):
    out = harness.check_structured_proposal(
        live["reg"], model=live["cfg"]["model"], expect_valid=False
    )
    # whether the model returns valid or invalid structure, NOTHING is auto-invoked;
    # a valid proposal is inert, an invalid one is rejected/bounded-corrected.
    assert "ok" in out


def test_live_budget_enforcement_blocks_after_one_call(live):
    inspector = harness.check_budget_enforcement(live["reg"], model=live["cfg"]["model"])
    assert inspector["calls_made"] == 1
    assert inspector["exhausted"] is True


def test_live_invalid_credential_is_surfaced_no_secret_leak(live):
    if live["cfg"]["kind"] != "cloud":
        pytest.skip("invalid-credential path only applies to a cloud provider")
    log = harness.CaptureLog()
    backend = harness.http_openai_backend(log=log, timeout_s=live["cfg"]["timeout"])
    bad_broker = harness.EnvSecretBroker(store={harness.ENV_API_KEY: "INVALID-CREDENTIAL"})
    from decima.models.providers import CloudProvider, ModelRequest

    prov = CloudProvider(
        model=live["cfg"]["model"],
        secret_name=harness.ENV_API_KEY,
        broker=bad_broker,
        backend=backend,
    )
    resp = prov.complete(ModelRequest(prompt=harness.SYNTHETIC_PROMPT, purpose="summarize"))
    # an invalid credential must be SURFACED as an error, never widen authority.
    assert resp.failed, "invalid credential must surface as a model error"
    assert not log.contains_secret("INVALID-CREDENTIAL")
    assert not any("INVALID-CREDENTIAL" in ln for ln in log.redacted())


def test_live_emit_evidence(live):
    routing_out = harness.check_connectivity_and_routing(live["reg"], model=live["cfg"]["model"])
    summary = {
        "lane": "WS3 live model-provider bounded qualification",
        "path": f"LIVE via {live['cfg']['kind']} provider",
        "model": live["cfg"]["model"],
        "connectivity_routing": routing_out["qualification"],
        "env_var_names": harness.ENV_VARS,  # NAMES ONLY
    }
    _EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    _EVIDENCE.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    key = os.environ.get(harness.ENV_API_KEY)
    if key:
        assert key not in _EVIDENCE.read_text()
