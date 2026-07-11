"""Every durable API mutation becomes one+ accepted Weft events (invariant 1).

The web layer never writes storage directly: it goes through the command service, which
asserts Cells the signed log accepts. So a successful mutation must (a) advance the Weft
count and (b) name the exact events it produced — and those ids must be present, verified,
in the log's tail.
"""

from __future__ import annotations

from decima.kernel.weft import Weft


def _events_present(app, event_ids):
    weft: Weft = app.weft
    ids_on_log = {ev.id for ev in weft.events()}  # events() re-verifies each on read
    return all(eid in ids_on_log for eid in event_ids)


def test_create_project_and_task_emit_accepted_events(client, env):
    app = env["app"]
    before = app.weft.count()

    r = client.request("POST", "/api/v1/projects", body={"objective": "ship the thing"})
    assert r.status == 201, r.json()
    project_events = r.json()["event_ids"]
    assert len(project_events) >= 1
    pid = r.json()["data"]["id"]

    r = client.request("POST", "/api/v1/tasks",
                       body={"project_id": pid, "description": "do the work"})
    assert r.status == 201, r.json()
    task_events = r.json()["event_ids"]
    assert len(task_events) >= 1

    assert app.weft.count() > before
    assert _events_present(app, project_events + task_events)


def test_note_lifecycle_each_step_emits_events(client, env):
    app = env["app"]
    r = client.request("POST", "/api/v1/notes",
                       body={"text": "remember the milk", "instruction_eligible": True})
    assert r.status == 201, r.json()
    note_id = r.json()["data"]["id"]
    assert len(r.json()["event_ids"]) >= 1

    r = client.request("POST", "/api/v1/notes/update",
                       body={"id": note_id, "text": "remember the oat milk"})
    assert r.status == 200, r.json()
    assert len(r.json()["event_ids"]) >= 1

    r = client.request("POST", "/api/v1/notes/retract", body={"id": note_id})
    assert r.status == 200, r.json()
    assert len(r.json()["event_ids"]) >= 1
    assert _events_present(app, r.json()["event_ids"])


def test_failed_mutation_emits_no_events(client, env):
    """A rejected command (missing field) produces NO Weft event — fail closed."""
    app = env["app"]
    before = app.weft.count()
    r = client.request("POST", "/api/v1/notes", body={})  # missing 'text'
    assert r.status == 400, r.json()
    assert r.json()["reason_code"] == "BAD_REQUEST"
    assert app.weft.count() == before
