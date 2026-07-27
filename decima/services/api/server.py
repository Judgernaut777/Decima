"""The loopback HTTP host + application builder (Phase 8).

``build_application`` assembles the whole backend from the kernel/runtime/projection
seams: a signed ``Weft``, the generated local app identity, a ``ProjectionDriver`` with
the disposable read-models registered, and the ``Application`` over them. ``serve`` runs
it on a SINGLE-THREADED WSGI server BOUND TO LOOPBACK by default (127.0.0.1) — this is a
local daemon, not a network service. Binding a non-loopback address is refused unless
the caller explicitly opts in, and then a WARNING is emitted: exposing the API off-host
widens its trust surface and must be a deliberate choice.

MULTI-USER (T3.2). The builder can attach a ``users.UserDirectory``; each authenticated
user then works in their OWN Weft under ``<weftdir>/users/`` (see ``tenancy``), so
multi-user isolation costs no filtering and no ambient admin role. Off-host exposure is
triple-gated in ``make_http_server``: explicit opt-in, real per-user authentication
provisioned, and transport confidentiality (a supplied ``ssl_context``, or an explicit
acknowledgement that TLS is terminated in front). Certificate lifecycle and network rate
limiting are NOT provided — remote exposure is designed and gated, not enabled.

Why single-threaded: it is the simplest correct shape for a single-user local daemon —
requests serialize, ``/stream`` frames are drained finitely, and nothing is queued behind
a long-held connection. It is no longer a KERNEL constraint: as of 0.3.1 (T1.3) the Weft
opens its ``sqlite3`` connection ``check_same_thread=False`` and serializes every read and
write under ``Weft.lock``, so cross-thread use is safe (it used to raise
``sqlite3.ProgrammingError`` on the first Weft read from a fresh request thread — see
``docs/release-evidence/browser/known-issues.md``). Serving threaded is therefore now a
deliberate, separately-qualified choice rather than something the store forbids.

KEY CUSTODY: a served instance holds PER-PRINCIPAL signing keys. ``build_application``
builds its Keyring through ``decima.services.custody.install_keyring``, i.e. a
``DirectoryKeyStore`` beside the Weft (one 0600 seed per principal in a 0700 directory),
and provisions the app + human principals into it. The DEV-ONLY derived custodian — one
master seed deriving EVERY principal's key — is never used by this daemon path (see
SECURITY.md, "Key custody"). A restart re-loads the same keys, so the events the previous
run signed still verify. An explicitly passed ``keyring`` is still honoured for tests and
embedders; only the default flips.

Only stdlib transport is used (``wsgiref``/``http.server``): NO web-framework dependency
(house rule).
"""

from __future__ import annotations

import os
import ssl
import warnings
from wsgiref.simple_server import WSGIRequestHandler, make_server

from decima._wsgi_util import is_loopback, write_pairing_secret
from decima.kernel import sealing
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
from decima.services.api.nona_service import ensure_store_anchor as ensure_nona_anchor
from decima.services.api.users import UserDirectory, users_path
from decima.services.custody import ensure_custody, install_keyring

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
    users: UserDirectory | None = None,
    multiuser: bool = False,
    vault: sealing.PayloadVault | None = None,
) -> tuple[Application, AppIdentity]:
    """Construct the backend over a Weft at ``db_path``. Returns the app and its
    identity (the identity's ``pairing_secret`` is what a browser presents to log in).
    A fixed ``seed`` reproduces the identity across restarts.

    Custody: unless the caller supplies its own ``keyring``, the Keyring is backed by the
    install's PER-PRINCIPAL ``DirectoryKeyStore`` (0600 keys, 0700 dir) — the default for
    a real run — and the app + human principals are provisioned into it before anything
    folds or signs. ``seed`` still seeds the non-signing master derivations (the loopback
    pairing secret); it is no longer every principal's signing key.

    MULTI-USER (T3.2) is attached when a ``users`` directory is passed, when
    ``multiuser=True``, or when a ``users.json`` already exists beside the Weft (an
    install whose operator has provisioned accounts should not silently forget them).
    Each authenticated user then gets their OWN Weft under ``<weftdir>/users/``; the
    store at ``db_path`` remains the host operator's. With none of the three, this is
    exactly the single-operator loopback daemon it has always been.

    ``vault`` is the sealed-payload data-key custodian (FOLD §10.3) — pass a
    ``sealing.DirectoryPayloadVault`` to make REDACT a real byte-erasure for sealed
    payloads. Omitted, the Weft simply cannot seal and REDACT stays projection-only,
    exactly as before."""
    kr = keyring or install_keyring(db_path, seed=seed)
    weft = Weft(db_path, kr, vault=vault)
    identity = generate_identity(kr)
    # Provision custody BEFORE the driver folds: the fold performs a verifying read of
    # every event (Weft.events), which needs the authors' keys present.
    ensure_custody(kr, (identity.app, identity.human))
    ensure_nona_anchor(weft, kr, identity.app)
    driver = build_driver(weft)
    directory = users
    if directory is None and (multiuser or os.path.exists(users_path(db_path))):
        directory = UserDirectory(users_path(db_path), kr)
    app = Application(
        weft=weft,
        driver=driver,
        identity=identity,
        event_bus=EventBus(),
        secure_cookie=secure_cookie,
        users=directory,
        db_path=db_path,
        driver_factory=build_driver,
    )
    return app, identity


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args: object) -> None:  # silence stderr access logs
        return


