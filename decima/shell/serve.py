"""The Shell host — one local endpoint that serves the trusted frontend and delegates
``/api/*`` to the imported backend application (Phase 9).

The whole Shell runs from a SINGLE loopback origin: a browser loads the static frontend
(HTML/CSS/JS shipped under ``frontend/``) and talks to the SAME origin for the API, so
the session cookie, CSRF double-submit, and a strict same-origin CSP all hold without any
cross-origin surface. This module is pure stdlib (house rule): it opens no network client
of its own — it composes a static file server with the backend WSGI app in-process.

Design for determinism + testability:

  * :class:`ShellApp` exposes :meth:`handle` — a socket-free core that takes a
    method/path/headers/body and returns a :class:`ShellResponse`. Static (non-``/api``)
    paths are resolved and served from ``frontend/``; ``/api/*`` paths are delegated to
    the backend's own deterministic ``dispatch`` (or, in a real server, its WSGI call).
    Tests drive :meth:`handle` directly, opening no socket.
  * :meth:`__call__` adapts the same path to WSGI so :func:`serve` can run it on the
    stdlib ``wsgiref`` server, bound to loopback.

Security posture of the STATIC surface:
  * a strict Content-Security-Policy pins every resource to ``'self'`` (no remote
    scripts/styles/fonts, no inline handlers, ``object-src 'none'``), so an injected
    string can never load or run off-origin code;
  * path resolution is confined to ``frontend/`` (``..`` traversal is refused);
  * ``X-Content-Type-Options: nosniff`` and ``X-Frame-Options: DENY`` are always set.
"""

from __future__ import annotations

import ipaddress
import mimetypes
import os
from dataclasses import dataclass, field

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")

API_PREFIX = "/api/"

# A strict, entirely same-origin CSP. Everything the Shell needs is served locally from
# this one origin, so nothing off-'self' is ever permitted; ``data:`` is allowed for
# images only (inline favicons/badges). No 'unsafe-inline'/'unsafe-eval' — the frontend
# ships as separate local files and never evals.
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

_SECURITY_HEADERS = [
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
]

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}

_STATUS_TEXT = {
    200: "OK",
    301: "Moved Permanently",
    304: "Not Modified",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
}


@dataclass
class ShellResponse:
    """A minimal, socket-free response the WSGI adapter turns into a real reply."""

    status: int
    body: bytes
    headers: list[tuple[str, str]] = field(default_factory=list)


def _content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _CONTENT_TYPES:
        return _CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _safe_static_path(url_path: str) -> str | None:
    """Resolve a URL path to a file inside ``FRONTEND_DIR``, or ``None`` if it escapes
    the directory or does not name a regular file. ``/`` maps to ``index.html``."""
    clean = url_path.split("?", 1)[0].split("#", 1)[0]
    if clean in ("", "/"):
        clean = "/index.html"
    # Normalize and confine to the frontend root — refuse any traversal.
    rel = clean.lstrip("/")
    root = os.path.realpath(FRONTEND_DIR)
    candidate = os.path.realpath(os.path.join(root, rel))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


