"""Planning-lane qualification: model-planned durable agents through the real API.

Everything here drives the composed product path — commands and readers over the
loopback ``Application`` (session + CSRF, exactly as the Shell calls it), a real temp
Weft, the shared deterministic model stack — never library shortcuts or direct DB
setup. The suite proves the lane's obligations:

  * RequestPlanProposal routes via the recorded task spec, validates the structured
    output DETERMINISTICALLY, records the proposal as inert DATA, and mints/executes
    NOTHING;
  * AcceptPlanProposal is the sole minting point (durable Plan/Step/bounded Agent
    Cells); rejection mints nothing;
  * execution drives the existing budget-gated scheduler/supervisor; pause is
    server-enforced; cancel bounds everything; TerminateAgent stays approval-gated;
  * hostile/malformed/cyclic/over-budget/authority-seeking proposals are REFUSED;
  * runs survive a backend restart over the same Weft and a projection rebuild.

The deterministic provider is the default model path; where a test needs garbage
model output it monkeypatches the model stack IN THE TEST ONLY (the seam
``CommandService.models`` is an injected dependency).
"""

from __future__ import annotations

import pytest

from decima.kernel.weave import Weave
from decima.models.providers import ModelResponse
from decima.models.routing import RouteResult, RoutingDecision
from decima.runtime import cells
from decima.services.api import plan_service
from decima.services.api.server import build_application, build_driver

OBJECTIVE = "Ship the 0.3 daily driver"


# ── helpers ───────────────────────────────────────────────────────────────────
class _FakeStack:
    """A test-only model stack returning a fixed structured proposal — the seam the
    real stack fills. Used ONLY to steer validation-rejection paths."""

    def __init__(self, structured):
        self.structured = structured

    def propose(self, spec, request, *, max_hops=3):
        decision = RoutingDecision(
            selected_model="deterministic-offline", reason_codes=("selected",)
        )
        response = ModelResponse(
            model="deterministic-offline",
            text="{}",
            input_tokens=1,
            output_tokens=1,
            structured=self.structured,
        )
        attempts = ({"model": "deterministic-offline", "outcome": "ok"},)
        return RouteResult(response, "deterministic-offline", decision, attempts), decision


def _plan_body(**over):
    """A valid proposal body a fake stack can perturb one field at a time."""
    body = {
        "objective": OBJECTIVE,
        "summary": "three bounded steps",
        "steps": [
            {
                "id": "s1",
                "description": "gather",
                "depends_on": [],
                "expected_output": "notes",
                "capability": "local:derive",
                "agent": "researcher",
            },
            {
                "id": "s2",
                "description": "build",
                "depends_on": ["s1"],
                "expected_output": "draft",
                "capability": "local:derive",
                "agent": "builder",
            },
            {
                "id": "s3",
                "description": "review",
                "depends_on": ["s1", "s2"],
                "expected_output": "final",
                "capability": "local:note",
                "agent": "builder",
            },
        ],
        "risk": "low",
        "expected_approvals": [],
        "model_budget": 4096,
        "execution_budget": 0,
    }
    body.update(over)
    return body


def _propose(client, objective=OBJECTIVE):
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": objective})
    assert r.status == 201, r.json()
    return r.json()["data"]


def _accept(client, proposal_id):
    r = client.request("POST", "/api/v1/plans/accept", body={"proposal_id": proposal_id})
    assert r.status == 201, r.json()
    return r.json()["data"]


def _execute(client, plan_id):
    r = client.request("POST", "/api/v1/plans/execute", body={"id": plan_id})
    assert r.status == 200, r.json()
    return r.json()["data"]


def _run_to_completion(client, plan_id, max_passes=10):
    for _ in range(max_passes):
        data = _execute(client, plan_id)
        if data["complete"]:
            return data
        if not data["dispatched"] and not data["cancelled_steps"]:
            return data  # stalled — caller asserts why
    raise AssertionError("plan did not complete within the pass bound")


def _events(env):
    return [e.data.get("event") for e in env["app"].bus.since(0) if "event" in e.data]


def _weave(env):
    return Weave.fold(env["app"].weft)


