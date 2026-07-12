"""Interface freeze for the Path-A product lanes (Q&A / planning / workspace).

These tests pin the SHARED contract the three feature lanes build against: the route
table entries (with their exact auth levels), the command registry names, the stub 501
``ApplicationError`` envelope, the contract dataclasses' ``as_dict`` round-trips, and
the stream-event families. A lane implements its own service module + screen WITHOUT
editing a shared file — if one of these tests breaks, a shared contract changed and
every lane is affected, so the change must happen here first.

Integration reconciliation (lead-owned, NOT lane-editable): when a lane LANDS its
service module, every route/auth/shape pin above stays frozen, but its
pre-implementation 501-stub pins are replaced by "landed" pins — the endpoint must
now be genuinely implemented (never ``NOT_IMPLEMENTED``) and refusals must stay
bounded and durable-effect-free. Lanes that have not landed keep the original 501
pins untouched.
"""

from __future__ import annotations

import json

import pytest

from decima.services.api import contracts, events, routes

# ── the frozen route additions ────────────────────────────────────────────────
NEW_COMMAND_ROUTES = [
    # (method, path, command, owning service module)
    ("POST", "/api/v1/questions/ask", "AskGroundedQuestion", "qa_service"),
    ("POST", "/api/v1/workspaces", "CreateWorkspaceRun", "workspace_service"),
    ("POST", "/api/v1/workspaces/start", "StartWorkspaceRun", "workspace_service"),
    ("POST", "/api/v1/workspaces/cancel", "CancelWorkspaceRun", "workspace_service"),
    ("POST", "/api/v1/plans/propose", "RequestPlanProposal", "plan_service"),
    ("POST", "/api/v1/plans/accept", "AcceptPlanProposal", "plan_service"),
    ("POST", "/api/v1/plans/execute", "StartPlanExecution", "plan_service"),
    ("POST", "/api/v1/plans/resume", "ResumePlan", "plan_service"),
    ("POST", "/api/v1/plans/cancel", "CancelPlan", "plan_service"),
]

NEW_READER_ROUTES = [
    ("GET", "/api/v1/questions", "question_runs"),
    ("GET", "/api/v1/questions/detail", "question_run"),
    ("GET", "/api/v1/workspaces", "workspace_runs"),
    ("GET", "/api/v1/workspaces/detail", "workspace_run"),
    ("GET", "/api/v1/plans/proposals", "plan_proposals"),
    ("GET", "/api/v1/agents/runs", "agent_run_summaries"),
]

# Which surfaces are "landed" vs still a 501 stub is detected at RUNTIME (see
# _command_stub_active / _reader_stub_active below), not from a hand-maintained list —
# so this freeze auto-reconciles as each lane lands, and correctly treats a surface
# that is implemented-but-gated (e.g. the workspace lane with no operator grant in the
# default test env, which genuinely still answers NOT_IMPLEMENTED) as a stub.


# ── route registration + auth discipline ─────────────────────────────────────
@pytest.mark.parametrize("method,path,command,_module", NEW_COMMAND_ROUTES)
def test_command_route_registered_at_write_level(method, path, command, _module):
    route = routes.match(method, path)
    assert route is not None, f"{method} {path} must be registered"
    assert route.auth == routes.WRITE  # durable mutation ⇒ session + CSRF
    assert route.kind == routes.COMMAND
    assert route.target == command


@pytest.mark.parametrize("method,path,target", NEW_READER_ROUTES)
def test_reader_route_registered_at_read_level(method, path, target):
    route = routes.match(method, path)
    assert route is not None, f"{method} {path} must be registered"
    assert route.auth == routes.READ  # disposable projection read ⇒ session
    assert route.kind == routes.READER
    assert route.target == target


def test_composed_existing_routes_unchanged():
    """The lanes COMPOSE with these pre-existing commands; their contract is frozen."""
    for method, path, target, auth in (
        ("POST", "/api/v1/plans/start", "StartPlan", routes.WRITE),
        ("POST", "/api/v1/plans/pause", "PausePlan", routes.WRITE),
        ("POST", "/api/v1/agents/terminate", "TerminateAgent", routes.WRITE),
        ("POST", "/api/v1/approvals/approve", "ApproveInvocation", routes.REAUTH),
    ):
        route = routes.match(method, path)
        assert route is not None and route.target == target and route.auth == auth


def test_terminate_agent_stays_approval_gated():
    from decima.services.api.commands import GATED

    assert "TerminateAgent" in GATED


def test_no_new_route_carries_an_id_in_the_path():
    """Ids travel in the body (commands) or query (detail readers), never the path."""
    for _method, path, *_ in [*NEW_COMMAND_ROUTES, *NEW_READER_ROUTES]:
        for segment in path.split("/"):
            assert not segment.startswith("{"), path
        assert path == path.rstrip("/")


