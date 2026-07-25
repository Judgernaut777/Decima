"""The stdlib loopback server: real ephemeral socket + the bind guard.

Proves the WSGI adapter (`Application.__call__`) works over an actual HTTP request on
127.0.0.1, and that binding a non-loopback address is refused without an explicit opt-in
(a local daemon must not silently expose itself off-host).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.request

import pytest

from decima.kernel.crypto import Keyring
from decima.services.api.server import build_application, make_http_server
from decima.services.api.users import UserDirectory, users_path


def _multiuser_app(*, secure_cookie=True):
    """An app with real per-user authentication provisioned (the precondition the bind
    guard requires before it will expose anything off-host)."""
    db = os.path.join(tempfile.mkdtemp(), "w.db")
    keyring = Keyring(seed=bytes(32))
    directory = UserDirectory(users_path(db), keyring)
    directory.create("alice", "alice-correct-horse")
    return build_application(db, keyring=keyring, users=directory, secure_cookie=secure_cookie)


def test_refuses_nonloopback_bind_without_optin():
    db = os.path.join(tempfile.mkdtemp(), "w.db")
    app, _ = build_application(db, seed=bytes(32))
    with pytest.raises(ValueError):
        make_http_server(app, host="0.0.0.0", port=0)


def test_refuses_nonloopback_bind_without_per_user_auth():
    """Opting in is not enough: a pairing-secret-only daemon must never be exposed
    off-host, because that secret is one shared bearer token protected only by local
    file permissions."""
    db = os.path.join(tempfile.mkdtemp(), "w.db")
    app, _ = build_application(db, seed=bytes(32))
    assert not app.multiuser_enabled()
    with pytest.raises(ValueError, match="per-user authentication"):
        make_http_server(app, host="0.0.0.0", port=0, allow_nonloopback=True)


def test_refuses_plaintext_nonloopback_bind():
    """Even with users provisioned, a cleartext off-host bind is refused: cookies and
    passwords must not cross a network unprotected by default."""
    app, _ = _multiuser_app()
    assert app.multiuser_enabled()
    with pytest.raises(ValueError, match="PLAINTEXT"):
        make_http_server(app, host="0.0.0.0", port=0, allow_nonloopback=True)


def test_nonloopback_bind_warns_when_fully_configured(recwarn):
    """With ALL THREE gates deliberately satisfied the bind proceeds — and still warns,
    loudly, that the trust surface is now off-host."""
    app, _ = _multiuser_app()
    with pytest.warns(UserWarning, match="NON-LOOPBACK"):
        server = make_http_server(
            app,
            host="0.0.0.0",
            port=0,
            allow_nonloopback=True,
            allow_plaintext_remote=True,
        )
    server.server_close()


def test_real_loopback_request_roundtrip():
    db = os.path.join(tempfile.mkdtemp(), "w.db")
    app, identity = build_application(db, seed=bytes(32), secure_cookie=False)
    server = make_http_server(app, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request)  # serve one request
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # health is public
        with urllib.request.urlopen(f"{base}/api/v1/health", timeout=5) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode())
            assert body["status"] == "ok"
    finally:
        thread.join(timeout=5)
        server.server_close()

    # A second one-shot request proves login works over the socket too.
    server = make_http_server(app, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        payload = json.dumps({"pairing_secret": identity.pairing_secret}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/session/login",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert any(h.lower() == "set-cookie" for h, _ in resp.getheaders())
    finally:
        thread.join(timeout=5)
        server.server_close()
