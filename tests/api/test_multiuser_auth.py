"""Multi-user identity, real authentication, and per-user isolation (T3.2).

The two headline properties: an UNAUTHENTICATED request is still refused before anything
runs, and user A can neither READ nor ACT ON user B's cells. Isolation is structural (each
user folds their own signed store), so the tests assert it through the HTTP surface the
way a browser would — and also assert the credential discipline (salted, hashed, never
plaintext, non-enumerating) and that no admin/ambient path exists.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile

import pytest

from decima.kernel.crypto import Keyring
from decima.services.api import routes
from decima.services.api.tenancy import store_path_for
from decima.services.api.users import (
    MIN_PASSWORD_LENGTH,
    UserDirectory,
    UserError,
    users_path,
)
from tests.api.conftest import ALICE_PASSWORD, BOB_PASSWORD, Client


# ── identity: a user is a principal, and the session is authorized as it ──────
def test_user_login_binds_the_session_to_that_users_principal(alice, multiuser_env):
    r = alice.request("GET", "/api/v1/session")
    assert r.status == 200
    body = r.json()
    assert body["username"] == "alice"
    assert body["multiuser"] is True
    assert body["principal"] == multiuser_env["users"].principal_of("alice")
    # A user principal is a real Decima principal id, and NOT the daemon's operator.
    assert body["principal"].startswith("prn_")
    assert body["principal"] != multiuser_env["identity"].human


def test_two_users_get_two_distinct_principals(multiuser_env):
    users = multiuser_env["users"]
    assert users.principal_of("alice") != users.principal_of("bob")
    assert users.usernames() == ["alice", "bob"]


def test_unauthenticated_requests_are_still_rejected_with_multiuser_enabled(multiuser_env):
    app = multiuser_env["app"]
    for method, path, body in (
        ("GET", "/api/v1/tasks", None),
        ("GET", "/api/v1/notes", None),
        ("POST", "/api/v1/projects", "{}"),
        ("POST", "/api/v1/notes", '{"text": "x"}'),
        ("GET", "/api/v1/stream", None),
        ("POST", "/api/v1/session/password", "{}"),
    ):
        r = app.dispatch(method, path, body=body)
        assert r.status == 401, (path, r.json())
        assert r.json()["reason_code"] == "UNAUTHENTICATED"


# ── isolation: A cannot READ B's cells ───────────────────────────────────────
def test_user_a_cannot_read_user_b_cells(alice, bob):
    created = alice.request("POST", "/api/v1/notes", body={"text": "alice private note"})
    assert created.status == 201, created.json()
    note_id = created.json()["data"]["id"]

    mine = alice.request("GET", "/api/v1/notes").json()["items"]
    assert [n["id"] for n in mine] == [note_id]

    theirs = bob.request("GET", "/api/v1/notes")
    assert theirs.status == 200
    assert theirs.json()["items"] == []


def test_isolation_covers_every_reader_route(alice, bob):
    assert alice.request("POST", "/api/v1/notes", body={"text": "n"}).status == 201
    project = alice.request("POST", "/api/v1/projects", body={"objective": "alice plan"})
    assert project.status == 201, project.json()
    assert (
        alice.request(
            "POST",
            "/api/v1/tasks",
            body={"project_id": project.json()["data"]["id"], "description": "do it"},
        ).status
        == 201
    )

    reader_paths = [r.path for r in routes.ROUTES if r.kind == routes.READER]
    assert reader_paths, "the route table declares no readers"
    for path in reader_paths:
        r = bob.request("GET", path)
        # 501 = the lane is not enabled in this environment (e.g. /api/v1/workspaces with no
        # granted DECIMA_WORKSPACE_ROOTS). A disabled lane returns no rows at all, so it
        # cannot leak another user's data — the isolation property this test guards is about
        # what a reader RETURNS, and there is nothing to return.
        assert r.status in (200, 400, 404, 501), (path, r.json())
        if r.status == 200:
            payload = json.dumps(r.json())
            assert "alice plan" not in payload, path
            assert project.json()["data"]["id"] not in payload, path


def test_activity_and_stream_do_not_leak_across_users(alice, bob):
    assert alice.request("POST", "/api/v1/notes", body={"text": "alice only"}).status == 201
    assert alice.request("GET", "/api/v1/activity").json()["items"]
    assert bob.request("GET", "/api/v1/activity").json()["items"] == []
    bob_stream = bob.request("GET", "/api/v1/stream")
    assert bob_stream.status == 200
    assert b"note_created" not in bob_stream.body
    alice_stream = alice.request("GET", "/api/v1/stream")
    assert b"note_created" in alice_stream.body


def test_operator_pairing_session_sees_neither_users_cells(alice, multiuser_env):
    assert alice.request("POST", "/api/v1/notes", body={"text": "alice only"}).status == 201
    operator = Client(
        app=multiuser_env["app"], pairing_secret=multiuser_env["identity"].pairing_secret
    )
    operator.login()
    assert operator.request("GET", "/api/v1/notes").json()["items"] == []


# ── isolation: A cannot ACT ON B's cells ─────────────────────────────────────
def test_user_a_cannot_mutate_user_b_cells(alice, bob):
    created = alice.request("POST", "/api/v1/notes", body={"text": "original"})
    note_id = created.json()["data"]["id"]

    for path, body in (
        ("/api/v1/notes/update", {"id": note_id, "text": "hijacked"}),
        ("/api/v1/notes/retract", {"id": note_id}),
    ):
        r = bob.request("POST", path, body=body)
        assert r.status == 404, (path, r.json())
        assert r.json()["reason_code"] == "NOT_FOUND"

    still_mine = alice.request("GET", "/api/v1/notes").json()["items"]
    assert [n["text"] for n in still_mine] == ["original"]


def test_user_a_cannot_complete_user_b_tasks(alice, bob):
    project = alice.request("POST", "/api/v1/projects", body={"objective": "o"})
    task = alice.request(
        "POST",
        "/api/v1/tasks",
        body={"project_id": project.json()["data"]["id"], "description": "d"},
    )
    r = bob.request("POST", "/api/v1/tasks/complete", body={"id": task.json()["data"]["id"]})
    assert r.status == 404
    assert r.json()["reason_code"] == "NOT_FOUND"


def test_user_a_cannot_approve_user_b_gated_item(alice, bob):
    imported = alice.request(
        "POST", "/api/v1/artifacts/import", body={"name": "a.txt", "body": "secret"}
    )
    assert imported.status == 201, imported.json()
    deferred = alice.request(
        "POST", "/api/v1/artifacts/export", body={"id": imported.json()["data"]["id"]}
    )
    assert deferred.status == 202
    assert deferred.json()["reason_code"] == "APPROVAL_REQUIRED"
    item = deferred.json()["data"]["item"]

    # Bob passes his OWN reauth (so this is not a reauth failure) and still cannot reach
    # an approval item that lives in Alice's store.
    stolen = bob.request("POST", "/api/v1/approvals/approve", body={"item": item}, reauth=True)
    assert stolen.status == 404
    assert stolen.json()["reason_code"] == "NOT_FOUND"
    assert bob.request("GET", "/api/v1/approvals").json()["items"] == []


# ── reauth is the USER's own credential, never the host-wide token ───────────
def test_reauth_for_a_user_session_requires_that_users_password(alice, multiuser_env):
    imported = alice.request(
        "POST", "/api/v1/artifacts/import", body={"name": "a.txt", "body": "data"}
    )
    deferred = alice.request(
        "POST", "/api/v1/artifacts/export", body={"id": imported.json()["data"]["id"]}
    )
    item = deferred.json()["data"]["item"]

    # No reauth at all.
    refused = alice.request("POST", "/api/v1/approvals/approve", body={"item": item})
    assert refused.status == 401
    assert refused.json()["reason_code"] == "REAUTH_REQUIRED"

    # The HOST-WIDE pairing secret must NOT stand in for a person's credential.
    alice.reauth_secret = multiuser_env["identity"].pairing_secret
    with_pairing = alice.request(
        "POST", "/api/v1/approvals/approve", body={"item": item}, reauth=True
    )
    assert with_pairing.status == 401
    assert with_pairing.json()["reason_code"] == "REAUTH_REQUIRED"

    # Another user's password does not work either.
    alice.reauth_secret = BOB_PASSWORD
    with_other = alice.request(
        "POST", "/api/v1/approvals/approve", body={"item": item}, reauth=True
    )
    assert with_other.status == 401
    assert with_other.json()["reason_code"] == "REAUTH_REQUIRED"

    # Her own password does.
    alice.reauth_secret = ALICE_PASSWORD
    approved = alice.request("POST", "/api/v1/approvals/approve", body={"item": item}, reauth=True)
    assert approved.status == 200, approved.json()
    assert approved.json()["data"]["enacted"] is True


def test_operator_reauth_still_uses_the_pairing_secret(env, client):
    imported = client.request(
        "POST", "/api/v1/artifacts/import", body={"name": "a.txt", "body": "data"}
    )
    deferred = client.request(
        "POST", "/api/v1/artifacts/export", body={"id": imported.json()["data"]["id"]}
    )
    item = deferred.json()["data"]["item"]
    approved = client.request("POST", "/api/v1/approvals/approve", body={"item": item}, reauth=True)
    assert approved.status == 200, approved.json()


# ── credential discipline ───────────────────────────────────────────────────
def test_bad_credentials_create_no_session_and_do_not_leak_existence(multiuser_env):
    app = multiuser_env["app"]
    wrong_password = app.dispatch(
        "POST",
        "/api/v1/session/login",
        body=json.dumps({"username": "alice", "password": "not-the-password"}),
    )
    unknown_user = app.dispatch(
        "POST",
        "/api/v1/session/login",
        body=json.dumps({"username": "nobody", "password": "not-the-password"}),
    )
    for r in (wrong_password, unknown_user):
        assert r.status == 401
        assert r.json()["reason_code"] == "BAD_CREDENTIALS"
        assert not any(k == "Set-Cookie" for k, _ in r.headers)
    # Byte-identical refusals: the endpoint is not a user enumeration oracle.
    assert wrong_password.body == unknown_user.body


def test_username_login_is_never_satisfied_by_the_pairing_secret(multiuser_env):
    r = multiuser_env["app"].dispatch(
        "POST",
        "/api/v1/session/login",
        body=json.dumps(
            {"username": "alice", "password": multiuser_env["identity"].pairing_secret}
        ),
    )
    assert r.status == 401
    assert r.json()["reason_code"] == "BAD_CREDENTIALS"


def test_credentials_on_disk_are_hashed_salted_and_mode_0600():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    keyring = Keyring(seed=bytes(32))
    directory = UserDirectory(users_path(db), keyring)
    shared = "shared-passphrase-x"
    directory.create("carol", shared)
    directory.create("dave", shared)

    path = users_path(db)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    raw = open(path, encoding="utf-8").read()
    assert shared not in raw  # no plaintext, anywhere
    stored = json.loads(raw)["users"]
    salts = {u["salt"] for u in stored}
    hashes = {u["hash"] for u in stored}
    assert len(salts) == 2, "each user must get its own salt"
    assert len(hashes) == 2, "the same password must not produce the same hash"
    for entry in stored:
        assert "password" not in entry
        assert entry["n"] >= 1 << 14  # work factor is stored per record

    assert directory.verify_password("carol", shared) is True
    assert directory.verify_password("carol", shared + "!") is False
    assert directory.verify_password("ghost", shared) is False
    # The public projection of a record carries no credential material.
    assert set(directory.public_records()[0]) == {"username", "principal", "disabled"}


def test_directory_refuses_weak_and_malformed_input():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    directory = UserDirectory(users_path(db), Keyring(seed=bytes(32)))
    with pytest.raises(UserError):
        directory.create("alice", "x" * (MIN_PASSWORD_LENGTH - 1))
    with pytest.raises(UserError):
        directory.create("../escape", "a-long-enough-password")
    with pytest.raises(UserError):
        directory.create("Alice", "a-long-enough-password")
    directory.create("alice", "a-long-enough-password")
    with pytest.raises(UserError):
        directory.create("alice", "another-long-password")


def test_tampered_directory_fails_closed_on_load():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    keyring = Keyring(seed=bytes(32))
    UserDirectory(users_path(db), keyring).create("alice", ALICE_PASSWORD)
    path = users_path(db)
    payload = json.loads(open(path, encoding="utf-8").read())
    payload["users"][0]["principal"] = "prn_" + "a" * 52
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    with pytest.raises(UserError, match="principal mismatch"):
        UserDirectory(path, Keyring(seed=bytes(32)))


def test_disabled_user_cannot_log_in_and_loses_store_resolution(multiuser_env, alice):
    assert alice.request("GET", "/api/v1/notes").status == 200
    multiuser_env["users"].set_disabled("alice", True)
    # The live session stops resolving to a store — fail closed, not "serve anyway".
    after = alice.request("GET", "/api/v1/notes")
    assert after.status == 401
    assert after.json()["reason_code"] == "UNAUTHENTICATED"
    r = multiuser_env["app"].dispatch(
        "POST",
        "/api/v1/session/login",
        body=json.dumps({"username": "alice", "password": ALICE_PASSWORD}),
    )
    assert r.status == 401
    assert r.json()["reason_code"] == "BAD_CREDENTIALS"


# ── throttling is per identity, not global ──────────────────────────────────
def test_failed_logins_throttle_the_target_user_only(multiuser_env):
    app = multiuser_env["app"]
    bad = json.dumps({"username": "alice", "password": "wrong-but-long-enough"})
    throttled = False
    for _ in range(20):
        r = app.dispatch("POST", "/api/v1/session/login", body=bad)
        if r.status == 429:
            assert r.json()["reason_code"] == "LOGIN_THROTTLED"
            throttled = True
            break
        assert r.status == 401 and r.json()["reason_code"] == "BAD_CREDENTIALS"
    assert throttled, "per-user login never engaged the lockout"

    # Even Alice's CORRECT password is refused while she is locked out ...
    locked = app.dispatch(
        "POST",
        "/api/v1/session/login",
        body=json.dumps({"username": "alice", "password": ALICE_PASSWORD}),
    )
    assert locked.status == 429
    # ... and Bob is unaffected: one user's brute force must not lock everyone out.
    ok = app.dispatch(
        "POST",
        "/api/v1/session/login",
        body=json.dumps({"username": "bob", "password": BOB_PASSWORD}),
    )
    assert ok.status == 200, ok.json()


# ── self-service password change: no admin path ─────────────────────────────
def test_user_can_rotate_only_their_own_password(bob, multiuser_env):
    wrong = bob.request(
        "POST",
        "/api/v1/session/password",
        body={"current_password": "not-it-at-all", "new_password": "bob-new-password-1"},
    )
    assert wrong.status == 401
    assert wrong.json()["reason_code"] == "BAD_CREDENTIALS"

    weak = bob.request(
        "POST",
        "/api/v1/session/password",
        body={"current_password": BOB_PASSWORD, "new_password": "short"},
    )
    assert weak.status == 400

    ok = bob.request(
        "POST",
        "/api/v1/session/password",
        body={"current_password": BOB_PASSWORD, "new_password": "bob-new-password-1"},
    )
    assert ok.status == 200, ok.json()

    users = multiuser_env["users"]
    assert users.verify_password("bob", "bob-new-password-1") is True
    assert users.verify_password("bob", BOB_PASSWORD) is False
    # Alice is untouched: the endpoint takes no username, so it cannot reach her.
    assert users.verify_password("alice", ALICE_PASSWORD) is True


def test_password_change_is_refused_for_the_operator_session(multiuser_env):
    operator = Client(
        app=multiuser_env["app"], pairing_secret=multiuser_env["identity"].pairing_secret
    )
    operator.login()
    r = operator.request(
        "POST",
        "/api/v1/session/password",
        body={"current_password": "x" * 20, "new_password": "y" * 20},
    )
    assert r.status == 403
    assert r.json()["reason_code"] == "NO_USER_CREDENTIAL"


def test_no_route_can_create_or_administer_users():
    """Provisioning must stay a host-side act: an endpoint that minted users or reset
    other people's credentials would be the ambient admin authority Law 2 forbids."""
    paths = {r.path for r in routes.ROUTES}
    for forbidden in (
        "/api/v1/users",
        "/api/v1/users/create",
        "/api/v1/users/delete",
        "/api/v1/users/disable",
        "/api/v1/users/password",
        "/api/v1/admin",
    ):
        assert forbidden not in paths