# ── proposal generation: routed, recorded, validated, inert ──────────────────
def test_request_plan_proposal_records_and_mints_nothing(client, env):
    weave_before = _weave(env)
    assert weave_before.of_type(cells.PLAN) == []

    data = _propose(client)
    assert data["status"] == "PROPOSED"
    assert data["model"] == "deterministic-offline"
    assert len(data["steps"]) >= 3
    assert any(s["depends_on"] for s in data["steps"])  # >=1 dependency
    assert isinstance(data["model_budget"], int)
    assert isinstance(data["execution_budget"], int)
    # the routing decision is RECORDED and surfaced (model + policy the Shell shows)
    assert data["routing"]["selected_model"] == "deterministic-offline"
    assert "selected" in data["routing"]["reason_codes"]
    weave = _weave(env)
    assert weave.get(data["routing_cell"]).type == "model_routing"
    # proposal is inert DATA on the Weft…
    prop = weave.get(data["id"])
    assert prop.type == plan_service.PLAN_PROPOSAL
    assert prop.content["instruction_eligible"] is False
    # …and NOTHING durable/executable was minted by proposing alone.
    assert weave.of_type(cells.PLAN) == []
    assert weave.of_type(cells.PLAN_STEP) == []
    assert weave.of_type(cells.AGENT) == []
    assert weave.of_type(cells.RECEIPT) == []
    assert weave.of_type(plan_service.STEP_OUTPUT) == []
    ev = _events(env)
    assert "plan.proposal_requested" in ev and "plan.proposal_ready" in ev


def test_proposals_reader_lists_newest_first(client, env):
    a = _propose(client, "objective A")["id"]
    b = _propose(client, "objective B")["id"]
    r = client.request("GET", "/api/v1/plans/proposals", csrf=False)
    assert r.status == 200
    items = r.json()["items"]
    assert [i["id"] for i in items[:2]] == [b, a]
    assert all(i["steps"] for i in items)


def test_proposal_requires_objective(client):
    r = client.request("POST", "/api/v1/plans/propose", body={})
    assert r.status == 400
    assert r.json()["reason_code"] == "BAD_REQUEST"


# ── acceptance: the sole minting point ────────────────────────────────────────
def test_accept_mints_durable_plan_steps_and_bounded_agents(client, env):
    proposal = _propose(client)
    acc = _accept(client, proposal["id"])
    assert acc["proposal_id"] == proposal["id"]
    assert len(acc["step_ids"]) == len(proposal["steps"])
    assert len(acc["agent_ids"]) >= 2  # parent + worker groups

    weave = _weave(env)
    plan = weave.get(acc["plan_id"])
    assert plan.type == cells.PLAN and plan.content["status"] == "DRAFT"
    for sid in acc["step_ids"]:
        step = weave.get(sid)
        assert step.type == cells.PLAN_STEP
        assert step.content["status"] == "PENDING"
        assert step.content["assigned_agent_id"] in acc["agent_ids"]
    parent = weave.get(acc["parent_agent_id"])
    assert parent.type == cells.AGENT
    assert isinstance(parent.content["token_budget"], int)  # bounded, int (invariant 6)
    children = [a for a in acc["agent_ids"] if a != acc["parent_agent_id"]]
    for aid in children:
        agent = weave.get(aid)
        assert agent.content["parent_agent_id"] == acc["parent_agent_id"]
        assert isinstance(agent.content["token_budget"], int)
        assert agent.content["plan_id"] == acc["plan_id"]
        # bounded agents hold NO capability grants — authority never rides on a model plan
        assert agent.content["capability_grant_ids"] == []
    # the proposal is now ACCEPTED and back-references the minted plan
    r = client.request("GET", "/api/v1/plans/proposals", csrf=False).json()
    mine = [i for i in r["items"] if i["id"] == proposal["id"]][0]
    assert mine["status"] == "ACCEPTED" and mine["plan_id"] == acc["plan_id"]
    ev = _events(env)
    assert "plan.accepted" in ev and "agent.spawned" in ev


def test_accept_is_single_shot_and_reject_mints_nothing(client, env):
    proposal = _propose(client)
    r = client.request(
        "POST", "/api/v1/plans/accept", body={"proposal_id": proposal["id"], "decision": "reject"}
    )
    assert r.status == 200
    assert r.json()["data"]["status"] == "REJECTED"
    assert _weave(env).of_type(cells.PLAN) == []  # rejection mints nothing
    # a decided proposal cannot be re-decided (neither accept nor reject)
    r = client.request("POST", "/api/v1/plans/accept", body={"proposal_id": proposal["id"]})
    assert r.status == 409
    assert r.json()["reason_code"] == "ALREADY_DECIDED"

    accepted = _propose(client)
    _accept(client, accepted["id"])
    r = client.request("POST", "/api/v1/plans/accept", body={"proposal_id": accepted["id"]})
    assert r.status == 409


def test_accept_unknown_proposal_404(client):
    r = client.request("POST", "/api/v1/plans/accept", body={"proposal_id": "nope"})
    assert r.status == 404


