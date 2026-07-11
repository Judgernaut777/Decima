"""The Shell host serves the trusted static frontend and confines path resolution."""

from __future__ import annotations

from decima.shell import serve as serve_mod


def test_index_served_at_root(shell):
    r = shell.handle("GET", "/")
    assert r.status == 200
    body = r.body.decode("utf-8")
    assert "<title>Decima Shell</title>" in body
    ctype = dict(r.headers)["Content-Type"]
    assert ctype.startswith("text/html")


def test_index_served_explicitly(shell):
    r = shell.handle("GET", "/index.html")
    assert r.status == 200
    assert b"Decima" in r.body


def test_css_and_js_served_locally(shell):
    css = shell.handle("GET", "/app.css")
    assert css.status == 200
    assert dict(css.headers)["Content-Type"].startswith("text/css")

    for path, ctype in (
        ("/js/sanitize.js", "text/javascript"),
        ("/js/api.js", "text/javascript"),
        ("/js/screens/approvals.js", "text/javascript"),
    ):
        r = shell.handle("GET", path)
        assert r.status == 200, path
        assert dict(r.headers)["Content-Type"].startswith(ctype), path


def test_strict_csp_and_security_headers(shell):
    r = shell.handle("GET", "/")
    headers = dict(r.headers)
    csp = headers["Content-Security-Policy"]
    # Everything is pinned to the local origin; no remote/inline script or style is allowed.
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"


def test_missing_asset_is_404(shell):
    r = shell.handle("GET", "/js/does-not-exist.js")
    assert r.status == 404


def test_path_traversal_is_refused(shell):
    for path in (
        "/../serve.py",
        "/js/../../serve.py",
        "/%2e%2e/serve.py",
        "/../../decima/shell/serve.py",
    ):
        r = shell.handle("GET", path)
        assert r.status == 404, path
        assert b"build_shell" not in r.body


def test_static_rejects_non_get(shell):
    r = shell.handle("POST", "/app.css", body="x")
    assert r.status == 405


def test_safe_static_path_confined():
    assert serve_mod._safe_static_path("/../serve.py") is None
    assert serve_mod._safe_static_path("/") is not None
    assert serve_mod._safe_static_path("/index.html") is not None


def test_wsgi_adapter_roundtrips_index(shell):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/", "QUERY_STRING": ""}
    chunks = shell(environ, start_response)
    assert captured["status"].startswith("200")
    assert b"Decima Shell" in b"".join(chunks)