def make_http_server(
    app: Application,
    *,
    host: str = LOOPBACK_HOST,
    port: int = 0,
    allow_nonloopback: bool = False,
    ssl_context: ssl.SSLContext | None = None,
    allow_plaintext_remote: bool = False,
):
    """A stdlib WSGI server for ``app``. Bound to loopback unless ``allow_nonloopback``
    is explicitly set; ``port=0`` picks an ephemeral port — read
    ``server.server_address[1]`` for it.

    Off-host exposure is DELIBERATE, EXPLICIT and TRIPLE-GATED (T3.2). A non-loopback
    bind is refused unless ALL of:

      1. ``allow_nonloopback=True`` — the caller states the intent;
      2. the app has real per-user authentication provisioned. The loopback pairing
         secret is ONE shared bearer token whose only protection is local file
         permissions (it is written 0600 beside the Weft); it is a local pairing
         credential, not a remote one, and must never be the thing standing between the
         network and the store;
      3. transport confidentiality: an ``ssl_context`` (whose socket we wrap), or an
         explicit ``allow_plaintext_remote=True`` for the deployment that terminates TLS
         in front of the daemon. Session cookies and passwords must not cross a network
         in clear by default.

    Certificate lifecycle, per-IP rate limiting and origin policy are NOT provided here —
    remote exposure is designed and gated, not enabled."""
    if not is_loopback(host):
        if not allow_nonloopback:
            raise ValueError(
                f"refusing to bind non-loopback host {host!r}: this is a local daemon; "
                "pass allow_nonloopback=True to override deliberately"
            )
        if not app.multiuser_enabled():
            raise ValueError(
                f"refusing to expose the API off-host on {host!r} with no per-user "
                "authentication: the loopback pairing secret is a single shared bearer "
                "token bounded by local file permissions, not a remote credential. "
                "Provision users first (decima.services.api.users.provision_user)."
            )
        if ssl_context is None and not allow_plaintext_remote:
            raise ValueError(
                f"refusing to serve {host!r} in PLAINTEXT: session cookies and passwords "
                "would cross the network in clear. Pass ssl_context=... , or terminate "
                "TLS in front of the daemon and pass allow_plaintext_remote=True."
            )
        warnings.warn(
            f"decima API bound to NON-LOOPBACK {host!r}"
            + (" WITHOUT TLS at the daemon" if ssl_context is None else "")
            + ": the local API is now reachable off-host — ensure this is intended, "
            "network-protected, and rate-limited in front",
            stacklevel=2,
        )
    # Default WSGIServer: single-threaded, each request handled inline on the serving
    # thread. Kept deliberately (see the module docstring) — since 0.3.1 the Weft is safe
    # across threads, so this is a posture choice, not a store constraint.
    # Single-threaded also means every per-user Weft is opened and used on that one
    # thread, which is what sqlite3's same-thread rule needs.
    server = make_server(host, port, app, handler_class=_QuietHandler)
    if ssl_context is not None:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
    return server


def serve(
    db_path: str,
    *,
    host: str = LOOPBACK_HOST,
    port: int = 8973,
    seed: bytes | None = None,
    allow_nonloopback: bool = False,
    multiuser: bool = False,
    ssl_context: ssl.SSLContext | None = None,
    allow_plaintext_remote: bool = False,
) -> None:  # pragma: no cover - blocking entrypoint
    """Build and run the API until interrupted. The pairing secret is written to a
    ``0600`` file beside the Weft and only its PATH is printed — printing the value would
    land it in the systemd journal (the Shell entrypoint applies the same discipline).
    The operator's pairing credential exists in multi-user mode too: it authenticates the
    HOST OPERATOR, and it reaches only the operator's own store."""
    app, identity = build_application(db_path, seed=seed, multiuser=multiuser)
    server = make_http_server(
        app,
        host=host,
        port=port,
        allow_nonloopback=allow_nonloopback,
        ssl_context=ssl_context,
        allow_plaintext_remote=allow_plaintext_remote,
    )
    secret_path = write_pairing_secret(db_path, identity.pairing_secret)
    print(
        f"decima API on http://{host}:{server.server_address[1]}/api/v1  "
        f"(pairing secret written to {secret_path})"
    )
    server.serve_forever()
