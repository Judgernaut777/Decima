"""API-SERVE LAUNCHER — an HTTP transport that gives `api.handle_request` a socket.

Decima has a proven in-process API request handler (`decima.api.handle_request`) —
every endpoint is a capability invocation, authority comes from the CALLER'S
capability TOKEN, never from process/ambient identity or the request body — but
NOTHING drove it over a real wire, so an external HTTP client could not actually
reach it. This module is the missing production caller, mirroring how
`mcp_server.serve_stdio` wraps `handle`: **serving does NOT weaken the gate**. The
transport dispatches ONLY through `api.handle_request` and answers nothing itself
— token→caller resolution, the registered-endpoint capability + scope check, and
the untrusted-body stripping all still run exactly as in-process. The HTTP layer
adds NO authority; it only parses bytes into the `{path, token, args}` shape
`handle_request` already expects and serializes its `{ok, ...}` response back out.

Three pieces:

  - `serve_once(k, request) -> dict` — the load-bearing seam. Routes ONE
    already-parsed request dict through `api.handle_request` and returns its
    response, verbatim. This is deliberately a one-line wrapper: the check drives
    THIS function directly (no socket needed to prove the gate holds), and the
    HTTP handler below calls nothing else to reach the kernel.
  - `make_handler(k)` — builds a `http.server.BaseHTTPRequestHandler` subclass
    bound to one live Kernel `k` (closure, not a global — a fresh handler class
    per kernel, the same shape a real deployment would use per-process). Parses
    the URL path, an `Authorization: Bearer <token>` header (or `X-Decima-Token`)
    as the token, and a JSON object body as `args`; calls `serve_once` and writes
    the JSON response with the matching HTTP status. A malformed body (not valid
    JSON, or not a JSON object) or an unsupported method never reaches
    `handle_request` — it is answered with a clean 4xx JSON error, never a crash
    and never a fabricated success.
  - `main(k=None, host="127.0.0.1", port=8790)` — boots a warm Kernel exactly like
    `run.py` (`Kernel("weft.db", fresh=False)`) if none is passed, then serves it
    forever over `http.server.ThreadingHTTPServer`. Guarded by
    `if __name__ == "__main__"` so importing this module never binds a socket.

Ints-not-floats: every recorded field this module touches (`path`, `token`,
`args`) passes straight through to `handle_request`, which already enforces the
Weft's ints-not-floats law on anything it records; this module records nothing
of its own. Fail closed throughout: an exception building the request, a bad
method, or a bad body all become a denial-shaped or error-shaped HTTP response —
never a 200 with fabricated content, and never an unhandled crash that could be
mistaken for the gate having run.

Pure composition over `decima.api` — no core edit, zero pip deps (stdlib
`http.server` + `json` only), and fully offline-testable: `serve_once` takes and
returns plain dicts, so a check can drive the entire gate surface without ever
binding a real socket.
"""
import http.server
import json

from decima import api

_MAX_BODY = 1 << 20     # 1 MiB — a generous but bounded body read (fail closed on garbage)


def serve_once(k, request: dict) -> dict:
    """Route ONE already-parsed API request `{path, token, args}` through
    `api.handle_request` and return its response verbatim. Answers NOTHING
    itself — no method resolves here, no authority is added or removed. This is
    the single seam both the check and the HTTP handler below call, so there is
    exactly one code path between an inbound request and the kernel's ocap gate."""
    return api.handle_request(k, request or {})


def _token_from_headers(headers) -> str | None:
    """Pull the caller's capability token from `Authorization: Bearer <token>`
    (preferred) or `X-Decima-Token` (a plain fallback). Returns None if neither
    is present — `handle_request` itself then denies with "no token": this
    function decides NOTHING about authority, it just locates where the token
    rides on the wire."""
    auth = headers.get("Authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    tok = headers.get("X-Decima-Token")
    if tok:
        tok = tok.strip()
        return tok or None
    return None


def make_handler(k):
    """Build a `BaseHTTPRequestHandler` subclass bound to the live Kernel `k`.

    GET and POST both parse `{path, token, args}` from the wire and dispatch
    through `serve_once` — GET carries no body (`args = {}`); POST's body, if
    present, must be a JSON object (used verbatim as `args`; it is UNTRUSTED
    DATA, exactly as `api.handle_request` already treats it — this handler
    strips nothing itself, `handle_request`'s own `_clean_args` does that).
    Any other HTTP method is refused with 405 before any parsing.

    Fail closed: a body that is not valid JSON, or is JSON but not an object,
    never reaches `handle_request` — it is answered 400, no effect ran. An
    unhandled exception while building the request is likewise answered as a
    clean 500 JSON error, never a crash that leaves the client mid-response."""

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "DecimaAPI/1"

        def _reply(self, status: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self):
            """Returns (obj_or_None, error_or_None). No body → ({}, None). A
            present body must decode as a JSON OBJECT — anything else fails
            closed with a reason, never a guess."""
            length = self.headers.get("Content-Length")
            if not length:
                return {}, None
            try:
                n = int(length)
            except ValueError:
                return None, "bad Content-Length"
            if n < 0 or n > _MAX_BODY:
                return None, "body too large or invalid"
            if n == 0:
                return {}, None
            raw = self.rfile.read(n)
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                return None, f"malformed JSON body: {e}"
            if not isinstance(obj, dict):
                return None, "JSON body must be an object"
            return obj, None

        def _dispatch(self, *, has_body: bool):
            token = _token_from_headers(self.headers)
            args = {}
            if has_body:
                args, err = self._read_json_body()
                if err is not None:
                    self._reply(400, {"ok": False, "denied": err})
                    return
            path = self.path.split("?", 1)[0]
            try:
                resp = serve_once(k, {"path": path, "token": token, "args": args})
            except Exception as e:      # noqa: BLE001 — never crash, never fabricate success
                self._reply(500, {"ok": False, "denied": f"internal error: {e}"})
                return
            status = 200 if resp.get("ok") else 403
            self._reply(status, resp)

        def do_GET(self):
            self._dispatch(has_body=False)

        def do_POST(self):
            self._dispatch(has_body=True)

        def do_PUT(self):
            self._reply(405, {"ok": False, "denied": "method not allowed"})

        def do_DELETE(self):
            self._reply(405, {"ok": False, "denied": "method not allowed"})

        def log_message(self, fmt, *args):
            pass    # quiet by default — the Weft is the audit trail, not stderr

    return Handler


def main(k=None, host="127.0.0.1", port=8790) -> None:
    """Boot a warm Kernel exactly like `run.py` (reuses `weft.db`; pass a live
    `k` to reuse an already-booted one instead) and serve `api.handle_request`
    forever over HTTP. Threaded so concurrent callers don't serialize behind one
    slow request; each request still resolves its OWN caller from its OWN token,
    so concurrency confers no shared or ambient authority."""
    if k is None:
        from decima.kernel import Kernel
        k = Kernel("weft.db", fresh=False)
    handler_cls = make_handler(k)
    httpd = http.server.ThreadingHTTPServer((host, port), handler_cls)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