# ── store scoping mechanics ─────────────────────────────────────────────────
def test_each_user_gets_their_own_store_file(alice, bob, multiuser_env):
    assert alice.request("POST", "/api/v1/notes", body={"text": "a"}).status == 201
    assert bob.request("POST", "/api/v1/notes", body={"text": "b"}).status == 201
    users = multiuser_env["users"]
    db = multiuser_env["db"]
    paths = [store_path_for(db, users.principal_of(n)) for n in ("alice", "bob")]
    assert paths[0] != paths[1]
    for path in paths:
        assert os.path.isfile(path)
    assert stat.S_IMODE(os.stat(os.path.dirname(paths[0])).st_mode) == 0o700
    # And the operator's own store is a third, separate file.
    assert os.path.abspath(db) not in {os.path.abspath(p) for p in paths}


def test_session_whose_principal_is_not_a_known_user_is_refused(multiuser_env):
    app = multiuser_env["app"]
    # A session minted for a username/principal pair the directory does not vouch for
    # must never resolve to a store.
    forged = app.sessions.begin_session("prn_" + "b" * 52, username="alice")
    r = app.dispatch("GET", "/api/v1/notes", headers={"cookie": f"decima_session={forged.token}"})
    assert r.status == 401
    assert r.json()["reason_code"] == "UNAUTHENTICATED"


