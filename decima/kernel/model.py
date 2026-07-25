"""The thin domain model — types and edges as DATA, not kernel code.

WEFT §4 says an ASSERT carries an *assertion kind*. The Heartbeat implements
three of them as thin helpers over `weft.append` (the fold dispatches on the
body's `kind` in `weave._apply`):

  - CONTENT   — a Cell version (today's path; the default).
  - EDGE      — a typed relation `src → rel → dst`, folded onto both endpoints.
  - TYPE_DEF  — a type is itself a Cell (Law 3), so a new type is just data.

Because the model lives in the log rather than in Python, the eventual Rust port
*reads* it instead of re-hardcoding it. Content is deliberately free-form here —
schemas/validation (WEFT §4 field 9) are a later phase.
"""

from __future__ import annotations

from typing import Any

from decima.kernel.hashing import content_id, nfc
from decima.kernel.weft import ASSERT, Event, Weft


def define_type(
    weft: Weft,
    author: str,
    name: str,
    merge_class: str | None = None,
    field_classes: dict[str, Any] | None = None,
) -> str:
    """Register a type as a Cell and return its id. Idempotent by content: the
    same type name always lands on the same TYPE_DEF cell id.

    `merge_class` (MERGE_SEMANTICS §3 — e.g. 'lww', 'mv', 'or-set', 'sequence',
    'map', 'counter', 'append-log', 'adjudicated') declares how the fold reconciles
    concurrent assertions to cells of this type. Omitted ⇒ the Weave defaults the
    type to LWW, which on a linear log is the historic overwrite behavior — so
    existing untagged callers are unchanged.

    `field_classes` (for a 'map' type, MERGE_SEMANTICS §3.1) declares the per-key
    merge class of a structured record; unlisted keys default to LWW.

    NOTE: the TYPE_DEF cell is content-addressed by NAME only (so re-declaring is
    idempotent and a type keeps one identity). Declare a type's class once."""
    cid = content_id({"type_def": name})
    content: dict[str, Any] = {"name": name}
    if merge_class is not None:
        content["merge_class"] = merge_class
    if field_classes is not None:
        content["field_classes"] = field_classes
    weft.append(
        author,
        ASSERT,
        {
            "cell": cid,
            "type": "type",
            "kind": "TYPE_DEF",
            "content": content,
        },
    )
    return cid


def assert_content(weft: Weft, author: str, cell: str, type: str, content: dict[str, Any]) -> Event:
    """Assert a CONTENT version of a Cell (the kernel's existing path, named)."""
    return weft.append(
        author,
        ASSERT,
        {
            "cell": cell,
            "type": type,
            "kind": "CONTENT",
            "content": content,
        },
    )


def assert_sealed(
    weft: Weft,
    author: str,
    cell: str,
    type: str,
    content: dict[str, Any],
    key: bytes | None = None,
) -> Event:
    """Assert a CONTENT version whose payload is SEALED — stored encrypted-at-rest under a
    per-payload data key held OUTSIDE the log (FOLD §10.3). Identical to `assert_content`
    for every reader (the fold sees the same plaintext), with the one difference that
    matters: a later REDACT can DESTROY the key, after which the payload is physically
    unrecoverable while the event id and signature still verify.

    Use it for payloads a right-to-be-forgotten request can be about. `key` pins the data
    key (a byte-reproducible seal, and the way to share one erasure domain across
    payloads); omitted, a fresh key is minted, so no two sealed payloads share ciphertext
    or an erasure domain."""
    return weft.append(
        author,
        ASSERT,
        {
            "cell": cell,
            "type": type,
            "kind": "CONTENT",
            "content": content,
        },
        seal=True,
        seal_key=key,
    )


def assert_edge(weft: Weft, author: str, src: str, rel: str, dst: str) -> Event:
    """Assert a typed relation `src → rel → dst`. The edge has no `cell` of its
    own; the fold folds it onto src.edges_out and dst.edges_in."""
    return weft.append(
        author,
        ASSERT,
        {
            "kind": "EDGE",
            "src": src,
            "rel": nfc(rel),
            "dst": dst,
        },
    )
