"""The loopback HTTP host + application builder (Phase 8).

``build_application`` assembles the whole backend from the kernel/runtime/projection
seams: a signed ``Weft``, the generated local app identity, a ``ProjectionDriver`` with
the disposable read-models registered, and the ``Application`` over them. ``serve`` runs
it on a threading WSGI server BOUND TO LOOPBACK by default (127.0.0.1) — this is a local
daemon, not a network service. Binding a non-loopback address is refused unless the
caller explicitly opts in, and then a WARNING is emitted: exposing the API off-host
widens its trust surface and must be a deliberate choice.

Only stdlib transport is used (``wsgiref``/``http.server`` via ``ThreadingWSGIServer``):
NO web-framework dependency (house rule).
"""

from __future__ import annotations

import ipaddress
import socketserver
import warnings
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from decima.kernel.crypto import Keyring
from decima.kernel.weft import Weft
from decima.projections.activity import ActivityProjection
from decima.projections.agents import AgentsProjection
from decima.projections.approvals import ApprovalsProjection
from decima.projections.engine import ProjectionDriver
from decima.projections.knowledge import KnowledgeProjection
from decima.projections.projects import ProjectsProjection
from decima.projections.tasks import TasksProjection
from decima.services.api.app import Application
from decima.services.api.events import EventBus
from decima.services.api.identity import AppIdentity, generate_identity

LOOPBACK_HOST = "127.0.0.1"


def build_driver(weft: Weft) -> ProjectionDriver:
    """A driver with the API's disposable read-models registered and built."""
    driver = ProjectionDriver(weft)
    for projection in (
        TasksProjection(),
        ProjectsProjection(),
        AgentsProjection(),
        KnowledgeProjection(),
        ApprovalsProjection(),
        ActivityProjection(),
    ):
        driver.register(projection)
    return driver


def build_application(
    db_path: str,
    *,
    seed: bytes | None = None,
    keyring: Keyring | None = None,
    secure_cookie: bool = True,
) -> tuple[Application, AppIdentity]:
    """Construct the backend over a Weft at ``db_path``. Returns the app and its
    identity (the identity's ``pairing_secret`` is what a browser presents to log in).
    A fixed ``seed`` reproduces the identity across restarts."""
    kr = keyring or Keyring(seed=seed)
    weft = Weft(db_path, kr)
    identity = generate_identity(kr)
    driver = build_driver(weft)
    app = Application(
        weft=weft,
        driver=driver,
        identity=identity,
        event_bus=EventBus(),
        secure_cookie=secure_cookie,
    )
    return app, identity


class ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    """A per-connection-threaded WSGI server, so the loopback UI can hold a streaming
    connection open without blocking other local requests."""

    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args: object) -> None:  # silence stderr access logs
        return


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_http_server(
    app: Application,
    *,
    host: str = LOOPBACK_HOST,
    port: int = 0,
    allow_nonloopback: bool = False,
):
    """A stdlib WSGI server for ``app``. Bound to loopback unless ``allow_nonloopback``
    is explicitly set; a non-loopback bind without that opt-in is REFUSED, and with it a
    warning is emitted (the trust surface widens off-host). ``port=0`` picks an ephemeral
    port — read ``server.server_address[1]`` for it."""
    if not _is_loopback(host):
        if not allow_nonloopback:
            raise ValueError(
                f"refusing to bind non-loopback host {host!r}: this is a local daemon; "
                "pass allow_nonloopback=True to override deliberately"
            )
        warnings.warn(
            f"decima API bound to NON-LOOPBACK {host!r}: the local API is now reachable "
            "off-host — ensure this is intended and network-protected",
            stacklevel=2,
        )
    server = make_server(
        host, port, app, server_class=ThreadingWSGIServer, handler_class=_QuietHandler
    )
    return server


def serve(
    db_path: str,
    *,
    host: str = LOOPBACK_HOST,
    port: int = 8973,
    seed: bytes | None = None,
    allow_nonloopback: bool = False,
) -> None:  # pragma: no cover - blocking entrypoint
    """Build and run the API until interrupted. Prints the pairing secret once so a local
    browser can authenticate."""
    app, identity = build_application(db_path, seed=seed)
    server = make_http_server(app, host=host, port=port, allow_nonloopback=allow_nonloopback)
    print(
        f"decima API on http://{host}:{server.server_address[1]}/api/v1  "
        f"(pairing secret: {identity.pairing_secret})"
    )
    server.serve_forever()