def test_context_cache_eviction_keeps_each_user_isolated(multiuser_env):
    app = multiuser_env["app"]
    app._max_user_contexts = 1  # force an eviction between the two users
    a = Client(app=app, pairing_secret=multiuser_env["identity"].pairing_secret)
    a.login_user("alice", ALICE_PASSWORD)
    b = Client(app=app, pairing_secret=multiuser_env["identity"].pairing_secret)
    b.login_user("bob", BOB_PASSWORD)
    assert a.request("POST", "/api/v1/notes", body={"text": "alice"}).status == 201
    assert b.request("POST", "/api/v1/notes", body={"text": "bob"}).status == 201
    # Reopened from disk after eviction — the store, not the cache, is canonical.
    assert [n["text"] for n in a.request("GET", "/api/v1/notes").json()["items"]] == ["alice"]
    assert [n["text"] for n in b.request("GET", "/api/v1/notes").json()["items"]] == ["bob"]


def test_single_user_daemon_reports_multiuser_disabled(env):
    app = env["app"]
    assert app.multiuser_enabled() is False
    assert app.dispatch("GET", "/api/v1/health").json()["multiuser"] is False


def test_provisioned_users_are_picked_up_on_restart(multiuser_env):
    """A warm restart over the same directory reproduces the same principals (the pid is
    derived from the username) and the same credentials still verify."""
    from decima.services.api.server import build_application

    db = multiuser_env["db"]
    app2, _ = build_application(db, keyring=Keyring(seed=bytes(32)), secure_cookie=True)
    assert app2.multiuser_enabled() is True  # users.json beside the Weft was found
    client = Client(app=app2, pairing_secret="unused")
    client.login_user("alice", ALICE_PASSWORD)
    assert client.request("GET", "/api/v1/session").json()["principal"] == multiuser_env[
        "users"
    ].principal_of("alice")