# ── command dispatch + the bounded 501 stub envelope ─────────────────────────
# The 501 freeze governs a surface only WHILE its lane is a stub. Once the owning
# service module implements a surface (its handler no longer refuses with
# NOT_IMPLEMENTED), the stub expectation SELF-RETIRES via skip — the implemented
# behavior is pinned by the lane's own suite, and any remaining stub/gated lanes keep
# the full 501 guarantee. The probes are effect-free: an implemented command refuses
# empty args at the contract-parsing stage, an implemented reader is a pure fold read.
def _command_stub_active(env, command: str) -> bool:
    return env["app"].commands.execute(command, {}).reason_code == contracts.NOT_IMPLEMENTED


def _reader_stub_active(env, target: str) -> bool:
    from decima.services.api import plan_service, qa_service, workspace_service

    for module in (plan_service, qa_service, workspace_service):
        if target in module.READERS:
            try:
                module.READERS[target](env["app"], {})
            except contracts.CommandError as exc:
                return exc.reason_code == contracts.NOT_IMPLEMENTED
            return False
    raise AssertionError(f"reader target {target!r} owned by no lane module")


@pytest.mark.parametrize("method,path,command,_module", NEW_COMMAND_ROUTES)
def test_command_dispatches_to_stub_501(client, env, method, path, command, _module):
    app = env["app"]
    assert command in app.commands.commands()  # registered, dispatchable
    if not _command_stub_active(env, command):
        pytest.skip(f"{command} implemented by {_module} — 501 stub freeze retired")
    before = app.weft.count()
    r = client.request(method, path, body={})
    assert r.status == 501
    body = r.json()
    assert body["ok"] is False
    assert body["reason_code"] == contracts.NOT_IMPLEMENTED
    assert body["error"]  # bounded, human-readable
    assert app.weft.count() == before  # a stub performs NO durable effect


@pytest.mark.parametrize("method,path,command,_module", NEW_COMMAND_ROUTES)
def test_landed_command_is_implemented_not_stubbed(client, env, method, path, command, _module):
    """A landed lane's command must be REAL: never NOT_IMPLEMENTED, and a
    contract-invalid request fails closed as BAD_REQUEST with no durable effect.
    Surfaces still stubbed/gated in this env are covered by the 501 test above."""
    app = env["app"]
    assert command in app.commands.commands()  # registered, dispatchable
    if _command_stub_active(env, command):
        pytest.skip(f"{command} still a 501 stub in this env — covered by the stub test")
    before = app.weft.count()
    r = client.request(method, path, body={})  # violates the request contract
    body = r.json()
    assert body["reason_code"] != contracts.NOT_IMPLEMENTED
    assert r.status == 400
    assert body["ok"] is False
    assert body["reason_code"] == "BAD_REQUEST"
    assert body["error"]  # bounded, human-readable
    assert app.weft.count() == before  # a refusal performs NO durable effect


@pytest.mark.parametrize("method,path,target", NEW_READER_ROUTES)
def test_reader_returns_stub_501_envelope(client, env, method, path, target):
    if not _reader_stub_active(env, target):
        pytest.skip(f"reader {target} implemented by its lane — 501 stub freeze retired")
    before = env["app"].weft.count()
    r = client.request(method, path, csrf=False)
    assert r.status == 501
    body = r.json()
    assert body["ok"] is False
    assert body["reason_code"] == contracts.NOT_IMPLEMENTED
    assert body["error"]
    assert env["app"].weft.count() == before


@pytest.mark.parametrize("method,path,target", NEW_READER_ROUTES)
def test_landed_reader_serves_real_reads(client, env, method, path, target):
    """A landed lane's readers must be REAL pure reads: never NOT_IMPLEMENTED, list
    readers answer 200 with the ``{"items": [...]}`` envelope on an empty fold, and
    a detail reader without its id is a bounded 404 — never a durable effect.
    Readers still stubbed/gated in this env are covered by the 501 reader test above."""
    if _reader_stub_active(env, target):
        pytest.skip(f"reader {target} still a 501 stub in this env — covered by the stub test")
    before = env["app"].weft.count()
    r = client.request(method, path, csrf=False)
    body = r.json()
    assert body.get("reason_code") != contracts.NOT_IMPLEMENTED
    if path.endswith("/detail"):
        assert r.status == 404
        assert body["ok"] is False
        assert body["error"]
    else:
        assert r.status == 200
        assert body["items"] == []
    assert env["app"].weft.count() == before  # readers stay pure


