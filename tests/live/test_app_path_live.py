"""LIVE-through-APP-PATH qualification — a REAL non-deterministic local model driven
through the SAME application path the Shell uses (marker: live_provider).

Unlike ``test_provider_qualification_live.py`` (which qualifies the models package in
isolation), this suite builds the REAL backend ``Application`` via
``server.build_application`` — whose ``CommandService.models`` is the env-configured
stack from ``models_setup.build_model_stack`` — and drives the ACTUAL product commands
end to end over the live provider:

  * ``RequestPlanProposal`` → the local model authors a structured plan; deterministic
    validation accepts a well-formed proposal or takes the bounded rejection path;
    the routing decision is recorded (model, reason codes, INT cost); the proposal is
    inert; ``AcceptPlanProposal`` alone mints the durable Plan/Steps deterministically.
  * ``AskGroundedQuestion`` over imported in-memory documents → a grounded live answer
    whose citations are validated against real segment Cells; the private task can
    NEVER select an external provider (a decoy external entry is present and rejected).
  * A malformed structured reply (forced by a tiny token budget truncating the JSON)
    is BOUNDED by validation — rejected and recorded, never repaired, never invoked.
  * No credential material appears anywhere durable (a local endpoint needs no key;
    its absence is asserted EXPLICITLY over logs, stream events, and every Cell).

Skipped cleanly without configuration — normal CI needs no endpoint. To run:

    DECIMA_LIVE_PROVIDER=local DECIMA_LIVE_MODEL=<model> \\
    DECIMA_LIVE_BASE_URL=http://127.0.0.1:8080 \\
    PYTHONPATH="$TESTENV:$PWD" python3 -m pytest -m live_provider \\
        tests/live/test_app_path_live.py -v

Fleet note: the env-configured stack keeps the deterministic provider catalogued as
the default (equal cost, alphabetical tie-break — proven in tests/api/
test_models_setup.py). This qualification applies the operator fleet preference
through the registry's OWN config API (``set_enabled``) so every product ask below is
genuinely served by the live model — same code path, different catalogue config,
exactly the operator knob the registry documents ("live-config flags").
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import platform
import urllib.error
import urllib.request

import pytest

from decima.kernel.weave import Weave
from decima.models.registry import ModelEntry
from decima.models.routing import ReasonCode
from decima.services.api import models_setup
from decima.services.api.auth import COOKIE_NAME
from decima.services.api.models_setup import DETERMINISTIC_MODEL
from decima.services.api.qa_service import UNGROUNDED_ANSWER
from decima.services.api.server import build_application

pytestmark = pytest.mark.live_provider

_EVIDENCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs" / "release-evidence" / "models" / "app-path-live-qualification.json"
)

_EXTERNAL_DECOY = "zz-external-decoy"

# An operator-authored objective. It is DATA (sent as the request context behind the
# untrusted preamble, never as instructions) — an operator who wants an acceptable
# plan naturally spells out the shape they want, so this doubles as the realistic
# happy-path ask AND keeps a temperature-0 local model inside the deterministic
# validator's strict bounds.
_OBJECTIVE = (
    "Prepare a short weekly reading digest from my imported articles. "
    "Desired plan shape: a flat JSON object whose top-level keys are exactly "
    "objective, summary, steps, risk, expected_approvals, model_budget, "
    "execution_budget (do NOT echo the schema, do NOT wrap anything in 'fields', "
    "omit 'kind' and 'strict'). Each step must be an object with string fields id "
    "(like 's1'), description, expected_output, agent, capability (only "
    "'local:derive' or 'local:note'), and a depends_on list of earlier step ids. "
    "Use risk 'low', empty expected_approvals, model_budget 4096, execution_budget 0."
)

_PORT_DOC = (
    "Aurora relay operations manual. The Aurora relay listens on port 7473 for "
    "inbound telemetry from field sensors. Operators must never expose the relay "
    "port beyond the station network."
)
_RETENTION_DOC = (
    "Aurora data policy. Telemetry received by the relay is retained for 30 days "
    "and then purged. Retention beyond 30 days requires a written waiver."
)
_QUESTION = "What port does the Aurora relay listen on?"


class _ListHandler(logging.Handler):
    def __init__(self, sink: list[str]) -> None:
        super().__init__(level=logging.DEBUG)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - passthrough
        try:
            self.sink.append(self.format(record))
        except Exception:
            self.sink.append(str(record.msg))


class _Client:
    """A minimal browser-shaped client over ``Application.dispatch`` (the identical
    deterministic core the loopback WSGI server serves) — session cookie + CSRF."""

    def __init__(self, app, pairing_secret: str) -> None:
        self.app = app
        self.pairing_secret = pairing_secret
        self.cookie = None
        self.csrf = None

    def login(self) -> None:
        r = self.app.dispatch(
            "POST", "/api/v1/session/login",
            body=json.dumps({"pairing_secret": self.pairing_secret}),
        )
        assert r.status == 200, r.json()
        set_cookie = [v for k, v in r.headers if k == "Set-Cookie"][0]
        token = set_cookie.split(";")[0].split("=", 1)[1]
        self.cookie = f"{COOKIE_NAME}={token}"
        self.csrf = r.json()["csrf"]

    def request(self, method: str, path: str, *, body=None, query=None):
        headers = {"cookie": self.cookie, "x-csrf-token": self.csrf}
        payload = None if body is None else json.dumps(body)
        return self.app.dispatch(method, path, headers=headers, body=payload, query=query)


def _config_or_skip() -> dict:
    kind = (os.environ.get(models_setup.ENV_PROVIDER) or "").strip().lower()
    model = (os.environ.get(models_setup.ENV_MODEL) or "").strip()
    base = (os.environ.get(models_setup.ENV_BASE_URL) or "").strip()
    if not (kind == "local" and model and base):
        pytest.skip(
            "no live LOCAL provider configured — set "
            f"{models_setup.ENV_PROVIDER}=local + {models_setup.ENV_MODEL} + "
            f"{models_setup.ENV_BASE_URL} to run the app-path qualification"
        )
    return {"kind": kind, "model": model, "base": base}


def _require_endpoint(base: str) -> None:
    """Configured-but-unreachable is a qualification FAILURE, not a skip."""
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/v1/models", timeout=10) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        pytest.fail(
            f"live provider configured at {base} but unreachable: {exc!r} — "
            "start the endpoint or unset the DECIMA_LIVE_* variables"
        )


def _fold(app) -> Weave:
    return Weave.fold(app.weft)


def _all_cell_text(app) -> str:
    weave = _fold(app)
    return json.dumps(
        {cid: {"type": c.type, "content": c.content} for cid, c in weave.cells.items()},
        sort_keys=True, default=str,
    )


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    cfg = _config_or_skip()
    _require_endpoint(cfg["base"])

    # A generous transport timeout for a CPU-served 30B model — env wins if set.
    prior_timeout = os.environ.get(models_setup.ENV_TIMEOUT)
    os.environ.setdefault(models_setup.ENV_TIMEOUT, "300")

    log_lines: list[str] = []
    handler = _ListHandler(log_lines)
    root = logging.getLogger()
    prior_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    db = str(tmp_path_factory.mktemp("live-app-path") / "weft.db")
    # THE application path: the same builder the loopback server runs; the command
    # service constructs its ModelStack from the process environment.
    app, identity = build_application(db, seed=bytes(32), secure_cookie=True)
    svc = app.commands

    # The env-configured stack picked up the REAL local provider (no injection).
    entry = svc.models.registry.get(cfg["model"])
    assert entry is not None, f"env-configured stack is missing {cfg['model']!r}"
    assert entry.provider == "local" and entry.local
    assert entry.privacy_class == "local_only"
    assert entry.est_cost_per_1k_microcents == 0

    # Operator fleet preference via the registry's own config API: serve everything
    # below with the live model (the deterministic default otherwise wins the
    # equal-cost alphabetical tie-break — see tests/api/test_models_setup.py).
    svc.models.registry.set_enabled(DETERMINISTIC_MODEL, False)

    # A decoy EXTERNAL catalogue entry (cheap, huge context, fully capable on paper):
    # if privacy enforcement ever regressed, routing would select it. No provider is
    # bound — being SELECTED would already be the failure, and any completion attempt
    # against it could only fail loudly.
    svc.models.registry.register(ModelEntry(
        provider="external", model=_EXTERNAL_DECOY, local=False,
        context_limit=1_000_000, modalities=("text", "code"),
        structured_output=True, tool_use=True,
        est_cost_per_1k_microcents=0, privacy_class="external",
    ))

    client = _Client(app, identity.pairing_secret)
    client.login()

    state: dict = {"passed": [], "notes": []}
    try:
        yield {
            "cfg": cfg, "app": app, "svc": svc, "client": client,
            "identity": identity, "logs": log_lines, "state": state,
        }
    finally:
        root.removeHandler(handler)
        root.setLevel(prior_level)
        if prior_timeout is None:
            os.environ.pop(models_setup.ENV_TIMEOUT, None)
        else:
            os.environ[models_setup.ENV_TIMEOUT] = prior_timeout


def _assert_routing_cell(live, routing_cell_id: str) -> dict:
    """The recorded ``model_routing`` Cell: live model selected, sensitivity honored
    (local-only), the external decoy explicitly rejected, cost an INT."""
    weave = _fold(live["app"])
    rc = weave.get(routing_cell_id)
    assert rc is not None and rc.type == "model_routing"
    content = rc.content
    assert content["selected_model"] == live["cfg"]["model"]
    assert ReasonCode.SENSITIVE_LOCAL_ONLY in content["reason_codes"]
    assert ReasonCode.SELECTED in content["reason_codes"]
    assert ReasonCode.LOCAL_AVAILABLE in content["reason_codes"]
    assert isinstance(content["estimated_cost"], int)
    rejected = {r["model"]: r["reason"] for r in content["rejected"]}
    assert rejected.get(_EXTERNAL_DECOY) == ReasonCode.SENSITIVE_LOCAL_ONLY, (
        "the private task must record the external candidate as REJECTED for privacy"
    )
    assert _EXTERNAL_DECOY not in (
        content["selected_model"], *content["fallback_models"]
    ), "an external provider must never serve (or back up) a local-only task"
    # The recorded decision is DATA: no credential-shaped key can even appear.
    assert not {"key", "api_key", "secret", "authorization", "token"} & {
        k.lower() for k in content
    }
    return content


# ── 1. RequestPlanProposal live → inert proposal → deterministic acceptance ───
def test_plan_proposal_live_then_deterministic_accept(live):
    client, app, cfg = live["client"], live["app"], live["cfg"]
    before = _fold(app)
    assert not before.of_type("plan") and not before.of_type("plan_step")
    assert not before.of_type("agent")

    proposal = None
    bounded_rejections = 0
    for _ in range(2):  # a bounded RE-ASK (a fresh command), never a repair
        r = client.request("POST", "/api/v1/plans/propose",
                           body={"objective": _OBJECTIVE, "token_budget": 900})
        if r.status == 201:
            proposal = r.json()["data"]
            break
        # The bounded correction path: deterministic validation REJECTED the live
        # output — recorded, surfaced, and nothing durable minted.
        assert r.status == 422, r.json()
        assert r.json().get("reason_code") == "INVALID_PROPOSAL"
        bounded_rejections += 1
        w = _fold(app)
        assert w.of_type("model_error"), "a rejection must be recorded, not swallowed"
        assert not w.of_type("plan") and not w.of_type("plan_step")
    assert proposal is not None, (
        f"live model produced no acceptable proposal in 2 attempts "
        f"({bounded_rejections} bounded rejections) — see recorded model_error cells"
    )

    # The LIVE model authored it, through the normal abstraction.
    assert proposal["model"] == cfg["model"]
    assert proposal["status"] == "PROPOSED"
    assert len(proposal["steps"]) >= 1
    for step in proposal["steps"]:
        assert step["capability"] in ("local:derive", "local:note")

    # The routing decision was recorded: provider=local, reason codes, INT cost.
    routing = _assert_routing_cell(live, proposal["routing_cell"])
    entry = live["svc"].models.registry.get(routing["selected_model"])
    assert entry.provider == "local" and entry.privacy_class == "local_only"

    # INERT: proposing minted no durable Plan/Step/Agent.
    mid = _fold(app)
    assert not mid.of_type("plan") and not mid.of_type("plan_step")
    assert not mid.of_type("agent")
    assert proposal["plan_id"] == "" and proposal["minted_step_ids"] == []

    # AcceptPlanProposal — the human decision — mints deterministically.
    r2 = client.request("POST", "/api/v1/plans/accept",
                        body={"proposal_id": proposal["id"]})
    assert r2.status == 201, r2.json()
    acceptance = r2.json()["data"]
    assert acceptance["plan_id"]
    assert len(acceptance["step_ids"]) == len(proposal["steps"])
    after = _fold(app)
    assert after.get(acceptance["plan_id"]) is not None
    assert len(after.of_type("plan_step")) == len(proposal["steps"])
    assert after.get(proposal["id"]).content["status"] == "ACCEPTED"

    live["state"]["passed"].append(
        "RequestPlanProposal served LIVE; proposal inert; AcceptPlanProposal minted "
        f"{len(acceptance['step_ids'])} steps deterministically"
    )
    live["state"]["plan_bounded_rejections"] = bounded_rejections
    live["state"]["routing_reason_codes"] = list(routing["reason_codes"])


# ── 2. AskGroundedQuestion live over imported documents ───────────────────────
def test_grounded_question_live_citations_validate(live):
    client, app, cfg = live["client"], live["app"], live["cfg"]
    for name, body in (("aurora-port.md", _PORT_DOC),
                       ("aurora-retention.md", _RETENTION_DOC)):
        r = client.request("POST", "/api/v1/artifacts/import",
                           body={"name": name, "body": body})
        assert r.status == 201, r.json()

    r = client.request("POST", "/api/v1/questions/ask",
                       body={"question": _QUESTION, "max_output_tokens": 256})
    assert r.status == 201, r.json()
    run = r.json()["data"]
    assert run["status"] == "ANSWERED"
    assert run["grounded"] is True
    assert run["model"] == cfg["model"]           # the LIVE model answered
    assert run["answer_text"].strip()
    assert run["answer_text"] != UNGROUNDED_ANSWER
    assert "7473" in run["answer_text"], (
        "a grounded temperature-0 answer must surface the cited fact"
    )
    assert run["citations"], "a grounded answer must carry citations"

    # Citations validate against REAL segment Cells (re-checked independently here).
    weave = _fold(app)
    for cit in run["citations"]:
        segment = weave.get(cit["segment_id"])
        assert segment is not None and not segment.retracted
        assert segment.type == "claim"    # documents.SEGMENT — a segment is a claim Cell
        assert segment.content.get("source_document") == cit["location"]["source_document"]
        norm = " ".join(str(segment.content.get("text", "")).split())
        core = cit["snippet"][:-1] if cit["snippet"].endswith("…") else cit["snippet"]
        assert core in norm

    # The recorded routing decision for the run: local-only honored, decoy rejected.
    run_cell = weave.get(run["id"])
    routing = _assert_routing_cell(live, run_cell.content["routing_cell"])
    assert routing["selected_model"] == cfg["model"]

    live["state"]["passed"].append(
        f"AskGroundedQuestion served LIVE with {len(run['citations'])} validated "
        "citation(s); external decoy rejected (sensitive_local_only)"
    )


# ── 3. malformed structured output is BOUNDED (rejected, never invoked) ───────
def test_malformed_structured_reply_is_bounded(live):
    client, app = live["client"], live["app"]
    before = _fold(app)
    plans_before = len(before.of_type("plan"))
    steps_before = len(before.of_type("plan_step"))
    agents_before = len(before.of_type("agent"))
    errors_before = len(before.of_type("model_error"))

    # A 24-token budget guarantees the live model's JSON is truncated mid-object —
    # a REAL unparseable structured reply, not a mock.
    r = client.request("POST", "/api/v1/plans/propose",
                       body={"objective": "Plan my quarterly reading list.",
                             "token_budget": 24})
    assert r.status == 422, (r.status, r.json())
    assert r.json().get("reason_code") == "INVALID_PROPOSAL"

    after = _fold(app)
    # Bounded: the rejection is RECORDED and nothing was minted or invoked.
    assert len(after.of_type("model_error")) > errors_before
    assert len(after.of_type("plan")) == plans_before
    assert len(after.of_type("plan_step")) == steps_before
    assert len(after.of_type("agent")) == agents_before
    events = [e.data.get("event") for e in live["app"].bus.since(0)]
    assert "plan.proposal_rejected" in events

    live["state"]["passed"].append(
        "malformed (truncated) live structured reply BOUNDED: 422 INVALID_PROPOSAL, "
        "model_error recorded, zero cells minted, nothing invoked"
    )


# ── 4. no credential material anywhere (asserted explicitly for a local run) ──
def test_no_secret_material_in_logs_events_or_cells(live):
    # Corpus: every captured log line, every stream event, every durable Cell.
    corpus = "\n".join([
        *live["logs"],
        json.dumps([e.data for e in live["app"].bus.since(0)], default=str),
        _all_cell_text(live["app"]),
    ])

    # A local endpoint takes no credential: assert its ABSENCE explicitly.
    assert os.environ.get("DECIMA_LIVE_API_KEY") is None or (
        os.environ["DECIMA_LIVE_API_KEY"] not in corpus
    )
    for marker in ("Authorization:", "Bearer "):
        assert marker not in corpus, f"credential-shaped marker {marker!r} recorded"
    # Any secret-shaped DECIMA_* env value must be absent from everything recorded.
    for name, value in os.environ.items():
        if name.startswith("DECIMA_") and any(
            t in name for t in ("KEY", "SECRET", "TOKEN", "PASSWORD")
        ):
            if value:
                assert value not in corpus, f"value of {name} leaked into records"
    # The app's own pairing secret stays out of cells/logs/events too.
    assert live["identity"].pairing_secret not in corpus

    live["state"]["passed"].append(
        "no credential material in logs, stream events, routing cells, or any Cell"
    )


# ── 5. evidence (small, secret-free, committed) ───────────────────────────────
def test_write_evidence(live):
    cfg, state = live["cfg"], live["state"]
    summary = {
        "lane": "Phase 6 — live provider qualified through the APP PATH",
        "path": (
            "server.build_application → CommandService.models "
            "(models_setup.build_model_stack from env) → RequestPlanProposal / "
            "AcceptPlanProposal / AskGroundedQuestion via Application.dispatch"
        ),
        "provider": cfg["kind"],
        "model": cfg["model"],
        "endpoint": cfg["base"],
        "hardware": {
            "machine": platform.machine(),
            "system": platform.system(),
            "note": "llama.cpp OpenAI-compatible server on loopback (CPU)",
        },
        "passed": state["passed"],
        "plan_bounded_rejections_before_accept": state.get(
            "plan_bounded_rejections", 0
        ),
        "routing_reason_codes": state.get("routing_reason_codes", []),
        "limitations": [
            "single local model qualified; no cloud provider was constructed or called",
            "deterministic default disabled for the run via the registry's own "
            "set_enabled config API (operator fleet preference) — with both enabled, "
            "equal-cost tie-break keeps deterministic as default (unit-proven)",
            "external-provider refusal proven against a decoy catalogue entry; no "
            "real external endpoint exists in this environment",
            "temperature-0 local decoding: live outputs are stable but not guaranteed "
            "deterministic across llama.cpp versions",
        ],
        "env_var_names": [
            models_setup.ENV_PROVIDER, models_setup.ENV_MODEL,
            models_setup.ENV_BASE_URL, models_setup.ENV_TIMEOUT,
        ],  # NAMES only — never values beyond the public loopback endpoint
    }
    _EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    _EVIDENCE.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    text = _EVIDENCE.read_text()
    assert "Bearer" not in text and "Authorization" not in text
    assert live["identity"].pairing_secret not in text
