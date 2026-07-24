"""Shared stdlib WSGI + loopback + pairing-secret helpers (Phase 5 de-duplication).

Both the API host (``decima.services.api.app`` / ``.server``) and the trusted Shell
(``decima.shell.serve``) run a small stdlib ``wsgiref`` surface bound to loopback and
persist the pairing secret to a ``0600`` file beside the Weft. These primitives were
copied verbatim into both entrypoints; they live here once so the two stay byte-identical.

This module is pure stdlib and imports nothing from Decima's own subsystems, so it is a
neutral leaf both packages can share without a directional dependency — in particular the
Shell keeps its decoupling from the concrete backend package.
"""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import parse_qsl


def parse_query(qs: str) -> dict[str, str]:
    """Parse a raw WSGI ``QUERY_STRING`` into a flat ``{name: value}`` mapping."""
    return dict(parse_qsl(qs))


def headers_from_environ(environ: dict) -> dict[str, str]:
    """Recover lowercased request headers from a WSGI ``environ`` (``HTTP_*`` + CONTENT_TYPE)."""
    headers: dict[str, str] = {}
    if environ.get("CONTENT_TYPE"):
        headers["content-type"] = environ["CONTENT_TYPE"]
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").lower()
            headers[name] = value
    return headers


def read_wsgi_body(environ: dict) -> bytes:
    """Read exactly ``CONTENT_LENGTH`` bytes from ``wsgi.input`` (empty if absent/invalid)."""
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (ValueError, TypeError):
        length = 0
    if length <= 0:
        return b""
    stream = environ.get("wsgi.input")
    return stream.read(length) if stream is not None else b""


def is_loopback(host: str) -> bool:
    """True iff ``host`` names the loopback interface (``localhost`` or a loopback IP)."""
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def write_pairing_secret(db_path: str, secret: str) -> str:
    """Persist ``secret`` to a ``0600`` ``.pairing-secret`` file beside ``db_path``, returning
    the file path.

    The pairing secret is derived deterministically from the master seed, so it is a durable
    credential — printing it to stdout leaks it into the systemd journal (a shared, readable
    sink). Create/truncate the file at mode ``0600`` from the start (never briefly
    world-readable) and tighten again in case a looser file pre-existed; callers print only
    the returned PATH, never the value."""
    target = os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", ".pairing-secret")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (secret + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(target, 0o600)  # tighten even if a prior file existed with looser perms
    return target