def test_two_identical_objectives_mint_two_distinct_plans(client):
    a = _accept(client, _propose(client)["id"])
    b = _accept(client, _propose(client)["id"])
    assert a["plan_id"] != b["plan_id"]
    assert set(a["step_ids"]).isdisjoint(set(b["step_ids"]))


def test_reaccept_after_partial_mint_crash_converges(client, env, monkeypatch):
    """A crash mid-accept (after the plan + agents minted, before any step) leaves the
    proposal PROPOSED and re-acceptable; retrying converges on EXACTLY the same
    plan/agent/step cell set because every minted id is derived deterministically from
    the proposal id — the fold's LWW re-assert repairs the partial mint instead of
    duplicating it, and the recovered plan runs to completion."""
    proposal = _propose(client)

    def _crash(*args, **kwargs):
        raise RuntimeError("simulated crash before the first step was minted")

    with monkeypatch.context() as m:
        m.setattr(cells, "create_step", _crash)
        with pytest.raises(RuntimeError):
            env["app"].commands.execute("AcceptPlanProposal", {"proposal_id": proposal["id"]})

    weave = _weave(env)
    partial_plans = weave.of_type(cells.PLAN)
    assert len(partial_plans) == 1  # the partial mint IS on the Weft…
    assert weave.of_type(cells.PLAN_STEP) == []  # …with no steps yet
    # …but the proposal was never marked decided, so recovery is a plain retry.
    assert weave.get(proposal["id"]).content["status"] == "PROPOSED"

    acc = _accept(client, proposal["id"])  # the SAME command, retried
    weave = _weave(env)
    plans = weave.of_type(cells.PLAN)
    assert [p.id for p in plans] == [acc["plan_id"]]  # exactly ONE plan, the derived id
    assert acc["plan_id"] == partial_plans[0].id  # …the very cell the crash left
    steps = weave.of_type(cells.PLAN_STEP)
    assert sorted(s.id for s in steps) == sorted(acc["step_ids"])
    assert len(steps) == len(proposal["steps"])  # every step, no duplicates
    agents = weave.of_type(cells.AGENT)
    assert sorted(a.id for a in agents) == sorted(acc["agent_ids"])
    mine = weave.get(proposal["id"]).content
    assert mine["status"] == "ACCEPTED" and mine["plan_id"] == acc["plan_id"]
    assert mine["minted_step_ids"] == list(acc["step_ids"])
    # the recovered plan is fully functional — it executes to real completion
    final = _run_to_completion(client, acc["plan_id"])
    assert final["complete"] is True
    assert _weave(env).get(acc["plan_id"]).content["status"] == "COMPLETED"


def test_objective_with_shell_punctuation_proposes_and_accepts(client, env):
    """An innocuous operator objective containing backticks / ``$(`` must not make the
    deterministic default provider's OWN proposal trip the lane's executable-content
    scan: the echo into step descriptions is sanitized at synthesis, the recorded
    objective stays the operator's verbatim text, and the scan itself is untouched
    (hostile content in model-authored fields is still refused — see
    ``test_executable_content_hidden_in_fields_rejected``)."""
    objective = "Summarize `README.md` and note what $(git status) would show"
    proposal = _propose(client, objective)
    assert proposal["objective"] == objective  # canonical text, verbatim
    for step in proposal["steps"]:  # the echo is sanitized…
        assert "`" not in step["description"]
        assert "$(" not in step["description"]
        assert "README.md" in step["description"]  # …but still informative
    acc = _accept(client, proposal["id"])  # re-validation at accept passes too
    assert len(acc["step_ids"]) == len(proposal["steps"])


def test_execute_never_autocompletes_manual_tasks(client, env):
    pid = client.request("POST", "/api/v1/projects", body={"objective": "manual project"}).json()[
        "data"
    ]["id"]
    tid = client.request(
        "POST", "/api/v1/tasks", body={"project_id": pid, "description": "buy milk"}
    ).json()["data"]["id"]
    data = _execute(client, pid)  # starts + one pass
    assert data["dispatched"] == []  # the manual task is NOT run
    assert data["complete"] is False
    weave = _weave(env)
    assert weave.get(tid).content["status"] in ("PENDING", "READY")
    assert weave.of_type(cells.RECEIPT) == []


