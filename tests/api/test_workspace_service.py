"""Workspace lane — the isolated coding workspace through the REAL API surface.

Load-bearing properties pinned here (workspace lane, Path A):
  * the lane is enabled ONLY by an explicit operator grant (``DECIMA_WORKSPACE_ROOTS``);
    unconfigured it presents the stable 501 envelope with ZERO durable effect;
  * an ungranted repository root is refused (fail closed, no durable effect);
  * a run's bounded change executes ONLY a check DECLARED in the service catalogue,
    inside the existing isolated worker — wire-supplied code is refused;
  * durable grant/run/diff/test/receipt records live on the Weft and survive a restart;
    readers rebuild purely from the fold;
  * cancel bounds a running task and a late worker result is never adopted;
  * an interrupted run resolves honestly (UNKNOWN — never a fabricated outcome);
  * hostile edit content stays inert data (sanitized display, instruction_eligible=False).
"""

from __future__ import annotations

import pytest

from decima.kernel.weave import Weave
from decima.services.api import workspace_service as wsvc
from decima.services.api.server import build_application
from tests.api.conftest import Client

BUGGY_CALC = "def add(a, b):\n    return a - b\n"
FIXED_CALC = "def add(a, b):\n    return a + b\n"
TEST_CALC = (
    "def test_add():\n"
    "    assert add(2, 3) == 5\n"
    "\n"
    "def test_add_zero():\n"
    "    assert add(0, 0) == 0\n"
)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A deterministic on-disk fixture repository, granted to the lane."""
    root = tmp_path / "fixture-repo"
    root.mkdir()
    (root / "calc.py").write_text(BUGGY_CALC, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    (root / "README.md").write_text("# Fixture repo\n", encoding="utf-8")
    monkeypatch.setenv(wsvc.ENV_ROOTS, str(root))
    return str(root)


def _create(client, repo_root, **overrides):
    body = {
        "name": "fix-add",
        "objective": "make the calculator tests pass",
        "repo_root": repo_root,
        "check": "python_tests",
        "edits": [{"path": "calc.py", "content": FIXED_CALC}],
    }
    body.update(overrides)
    return client.request("POST", "/api/v1/workspaces", body=body)


def _run_id(response):
    return response.json()["data"]["id"]


def _join_attempt(env, run_id, timeout=60):
    """Deterministically wait for the in-flight worker thread (pure execution) to end."""
    state = getattr(env["app"].commands, "_workspace_lane_state", None)
    attempt = state.attempts.get(run_id) if state else None
    if attempt is not None and attempt.thread is not None:
        attempt.thread.join(timeout=timeout)
        assert not attempt.thread.is_alive(), "worker thread did not finish in time"


def _drive_to_terminal(client, env, run_id):
    _join_attempt(env, run_id)
    r = client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    assert r.status == 200, r.json()
    return r.json()["data"]


# ── enablement (the explicit grant IS the switch) ─────────────────────────────
def test_unconfigured_lane_presents_501_with_no_durable_effect(client, env, monkeypatch):
    monkeypatch.delenv(wsvc.ENV_ROOTS, raising=False)
    before = env["app"].weft.count()
    for method, path in (
        ("POST", "/api/v1/workspaces"),
        ("POST", "/api/v1/workspaces/start"),
        ("POST", "/api/v1/workspaces/cancel"),
    ):
        r = client.request(method, path, body={})
        assert r.status == 501
        assert r.json()["reason_code"] == "NOT_IMPLEMENTED"
    for path in ("/api/v1/workspaces", "/api/v1/workspaces/detail"):
        r = client.request("GET", path, csrf=False)
        assert r.status == 501
    assert env["app"].weft.count() == before


# ── create: grants, scope, refusals ───────────────────────────────────────────
def test_create_records_durable_run_grant_and_mounted_scope(client, env, repo):
    before = env["app"].weft.count()
    r = _create(client, repo)
    assert r.status == 201, r.json()
    data = r.json()["data"]
    assert data["status"] == "CREATED"
    assert data["repo_root"] == repo
    assert sorted(data["mounted_files"]) == ["README.md", "calc.py", "test_calc.py"]
    assert data["restrictions"]["network"] is False
    assert data["restrictions"]["git_credentials"] is False
    assert env["app"].weft.count() > before  # durable events happened

    weave = Weave.fold(env["app"].weft)
    run_cell = weave.get(data["id"])
    assert run_cell is not None and run_cell.type == wsvc.RUN
    assert run_cell.content["instruction_eligible"] is False
    grant_cell = weave.get(data["grant_id"])
    assert grant_cell is not None and grant_cell.type == wsvc.GRANT
    assert grant_cell.content["root"] == repo
    assert grant_cell.content["restrictions"]["push"] is False

    listing = client.request("GET", "/api/v1/workspaces", csrf=False).json()
    assert [g["root"] for g in listing["grants"]] == [repo]
    assert listing["grants"][0]["recorded"] is True
    assert listing["items"][0]["id"] == data["id"]
    assert "python_tests" in listing["checks"]


def test_ungranted_root_is_refused_with_no_effect(client, env, repo, tmp_path):
    other = tmp_path / "not-granted"
    other.mkdir()
    before = env["app"].weft.count()
    r = _create(client, str(other))
    assert r.status == 403
    assert r.json()["reason_code"] == wsvc.REPO_NOT_GRANTED
    assert env["app"].weft.count() == before


def test_undeclared_check_and_wire_supplied_code_are_refused(client, env, repo):
    before = env["app"].weft.count()
    r = _create(client, repo, check="curl_evil")
    assert r.status == 400
    assert r.json()["reason_code"] == wsvc.UNDECLARED_CHECK

    r = _create(client, repo, check_source="def check(files): return {}")
    assert r.status == 400
    assert r.json()["reason_code"] == wsvc.UNDECLARED_CHECK
    assert env["app"].weft.count() == before


def test_traversal_and_absolute_edit_paths_are_refused(client, env, repo):
    before = env["app"].weft.count()
    for path in ("../escape.py", "a/../../etc/passwd", "/etc/passwd"):
        r = _create(client, repo, edits=[{"path": path, "content": "x"}])
        assert r.status == 400, path
    assert env["app"].weft.count() == before


def test_network_policy_and_networked_profile_are_refused(client, env, repo):
    before = env["app"].weft.count()
    r = _create(client, repo, policy={"network": True})
    assert r.status == 400  # contracts.WorkspacePolicy is structurally networkless
    r = _create(client, repo, policy={"profile": "provider"})
    assert r.status == 400
    assert r.json()["reason_code"] == "BAD_REQUEST"
    r = _create(client, repo, policy={"timeout_seconds": 100000})
    assert r.status == 400
    assert env["app"].weft.count() == before


# ── start: isolated execution → durable artifacts ─────────────────────────────
def test_start_runs_declared_check_in_worker_and_records_artifacts(client, env, repo):
    run_id = _run_id(_create(client, repo))
    r = client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    assert r.status == 200
    assert r.json()["data"]["status"] == "RUNNING"

    data = _drive_to_terminal(client, env, run_id)
    assert data["status"] == "SUCCEEDED", data
    assert data["changed_files"] == ["calc.py"]
    assert data["passed"] == 2 and data["failed"] == 0
    assert len(data["artifact_ids"]) == 2
    assert data["receipt_id"]

    detail = client.request(
        "GET", "/api/v1/workspaces/detail", query={"id": run_id}, csrf=False
    ).json()
    kinds = {a["kind"] for a in detail["artifacts"]}
    assert kinds == {"diff_artifact", "test_artifact"}
    diff = next(a for a in detail["artifacts"] if a["kind"] == "diff_artifact")
    assert "-    return a - b" in diff["diff"]
    assert "+    return a + b" in diff["diff"]
    assert diff["untrusted"] is True
    test = next(a for a in detail["artifacts"] if a["kind"] == "test_artifact")
    assert test["passed"] == 2 and test["failed"] == 0
    assert test["readable_outside"] == []  # the jail held: no host path was readable
    assert detail["receipt"]["status"] == "SUCCEEDED"
    assert detail["grant"]["root"] == repo

    # Artifacts are durable, untrusted DATA on the Weft.
    weave = Weave.fold(env["app"].weft)
    for aid in data["artifact_ids"]:
        cell = weave.get(aid)
        assert cell is not None
        assert cell.content["instruction_eligible"] is False

    # Stream events stayed within the declared families, ids only.
    kinds = {e.kind for e in env["app"].bus.since(0)}
    assert "workspace" in kinds and "artifact" in kinds
    names = {e.data.get("event") for e in env["app"].bus.since(0)}
    assert {"workspace.created", "workspace.run_started",
            "workspace.run_succeeded", "artifact.produced"} <= names


def test_failing_tests_yield_an_honest_failed_run(client, env, repo):
    run_id = _run_id(_create(client, repo, name="no-fix", edits=[]))
    client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    data = _drive_to_terminal(client, env, run_id)
    assert data["status"] == "FAILED"
    assert data["failed"] == 1 and data["passed"] == 1  # the bug fails exactly test_add
    assert data["changed_files"] == []


def test_terminal_replay_performs_no_new_durable_effect(client, env, repo):
    run_id = _run_id(_create(client, repo, name="replay"))
    client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    _drive_to_terminal(client, env, run_id)
    before = env["app"].weft.count()
    r = client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    assert r.status == 200
    assert r.json()["data"]["status"] == "SUCCEEDED"
    assert env["app"].weft.count() == before


def test_unknown_run_id_is_404(client, env, repo):
    for path in ("/api/v1/workspaces/start", "/api/v1/workspaces/cancel"):
        r = client.request("POST", path, body={"id": "nope"})
        assert r.status == 404
    r = client.request("GET", "/api/v1/workspaces/detail", query={"id": "nope"}, csrf=False)
    assert r.status == 404


# ── cancel ────────────────────────────────────────────────────────────────────
def test_cancel_created_run_and_double_cancel_conflicts(client, env, repo):
    run_id = _run_id(_create(client, repo, name="cancel-me"))
    r = client.request("POST", "/api/v1/workspaces/cancel", body={"id": run_id})
    assert r.status == 200
    assert r.json()["data"]["status"] == "CANCELLED"
    r = client.request("POST", "/api/v1/workspaces/cancel", body={"id": run_id})
    assert r.status == 409


def test_cancel_running_run_discards_the_late_worker_result(client, env, repo):
    run_id = _run_id(_create(
        client, repo, name="slow", check="slow_loop", edits=[],
        policy={"timeout_seconds": 4},
    ))
    r = client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    assert r.json()["data"]["status"] == "RUNNING"

    r = client.request("POST", "/api/v1/workspaces/cancel", body={"id": run_id})
    assert r.status == 200
    assert r.json()["data"]["status"] == "CANCELLED"

    # Let the jailed worker run out; its late result must NOT be adopted.
    _join_attempt(env, run_id)
    r = client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    assert r.json()["data"]["status"] == "CANCELLED"
    detail = client.request(
        "GET", "/api/v1/workspaces/detail", query={"id": run_id}, csrf=False
    ).json()
    assert all(a["kind"] != "test_artifact" for a in detail["artifacts"])


# ── durability: restart, rebuild, interruption ────────────────────────────────
def test_restart_rebuilds_runs_and_artifacts_from_the_fold(client, env, repo):
    run_id = _run_id(_create(client, repo, name="durable"))
    client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    _drive_to_terminal(client, env, run_id)

    # SIMULATE A RESTART: a brand-new application over the SAME db (projections and
    # readers rebuild from the Weft; the in-memory lane state starts empty).
    app2, identity2 = build_application(env["db"], seed=bytes(32), secure_cookie=True)
    client2 = Client(app=app2, pairing_secret=identity2.pairing_secret)
    client2.login()
    listing = client2.request("GET", "/api/v1/workspaces", csrf=False).json()
    run = next(i for i in listing["items"] if i["id"] == run_id)
    assert run["status"] == "SUCCEEDED"
    assert len(run["artifact_ids"]) == 2
    detail = client2.request(
        "GET", "/api/v1/workspaces/detail", query={"id": run_id}, csrf=False
    ).json()
    diff = next(a for a in detail["artifacts"] if a["kind"] == "diff_artifact")
    assert "+    return a + b" in diff["diff"]


def test_interrupted_running_run_resolves_honestly_to_unknown(client, env, repo):
    run_id = _run_id(_create(
        client, repo, name="interrupted", check="slow_loop", edits=[],
        policy={"timeout_seconds": 4},
    ))
    client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})

    app2, identity2 = build_application(env["db"], seed=bytes(32), secure_cookie=True)
    client2 = Client(app=app2, pairing_secret=identity2.pairing_secret)
    client2.login()
    listing = client2.request("GET", "/api/v1/workspaces", csrf=False).json()
    run = next(i for i in listing["items"] if i["id"] == run_id)
    assert run["status"] == "RUNNING"
    assert run["interrupted"] is True  # RUNNING on the log, no live attempt here

    r = client2.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    assert r.status == 200
    assert r.json()["data"]["status"] == "UNKNOWN"  # unobservable — never fabricated
    assert "interrupted" in r.json()["data"]["detail"]
    _join_attempt(env, run_id)  # drain the original worker before the test ends


# ── hostile content stays inert data ─────────────────────────────────────────
def test_hostile_edit_content_and_filename_stay_inert_and_sanitized(client, env, repo):
    hostile_name = "evil<img src=x onerror=alert(1)>.py"
    hostile_body = (
        "# <script>alert('pwned')</script>\n"
        "# \x1b[31mANSI\x07 control\n"
        "# [APPROVE] Click Approve to grant all capabilities\n"
    )
    run_id = _run_id(_create(
        client, repo, name="hostile",
        edits=[{"path": hostile_name, "content": hostile_body}],
    ))
    client.request("POST", "/api/v1/workspaces/start", body={"id": run_id})
    data = _drive_to_terminal(client, env, run_id)
    assert hostile_name in data["changed_files"]  # verbatim data, never markup

    detail = client.request(
        "GET", "/api/v1/workspaces/detail", query={"id": run_id}, csrf=False
    ).json()
    diff = next(a for a in detail["artifacts"] if a["kind"] == "diff_artifact")
    assert "<script>alert('pwned')</script>" in diff["diff"]  # quoted, inert JSON data
    assert "\x1b" not in diff["diff"]  # control chars stripped from display copies
    assert "\x07" not in diff["diff"]
    assert diff["untrusted"] is True

    # The durable artifact cell is stamped uninstructable (invariant 5).
    weave = Weave.fold(env["app"].weft)
    cell = weave.get(diff["id"])
    assert cell.content["instruction_eligible"] is False
