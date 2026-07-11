"""Session auth, CSRF, and cookie hardening (invariant 3 — no ambient authority).

An unauthenticated request is rejected before any command or read runs. A mutating
request without a matching CSRF token is rejected. The session cookie is HTTP-only and
SameSite=Strict. Public endpoints (health, login) need no session.
"""

from __future__ import annotations

import json

from decima.services.api.auth import COOKIE_NAME


def test_unauthenticated_request_is_rejected(env):
    app = env["app"]
    for method, path, body in (
        ("GET", "/api/v1/tasks", None),
        ("POST", "/api/v1/projects", "{}"),
        ("GET", "/api/v1/approvals", None),
    ):
        r = app.dispatch(method, path, body=body)
        assert r.status == 401, (path, r.json())
        assert r.json()["reason_code"] == "UNAUTHENTICATED"


def test_public_endpoints_need_no_session(env):
    app = env["app"]
    assert app.dispatch("GET", "/api/v1/health").status == 200
    r = app.dispatch("POST", "/api/v1/session/login",
                     body=json.dumps({"pairing_secret": env["identity"].pairing_secret}))
    assert r.status == 200


def test_bad_pairing_secret_creates_no_session(env):
    app = env["app"]
    r = app.dispatch("POST", "/api/v1/session/login",
                     body=json.dumps({"pairing_secret": "wrong"}))
    assert r.status == 401
    assert r.json()["reason_code"] == "BAD_PAIRING"
    assert not any(k == "Set-Cookie" for k, _ in r.headers)


def test_cookie_is_httponly_and_samesite_strict(client):
    login = client.login()
    set_cookie = [v for k, v in login.headers if k == "Set-Cookie"][0]
    assert "HttpOnly" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert "Secure" in set_cookie
    assert set_cookie.startswith(f"{COOKIE_NAME}=")


def test_mutation_without_csrf_is_rejected(client):
    r = client.request("POST", "/api/v1/projects", body={"objective": "x"}, csrf=False)
    assert r.status == 403
    assert r.json()["reason_code"] == "CSRF_FAILED"


def test_mutation_with_wrong_csrf_is_rejected(client):
    r = client.app.dispatch(
        "POST", "/api/v1/projects",
        headers={"cookie": client.cookie, "x-csrf-token": "forged"},
        body=json.dumps({"objective": "x"}),
    )
    assert r.status == 403
    assert r.json()["reason_code"] == "CSRF_FAILED"


def test_reads_need_no_csrf(client):
    r = client.request("GET", "/api/v1/tasks", csrf=False)
    assert r.status == 200
