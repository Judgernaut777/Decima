"""Shell test harness: a real backend over a temp Weft, wrapped in the Shell host.

No socket is opened — tests call ``ShellApp.handle`` directly (its deterministic core),
so static-serving and API-delegation are both exercised in-process. A tiny ``ShellClient``
carries the session cookie + CSRF token the way a browser would.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from dataclasses import dataclass

import pytest

from decima.services.api.auth import COOKIE_NAME
from decima.services.api.server import build_application
from decima.shell.serve import build_shell

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "decima" / "shell" / "frontend"
SCREENS_DIR = FRONTEND / "js" / "screens"


@dataclass
class ShellClient:
    shell: object
    pairing_secret: str
    cookie: str | None = None
    csrf: str | None = None

    def get(self, path, *, query=None):
        headers = {}
        if self.cookie:
            headers["cookie"] = self.cookie
        return self.shell.handle("GET", path, headers=headers, query=query)

    def post(self, path, body=None, *, csrf=True, reauth=None):
        headers = {}
        if self.cookie:
            headers["cookie"] = self.cookie
        if csrf and self.csrf:
            headers["x-csrf-token"] = self.csrf
        if reauth:
            headers["x-reauth"] = reauth
        payload = None if body is None else json.dumps(body)
        return self.shell.handle("POST", path, headers=headers, body=payload)

    def login(self):
        r = self.shell.handle(
            "POST",
            "/api/v1/session/login",
            body=json.dumps({"pairing_secret": self.pairing_secret}),
        )
        assert r.status == 200, r.body
        set_cookie = [v for k, v in r.headers if k == "Set-Cookie"][0]
        token = set_cookie.split(";")[0].split("=", 1)[1]
        self.cookie = f"{COOKIE_NAME}={token}"
        self.csrf = json.loads(r.body.decode())["csrf"]
        return r


@pytest.fixture()
def env():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    backend, identity = build_application(db, seed=bytes(32), secure_cookie=False)
    shell = build_shell(backend)
    return {"shell": shell, "backend": backend, "identity": identity}


@pytest.fixture()
def shell(env):
    return env["shell"]


@pytest.fixture()
def client(env):
    c = ShellClient(shell=env["shell"], pairing_secret=env["identity"].pairing_secret)
    c.login()
    return c
