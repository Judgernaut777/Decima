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

import hmac
import json
from collections.abc import Callable
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
from decima.services.api import nona_service, plan_service, qa_service, routes, workspace_service
from decima.services.api.auth import (
    BAD_CREDENTIALS,
    COOKIE_NAME,
    REAUTH_REQUIRED,
    UNAUTHENTICATED,
    AuthError,
    Session,
    SessionStore,
    parse_cookie,
)
from decima.services.api.commands import CommandService
from decima.services.api.contracts import ApplicationError, CommandError
from decima.services.api.events import EventBus
from decima.services.api.identity import AppIdentity
from decima.services.api.tenancy import UserContext, build_user_context
from decima.services.api.users import UserDirectory, UserError

# Path-A feature readers: reader-route target → callable(app, query) -> JSON-safe dict.
# Wired ONCE here so a feature lane only ever edits its own service module.
FEATURE_READERS = {
    **qa_service.READERS,
    **plan_service.READERS,
    **workspace_service.READERS,
    **nona_service.READERS,
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
    before touching any command or read-model.

    MULTI-USER (T3.2). When a ``users`` directory is configured, an authenticated request
    runs against THAT USER'S OWN store — its own Weft, projections, command service and
    stream bus (``tenancy.UserContext``) — resolved once per request by ``context_for``
    from the session's principal. Isolation is therefore by CONSTRUCTION, not by
    filtering: another user's events are not in the log the request folds, so there is no
    id a user can name to read or act on another user's Cell. There is deliberately NO
    admin/superuser context and no code path that widens one context to another (Law 2);
    user PROVISIONING is a host-side filesystem act (``users.provision_user``), never an
    HTTP endpoint, so no request can mint a user or another user's authority.

    With no user directory the application is exactly the single-operator loopback daemon
    it was: one default context over the store it was built on."""

    def __init__(
        self,
        *,
        weft: Weft,
        driver: ProjectionDriver,
        identity: AppIdentity,
        event_bus: EventBus | None = None,
        secure_cookie: bool = True,
        users: UserDirectory | None = None,
        db_path: str | None = None,
        driver_factory: Callable[[Weft], ProjectionDriver] | None = None,
        max_user_contexts: int = 16,
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
        # -- multi-user tenancy wiring (all optional; absent ⇒ single-operator daemon) --
        self.users = users
        self._db_path = db_path
        self._driver_factory = driver_factory
        self._max_user_contexts = max_user_contexts
        # The default context IS the objects above, so the single-user path is unchanged.
        self._default_context = UserContext(
            principal=identity.human,
            weft=weft,
            driver=driver,
            commands=self.commands,
            bus=self.bus,
        )
        self._contexts: dict[str, UserContext] = {identity.human: self._default_context}

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

        # -- authorize the request per its declared level, then bind it to the
        # -- authenticated principal's OWN store (both fail closed) ----------
        try:
            session = self._authorize(route, headers)
            context = self.context_for(session)
        except AuthError as exc:
            return _json_response(
                exc.http_status, {"error": str(exc), "reason_code": exc.reason_code}
            )

        if route.kind == routes.SPECIAL:
            return self._special(route, headers, body, query, session, context)
        if route.kind == routes.READER:
            return self._read(route, query, context)
        return self._command(route, body, context)

    def _authorize(self, route: routes.Route, headers: dict[str, str]) -> Session | None:
        """Return the session for a request at ``route``'s level (None for public), or
        raise ``AuthError``. Escalation: read⇒session, write⇒+CSRF, reauth⇒+reauth."""
        if route.auth == routes.PUBLIC:
            return None
        cookies = parse_cookie(headers.get("cookie"))
        session = self.sessions.require_session(cookies.get(COOKIE_NAME))
        if route.auth in (routes.WRITE, routes.REAUTH):
            self.sessions.check_csrf(session, headers.get("x-csrf-token"))
        if route.auth == routes.REAUTH:
            self._require_reauth(session, headers.get("x-reauth"))
        return session

    def _require_reauth(self, session: Session, presented: str | None) -> None:
        """The reauth hook, per credential kind. A high-risk approval needs a FRESH
        credential re-presented at THIS call:

        * an operator (pairing) session re-presents the loopback pairing secret;
        * a USER session re-presents THAT USER'S OWN password.

        The host-wide pairing secret is deliberately NOT accepted for a user session (a
        shared host token must never stand in for a person's credential), and a user's
        password is not accepted for the operator session. Both fail closed at 401."""
        if session.username is None:
            self.sessions.check_reauth(presented)
            return
        if self.users is None or not self.users.verify_password(session.username, presented or ""):
            raise AuthError(REAUTH_REQUIRED, 401, "reauthentication required")

    # -- multi-user tenancy ------------------------------------------------
    def multiuser_enabled(self) -> bool:
        """True only when real per-user authentication is CONFIGURED AND PROVISIONED and
        per-user stores can actually be opened. Anything less is the single-operator
        loopback daemon, and the bind guard refuses to expose that off-host."""
        return (
            self.users is not None
            and self.users.count() > 0
            and self._db_path is not None
            and self._driver_factory is not None
        )

    def context_for(self, session: Session | None) -> UserContext:
        """The tenancy context a request runs in — the ONE store its principal owns.

        A public request or the host operator's pairing session gets the daemon's own
        store (unchanged behaviour). A USER session gets that user's own store, opened on
        first use. Fail closed: a session whose username is unknown to the directory, or
        whose principal no longer matches the directory's, is logged out and refused —
        it is never served a store it cannot prove title to, and there is no branch that
        returns another user's context."""
        if session is None or session.username is None:
            return self._default_context
        if self.users is None or self._db_path is None or self._driver_factory is None:
            raise AuthError(UNAUTHENTICATED, 401, "user authentication is not configured")
        principal = self.users.principal_of(session.username)
        if principal is None or not hmac.compare_digest(principal, session.principal):
            self.sessions.logout(session.token)
            raise AuthError(UNAUTHENTICATED, 401, "session principal is not a known user")
        context = self._contexts.get(principal)
        if context is None:
            self._evict_contexts()
            context = build_user_context(
                self._db_path,
                principal,
                keyring=self.weft.keyring,
                app_principal=self.identity.app,
                driver_factory=self._driver_factory,
                models=self.commands.models,
            )
            self._contexts[principal] = context
        return context

    def _evict_contexts(self) -> None:
        """Bound the number of OPEN per-user stores (each holds one sqlite connection).
        Eviction is a cache decision only — the store on disk is canonical, so an evicted
        context is simply reopened on that user's next request. The default context is
        never evicted."""
        while len(self._contexts) > self._max_user_contexts:
            for principal, context in self._contexts.items():
                if principal == self._default_context.principal:
                    continue
                context.weft.db.close()
                del self._contexts[principal]
                break
            else:  # pragma: no cover - only the default context remains
                return

    # -- special (auth / health / stream) ----------------------------------
    def _special(self, route, headers, body, query, session, context) -> Response:
        target = route.target
        if target == "health":
            # ``multiuser`` lets an unauthenticated client know WHICH login form to show.
            # It names no user and leaks no credential material.
            return _json_response(
                200,
                {
                    "status": "ok",
                    "app": self.identity.app,
                    "version": "v1",
                    "multiuser": self.multiuser_enabled(),
                },
            )
        if target == "login":
            return self._login(body)
        if target == "logout":
            cookies = parse_cookie(headers.get("cookie"))
            self.sessions.logout(cookies.get(COOKIE_NAME))
            return _json_response(
                200, {"ok": True}, extra=[("Set-Cookie", self.sessions.clear_cookie_header())]
            )
        if target == "session_info":
            return _json_response(
                200,
                {
                    "principal": session.principal,
                    "csrf": session.csrf,
                    "username": session.username,
                    "multiuser": self.multiuser_enabled(),
                },
            )
        if target == "change_password":
            return self._change_password(session, body)
        if target == "stream":
            return self._stream(query, context)
        return _json_response(500, {"error": "unhandled special route"})

    def _login(self, body: bytes | str | None) -> Response:
        """Exchange a credential for a session. TWO credential kinds, never interchangeable:

        * ``{"username", "password"}`` — a REAL per-user login against the user directory
          (per-user salt + scrypt hash on disk; no plaintext anywhere). The session's
          principal is that user's Decima principal and every later request runs against
          that user's own store.
        * ``{"pairing_secret"}`` — the host operator's loopback pairing credential
          (unchanged).

        A body naming a username is NEVER satisfied by the pairing secret, so the
        host-wide token can never stand in for a person's credential."""
        payload = _parse_json(body)
        if payload is None or not isinstance(payload, dict):
            return _json_response(400, {"error": "invalid JSON body"})
        if "username" in payload or "password" in payload:
            return self._login_user(payload)
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

    def _login_user(self, payload: dict) -> Response:
        """Username/password login. Every refusal — unknown user, wrong password, disabled
        user, directory not configured — returns the SAME status, code and shape, and the
        directory spends the same scrypt work in each case, so this is not a user
        enumeration oracle. Failures feed the SAME throttle the pairing path uses, keyed
        per username so one account's brute force cannot lock the others out. No session
        is created on failure."""
        if self.users is None:
            return _json_response(
                401,
                {"error": "invalid credentials", "reason_code": BAD_CREDENTIALS},
            )
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return _json_response(400, {"error": "username and password must be strings"})
        key = f"user:{username}"
        try:
            self.sessions.check_throttle(key)
        except AuthError as exc:
            return _json_response(
                exc.http_status, {"error": str(exc), "reason_code": exc.reason_code}
            )
        record = self.users.authenticate(username, password)
        if record is None:
            self.sessions.note_failure(key)
            return _json_response(
                401, {"error": "invalid credentials", "reason_code": BAD_CREDENTIALS}
            )
        self.sessions.clear_failures(key)
        session = self.sessions.begin_session(record.principal, username=record.username)
        return _json_response(
            200,
            {
                "ok": True,
                "csrf": session.csrf,
                "principal": session.principal,
                "username": record.username,
            },
            extra=[("Set-Cookie", self.sessions.cookie_header(session))],
        )

    def _change_password(self, session: Session, body: bytes | str | None) -> Response:
        """A user rotates THEIR OWN password. The target is the SESSION'S user — never a
        name from the body — so this is self-authority, not an admin capability: there is
        no way to reach another account through it. The current password must be
        re-presented (a stolen session cookie alone cannot take the account over)."""
        payload = _parse_json(body)
        if not isinstance(payload, dict):
            return _json_response(400, {"error": "invalid JSON body"})
        if session.username is None or self.users is None:
            return _json_response(
                403,
                {
                    "error": "this session has no user credential to change",
                    "reason_code": "NO_USER_CREDENTIAL",
                },
            )
        current = payload.get("current_password")
        new = payload.get("new_password")
        if not isinstance(current, str) or not isinstance(new, str):
            return _json_response(400, {"error": "passwords must be strings"})
        if not self.users.verify_password(session.username, current):
            return _json_response(
                401, {"error": "invalid credentials", "reason_code": BAD_CREDENTIALS}
            )
        try:
            self.users.set_password(session.username, new)
        except UserError as exc:
            return _json_response(400, {"error": str(exc), "reason_code": "BAD_REQUEST"})
        return _json_response(200, {"ok": True})

    def _stream(self, query: dict[str, str], context: UserContext) -> Response:
        cursor = 0
        raw = query.get("since")
        if raw is not None and str(raw).isdigit():
            cursor = int(raw)
        frames = context.bus.sse_stream(cursor)
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

    def _read(self, route: routes.Route, query: dict[str, str], context: UserContext) -> Response:
        """A disposable projection read, served from the REQUESTING USER'S OWN driver. The
        driver folds only that user's store, so a cross-user read is not filtered out — it
        is unrepresentable."""
        context.driver.update()
        target = route.target
        if target not in self._PROJECTION_OF:
            return self._feature_read(target, query, context)
        proj = context.driver.get(self._PROJECTION_OF[target])
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

    def _feature_read(self, target: str, query: dict[str, str], context: UserContext) -> Response:
        """A Path-A feature reader: still a DISPOSABLE read (fold/projection only),
        implemented in the owning lane's service module. A refusal (including the
        pre-implementation 501 stub) returns the stable ``ApplicationError`` envelope.

        The reader is handed the requesting user's ``UserContext``, which satisfies
        ``contracts.LaneReaderApp`` (``weft`` + ``commands``). That is why every lane
        reader is scoped for free: each one folds ``app.weft``, and that weft is the
        user's own store."""
        reader = FEATURE_READERS.get(target)
        if reader is None:  # pragma: no cover - table and code are in lockstep
            return _json_response(500, {"error": f"no reader {target!r}"})
        try:
            data = reader(context, dict(query))
        except CommandError as exc:
            envelope = ApplicationError(
                reason_code=exc.reason_code, message=str(exc), http_status=exc.http_status
            )
            return _json_response(exc.http_status, envelope.as_dict())
        return _json_response(200, data)

    # -- durable command mutations -----------------------------------------
    def _command(
        self, route: routes.Route, body: bytes | str | None, context: UserContext
    ) -> Response:
        payload = _parse_json(body)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return _json_response(400, {"error": "request body must be a JSON object"})
        if route.auth == routes.REAUTH and isinstance(payload.get("item"), str):
            # A fresh reauth (auth.check_reauth, already enforced in _authorize) proved a
            # LIVE human at THIS call. The host now mints that human's possession proof for
            # this exact approval item and hands it to the command boundary, which
            # re-verifies it before recording anything — mirroring kernel.invoke building a
            # proof that verify_proof then checks. A path that reaches the command service
            # WITHOUT this reauth-gated step carries no proof and is refused, so an approval
            # can never be minted by arbitrary in-process code.
            # The proof is minted by the ACTING user's command service, so it is signed by
            # that user's principal and bound to an item in that user's own store — a
            # proof minted for one user can never enact another user's item.
            item_id = payload["item"]
            payload = {
                **payload,
                "approval_proof": context.commands.mint_approval_proof(item_id),
            }
        result = context.commands.execute(route.target, payload)
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
