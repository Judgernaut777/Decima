"""WATCH1 — watchers / reactive triggers (native LOOM: condition → action).

A watcher is Decima's OWN trusted automation: the owner registers it, declaring a
**condition over the Weave** (a cell type + a declarative predicate on its content) and
an **action** (a disposition kind/brief). `check_watchers` folds the live Weave, evaluates
every watcher's condition, and for each MATCH FIRES the action — recording a `trigger` Cell
with a `triggered_by` edge to the matching cell (audited on the Weft) and routing the action
through `disposition.dispose`.

The laws this obeys (CAPABILITY_MAP B1 automation; the same recall-vs-instruct law DISP1 obeys):
  - A watcher firing does NOT bypass the gates. It PROPOSES/RECORDS a disposition; the action
    is routed through `disposition.dispose`, which keeps `task`/`invoke`/`policy` reserved for
    trusted intakes and an `invoke` only ever a PROPOSAL (still authorize/Morta-gated). A
    watcher is trusted automation (owner-registered) — that lets it open a task / set policy —
    but it can never skip Morta on an irreversible effect.
  - The PREDICATE is a SAFE, declarative match — NEVER eval'd code from data. We support a
    record of `{field: value}` equality plus a small set of explicit comparators
    (e.g. `severity>=high`); an unknown operator is refused, not executed. So a malicious
    `content` value can never select or run a watcher's logic.
  - Signed content carries ints, not floats; everything is audited on the Weft.

Public `weave` / `disposition` / `model` API only — no core edit, no edit to disposition.py.
"""
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc
from decima import disposition as disp

WATCHER = "watcher"
TRIGGER = "trigger"

# Declarative comparators (the ONLY operators a predicate may use). Each is a pure,
# 2-arg function over already-extracted values — there is no code path that executes a
# string from the data. An ordered severity ladder makes `severity>=high` meaningful.
_SEVERITY = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _as_rank(v):
    """Map a value onto the severity ladder for ordered comparison; fall back to the raw
    value (so numeric fields still order). Returns None when incomparable."""
    if isinstance(v, str) and v.lower() in _SEVERITY:
        return _SEVERITY[v.lower()]
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    return None


def _cmp_ge(a, b):
    ra, rb = _as_rank(a), _as_rank(b)
    return ra is not None and rb is not None and ra >= rb


def _cmp_le(a, b):
    ra, rb = _as_rank(a), _as_rank(b)
    return ra is not None and rb is not None and ra <= rb


def _cmp_gt(a, b):
    ra, rb = _as_rank(a), _as_rank(b)
    return ra is not None and rb is not None and ra > rb


def _cmp_lt(a, b):
    ra, rb = _as_rank(a), _as_rank(b)
    return ra is not None and rb is not None and ra < rb


