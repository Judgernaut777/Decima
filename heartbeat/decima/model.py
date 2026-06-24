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
from decima.weft import ASSERT
from decima.hashing import content_id, nfc


def define_type(weft, author: str, name: str, merge_class: str | None = None,
                field_classes: dict | None = None) -> str:
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
    content = {"name": name}
    if merge_class is not None:
        content["merge_class"] = merge_class
    if field_classes is not None:
        content["field_classes"] = field_classes
    weft.append(author, ASSERT, {
        "cell": cid, "type": "type", "kind": "TYPE_DEF",
        "content": content,
    })
    return cid


def assert_content(weft, author: str, cell: str, type: str, content: dict):
    """Assert a CONTENT version of a Cell (the kernel's existing path, named)."""
    return weft.append(author, ASSERT, {
        "cell": cell, "type": type, "kind": "CONTENT", "content": content,
    })


def assert_edge(weft, author: str, src: str, rel: str, dst: str):
    """Assert a typed relation `src → rel → dst`. The edge has no `cell` of its
    own; the fold folds it onto src.edges_out and dst.edges_in."""
    return weft.append(author, ASSERT, {
        "kind": "EDGE", "src": src, "rel": nfc(rel), "dst": dst,
    })
