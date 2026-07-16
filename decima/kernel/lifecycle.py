"""Lifecycle — revocation, supersession, and termination as RETRACT events (DEC-018).

Every lifecycle operation is a RETRACT on the Weft (Law 1: nothing happens off the log);
the fold (weave.py) derives the cascade that fails closed the affected authority subtree.
These are thin, faithful helpers extracted from the reference kernel's methods — the same
bodies, taking an explicit `weft` + author principal instead of a bound `self`, so they
compose over the extracted kernel without the runtime.

Semantics (WEFT §5, mirrored from the reference):
  * revoke     — WITHDRAW a capability; the fold defaults a capability RETRACT to a
                 DERIVED_AUTHORITY cascade, so every grant/lease descending from it
                 fails closed.
  * redact     — WITHDRAW and ERASE the payload from projections (right-to-be-forgotten
                 at the fold); the event skeleton stays on the log.
  * supersede  — tombstone a cell and record the replacement that took its place; payload
                 NOT erased, and NO cascade by default.
  * terminate  — hard shutdown: the cell becomes a cascade root (default LEASE_TREE) so
                 the whole descending authority tree fails closed.
"""

from __future__ import annotations

from decima.kernel.weft import RETRACT, Event, Weft


def revoke(weft: Weft, author: str, cap_id: str) -> Event:
    """Morta: revocation = RETRACT (WITHDRAW) of the capability cell. The fold cascades
    DERIVED_AUTHORITY, failing closed every descendant grant/lease."""
    return weft.append(author, RETRACT, {"cell": cap_id})


def redact(weft: Weft, author: str, cell_id: str) -> Event:
    """Morta: REDACT — withdraw AND erase the payload from every projection; a
    content-free tombstone remains and the event skeleton stays on the log."""
    return weft.append(author, RETRACT, {"cell": cell_id, "mode": "REDACT"})


def supersede(weft: Weft, author: str, cell_id: str, replacement: str | None = None) -> Event:
    """Morta: SUPERSEDE — tombstone a cell and record the `replacement` (event/cell id)
    that took its place. Payload is not erased; no cascade by default."""
    return weft.append(
        author, RETRACT, {"cell": cell_id, "mode": "SUPERSEDE", "replacement": replacement}
    )


def terminate(weft: Weft, author: str, cell_id: str, cascade: str = "LEASE_TREE") -> Event:
    """Morta: TERMINATE — hard shutdown; the cell becomes a cascade root so every
    grant/lease derived from it fails closed at the fold (default LEASE_TREE)."""
    return weft.append(author, RETRACT, {"cell": cell_id, "mode": "TERMINATE", "cascade": cascade})
