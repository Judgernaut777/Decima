"""The streaming endpoint relays UI events over a chunked, SSE-shaped response.

Mutations publish assistant/plan/step/approval events to the bus; the stream endpoint
emits them as SSE frames with a monotonically increasing logical ``id`` cursor (never
wall-clock — invariant 6), resumable via ``?since=``. The stream requires a session.
"""

from __future__ import annotations


def _parse_ids(sse_bytes: bytes) -> list[int]:
    ids = []
    for line in sse_bytes.decode("utf-8").splitlines():
        if line.startswith("id: "):
            ids.append(int(line[4:]))
    return ids


def test_stream_requires_authentication(env):
    r = env["app"].dispatch("GET", "/api/v1/stream")
    assert r.status == 401


def test_mutations_emit_ordered_stream_frames(client, env):
    pid = client.request("POST", "/api/v1/projects",
                         body={"objective": "s"}).json()["data"]["id"]
    client.request("POST", "/api/v1/tasks",
                   body={"project_id": pid, "description": "t"})
    client.request("POST", "/api/v1/notes", body={"text": "n"})

    r = client.request("GET", "/api/v1/stream")
    assert r.status == 200
    assert dict(r.headers)["Content-Type"] == "text/event-stream"
    ids = _parse_ids(r.body)
    assert ids == sorted(ids)          # monotonic logical cursor
    assert len(ids) >= 3
    # SSE frames carry event kinds we published.
    text = r.body.decode("utf-8")
    assert "event: plan" in text
    assert "event: step" in text


def test_stream_since_cursor_returns_only_the_tail(client, env):
    client.request("POST", "/api/v1/notes", body={"text": "first"})
    full = client.request("GET", "/api/v1/stream")
    first_ids = _parse_ids(full.body)
    assert first_ids

    client.request("POST", "/api/v1/notes", body={"text": "second"})
    tail = client.request("GET", "/api/v1/stream", query={"since": str(first_ids[-1])})
    tail_ids = _parse_ids(tail.body)
    assert tail_ids
    assert min(tail_ids) > first_ids[-1]
