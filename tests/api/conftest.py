"""In-process API harness: build the app over a temp Weft and drive it deterministically.

No real socket is opened — the tests call ``Application.dispatch`` directly (the WSGI
callable's deterministic core), so every assertion is reproducible. A tiny ``Client``
carries the session cookie + CSRF token the way a browser would.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field

import pytest

from decima.kernel.crypto import Keyring
from decima.services.api.auth import COOKIE_NAME
from decima.services.api.server import Application, build_application
from decima.services.api.users import UserDirectory, users_path

# Passwords the multi-user fixtures provision. Long enough for the directory's minimum,
# and distinct per user so a test can prove one user's credential never opens another's
# session or store.
ALICE_PASSWORD = "alice-correct-horse"
BOB_PASSWORD = "bob-correct-battery"


@dataclass
class Client:
    """A minimal browser-shaped client over ``Application.dispatch``: it remembers the
    session cookie and CSRF token and attaches them (plus optional reauth) per call."""

    app: Application
    pairing_secret: str
    cookie: str | None = None
    csrf: str | None = None
    # What this client re-presents in ``X-Reauth``. For the operator's pairing session
    # that is the pairing secret; for a per-user session it is that user's own password
    # (the host-wide token is deliberately not accepted there).
    reauth_secret: str | None = None
    username: str | None = None
    _extra: dict = field(default_factory=dict)

    def request(self, method, path, *, body=None, query=None, csrf=True, reauth=False, auth=True):
        headers: dict[str, str] = {}
        if auth and self.cookie:
            headers["cookie"] = self.cookie
        if csrf and self.csrf:
            headers["x-csrf-token"] = self.csrf
        if reauth:
            headers["x-reauth"] = (
                self.reauth_secret if self.reauth_secret is not None else self.pairing_secret
            )
        payload = None if body is None else json.dumps(body)
        return self.app.dispatch(method, path, headers=headers, body=payload, query=query)

    def login(self):
        r = self.app.dispatch(
            "POST",
            "/api/v1/session/login",
            body=json.dumps({"pairing_secret": self.pairing_secret}),
        )
        assert r.status == 200, r.json()
        set_cookie = [v for k, v in r.headers if k == "Set-Cookie"][0]
        token = set_cookie.split(";")[0].split("=", 1)[1]
        self.cookie = f"{COOKIE_NAME}={token}"
        body = r.json()
        assert isinstance(body, dict)
        self.csrf = body["csrf"]
        return r

    def login_user(self, username, password):
        """A REAL per-user login (username + password), the multi-user path."""
        r = self.app.dispatch(
            "POST",
            "/api/v1/session/login",
            body=json.dumps({"username": username, "password": password}),
        )
        assert r.status == 200, r.json()
        set_cookie = [v for k, v in r.headers if k == "Set-Cookie"][0]
        token = set_cookie.split(";")[0].split("=", 1)[1]
        self.cookie = f"{COOKIE_NAME}={token}"
        body = r.json()
        assert isinstance(body, dict)
        self.csrf = body["csrf"]
        self.username = username
        self.reauth_secret = password
        return r


@pytest.fixture()
def env():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    app, identity = build_application(db, seed=bytes(32), secure_cookie=True)
    return {"app": app, "identity": identity, "db": db}


@pytest.fixture()
def client(env):
    c = Client(app=env["app"], pairing_secret=env["identity"].pairing_secret)
    c.login()
    return c


@pytest.fixture()
def multiuser_env():
    """The same in-process harness with TWO provisioned users. Provisioning happens on the
    HOST side (the user directory beside the Weft), never over HTTP — there is no admin
    endpoint that could mint a user."""
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    keyring = Keyring(seed=bytes(32))
    users = UserDirectory(users_path(db), keyring)
    users.create("alice", ALICE_PASSWORD)
    users.create("bob", BOB_PASSWORD)
    app, identity = build_application(db, keyring=keyring, secure_cookie=True, users=users)
    return {"app": app, "identity": identity, "db": db, "users": users, "keyring": keyring}


@pytest.fixture()
def alice(multiuser_env):
    c = Client(app=multiuser_env["app"], pairing_secret=multiuser_env["identity"].pairing_secret)
    c.login_user("alice", ALICE_PASSWORD)
    return c


@pytest.fixture()
def bob(multiuser_env):
    c = Client(app=multiuser_env["app"], pairing_secret=multiuser_env["identity"].pairing_secret)
    c.login_user("bob", BOB_PASSWORD)
    return c