# ── execution through the existing runtime ────────────────────────────────────
def test_execution_runs_to_receipt_confirmed_completion(client, env):
    acc = _accept(client, _propose(client)["id"])
    plan_id = acc["plan_id"]
    first = _execute(client, plan_id)
    assert [d["status"] for d in first["dispatched"]] == ["SUCCEEDED"]
    final = _run_to_completion(client, plan_id)
    assert final["complete"] is True

    weave = _weave(env)
    assert weave.get(plan_id).content["status"] == "COMPLETED"
    receipts = weave.of_type(cells.RECEIPT)
    assert len(receipts) == len(acc["step_ids"])  # every completion has a receipt
    outputs = weave.of_type(plan_service.STEP_OUTPUT)
    assert outputs and all(o.content["instruction_eligible"] is False for o in outputs)
    for sid in acc["step_ids"]:
        assert weave.get(sid).content["status"] == "SUCCEEDED"
    # agents completed; summaries fold live from the Weft
    runs = client.request("GET", "/api/v1/agents/runs", csrf=False, query={"plan": plan_id}).json()[
        "items"
    ]
    assert len(runs) >= 3
    assert all(a["status"] == "COMPLETED" for a in runs)
    workers = [a for a in runs if a["parent_agent_id"]]
    assert sum(a["steps_succeeded"] for a in workers) == len(acc["step_ids"])
    ev = _events(env)
    assert "plan.execution_started" in ev
    assert ev.count("step.succeeded") == len(acc["step_ids"])
    assert "agent.status_changed" in ev


def test_pause_is_server_enforced_and_resume_continues(client, env):
    acc = _accept(client, _propose(client)["id"])
    plan_id = acc["plan_id"]
    _execute(client, plan_id)  # pass 1: first step done
    r = client.request("POST", "/api/v1/plans/pause", body={"id": plan_id})
    assert r.status == 200

    statuses_before = {sid: _weave(env).get(sid).content["status"] for sid in acc["step_ids"]}
    paused = _execute(client, plan_id)  # advance while PAUSED
    assert paused["dispatched"] == []  # NO new work
    assert paused["status"] == "PAUSED"
    statuses_after = {sid: _weave(env).get(sid).content["status"] for sid in acc["step_ids"]}
    assert statuses_after == statuses_before

    r = client.request("POST", "/api/v1/plans/resume", body={"id": plan_id})
    assert r.status == 200
    final = _run_to_completion(client, plan_id)
    assert final["complete"] is True
    assert "plan.resumed" in _events(env)


def test_resume_refuses_a_draft_or_terminal_plan(client, env):
    acc = _accept(client, _propose(client)["id"])
    r = client.request("POST", "/api/v1/plans/resume", body={"id": acc["plan_id"]})
    assert r.status == 409  # DRAFT: nothing to resume
    _run_to_completion(client, acc["plan_id"])
    r = client.request("POST", "/api/v1/plans/resume", body={"id": acc["plan_id"]})
    assert r.status == 409  # COMPLETED: terminal


def test_cancel_bounds_everything(client, env):
    acc = _accept(client, _propose(client)["id"])
    plan_id = acc["plan_id"]
    _execute(client, plan_id)  # one step already succeeded
    r = client.request("POST", "/api/v1/plans/cancel", body={"id": plan_id})
    assert r.status == 200
    weave = _weave(env)
    assert weave.get(plan_id).content["status"] == "CANCELLED"
    for sid in acc["step_ids"]:
        assert weave.get(sid).content["status"] in ("SUCCEEDED", "CANCELLED")
    runs = client.request("GET", "/api/v1/agents/runs", csrf=False, query={"plan": plan_id}).json()[
        "items"
    ]
    # every agent is bounded terminal; one already COMPLETED honestly stays COMPLETED
    # (cancellation stops future authority, it does not rewrite recorded outcomes)
    assert all(a["status"] in ("TERMINATED", "COMPLETED", "FAILED") for a in runs)
    assert any(a["status"] == "TERMINATED" for a in runs)
    # a cancelled plan can never dispatch again
    r = client.request("POST", "/api/v1/plans/execute", body={"id": plan_id})
    assert r.status == 409
    ev = _events(env)
    assert "plan.cancelled" in ev and "agent.terminated" in ev