@pytest.mark.parametrize("method,path,_t", NEW_READER_ROUTES)
def test_reader_requires_a_session(env, method, path, _t):
    r = env["app"].dispatch(method, path)
    assert r.status == 401


@pytest.mark.parametrize("method,path,_c,_m", NEW_COMMAND_ROUTES)
def test_command_requires_session_and_csrf(client, env, method, path, _c, _m):
    assert env["app"].dispatch(method, path, body="{}").status == 401  # no session
    r = client.request(method, path, body={}, csrf=False)  # no CSRF
    assert r.status == 403


def test_stub_501_is_distinct_from_unknown_command(env):
    unknown = env["app"].commands.execute("NoSuchCommand", {})
    assert unknown.reason_code == "UNKNOWN_COMMAND"
    # NOT_IMPLEMENTED (a known-but-stubbed/gated surface) must be a DISTINCT reason
    # from UNKNOWN_COMMAND. Pick whichever new command is still stub-active in this env
    # (workspace is grant-gated, so it is a stub here even after landing); if every
    # surface is fully live, the distinction is still asserted structurally.
    stub_cmd = next(
        (c for _m, _p, c, _mod in NEW_COMMAND_ROUTES if _command_stub_active(env, c)),
        None,
    )
    if stub_cmd is not None:
        stub = env["app"].commands.execute(stub_cmd, {})
        assert stub.reason_code == contracts.NOT_IMPLEMENTED
        assert stub.http_status == 501
        assert stub.reason_code != unknown.reason_code
    else:  # pragma: no cover - every lane live+ungated
        assert contracts.NOT_IMPLEMENTED != "UNKNOWN_COMMAND"


def test_service_stub_names_its_owning_lane():
    from decima.services.api import plan_service, qa_service, workspace_service

    assert "qa lane" in qa_service.__doc__.lower()
    assert "planning lane" in plan_service.__doc__.lower()
    assert "workspace lane" in workspace_service.__doc__.lower()
    for module, targets in (
        (qa_service, ("question_runs", "question_run")),
        (plan_service, ("plan_proposals", "agent_run_summaries")),
        (workspace_service, ("workspace_runs", "workspace_run")),
    ):
        assert set(targets) == set(module.READERS)


# ── contract dataclasses round-trip as_dict (JSON-safe, ints stay ints) ──────
def _roundtrip(obj) -> dict:
    d = obj.as_dict()
    return json.loads(json.dumps(d, sort_keys=True))


def test_question_contracts_round_trip():
    req = contracts.QuestionRequest.from_args(
        {"question": "what changed?", "scope": ["proj-1"], "limit": 3}
    )
    assert _roundtrip(req) == req.as_dict()
    assert contracts.QuestionRequest.from_args(req.as_dict()) == req
    cite = contracts.Citation(
        segment_id="seg-1",
        location=contracts.CitationLocation(source_document="doc-1", source="a.md", offset=7),
        snippet="…quoted untrusted text…",
    )
    run = contracts.QuestionRun(
        id="q-1",
        question="what changed?",
        status=contracts.QuestionStatus.ANSWERED,
        answer_text="an answer",
        model="det",
        grounded=True,
        citations=(cite,),
        scope=req.scope,
        asked_frontier=42,
    )
    data = _roundtrip(run)
    assert data == run.as_dict()
    assert data["citations"][0]["location"]["offset"] == 7
    assert isinstance(data["asked_frontier"], int)


def test_citation_wraps_the_qa_capability_type():
    from decima.capabilities import qa

    base = qa.Citation(segment_id="s", source_document="d", source="f.md", offset=3, snippet="x")
    wrapped = contracts.Citation.from_qa(base)
    assert wrapped.segment_id == "s"
    assert wrapped.location.source_document == "d"
    assert wrapped.location.offset == 3


def test_workspace_contracts_round_trip_and_policy_is_networkless():
    req = contracts.WorkspaceRequest.from_args(
        {"name": "fix-bug", "objective": "make tests pass", "policy": {"timeout_seconds": 5}}
    )
    assert _roundtrip(req) == req.as_dict()
    assert req.policy.network is False
    with pytest.raises(contracts.ContractError):
        contracts.WorkspacePolicy.from_args({"network": True})
    with pytest.raises(contracts.ContractError):
        contracts.WorkspacePolicy(timeout_seconds=1, network=True)
    run = contracts.WorkspaceRun(
        id="run-1",
        workspace_id="ws-1",
        name="fix-bug",
        status=contracts.WorkspaceRunStatus.SUCCEEDED,
        artifact_ids=("art-1", "art-2"),
        receipt_id="rcpt-1",
        created_frontier=9,
    )
    art = contracts.WorkspaceArtifact(
        id="art-1",
        workspace_id="ws-1",
        kind="diff_artifact",
        digest="abc",
        status="SUCCEEDED",
        applied=False,
    )
    assert _roundtrip(run) == run.as_dict()
    assert _roundtrip(art) == art.as_dict()


