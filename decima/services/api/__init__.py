"""Decima local backend API — a narrow, authenticated, loopback HTTP surface (Phase 8).

This subpackage is the ONLY web surface, and it is deliberately thin. It never writes
storage directly: every durable change is a named command translated to accepted Weft
events through ``decima.kernel`` / ``decima.runtime`` (invariant 1). It reads only from
DISPOSABLE projections (invariant 2). It grants no ambient authority — each endpoint maps
to an explicit command behind session/CSRF/reauth, and a high-risk (gated) command cannot
bypass the approval path (invariant 3). It evaluates no untrusted code: request bodies are
JSON DATA parsed into typed command args (invariants 5, 7). Transport is stdlib only — no
web framework.

  * ``identity`` — the generated local app + operator principals and the pairing secret.
  * ``auth``     — session auth, double-submit CSRF, and the high-risk reauth hook.
  * ``events``   — the disposable UI stream bus (assistant/plan/step/approval/error).
  * ``commands`` — the command service: user intents → Weft mutations via kernel/runtime.
  * ``routes``   — endpoints → commands + per-endpoint authorization level.
  * ``app``      — the WSGI ``Application`` tying it together (driveable in-process).
  * ``server``   — the loopback-bound stdlib HTTP host + full application builder.
"""

from decima.services.api.app import Application, Response
from decima.services.api.commands import CommandResult, CommandService
from decima.services.api.events import EventBus
from decima.services.api.identity import AppIdentity, generate_identity
from decima.services.api.server import build_application, build_driver, make_http_server

__all__ = [
    "Application",
    "Response",
    "CommandService",
    "CommandResult",
    "EventBus",
    "AppIdentity",
    "generate_identity",
    "build_application",
    "build_driver",
    "make_http_server",
]