def _cmp_eq(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    ra, rb = _as_rank(a), _as_rank(b)
    if ra is not None and rb is not None:
        return ra == rb
    return a == b


def _cmp_ne(a, b):
    return not _cmp_eq(a, b)


def _cmp_contains(a, b):
    """`field~substr` — case-insensitive substring of a text field (the 'a claim containing
    X' shape). Operates on already-extracted strings; never a regex from data, never eval."""
    return isinstance(a, str) and isinstance(b, str) and b.lower() in a.lower()


# op token -> comparator. A predicate value may be a bare value (→ equality) or a
# {"op": <token>, "value": <v>} record. Unknown ops are REFUSED (see _match_field).
_OPS = {
    "eq": _cmp_eq, "==": _cmp_eq,
    "ne": _cmp_ne, "!=": _cmp_ne,
    "ge": _cmp_ge, ">=": _cmp_ge,
    "le": _cmp_le, "<=": _cmp_le,
    "gt": _cmp_gt, ">": _cmp_gt,
    "lt": _cmp_lt, "<": _cmp_lt,
    "contains": _cmp_contains, "~": _cmp_contains,
}


def _match_field(actual, spec):
    """Evaluate one field clause. `spec` is either a bare value (equality) or a
    {"op", "value"} record. An unknown operator raises — a malformed predicate must fail
    loud, never silently match or execute."""
    if isinstance(spec, dict) and "op" in spec:
        op = spec["op"]
        if op not in _OPS:
            raise ValueError(f"watcher predicate: unknown operator {op!r} (refused, not eval'd)")
        return _OPS[op](actual, spec.get("value"))
    return _cmp_eq(actual, spec)


def _matches(content, predicate):
    """A predicate is a record {field: spec, ...}; it matches iff EVERY clause holds
    (AND). An empty predicate never matches (a watcher must state a condition)."""
    if not predicate:
        return False
    if not isinstance(content, dict):
        return False
    for field, spec in predicate.items():
        if field not in content:
            return False
        if not _match_field(content[field], spec):
            return False
    return True


def _validate_predicate(predicate):
    """Reject a predicate that isn't a safe declarative record up front, so an
    ill-formed watcher can never be registered (fail closed at the boundary)."""
    if not isinstance(predicate, dict) or not predicate:
        raise ValueError("watcher predicate must be a non-empty {field: value/spec} record")
    for field, spec in predicate.items():
        if not isinstance(field, str):
            raise ValueError("watcher predicate fields must be strings")
        if isinstance(spec, dict) and "op" in spec and spec["op"] not in _OPS:
            raise ValueError(f"watcher predicate: unknown operator {spec['op']!r}")


def register_watcher(k, name, *, on_type, predicate, action, author=None):
    """Register a `watcher` Cell: a condition (`on_type` + declarative `predicate`) and an
    `action` (a disposition kind/brief). The owner registers it, so it is trusted
    automation — but firing it still routes through `disposition.dispose`.

    `action` is a record describing the disposition to raise on a match, e.g.
        {"source": "watcher", "text": "high-severity finding seen", "kind": "request"}
    (kind ∈ disposition's trusted kinds: directive→policy, request/actionable→task,
    command→invoke proposal; or omitted → remembered note). Returns the watcher cell id."""
    author = author or k.decima_agent_id
    _validate_predicate(predicate)
    if not isinstance(action, dict) or not action.get("text"):
        raise ValueError("watcher action must be a {text, kind?, ...} disposition brief")
    wid = content_id({"watcher": nfc(name), "on": on_type, "pred": predicate})
    assert_content(k.weft, author, wid, WATCHER, {
        "name": nfc(name),
        "on_type": on_type,
        "predicate": predicate,          # declarative DATA, never code
        "action": action,                # a disposition brief
        "status": "armed",
    })
    return wid


def check_watchers(k, author=None):
    """Evaluate every armed watcher's condition over the CURRENT Weave. For each matching
    cell, FIRE: record a `trigger` Cell + a `triggered_by` edge to the match (audited), and
    route the watcher's action through `disposition.dispose` (so the effect stays gated —
    a watcher proposes, it never bypasses Morta/authorize). Returns the list of fired
    trigger cell ids.

    Idempotent: a (watcher, matched-cell) pair fires at most once — its trigger id is
    content-addressed over the pair, and an existing live trigger is skipped. So re-running
    the check does not re-fire on the same match (no automation self-DoS)."""
    author = author or k.decima_agent_id
    w = k.weave()
    fired = []
    for watcher in w.of_type(WATCHER):
        c = watcher.content
        if c.get("status") != "armed":
            continue
        predicate = c.get("predicate") or {}
        action = c.get("action") or {}
        for cell in w.of_type(c.get("on_type")):
            if cell.id == watcher.id:
                continue
            if not _matches(cell.content, predicate):
                continue
            tid = content_id({"trigger": watcher.id, "match": cell.id})
            existing = w.get(tid)
            if existing is not None and not existing.retracted:
                continue                          # already fired on this match — idempotent
            # Route the action through disposition — trusted automation, still gated.
            d = disp.dispose(
                k, action.get("source", "watcher"), action["text"],
                trusted=True, kind=action.get("kind"),
                target=action.get("target"), author=author)
            # Record the trigger Cell (the firing receipt) + a provenance edge to the match.
            assert_content(k.weft, author, tid, TRIGGER, {
                "watcher": watcher.id,
                "watcher_name": c.get("name"),
                "matched": cell.id,
                "action": action.get("kind") or "remember",
                "disposition": d["disposition"],
                "disposed_action": d["action"],
            })
            assert_edge(k.weft, author, tid, "triggered_by", cell.id)
            assert_edge(k.weft, author, watcher.id, "fired", tid)
            fired.append(tid)
    return fired
