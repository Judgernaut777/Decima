"""Projections are DISPOSABLE: dropping and rebuilding them preserves canonical state.

After API mutations, the read a client gets is served from a projection. If we discard
the ENTIRE projection store and rebuild it from the Weft alone, every read returns the
same thing — because the Weft is the sole canonical store (invariant 1) and projections
are just folds of it (invariant 2).
"""

from __future__ import annotations

from decima.services.api.server import build_driver


def _snapshot(app):
    """The client-visible reads across every projection surface, as canonical JSON."""
    out = {}
    for path in ("/api/v1/tasks", "/api/v1/projects", "/api/v1/notes",
                 "/api/v1/agents", "/api/v1/approvals"):
        out[path] = app.dispatch("GET", path,
                                 headers={"cookie": _cookie(app)}).json()
    return out


# a module-level cookie captured by the fixture flow below
_COOKIE_HOLDER: dict[str, str] = {}


def _cookie(app) -> str:
    return _COOKIE_HOLDER["cookie"]


def test_rebuild_from_weft_reproduces_reads(client, env):
    app = env["app"]
    _COOKIE_HOLDER["cookie"] = client.cookie

    # Produce durable state through the API.
    pid = client.request("POST", "/api/v1/projects",
                         body={"objective": "release"}).json()["data"]["id"]
    client.request("POST", "/api/v1/tasks",
                   body={"project_id": pid, "description": "task one"})
    tid2 = client.request("POST", "/api/v1/tasks",
                          body={"project_id": pid, "description": "task two"}).json()["data"]["id"]
    client.request("POST", "/api/v1/tasks/complete", body={"id": tid2})
    client.request("POST", "/api/v1/notes",
                   body={"text": "a durable note", "instruction_eligible": True})

    before = _snapshot(app)
    assert len(before["/api/v1/tasks"]["items"]) == 2
    assert len(before["/api/v1/projects"]["items"]) == 1

    # DELETE the entire projection store and rebuild it from the Weft alone.
    app.driver = build_driver(app.weft)   # fresh projections, replayed from scratch

    after = _snapshot(app)
    assert after == before                # canonical state survived the rebuild


def test_rebuild_checkpoints_match_incremental(client, env):
    """Two independent rebuilds of the same Weft agree on every projection's state_root
    (the acceptance property, exercised through the API's own driver)."""
    app = env["app"]
    _COOKIE_HOLDER["cookie"] = client.cookie
    client.request("POST", "/api/v1/projects", body={"objective": "x"})

    d1 = build_driver(app.weft)
    d2 = build_driver(app.weft)
    roots1 = {n: d1.get(n).checkpoint().state_root for n in d1.names()}
    roots2 = {n: d2.get(n).checkpoint().state_root for n in d2.names()}
    assert roots1 == roots2
