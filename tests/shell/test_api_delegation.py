"""The Shell delegates /api/* to the imported backend verbatim (no rewrite, no bypass)."""

from __future__ import annotations

import json


def test_health_delegates(shell):
    r = shell.handle("GET", "/api/v1/health")
    assert r.status == 200
    data = json.loads(r.body.decode())
    assert data["status"] == "ok"
    assert data["version"] == "v1"


def test_unauthenticated_read_is_401_through_shell(shell):
    r = shell.handle("GET", "/api/v1/tasks")
    assert r.status == 401


def test_login_flow_through_shell(env):
    shell = env["shell"]
    secret = env["identity"].pairing_secret
    r = shell.handle("POST", "/api/v1/session/login", body=json.dumps({"pairing_secret": secret}))
    assert r.status == 200
    assert any(k == "Set-Cookie" for k, _ in r.headers)
    body = json.loads(r.body.decode())
    assert body["ok"] is True
    assert body["csrf"]


def test_bad_pairing_is_rejected(shell):
    r = shell.handle("POST", "/api/v1/session/login", body=json.dumps({"pairing_secret": "wrong"}))
    assert r.status == 401


def test_authenticated_reads_delegate(client):
    for path in (
        "/api/v1/tasks",
        "/api/v1/projects",
        "/api/v1/agents",
        "/api/v1/notes",
        "/api/v1/approvals",
        "/api/v1/activity",
    ):
        r = client.get(path)
        assert r.status == 200, path
        assert "items" in json.loads(r.body.decode()), path


def test_mutation_requires_csrf_through_shell(client):
    # Without the CSRF token the backend must refuse the mutation (403).
    r = client.post("/api/v1/notes", {"text": "hello"}, csrf=False)
    assert r.status == 403


def test_create_note_roundtrip(client):
    r = client.post("/api/v1/notes", {"text": "a durable note"})
    assert r.status in (200, 201), r.body
    notes = json.loads(client.get("/api/v1/notes").body.decode())["items"]
    assert any(n["text"] == "a durable note" for n in notes)


def test_gated_command_defers_to_inbox(client):
    # A gated proposal is accepted (202) and appears as a pending approval — never runs now.
    r = client.post("/api/v1/agents/terminate", {"id": "agent-xyz"})
    assert r.status == 202, r.body
    approvals = json.loads(client.get("/api/v1/approvals").body.decode())["items"]
    assert any(a["state"] == "pending" for a in approvals)


def test_approve_requires_reauth_through_shell(client):
    client.post("/api/v1/agents/terminate", {"id": "agent-xyz"})
    approvals = json.loads(client.get("/api/v1/approvals").body.decode())["items"]
    item = [a for a in approvals if a["state"] == "pending"][0]["item"]
    # CSRF present but no reauth header -> 401 (the reauth gate rejects before the command).
    denied = client.post("/api/v1/approvals/approve", {"item": item})
    assert denied.status == 401
    # With reauth the gate is cleared and the command runs (its own result, not a 401).
    ok = client.post("/api/v1/approvals/approve", {"item": item}, reauth=client.pairing_secret)
    assert ok.status != 401, ok.body


def test_unknown_api_path_delegates_404(client):
    r = client.get("/api/v1/nope")
    assert r.status == 404
