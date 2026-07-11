"""A high-risk (gated) command CANNOT bypass approval (invariant 3).

Submitting a gated command returns APPROVAL_REQUIRED and performs NO effect — it only
enqueues a pending inbox item on the Weft. The effect runs solely when a human approves
(a recorded decision), which re-drives the same command. Denial leaves the effect unrun
forever.
"""

from __future__ import annotations

from decima.kernel.weave import Weave
from decima.runtime import cells
from decima.runtime.cells import AgentStatus


def _agent_status(app, agent_id):
    return Weave.fold(app.weft).get(agent_id).content.get("status")


def _has_type(app, type_):
    return bool(Weave.fold(app.weft).of_type(type_))


def test_export_is_deferred_then_enacted_by_approval(client, env):
    app = env["app"]
    # Import an artifact (an ordinary write), then try to export it (the gated effect).
    art = client.request("POST", "/api/v1/artifacts/import",
                         body={"name": "report", "body": "sensitive bytes"})
    art_id = art.json()["data"]["id"]

    r = client.request("POST", "/api/v1/artifacts/export", body={"id": art_id})
    assert r.status == 202
    assert r.json()["reason_code"] == "APPROVAL_REQUIRED"
    assert r.json()["required_approval"] is True
    # No effect: no export receipt exists yet.
    assert not _has_type(app, "artifact_export")
    item_id = r.json()["data"]["item"]

    # It shows up pending in the approvals read-model.
    approvals = client.request("GET", "/api/v1/approvals").json()["items"]
    assert any(a["item"] == item_id and a["state"] == "pending" for a in approvals)

    # Approve WITH reauth → the deferred export now runs.
    r = client.request("POST", "/api/v1/approvals/approve",
                       body={"item": item_id}, reauth=True)
    assert r.status == 200, r.json()
    assert r.json()["data"]["enacted"] is True
    assert _has_type(app, "artifact_export")


def test_gated_terminate_has_no_effect_until_approved(client, env):
    app = env["app"]
    # Seed a real agent to target (through the runtime seam, as the system would).
    agent_id = cells.create_agent(app.weft, app.identity.app,
                                  objective="worker", principal=app.identity.human)

    r = client.request("POST", "/api/v1/agents/terminate", body={"id": agent_id})
    assert r.status == 202
    assert r.json()["reason_code"] == "APPROVAL_REQUIRED"
    # Fail closed: the agent is NOT terminated by merely submitting the command.
    assert _agent_status(app, agent_id) != AgentStatus.TERMINATED
    item_id = r.json()["data"]["item"]

    # Deny it → the effect never runs.
    r = client.request("POST", "/api/v1/approvals/deny",
                       body={"item": item_id, "reason": "not now"})
    assert r.status == 200, r.json()
    assert _agent_status(app, agent_id) != AgentStatus.TERMINATED

    # A denied item cannot then be approved (fail closed, decided once).
    r = client.request("POST", "/api/v1/approvals/approve",
                       body={"item": item_id}, reauth=True)
    assert r.status == 409
    assert r.json()["reason_code"] == "ALREADY_DECIDED"
    assert _agent_status(app, agent_id) != AgentStatus.TERMINATED


def test_approval_endpoint_requires_reauth(client, env):
    """Clearing a Morta gate needs a fresh reauth even with a valid session + CSRF."""
    app = env["app"]
    art_id = client.request("POST", "/api/v1/artifacts/import",
                            body={"name": "r", "body": "x"}).json()["data"]["id"]
    item_id = client.request("POST", "/api/v1/artifacts/export",
                             body={"id": art_id}).json()["data"]["item"]

    # No reauth header → refused, no effect.
    r = client.request("POST", "/api/v1/approvals/approve",
                       body={"item": item_id}, reauth=False)
    assert r.status == 401
    assert r.json()["reason_code"] == "REAUTH_REQUIRED"
    assert not _has_type(app, "artifact_export")