class ShellApp:
    """Compose the static frontend with the backend API behind one origin.

    ``backend`` is the existing :class:`decima.services.api.app.Application` (or anything
    exposing a compatible ``dispatch``/WSGI ``__call__``). Non-``/api`` requests are served
    as static files from ``frontend/``; ``/api/*`` requests are delegated verbatim to the
    backend — the Shell adds NO authority and rewrites no command."""

    def __init__(self, backend: object, *, frontend_dir: str = FRONTEND_DIR) -> None:
        self.backend = backend
        self.frontend_dir = frontend_dir

    # -- the deterministic, socket-free core -------------------------------
    def handle(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | str | None = None,
        query: dict[str, str] | None = None,
    ) -> ShellResponse:
        if path == API_PREFIX.rstrip("/") or path.startswith(API_PREFIX):
            return self._delegate(method, path, headers=headers, body=body, query=query)
        return self._static(method, path)

    # -- static frontend ---------------------------------------------------
    def _static(self, method: str, path: str) -> ShellResponse:
        if method not in ("GET", "HEAD"):
            return self._error(405, "method not allowed for static asset")
        target = _safe_static_path(path)
        if target is None:
            return self._error(404, "not found")
        with open(target, "rb") as fh:
            data = fh.read()
        headers = [("Content-Type", _content_type(target))] + list(_SECURITY_HEADERS)
        return ShellResponse(status=200, body=b"" if method == "HEAD" else data, headers=headers)

    # -- API delegation ----------------------------------------------------
    def _delegate(self, method, path, *, headers, body, query) -> ShellResponse:
        """Hand the request to the backend's deterministic dispatch and adapt its
        ``Response`` back. The Shell never inspects or rewrites the backend's decision —
        it is a pass-through so the API's auth/CSRF/reauth remain the sole gate."""
        resp = self.backend.dispatch(method, path, headers=headers, body=body, query=query)
        # The backend ``Response`` streams SSE via ``.stream`` when set.
        stream = getattr(resp, "stream", None)
        raw = b"".join(stream) if stream is not None else resp.body
        # Layer the Shell's static-surface security headers onto API replies too; the
        # backend already sets its own content-type + nosniff, so we don't duplicate CT.
        out_headers = list(resp.headers)
        _merge_header(out_headers, "X-Frame-Options", "DENY")
        _merge_header(out_headers, "Referrer-Policy", "no-referrer")
        return ShellResponse(status=resp.status, body=raw, headers=out_headers)

    def _error(self, status: int, message: str) -> ShellResponse:
        body = (
            f"<!doctype html><meta charset=utf-8><title>{status}</title>"
            f"<h1>{status} {_STATUS_TEXT.get(status, 'Error')}</h1><p>{message}</p>"
        ).encode()
        headers = [("Content-Type", "text/html; charset=utf-8")] + list(_SECURITY_HEADERS)
        return ShellResponse(status=status, body=body, headers=headers)

    # -- WSGI adapter (real loopback server) -------------------------------
    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        query = _parse_query(environ.get("QUERY_STRING", ""))
        headers = _headers_from_environ(environ)
        body = _read_wsgi_body(environ)
        resp = self.handle(method, path, headers=headers, body=body, query=query)
        status_line = f"{resp.status} {_STATUS_TEXT.get(resp.status, 'Status')}"
        out = list(resp.headers)
        out.append(("Content-Length", str(len(resp.body))))
        start_response(status_line, out)
        return [resp.body]


def _merge_header(headers: list[tuple[str, str]], name: str, value: str) -> None:
    if not any(k.lower() == name.lower() for k, _ in headers):
        headers.append((name, value))


def _parse_query(qs: str) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(qs))


def _headers_from_environ(environ: dict) -> dict[str, str]:
    headers: dict[str, str] = {}
    if environ.get("CONTENT_TYPE"):
        headers["content-type"] = environ["CONTENT_TYPE"]
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").lower()
            headers[name] = value
    return headers


def _read_wsgi_body(environ: dict) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (ValueError, TypeError):
        length = 0
    if length <= 0:
        return b""
    stream = environ.get("wsgi.input")
    return stream.read(length) if stream is not None else b""


def build_shell(backend: object) -> ShellApp:
    """Wrap an existing backend :class:`Application` in the Shell host."""
    return ShellApp(backend)