def test_terminate_agent_stays_gated_and_valid_work_still_completes(client, env):
    acc = _accept(client, _propose(client)["id"])
    plan_id = acc["plan_id"]
    runs = client.request("GET", "/api/v1/agents/runs", csrf=False, query={"plan": plan_id}).json()[
        "items"
    ]
    builder = [a for a in runs if a["objective"].startswith("builder")][0]

    # submitting TerminateAgent DEFERS to the approval inbox — no effect yet
    r = client.request("POST", "/api/v1/agents/terminate", body={"id": builder["agent_id"]})
    assert r.status == 202
    body = r.json()
    assert body["required_approval"] is True
    assert _weave(env).get(builder["agent_id"]).content["status"] == "CREATED"

    # the human approves (reauth) — ONLY then does the termination run
    r = client.request(
        "POST", "/api/v1/approvals/approve", body={"item": body["data"]["item"]}, reauth=True
    )
    assert r.status == 200 and r.json()["ok"] is True
    weave = _weave(env)
    assert weave.get(builder["agent_id"]).content["status"] == "TERMINATED"

    # remaining VALID work completes; the terminated agent's steps are cancelled
    final = _run_to_completion(client, plan_id)
    assert final["complete"] is True
    weave = _weave(env)
    step_status = {sid: weave.get(sid).content["status"] for sid in acc["step_ids"]}
    # the composed default plan puts two steps on each worker group; terminating the
    # builder cancels its two steps while the researcher's two still complete.
    assert sorted(step_status.values()) == ["CANCELLED", "CANCELLED", "SUCCEEDED", "SUCCEEDED"]
    assert weave.get(plan_id).content["status"] == "COMPLETED"
    # the terminated agent still appears in the run summaries (history, not authority)
    runs = client.request("GET", "/api/v1/agents/runs", csrf=False, query={"plan": plan_id}).json()[
        "items"
    ]
    by_id = {a["agent_id"]: a for a in runs}
    assert by_id[builder["agent_id"]]["status"] == "TERMINATED"


# ── hostile / malformed proposals are refused, never repaired ────────────────
def _steer(env, structured):
    env["app"].commands.models = _FakeStack(structured)


@pytest.mark.parametrize(
    "garbage",
    [
        None,  # no structured payload at all
        {"nonsense": True},  # missing every required field
        {
            "objective": 7,
            "summary": "s",
            "steps": [],
            "risk": "low",
            "expected_approvals": [],
            "model_budget": 1,
            "execution_budget": 1,
        },
    ],
)
def test_malformed_proposal_rejected(client, env, garbage):
    _steer(env, garbage)
    weave_before = _weave(env)
    n_proposals = len(weave_before.of_type(plan_service.PLAN_PROPOSAL))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert r.json()["reason_code"] == "INVALID_PROPOSAL"
    weave = _weave(env)
    assert len(weave.of_type(plan_service.PLAN_PROPOSAL)) == n_proposals  # not recorded
    assert weave.of_type("model_error")  # the rejection IS recorded
    assert "plan.proposal_rejected" in _events(env)