def test_plan_contracts_round_trip_and_proposal_holds_no_authority():
    req = contracts.PlanProposalRequest.from_args(
        {"objective": "ship the thing", "max_steps": 4, "monetary_budget_microcents": 1000}
    )
    assert _roundtrip(req) == req.as_dict()
    spec = req.task_spec()
    assert spec.is_sensitive  # private by default ⇒ routing is local-only
    step = contracts.ProposedPlanStep.from_dict({"description": "write tests", "depends_on": [0]})
    proposal = contracts.PlanProposal(
        id="prop-1",
        objective="ship the thing",
        steps=(step,),
        model="det",
        proposed_frontier=5,
    )
    data = _roundtrip(proposal)
    assert data == proposal.as_dict()
    # A proposal is DATA: no capability/grant/principal/key fields exist on it.
    for forbidden in ("capability", "grant", "principal", "key", "secret", "token"):
        assert not any(forbidden in k for k in data), forbidden
    acceptance = contracts.PlanAcceptance(
        proposal_id="prop-1",
        plan_id="plan-1",
        step_ids=("s1",),
        accepted_frontier=6,
    )
    summary = contracts.AgentRunSummary(
        agent_id="a-1",
        objective="o",
        status="RUNNING",
        token_budget=100,
        monetary_budget=200,
        steps_total=3,
        steps_succeeded=1,
    )
    assert _roundtrip(acceptance) == acceptance.as_dict()
    assert _roundtrip(summary) == summary.as_dict()


def test_recorded_numbers_refuse_floats_and_bools():
    with pytest.raises(contracts.ContractError):
        contracts.QuestionRequest.from_args({"question": "q", "limit": 2.5})
    with pytest.raises(contracts.ContractError):
        contracts.PlanProposalRequest.from_args(
            {"objective": "o", "monetary_budget_microcents": 1.5}
        )
    with pytest.raises(contracts.ContractError):
        contracts.ProposedPlanStep.from_dict({"description": "d", "depends_on": [0.5]})
    with pytest.raises(contracts.ContractError):
        contracts.WorkspacePolicy(timeout_seconds=True)


def test_contract_violation_maps_to_bad_request_envelope(env):
    """A lane can let ``ContractError`` propagate from ``from_args``: the command
    service converts it to the stable BAD_REQUEST envelope, fail closed."""
    svc = env["app"].commands

    def probe(_args):
        raise contracts.ContractError("field 'question' invalid")

    svc._handlers["__ContractProbe"] = probe
    try:
        result = svc.execute("__ContractProbe", {})
    finally:
        del svc._handlers["__ContractProbe"]
    assert result.ok is False
    assert result.reason_code == "BAD_REQUEST"
    assert result.http_status == 400


def test_application_error_envelope_shape():
    env_ = contracts.ApplicationError(
        reason_code=contracts.NOT_IMPLEMENTED, message="not yet", http_status=501
    )
    assert env_.as_dict() == {"ok": False, "reason_code": "NOT_IMPLEMENTED", "error": "not yet"}
    assert contracts.ApplicationError(reason_code="X").as_dict()["error"] == "X"


def test_command_error_is_the_shared_canonical_type():
    from decima.services.api.commands import CommandError as CE

    assert CE is contracts.CommandError


# ── stream-event families + the emit seam ────────────────────────────────────
def test_event_families_declared():
    for family in (
        events.QUESTION,
        events.WORKSPACE,
        events.PLAN,
        events.STEP,
        events.AGENT,
        events.APPROVAL,
        events.ARTIFACT,
    ):
        assert family in events.KINDS
        assert family in events.FAMILY_EVENTS
        for name in events.FAMILY_EVENTS[family]:
            assert name.startswith(family + "."), name


def test_emit_seam_publishes_within_a_family():
    bus = events.EventBus()
    ev = bus.emit("plan.accepted", id="plan-1", proposal="prop-1")
    assert ev.kind == events.PLAN
    assert ev.data == {"event": "plan.accepted", "id": "plan-1", "proposal": "prop-1"}
    frame = ev.as_sse().decode("utf-8")
    assert "event: plan" in frame and "plan.accepted" in frame


def test_emit_refuses_an_undeclared_family():
    bus = events.EventBus()
    with pytest.raises(ValueError):
        bus.emit("finance.transfer", id="x")
    with pytest.raises(ValueError):
        bus.emit("question")  # no leaf event name


def test_stream_event_type_is_reused_not_duplicated():
    assert contracts.StreamEvent is events.StreamEvent
