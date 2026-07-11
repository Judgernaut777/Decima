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

from decima.services.api.auth import COOKIE_NAME
from decima.services.api.server import build_application


@dataclass
class Client:
    """A minimal browser-shaped client over ``Application.dispatch``: it remembers the
    session cookie and CSRF token and attaches them (plus optional reauth) per call."""

    app: object
    pairing_secret: str
    cookie: str | None = None
    csrf: str | None = None
    _extra: dict = field(default_factory=dict)

    def request(self, method, path, *, body=None, query=None, csrf=True,
                reauth=False, auth=True):
        headers: dict[str, str] = {}
        if auth and self.cookie:
            headers["cookie"] = self.cookie
        if csrf and self.csrf:
            headers["x-csrf-token"] = self.csrf
        if reauth:
            headers["x-reauth"] = self.pairing_secret
        payload = None if body is None else json.dumps(body)
        return self.app.dispatch(method, path, headers=headers, body=payload, query=query)

    def login(self):
        r = self.app.dispatch("POST", "/api/v1/session/login",
                              body=json.dumps({"pairing_secret": self.pairing_secret}))
        assert r.status == 200, r.json()
        set_cookie = [v for k, v in r.headers if k == "Set-Cookie"][0]
        token = set_cookie.split(";")[0].split("=", 1)[1]
        self.cookie = f"{COOKIE_NAME}={token}"
        self.csrf = r.json()["csrf"]
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
