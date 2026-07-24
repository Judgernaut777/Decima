"""Session auth, CSRF, and cookie hardening (invariant 3 — no ambient authority).

An unauthenticated request is rejected before any command or read runs. A mutating
request without a matching CSRF token is rejected. The session cookie is HTTP-only and
SameSite=Strict. Public endpoints (health, login) need no session.
"""

from __future__ import annotations

import json

import pytest

from decima.services.api.auth import (
    BAD_PAIRING,
    COOKIE_NAME,
    LOGIN_THROTTLED,
    AuthError,
    SessionStore,
)


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
    r = app.dispatch(
        "POST",
        "/api/v1/session/login",
        body=json.dumps({"pairing_secret": env["identity"].pairing_secret}),
    )
    assert r.status == 200


def test_bad_pairing_secret_creates_no_session(env):
    app = env["app"]
    r = app.dispatch("POST", "/api/v1/session/login", body=json.dumps({"pairing_secret": "wrong"}))
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
        "POST",
        "/api/v1/projects",
        headers={"cookie": client.cookie, "x-csrf-token": "forged"},
        body=json.dumps({"objective": "x"}),
    )
    assert r.status == 403
    assert r.json()["reason_code"] == "CSRF_FAILED"


def test_reads_need_no_csrf(client):
    r = client.request("GET", "/api/v1/tasks", csrf=False)
    assert r.status == 200


class _Clock:
    """A deterministic logical clock: no wall-clock, no sleeping."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_session_expires_after_absolute_ttl():
    clock = _Clock()
    store = SessionStore("s3cret", now=clock, ttl_seconds=100.0, idle_seconds=1000.0)
    s = store.login("op", "s3cret")
    assert store.get(s.token) is s
    clock.advance(100.0)
    assert store.get(s.token) is None  # past the absolute TTL
    assert s.token not in store._sessions  # and dropped, not just hidden


def test_session_idle_expiry_slides_on_access():
    clock = _Clock()
    store = SessionStore("s3cret", now=clock, ttl_seconds=10_000.0, idle_seconds=100.0)
    s = store.login("op", "s3cret")
    clock.advance(60.0)
    assert store.get(s.token) is not None  # under idle window; access slides it
    clock.advance(60.0)
    assert store.get(s.token) is not None  # last access was 60s ago, still alive
    clock.advance(100.0)
    assert store.get(s.token) is None  # idle window elapsed with no access


def test_session_cap_evicts_oldest():
    clock = _Clock()
    store = SessionStore("s3cret", now=clock, max_sessions=2)
    a = store.login("op", "s3cret")
    clock.advance(1.0)
    b = store.login("op", "s3cret")
    clock.advance(1.0)
    c = store.login("op", "s3cret")
    assert store.get(a.token) is None  # oldest (lowest seq) evicted
    assert store.get(b.token) is not None
    assert store.get(c.token) is not None
    assert len(store._sessions) == 2


def test_login_throttle_locks_out_then_recovers():
    clock = _Clock()
    store = SessionStore("s3cret", now=clock, max_login_failures=3, lockout_seconds=30.0)
    for _ in range(3):
        with pytest.raises(AuthError) as ei:
            store.login("op", "wrong")
        assert ei.value.reason_code == BAD_PAIRING
    # Locked out now — even the CORRECT secret is refused.
    with pytest.raises(AuthError) as ei:
        store.login("op", "s3cret")
    assert ei.value.reason_code == LOGIN_THROTTLED
    assert ei.value.http_status == 429
    clock.advance(30.0)
    s = store.login("op", "s3cret")  # lockout window elapsed
    assert store.get(s.token) is s


def test_repeated_bad_logins_are_throttled_through_the_app(env):
    app = env["app"]
    body = json.dumps({"pairing_secret": "wrong"})
    throttled = False
    for _ in range(20):
        r = app.dispatch("POST", "/api/v1/session/login", body=body)
        if r.status == 429:
            assert r.json()["reason_code"] == "LOGIN_THROTTLED"
            throttled = True
            break
        assert r.status == 401 and r.json()["reason_code"] == "BAD_PAIRING"
    assert throttled, "login endpoint never engaged the lockout"
