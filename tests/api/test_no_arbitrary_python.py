"""No endpoint evaluates arbitrary Python (invariants 5, 7).

Command dispatch is a fixed name→handler table. An unknown command name is refused, a
code-shaped payload is stored/handled as inert DATA (never executed), and imported
content is quarantined (instruction_eligible=False). The API/kernel process runs nothing
untrusted.
"""

from __future__ import annotations

import json

from decima.services.api.commands import CommandService


def test_command_table_is_closed_and_has_no_eval():
    import inspect

    src = inspect.getsource(CommandService)
    assert "eval(" not in src
    assert "exec(" not in src
    assert "__import__" not in src


def test_unknown_command_is_refused(client, env):
    """Even reaching the command service directly, an unregistered command runs nothing."""
    app = env["app"]
    before = app.weft.count()
    result = app.commands.execute("os.system", {"cmd": "rm -rf /"})
    assert result.ok is False
    assert result.reason_code == "UNKNOWN_COMMAND"
    assert app.weft.count() == before


def test_code_shaped_note_body_is_stored_as_inert_data(client, env):
    """A note whose text is Python source is stored verbatim as text — never executed —
    and an untrusted note is stamped instruction_eligible=False."""
    payload = "__import__('os').system('touch /tmp/pwned')"
    r = client.request("POST", "/api/v1/notes", body={"text": payload})
    assert r.status == 201
    note_id = r.json()["data"]["id"]

    notes = client.request("GET", "/api/v1/notes").json()["items"]
    stored = next(n for n in notes if n["id"] == note_id)
    assert stored["text"] == payload  # kept as data, byte-for-byte
    assert stored["instruction_eligible"] is False  # untrusted → not an instruction
    assert stored["trust"] == "untrusted"


def test_imported_artifact_is_quarantined(client, env):
    app = env["app"]
    r = client.request(
        "POST", "/api/v1/artifacts/import", body={"name": "payload.py", "body": "print('x')"}
    )
    assert r.status == 201
    from decima.kernel.weave import Weave

    cell = Weave.fold(app.weft).get(r.json()["data"]["id"])
    assert cell.content["instruction_eligible"] is False
    assert cell.content["trust"] == "untrusted"


def test_non_object_json_body_is_rejected(client):
    """A JSON array/string body (not an object) is refused — args must be a mapping."""
    r = client.app.dispatch(
        "POST",
        "/api/v1/notes",
        headers={"cookie": client.cookie, "x-csrf-token": client.csrf},
        body=json.dumps(["not", "an", "object"]),
    )
    assert r.status == 400