def test_cyclic_proposal_rejected(client, env):
    steps = [
        {"id": "s1", "description": "a", "depends_on": ["s2"], "capability": "local:derive"},
        {"id": "s2", "description": "b", "depends_on": ["s1"], "capability": "local:derive"},
        {"id": "s3", "description": "c", "depends_on": [], "capability": "local:derive"},
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "cycle" in r.json()["error"]


def test_duplicate_and_missing_dependency_rejected(client, env):
    steps = [
        {"id": "s1", "description": "a", "depends_on": [], "capability": "local:derive"},
        {"id": "s1", "description": "b", "depends_on": ["ghost"], "capability": "local:derive"},
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    msg = r.json()["error"]
    assert "duplicate" in msg and "unknown step" in msg


def test_unknown_capability_rejected(client, env):
    steps = [
        {"id": "s1", "description": "exfiltrate", "depends_on": [], "capability": "net:egress"}
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "unknown capability" in r.json()["error"]


def test_executable_content_hidden_in_fields_rejected(client, env):
    steps = [
        {
            "id": "s1",
            "description": "run $(rm -rf /) now",
            "depends_on": [],
            "capability": "local:derive",
        }
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "executable content" in r.json()["error"]


def test_budget_above_policy_rejected(client, env):
    _steer(env, _plan_body(model_budget=plan_service.MAX_MODEL_BUDGET + 1))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "above policy cap" in r.json()["error"]


def test_arbitrary_authority_requests_rejected(client, env):
    # an unexpected top-level field is refused by the strict schema…
    body = _plan_body()
    body["grants"] = ["root"]
    _steer(env, body)
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    # …an unexpected step field is refused by the deep validator…
    steps = [
        {
            "id": "s1",
            "description": "a",
            "depends_on": [],
            "capability": "local:derive",
            "capability_ids": ["cap-1"],
        }
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "unexpected fields" in r.json()["error"]
    # …and an approval expectation outside the gated set is an authority request.
    _steer(env, _plan_body(expected_approvals=["GrantRootAccess"]))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "authority request" in r.json()["error"]


def test_exhausted_budget_blocks_dispatch_before_any_effect(client, env):
    _steer(env, _plan_body(model_budget=2))  # tiny but policy-valid
    proposal = _propose(client)
    acc = _accept(client, proposal["id"])
    plan_id = acc["plan_id"]
    data = _execute(client, plan_id)
    assert data["dispatched"] == []  # the gate ran BEFORE dispatch
    assert data["refused"]
    weave = _weave(env)
    assert weave.of_type(cells.RECEIPT) == []  # nothing executed
    assert weave.of_type(plan_service.STEP_OUTPUT) == []
    runs = client.request("GET", "/api/v1/agents/runs", csrf=False, query={"plan": plan_id}).json()[
        "items"
    ]
    blocked = [a for a in runs if a["status"] == "BUDGET_BLOCKED"]
    assert blocked and all("budget" in a.get("budget_block_reason", "") for a in blocked)
    # the block is durable: another pass still refuses, still executes nothing
    again = _execute(client, plan_id)
    assert again["dispatched"] == []
    assert _weave(env).of_type(cells.RECEIPT) == []


# ── durability: restart and projection rebuild ────────────────────────────────
def test_run_survives_backend_restart_and_continues(client, env):
    from tests.api.conftest import Client

    acc = _accept(client, _propose(client)["id"])
    plan_id = acc["plan_id"]
    _execute(client, plan_id)  # partial progress

    # a brand-new process over the SAME Weft: fold IS the state
    app2, identity2 = build_application(env["db"], seed=bytes(32), secure_cookie=True)
    client2 = Client(app=app2, pairing_secret=identity2.pairing_secret)
    client2.login()

    items = client2.request("GET", "/api/v1/plans/proposals", csrf=False).json()["items"]
    assert items and items[0]["plan_id"] == plan_id
    runs = client2.request(
        "GET", "/api/v1/agents/runs", csrf=False, query={"plan": plan_id}
    ).json()["items"]
    assert len(runs) >= 3

    final = None
    for _ in range(10):
        r = client2.request("POST", "/api/v1/plans/execute", body={"id": plan_id})
        final = r.json()["data"]
        if final["complete"]:
            break
    assert final and final["complete"] is True
    weave = Weave.fold(app2.weft)
    plan_cell = weave.get(plan_id)
    assert plan_cell is not None
    assert plan_cell.content["status"] == "COMPLETED"


def test_readers_survive_projection_rebuild(client, env):
    acc = _accept(client, _propose(client)["id"])
    plan_id = acc["plan_id"]
    _run_to_completion(client, plan_id)
    app = env["app"]
    before_p = client.request("GET", "/api/v1/plans/proposals", csrf=False).json()
    before_a = client.request("GET", "/api/v1/agents/runs", csrf=False).json()
    before_t = client.request("GET", "/api/v1/tasks", csrf=False).json()

    app.driver = build_driver(app.weft)  # drop + rebuild EVERY projection from the Weft

    assert client.request("GET", "/api/v1/plans/proposals", csrf=False).json() == before_p
    assert client.request("GET", "/api/v1/agents/runs", csrf=False).json() == before_a
    assert client.request("GET", "/api/v1/tasks", csrf=False).json() == before_t


# ── auth discipline on the lane's surfaces (composes with the frozen contract) ─
def test_plan_surfaces_require_session_and_csrf(env):
    app = env["app"]
    assert app.dispatch("GET", "/api/v1/plans/proposals").status == 401
    assert app.dispatch("POST", "/api/v1/plans/propose", body='{"objective": "x"}').status == 401


# ── composition: the deterministic default composes the real product capabilities ──
def test_default_plan_composes_real_capabilities_with_selectors(client, env):
    """The offline default now proposes a COMPOSED plan — document ingestion, grounded
    Q&A, a bounded derivation, and a note — each carrying its required typed selector,
    over a richer dependency chain. All BASELINE, so it validates under the default held
    set (the default can never self-reject)."""
    data = _propose(client)
    caps = [s["capability"] for s in data["steps"]]
    assert set(caps) == {"local:ingest", "local:qa", "local:derive", "local:note"}
    qa = next(s for s in data["steps"] if s["capability"] == "local:qa")
    assert qa["selector"]["question"]
    ingest = next(s for s in data["steps"] if s["capability"] == "local:ingest")
    assert ingest["selector"]["document"]
    # a real dependency chain, not a flat list
    assert sum(1 for s in data["steps"] if s["depends_on"]) >= 3
    # baseline only ⇒ the recorded held set is the baseline default
    assert set(data["granted_capabilities"]) == {
        "local:derive",
        "local:note",
        "local:qa",
        "local:ingest",
    }


def test_accept_mints_typed_selectors_into_capability_selector(client, env):
    """Acceptance mints each composed step with its kind AND its selector fields folded
    into the durable ``required_capability_selector`` (the authorization key)."""
    proposal = _propose(client)
    acc = _accept(client, proposal["id"])
    weave = _weave(env)
    by_cap = {}
    for sid in acc["step_ids"]:
        sel = weave.get(sid).content["required_capability_selector"]
        by_cap[sel["capability"]] = sel
    assert by_cap["local:qa"].get("question")
    assert by_cap["local:ingest"].get("document")
    assert set(by_cap["local:note"].keys()) == {"capability"}  # a note carries no selector


def test_deterministic_plan_proposal_is_a_pure_function_with_stable_fingerprint():
    """Same request ⇒ byte-identical proposal (determinism: no clock, no random). The
    canonical fingerprint of the recorded content reproduces exactly — an incremental
    proposal and a rebuild of the same request agree."""
    from decima.kernel.hashing import content_id
    from decima.models.providers import ModelRequest
    from decima.services.api.models_setup import PlanAwareDeterministicProvider

    prov = PlanAwareDeterministicProvider(
        model="deterministic-offline", local=True, structured_output=True
    )
    req = ModelRequest(
        prompt="plan it",
        purpose="plan",
        context=OBJECTIVE,
        structured_schema=plan_service.PLAN_PROPOSAL_SCHEMA,
    )
    a = prov.complete(req).structured
    b = prov.complete(req).structured
    assert a is not None and a == b
    assert content_id({"proposal": a}) == content_id({"proposal": b})


# ── per-kind selector contracts (required typed fields; closed key set) ───────
def test_qa_step_requires_a_question_selector(client, env):
    steps = [{"id": "s1", "description": "answer", "depends_on": [], "capability": "local:qa"}]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "selector.question" in r.json()["error"]


def test_ingest_step_requires_a_document_selector(client, env):
    steps = [
        {
            "id": "s1",
            "description": "ingest",
            "depends_on": [],
            "capability": "local:ingest",
            "selector": {"source": "somewhere"},  # allowed key, but document is required
        }
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "selector.document" in r.json()["error"]


def test_unknown_selector_field_is_an_authority_request(client, env):
    steps = [
        {
            "id": "s1",
            "description": "answer",
            "depends_on": [],
            "capability": "local:qa",
            "selector": {"question": "q", "grants": ["root"]},
        }
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "unexpected fields" in r.json()["error"]


def test_executable_content_hidden_in_selector_rejected(client, env):
    steps = [
        {
            "id": "s1",
            "description": "answer",
            "depends_on": [],
            "capability": "local:qa",
            "selector": {"question": "run $(rm -rf /) please"},
        }
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "executable content" in r.json()["error"]


# ── the capability model: no weakening, no self-granted authority ─────────────
def test_over_privileged_known_capability_refused_without_grant(client, env):
    """A PRIVILEGED kind the requesting principal has not been granted is refused at
    validation with NO durable effect — a known capability, but not held."""
    steps = [
        {
            "id": "s1",
            "description": "checkpoint",
            "depends_on": [],
            "capability": "local:approval",
            "selector": {"approval": "TerminateAgent"},
        }
    ]
    _steer(env, _plan_body(steps=steps))
    n = len(_weave(env).of_type(plan_service.PLAN_PROPOSAL))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "over-privileged" in r.json()["error"]
    assert len(_weave(env).of_type(plan_service.PLAN_PROPOSAL)) == n  # nothing recorded


def test_privileged_workspace_step_requires_and_honors_explicit_grant(client, env):
    """The isolated-workspace kind is composable ONLY when the operator explicitly grants
    it. Granted, it validates, records, and mints — but the bounded pass does NOT auto-run
    it: a workspace effect belongs to the isolated worker, not this deterministic pass."""
    steps = [
        {
            "id": "s1",
            "description": "run the checks",
            "depends_on": [],
            "capability": "local:workspace",
            "selector": {"workspace": "scratch"},
        }
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422 and "over-privileged" in r.json()["error"]

    r = client.request(
        "POST",
        "/api/v1/plans/propose",
        body={"objective": OBJECTIVE, "capabilities": ["local:workspace"]},
    )
    assert r.status == 201, r.json()
    proposal = r.json()["data"]
    assert proposal["granted_capabilities"] == ["local:workspace"]
    assert proposal["steps"][0]["selector"] == {"workspace": "scratch"}
    acc = _accept(client, proposal["id"])
    sel = _weave(env).get(acc["step_ids"][0]).content["required_capability_selector"]
    assert sel == {"capability": "local:workspace", "workspace": "scratch"}
    data = _execute(client, acc["plan_id"])
    assert data["dispatched"] == []  # privileged: minted, not auto-run
    assert data["complete"] is False
    assert _weave(env).of_type(cells.RECEIPT) == []
    assert _weave(env).get(acc["step_ids"][0]).content["status"] in (
        "PENDING",
        "BLOCKED",
        "READY",
    )


def test_approval_step_must_name_a_real_gated_command(client, env):
    good = [
        {
            "id": "s1",
            "description": "gate",
            "depends_on": [],
            "capability": "local:approval",
            "selector": {"approval": "TerminateAgent"},  # a real GATED command
        }
    ]
    _steer(env, _plan_body(steps=good))
    r = client.request(
        "POST",
        "/api/v1/plans/propose",
        body={"objective": OBJECTIVE, "capabilities": ["local:approval"]},
    )
    assert r.status == 201, r.json()

    bad = [
        {
            "id": "s1",
            "description": "gate",
            "depends_on": [],
            "capability": "local:approval",
            "selector": {"approval": "GrantRootAccess"},  # invented authority
        }
    ]
    _steer(env, _plan_body(steps=bad))
    r = client.request(
        "POST",
        "/api/v1/plans/propose",
        body={"objective": OBJECTIVE, "capabilities": ["local:approval"]},
    )
    assert r.status == 422
    assert "authority request" in r.json()["error"]


def test_declared_capabilities_scope_narrows_what_is_composable(client, env):
    """An operator can DOWN-scope: declaring only ``local:qa`` makes any other kind
    over-privileged for this plan — the model cannot compose outside the grant."""
    steps = [
        {
            "id": "s1",
            "description": "answer",
            "depends_on": [],
            "capability": "local:qa",
            "selector": {"question": "q"},
        },
        {"id": "s2", "description": "note it", "depends_on": ["s1"], "capability": "local:note"},
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request(
        "POST",
        "/api/v1/plans/propose",
        body={"objective": OBJECTIVE, "capabilities": ["local:qa"]},
    )
    assert r.status == 422
    assert "over-privileged" in r.json()["error"]


def test_bad_capability_grant_is_a_bad_request(client, env):
    r = client.request(
        "POST",
        "/api/v1/plans/propose",
        body={"objective": OBJECTIVE, "capabilities": ["net:egress"]},
    )
    assert r.status == 400
    assert r.json()["reason_code"] == "BAD_REQUEST"
    r = client.request(
        "POST",
        "/api/v1/plans/propose",
        body={"objective": OBJECTIVE, "capabilities": "local:qa"},  # not a list
    )
    assert r.status == 400


def test_over_max_plan_steps_refused_with_no_effect(client, env):
    """The hard step cap holds even when the request asks for more: 33 steps against the
    32-step ceiling is refused, and nothing is recorded."""
    steps = [
        {"id": f"s{i}", "description": "x", "depends_on": [], "capability": "local:derive"}
        for i in range(plan_service.MAX_PLAN_STEPS + 1)
    ]
    _steer(env, _plan_body(steps=steps))
    n = len(_weave(env).of_type(plan_service.PLAN_PROPOSAL))
    r = client.request(
        "POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE, "max_steps": 128}
    )
    assert r.status == 422
    assert "exceeds" in r.json()["error"]
    assert len(_weave(env).of_type(plan_service.PLAN_PROPOSAL)) == n


def test_composed_graph_with_dangling_dependency_refused(client, env):
    """A richer graph is still fully validated: a dependency on a nonexistent step id is
    refused (no partial acceptance of a broken DAG)."""
    steps = [
        {
            "id": "s1",
            "description": "ingest",
            "depends_on": [],
            "capability": "local:ingest",
            "selector": {"document": "d"},
        },
        {
            "id": "s2",
            "description": "answer",
            "depends_on": ["ghost"],
            "capability": "local:qa",
            "selector": {"question": "q"},
        },
    ]
    _steer(env, _plan_body(steps=steps))
    r = client.request("POST", "/api/v1/plans/propose", body={"objective": OBJECTIVE})
    assert r.status == 422
    assert "unknown step" in r.json()["error"]


def test_emitted_events_stay_within_declared_families(client, env):
    from decima.services.api import events as ev_mod

    acc = _accept(client, _propose(client)["id"])
    _run_to_completion(client, acc["plan_id"])
    client.request("POST", "/api/v1/plans/cancel", body={"id": acc["plan_id"]})
    declared = {name for family in ev_mod.FAMILY_EVENTS.values() for name in family}
    emitted = {e.data.get("event") for e in env["app"].bus.since(0) if "event" in e.data}
    lane = {e for e in emitted if e and e.split(".")[0] in ("plan", "step", "agent")}
    assert lane <= declared
