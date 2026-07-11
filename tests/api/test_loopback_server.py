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

from decima.services.api.server import build_application, make_http_server


def test_refuses_nonloopback_bind_without_optin():
    db = os.path.join(tempfile.mkdtemp(), "w.db")
    app, _ = build_application(db, seed=bytes(32))
    with pytest.raises(ValueError):
        make_http_server(app, host="0.0.0.0", port=0)


def test_nonloopback_bind_warns_when_optin(recwarn):
    db = os.path.join(tempfile.mkdtemp(), "w.db")
    app, _ = build_application(db, seed=bytes(32))
    # Bind to a loopback-family test that still exercises the warning path is not
    # possible; instead assert the guard emits a warning for a routable host with opt-in.
    with pytest.warns(UserWarning):
        server = make_http_server(app, host="0.0.0.0", port=0, allow_nonloopback=True)
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
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert any(h.lower() == "set-cookie" for h, _ in resp.getheaders())
    finally:
        thread.join(timeout=5)
        server.server_close()