def make_loopback_server(app: object, *, host: str = "127.0.0.1", port: int = 0):
    """A SINGLE-THREADED stdlib WSGI server for the Shell daemon, bound to loopback.

    Why single-threaded: the kernel Weft is a single-connection ``sqlite3`` store, and a
    plain ``sqlite3`` connection may only be used from the thread that created it. The
    daily-driver Shell builds the backend (and thus opens that connection) on the caller's
    thread; serving every request on that SAME thread keeps all Weft access on one thread,
    so authenticated reads and mutations work. A per-connection-threaded server would hand
    a request to a fresh thread and raise ``sqlite3.ProgrammingError`` on the first read
    (see docs/release-evidence/browser/known-issues.md). For a local single-user daemon the
    serialization is invisible — projection reads are in-memory and the ``/stream`` frames
    are drained finitely, never a long-held connection.

    Binding a non-loopback address is refused (this is a local daemon), mirroring the API
    host's guard. ``port=0`` picks an ephemeral port; read ``server.server_address[1]``."""
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    if host not in ("localhost",):
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError(
                    f"refusing to bind non-loopback host {host!r}: the Shell is a local daemon"
                )
        except ValueError as exc:
            # A non-parseable host is also refused (fail closed).
            raise ValueError(
                f"refusing to bind non-loopback host {host!r}: the Shell is a local daemon"
            ) from exc

    class _QuietHandler(WSGIRequestHandler):
        def log_message(self, *args: object) -> None:  # silence stderr access logs
            return

    # Default WSGIServer is single-threaded: serve_forever handles each request inline on
    # the calling thread. That is exactly the property we need here.
    return make_server(host, port, app, handler_class=_QuietHandler)


def _write_pairing_secret(db_path: str, secret: str) -> str:
    """Persist the pairing secret to a ``0600`` file beside the Weft, returning its path.

    The pairing secret is derived deterministically from the master seed, so it is a durable
    credential — printing it to stdout leaks it into the systemd journal (a shared, readable
    sink). Instead write it to an owner-only file next to the database and print only the
    PATH; an operator reads the file, the journal never sees the value."""
    target = os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", ".pairing-secret")
    # Create/truncate with mode 0600 from the start (never briefly world-readable).
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (secret + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(target, 0o600)  # tighten even if a prior file existed with looser perms
    return target


def serve(  # pragma: no cover - blocking entrypoint
    db_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8973,
    seed: bytes | None = None,
    secure_cookie: bool = True,
    print_secret: bool = False,
) -> None:
    """Build the backend, wrap it in the Shell, and run on loopback until interrupted.

    The backend is built on THIS thread (opening the Weft's sqlite connection here) and the
    server serves every request on THIS thread (see :func:`make_loopback_server`), so all
    canonical-store access stays single-threaded and correct.

    The pairing secret is written to a ``0600`` file beside ``db_path`` and only its path is
    printed, so a service manager's journal never captures the credential. Pass
    ``print_secret=True`` (e.g. an interactive first run) to also echo the value to stdout."""
    from decima.services.api.server import build_application

    backend, identity = build_application(db_path, seed=seed, secure_cookie=secure_cookie)
    shell = build_shell(backend)
    server = make_loopback_server(shell, host=host, port=port)
    secret_path = _write_pairing_secret(db_path, identity.pairing_secret)
    print(f"decima Shell on http://{host}:{server.server_address[1]}/")
    if print_secret:
        print(f"pairing secret: {identity.pairing_secret}")
    else:
        print(f"pairing secret written to {secret_path} (mode 0600)")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - blocking entrypoint
    """``python3 -m decima.shell.serve <weft.db>`` — run the local Shell on loopback."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="decima.shell.serve", description="Run the Decima trusted Shell on loopback."
    )
    parser.add_argument("db_path", help="path to the Weft database (created if absent)")
    parser.add_argument("--host", default="127.0.0.1", help="loopback host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8973, help="port (0 = ephemeral)")
    parser.add_argument(
        "--print-secret",
        action="store_true",
        help="echo the pairing secret to stdout instead of only writing the 0600 file",
    )
    args = parser.parse_args(argv)
    try:
        serve(args.db_path, host=args.host, port=args.port, print_secret=args.print_secret)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
