"""Per-user tenancy — one signed store per authenticated user principal (T3.2).

Multi-user isolation here is BY CONSTRUCTION, not by filtering. Each user gets:

  * their OWN ``Weft`` (their own append-only signed log) under ``<weftdir>/users/``,
  * their OWN ``ProjectionDriver`` over it (the disposable read-models),
  * their OWN ``CommandService`` (whose ``human_principal`` is that user), and
  * their OWN ``EventBus`` (so the UI stream carries only their events).

Why this shape and not a per-row owner filter: every read and every existence check in
the API already goes through exactly one seam — ``svc.weft`` / ``app.weft`` (folded by
``Weave.fold``) or the projection driver over it. Swapping that seam per request scopes
EVERY reader and EVERY command at once, including the Path-A lane readers (which satisfy
``contracts.LaneReaderApp`` with ``weft`` + ``commands``). So the guarantee is stronger
than "other users' rows are filtered out": another user's events are not in the log the
request folds, and a cross-user read or mutation is unrepresentable rather than merely
refused. A cell id guessed or stolen from another user resolves to nothing, which is why
the existing ``NOT_FOUND`` fail-closed paths in the command service cover it.

Law 2 / invariant 3. A user's authority is the capability envelope reachable from that
user's OWN log. There is NO admin, superuser, or ambient context in this module: nothing
here can widen one user's view to another's, and no principal is privileged over another.
Cross-user sharing is future work and must be an explicit, RECORDED capability grant
(and, across stores, a sync/ingest of signed events) — never an ambient read.

Determinism is untouched. Every per-user store is an ordinary ``Weft``: the fold is
replayable, the same events still produce the same ``state_root``, and nothing in this
module is content-addressed state — it is process wiring. No hashing, canonical encoding,
id text, principal-id width, or signed-struct shape changes.

Threading. Each ``Weft`` is a single ``sqlite3`` connection usable only from the thread
that opened it. Contexts are opened lazily ON the serving thread of the single-threaded
WSGI server (``server.make_http_server``), which is the same constraint the default store
already lives under.
"""

from __future__ import annotations

import os
import string
from collections.abc import Callable
from dataclasses import dataclass

from decima.kernel.crypto import Keyring
from decima.kernel.weft import Weft
from decima.projections.engine import ProjectionDriver
from decima.services.api.commands import CommandService
from decima.services.api.events import EventBus
from decima.services.api.models_setup import ModelStack

__all__ = ["STORES_DIR", "UserContext", "build_user_context", "store_path_for"]

STORES_DIR = "users"

# A principal id is kind-prefixed base32 (``prn_...``): lowercase letters, digits and one
# underscore. Anything else is refused rather than trusted as a path component — no
# traversal, no absolute path, no separator, no shell-significant character.
_SAFE_ID_CHARS = frozenset(string.ascii_lowercase + string.digits + "_")


def store_path_for(db_path: str, principal: str) -> str:
    """The per-user Weft path for ``principal``, beside the daemon's own store.

    The principal id is validated character-by-character first: it becomes a filename, and
    a filename is not the place to find out whether an id was well formed.
    """
    if not principal or not set(principal) <= _SAFE_ID_CHARS:
        raise ValueError(f"refusing unsafe principal id as a store name: {principal!r}")
    base = os.path.dirname(os.path.abspath(db_path)) or "."
    return os.path.join(base, STORES_DIR, f"{principal}.db")


@dataclass
class UserContext:
    """One principal's slice of the daemon: the store it owns plus the disposable machinery
    over it. Structurally satisfies ``contracts.LaneReaderApp`` (``weft`` + ``commands``),
    which is what lets every Path-A lane reader be scoped without touching a lane module.

    It holds no authority of its own — it is the ADDRESS of a store, and a request only
    ever gets the one its authenticated principal owns."""

    principal: str
    weft: Weft
    driver: ProjectionDriver
    commands: CommandService
    bus: EventBus


def build_user_context(
    db_path: str,
    principal: str,
    *,
    keyring: Keyring,
    app_principal: str,
    driver_factory: Callable[[Weft], ProjectionDriver],
    models: ModelStack | None = None,
) -> UserContext:
    """Open (creating on first use) ``principal``'s own store and wire a context over it.

    ``app_principal`` stays the daemon's application principal — it is the AUTHOR of the
    events the host records on the user's behalf, exactly as in the single-user daemon —
    while ``principal`` becomes the ``human_principal``: the creator/approver of record,
    and the identity whose signature backs an approval possession proof. The user's key is
    already derivable by the keyring because the user directory minted the principal on it.

    The stores directory is created ``0700`` (and re-tightened if it pre-existed looser),
    so one local account's store is not readable by another local user.
    """
    path = store_path_for(db_path, principal)
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    os.chmod(parent, 0o700)
    weft = Weft(path, keyring)
    driver = driver_factory(weft)
    bus = EventBus()
    commands = CommandService(
        weft,
        driver,
        app_principal=app_principal,
        human_principal=principal,
        event_bus=bus,
        models=models,
    )
    return UserContext(principal=principal, weft=weft, driver=driver, commands=commands, bus=bus)
