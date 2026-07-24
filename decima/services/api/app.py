"""The API application — a narrow, authenticated, loopback WSGI surface (Phase 8).

``Application`` is a plain WSGI callable that wires the route table (``routes``) to the
command service (``commands``) and disposable projection reads, behind session/CSRF/
reauth (``auth``). It is intentionally small and driveable IN-PROCESS: ``dispatch``
takes a method/path/headers/body and returns a ``Response`` with zero sockets, so tests
are deterministic; ``__call__`` adapts the same path to WSGI for a real loopback server.

The kernel/API process executes NOTHING untrusted (invariant 7): a request body is
parsed as JSON DATA into a command's typed args — there is no endpoint that evaluates
caller-supplied Python. Every durable change flows through the command service to the
Weft (invariant 1); reads come only from projections (invariant 2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

from decima._wsgi_util import headers_from_environ, parse_query, read_wsgi_body
from decima.kernel.weft import Weft
from decima.projections.activity import ActivityProjection
from decima.projections.agents import AgentsProjection
from decima.projections.approvals import ApprovalsProjection
from decima.projections.engine import ProjectionDriver
from decima.projections.knowledge import KnowledgeProjection
from decima.projections.projects import ProjectsProjection
from decima.projections.tasks import TasksProjection
from decima.services.api import plan_service, qa_service, routes, workspace_service
from decima.services.api.auth import AuthError, SessionStore, parse_cookie
from decima.services.api.commands import CommandService
from decima.services.api.contracts import ApplicationError, CommandError
from decima.services.api.events import EventBus
from decima.services.api.identity import AppIdentity

# Path-A feature readers: reader-route target → callable(app, query) -> JSON-safe dict.
# Wired ONCE here so a feature lane only ever edits its own service module.
FEATURE_READERS = {
    **qa_service.READERS,
    **plan_service.READERS,
    **workspace_service.READERS,
}


@dataclass
class Response:
    status: int
    body: bytes
    headers: list[tuple[str, str]] = field(default_factory=list)
    stream: list[bytes] | None = None  # SSE frames (chunked) when set

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8")) if self.body else None


_STATUS_TEXT = {
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    429: "Too Many Requests",
    500: "Internal Server Error",
    501: "Not Implemented",
}


def _json_response(
    status: int, obj: object, extra: list[tuple[str, str]] | None = None
) -> Response:
    body = json.dumps(obj, sort_keys=True).encode("utf-8")
    headers = [("Content-Type", "application/json"), ("X-Content-Type-Options", "nosniff")]
    if extra:
        headers.extend(extra)
    return Response(status=status, body=body, headers=headers)


class Application:
    """The loopback API. Owns the session store, the command service, and the projection
    driver; routes each request through the declared authorization level for its endpoint
    before touching any command or read-model."""

    def __init__(
        self,
        *,
        weft: Weft,
        driver: ProjectionDriver,
        identity: AppIdentity,
        event_bus: EventBus | None = None,
        secure_cookie: bool = True,
    ) -> None:
        self.weft = weft
        self.driver = driver
        self.identity = identity
        self.bus = event_bus or EventBus()
        self.sessions = SessionStore(identity.pairing_secret, secure_cookie=secure_cookie)
        self.commands = CommandService(
            weft,
            driver,
            app_principal=identity.app,
            human_principal=identity.human,
            event_bus=self.bus,
        )

    # -- the deterministic driving surface ---------------------------------
    def dispatch(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | str | None = None,
        query: dict[str, str] | None = None,
    ) -> Response:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        query = query or {}
        route = routes.match(method, path)
        if route is None:
            if routes.path_known(path):
                return _json_response(405, {"error": "method not allowed", "path": path})
            return _json_response(404, {"error": "not found", "path": path})

        # -- authorize the request per its declared level -------------------
        try:
            session = self._authorize(route, headers)
        except AuthError as exc:
            return _json_response(
                exc.http_status, {"error": str(exc), "reason_code": exc.reason_code}
            )

        if route.kind == routes.SPECIAL:
            return self._special(route, headers, body, query, session)
        if route.kind == routes.READER:
            return self._read(route, query)
        return self._command(route, body)

    def _authorize(self, route: routes.Route, headers: dict[str, str]):
        """Return the session for a request at ``route``'s level (None for public), or
        raise ``AuthError``. Escalation: read⇒session, write⇒+CSRF, reauth⇒+reauth."""
        if route.auth == routes.PUBLIC:
            return None
        cookies = parse_cookie(headers.get("cookie"))
        from decima.services.api.auth import COOKIE_NAME

        session = self.sessions.require_session(cookies.get(COOKIE_NAME))
        if route.auth in (routes.WRITE, routes.REAUTH):
            self.sessions.check_csrf(session, headers.get("x-csrf-token"))
        if route.auth == routes.REAUTH:
            self.sessions.check_reauth(headers.get("x-reauth"))
        return session

    # -- special (auth / health / stream) ----------------------------------
    def _special(self, route, headers, body, query, session) -> Response:
        target = route.target
        if target == "health":
            return _json_response(200, {"status": "ok", "app": self.identity.app, "version": "v1"})
        if target == "login":
            return self._login(body)
        if target == "logout":
            from decima.services.api.auth import COOKIE_NAME

            cookies = parse_cookie(headers.get("cookie"))
            self.sessions.logout(cookies.get(COOKIE_NAME))
            return _json_response(
                200, {"ok": True}, extra=[("Set-Cookie", self.sessions.clear_cookie_header())]
            )
        if target == "session_info":
            return _json_response(200, {"principal": session.principal, "csrf": session.csrf})
        if target == "stream":
            return self._stream(query)
        return _json_response(500, {"error": "unhandled special route"})

    def _login(self, body: bytes | str | None) -> Response:
        payload = _parse_json(body)
        if payload is None or not isinstance(payload, dict):
            return _json_response(400, {"error": "invalid JSON body"})
        secret = payload.get("pairing_secret")
        try:
            session = self.sessions.login(self.identity.human, secret)
        except AuthError as exc:
            return _json_response(
                exc.http_status, {"error": str(exc), "reason_code": exc.reason_code}
            )
        return _json_response(
            200,
            {"ok": True, "csrf": session.csrf, "principal": session.principal},
            extra=[("Set-Cookie", self.sessions.cookie_header(session))],
        )

    def _stream(self, query: dict[str, str]) -> Response:
        cursor = 0
        raw = query.get("since")
        if raw is not None and str(raw).isdigit():
            cursor = int(raw)
        frames = self.bus.sse_stream(cursor)
        return Response(
            status=200,
            body=b"".join(frames),
            headers=[
                ("Content-Type", "text/event-stream"),
                ("Cache-Control", "no-cache"),
                ("X-Content-Type-Options", "nosniff"),
            ],
            stream=frames,
        )

    # -- disposable projection reads ---------------------------------------
    _PROJECTION_OF = {
        "tasks": "tasks",
        "projects": "projects",
        "agents": "agents",
        "notes": "knowledge",
        "approvals": "approvals",
        "activity": "activity",
    }

    def _read(self, route: routes.Route, query: dict[str, str]) -> Response:
        self.driver.update()
        target = route.target
        if target not in self._PROJECTION_OF:
            return self._feature_read(target, query)
        proj = self.driver.get(self._PROJECTION_OF[target])
        if target == "tasks":
            data = [t.as_dict() for t in cast(TasksProjection, proj).tasks()]
        elif target == "projects":
            data = [p.as_dict() for p in cast(ProjectsProjection, proj).projects()]
        elif target == "agents":
            data = [a.as_dict() for a in cast(AgentsProjection, proj).agents()]
        elif target == "notes":
            data = [k.as_dict() for k in cast(KnowledgeProjection, proj).notes()]
        elif target == "approvals":
            data = [a.as_dict() for a in cast(ApprovalsProjection, proj).approvals()]
        elif target == "activity":
            data = [e.as_dict() for e in cast(ActivityProjection, proj).timeline()]
        else:  # pragma: no cover - table and code are in lockstep
            return _json_response(500, {"error": f"no reader {target!r}"})
        return _json_response(200, {"items": data})

    def _feature_read(self, target: str, query: dict[str, str]) -> Response:
        """A Path-A feature reader: still a DISPOSABLE read (fold/projection only),
        implemented in the owning lane's service module. A refusal (including the
        pre-implementation 501 stub) returns the stable ``ApplicationError`` envelope."""
        reader = FEATURE_READERS.get(target)
        if reader is None:  # pragma: no cover - table and code are in lockstep
            return _json_response(500, {"error": f"no reader {target!r}"})
        try:
            data = reader(self, dict(query))
        except CommandError as exc:
            envelope = ApplicationError(
                reason_code=exc.reason_code, message=str(exc), http_status=exc.http_status
            )
            return _json_response(exc.http_status, envelope.as_dict())
        return _json_response(200, data)

    # -- durable command mutations -----------------------------------------
    def _command(self, route: routes.Route, body: bytes | str | None) -> Response:
        payload = _parse_json(body)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return _json_response(400, {"error": "request body must be a JSON object"})
        result = self.commands.execute(route.target, payload)
        return _json_response(result.http_status, result.as_dict())

    # -- WSGI adapter ------------------------------------------------------
    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        query = parse_query(environ.get("QUERY_STRING", ""))
        headers = headers_from_environ(environ)
        body = read_wsgi_body(environ)
        response = self.dispatch(method, path, headers=headers, body=body, query=query)
        status_line = f"{response.status} {_STATUS_TEXT.get(response.status, 'Status')}"
        chunks = response.stream if response.stream is not None else [response.body]
        headers_out = list(response.headers)
        if response.stream is None:
            headers_out.append(("Content-Length", str(len(response.body))))
        start_response(status_line, headers_out)
        return chunks


# -- request parsing helpers (stdlib only) ---------------------------------
def _parse_json(body: bytes | str | None) -> object | None:
    if body is None or body == b"" or body == "":
        return None
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None
