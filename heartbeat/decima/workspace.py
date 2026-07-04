"""The workspace — projections of the Weave (Law 5: views are derived, not stored).

The workspace is **not new storage**. It is read-only lenses over the same Cells:
the very same graph shows up as a document outline, a task board, a knowledge
graph, and a timeline. A `claim` Cell appears in `notes` (its proposition), in
`graph` (a node with edges), and its asserting events in `timeline` — one Weave,
many lenses. Nothing here is canonical; every view rebuilds from the log.

Each function returns a list of display lines (like `kernel.task_tree`), so the
Shell and the smoke test render them the same way.

ACCRETING VIEWS (VISION "the accreting Shell"): the four lenses above are
built-in, but a view is not a privilege of the source tree — a USER-DEFINED
view is a Cell. `define_view` records a named, declarative lens spec on the
Weft (which cell type(s), which scope, which edge/backlink relation — DATA,
never code); `render` folds the Weave through that spec into display lines
exactly like the built-ins; `views` lists what has accreted. Because the
definition lives in the log, a lens defined today folds back on every future
Kernel over the same Weft — the workspace GROWS the longer it runs — while
every render stays a pure Law-5 projection: rebuilt from the log on each call,
nothing stored, no authority conferred, no user code executed.
"""
from decima.weft import ASSERT, RETRACT, INVOKE, ATTEST
from decima.model import assert_content
from decima.hashing import content_id, nfc

_DOC_TYPES = ("note", "claim", "entity")

# -- accreting views: a user-defined lens is a Cell ---------------------------
VIEW = "view"                                       # the view-definition cell type
BUILTINS = ("notes", "board", "graph", "timeline")  # reserved lens names
_SPEC_KEYS = ("types", "scope", "rel", "backlink")  # the whole declarative surface


class ViewError(Exception):
    """A view definition or lookup refused CLOSED (bad spec / unknown view)."""


def view_id(name: str) -> str:
    """Content-addressed view identity: one name, one Cell — redefining a view
    is an LWW update of the same Cell (like `model.define_type`)."""
    return content_id({"view": nfc(name)})


def _validated_spec(spec) -> dict:
    """Validate + normalize a view spec. A spec is declarative DATA: an
    allow-listed dict of non-empty strings (plus a list of strings for
    `types`). Anything else — unknown keys, callables, numbers (no float can
    enter recorded content), nested structures — is refused loud, so a view
    spec is config, never code and never authority. `types` is required: a
    lens names what it shows."""
    if not isinstance(spec, dict) or not spec:
        raise ViewError("a view spec must be a non-empty dict of declarative filters")
    out = {}
    for key, val in spec.items():
        if key not in _SPEC_KEYS:
            raise ViewError(f"unknown view-spec key {key!r} (allowed: {', '.join(_SPEC_KEYS)})")
        if key == "types":
            if (not isinstance(val, (list, tuple)) or not val
                    or not all(isinstance(t, str) and t for t in val)):
                raise ViewError("view-spec 'types' must be a non-empty list of type names")
            out["types"] = [nfc(t) for t in val]
        else:
            if not isinstance(val, str) or not val:
                raise ViewError(f"view-spec {key!r} must be a non-empty string")
            out[key] = nfc(val)
    if "types" not in out:
        raise ViewError("a view spec must name at least one cell type ('types')")
    return out


def define_view(k, name: str, spec: dict) -> str:
    """Record a user-defined view as a Cell — the workspace ACCRETES a lens.

    `spec` is a declarative filter over the Weave (see `_validated_spec`):
      types    — cell type name(s) the view shows (required)
      scope    — only cells whose content['scope'] equals this
      rel      — only cells with an OUTGOING edge of this relation
      backlink — only cells with an INCOMING edge of this relation
    The definition is content-addressed by NAME (redefining updates the same
    Cell, append-only underneath) and confers NO authority — rendering it later
    is a pure read; nothing here mints, grants, or invokes. Built-in lens names
    are reserved so a user view can never shadow one (fail closed). Returns the
    view cell id."""
    if not isinstance(name, str) or not name.strip():
        raise ViewError("a view needs a non-empty name")
    nm = nfc(name.strip())
    if nm in BUILTINS:
        raise ViewError(f"{nm!r} is a built-in lens and cannot be redefined")
    body = _validated_spec(spec)
    cid = view_id(nm)
    assert_content(k.weft, k.human.id, cid, VIEW, {"name": nm, "spec": body})
    return cid


def _matches(cell, spec) -> bool:
    """Does a live cell pass a view's declarative filter? Pure data comparison
    — the spec is never executed, only compared against folded state."""
    if cell.type not in spec["types"]:
        return False
    if "scope" in spec and cell.content.get("scope") != spec["scope"]:
        return False
    if "rel" in spec and not any(e["rel"] == spec["rel"] for e in cell.edges_out):
        return False
    if "backlink" in spec and not any(e["rel"] == spec["backlink"] for e in cell.edges_in):
        return False
    return True


def views(k) -> list:
    """List the user-defined views — folded from the log like every other lens
    (Law 5: the catalogue of views is itself a projection, nothing stored)."""
    lines = []
    for c in k.weave().of_type(VIEW):
        spec = c.content.get("spec") or {}
        parts = ["types=" + ",".join(spec.get("types") or [])]
        for key in ("scope", "rel", "backlink"):
            if key in spec:
                parts.append(f"{key}={spec[key]}")
        lines.append(f"view   {c.id[:8]}  {c.content.get('name', '?'):<16} {' '.join(parts)}")
    return lines


def render(k, name: str) -> list:
    """Render a lens by name — a built-in or a user-defined view Cell.

    A defined view folds the Weave through its declarative spec into display
    lines shaped like `notes`/`board`, rebuilt from the log on every call
    (Law 5 — no stored view state; rendering appends NOTHING). The folded spec
    is re-validated at the point of use (untrusted content is data — a junk
    view cell asserted around `define_view` still cannot smuggle code in), and
    an unknown or retracted view FAILS CLOSED (ViewError) rather than rendering
    something else. Counts are ints."""
    nm = nfc((name or "").strip())
    if nm == "notes":
        return notes(k.weave())
    if nm == "board":
        return board(k)
    if nm == "graph":
        return graph(k.weave())
    if nm == "timeline":
        return timeline(k.weft, k.keyring, limit=30)
    weave = k.weave()
    cell = weave.get(view_id(nm))
    if cell is None or cell.retracted or cell.type != VIEW:
        raise ViewError(f"unknown view {nm!r} — define_view first (fail closed)")
    spec = _validated_spec(cell.content.get("spec") or {})
    matched = [c for c in weave.cells.values()
               if not c.retracted and _matches(c, spec)]
    lines = [f"[{nm}] ({len(matched)})"]
    for c in matched:
        lines.append(f"{c.type:<6} {c.id[:8]}  {_title(c)[:48]}")
        if "rel" in spec:
            for e in c.edges_out:
                if e["rel"] == spec["rel"]:
                    lines.append(f"           → {e['rel']} {e['dst'][:8]}")
        if "backlink" in spec:
            for e in c.edges_in:
                if e["rel"] == spec["backlink"]:
                    lines.append(f"           ← {e['rel']} {e['src'][:8]}")
    return lines


def _title(cell) -> str:
    c = cell.content
    return c.get("text") or c.get("proposition") or c.get("name") or ""


def notes(weave) -> list:
    """Document/outline lens: doc-like Cells with their typed relations (backlinks)."""
    lines = []
    for cell in weave.cells.values():
        if cell.retracted or cell.type not in _DOC_TYPES:
            continue
        lines.append(f"{cell.type:<6} {cell.id[:8]}  {_title(cell)[:48]}")
        for e in cell.edges_out:
            lines.append(f"           → {e['rel']} {e['dst'][:8]}")
        for e in cell.edges_in:
            lines.append(f"           ← {e['rel']} {e['src'][:8]}")
    return lines


def board(kernel) -> list:
    """Task-board lens: `task` Cells grouped into status columns."""
    cols = {}
    for t in kernel.weave().of_type("task"):
        c = t.content
        cols.setdefault(c.get("status", "?"), []).append(c)
    lines = []
    for status in ("assigned", "done", "delegated", "ungranted", "refused"):
        items = cols.get(status, [])
        if not items:
            continue
        lines.append(f"[{status}] ({len(items)})")
        for c in items:
            lines.append(f"   {c['delegator_name']} ⇒ {c['worker_name']}: "
                         f"{c['capability']} — “{(c.get('objective') or '')[:38]}”")
    return lines


def graph(weave) -> list:
    """Knowledge-graph lens: claim/entity nodes and the edges between them."""
    lines = []
    for cell in weave.cells.values():
        if cell.retracted or cell.type not in ("claim", "entity"):
            continue
        lines.append(f"({cell.type}) {cell.id[:8]}  {_title(cell)[:44]}")
        for e in cell.edges_out:
            lines.append(f"      —{e['rel']}→ {e['dst'][:8]}")
    return lines


def _summary(ev) -> str:
    b = ev.body
    if ev.verb == ASSERT:
        if b.get("kind") == "EDGE":
            return f"EDGE {b.get('src', '')[:8]} —{b.get('rel')}→ {b.get('dst', '')[:8]}"
        return f"{b.get('type', '?')} {b.get('cell', '')[:8]}"
    if ev.verb == INVOKE:
        return f"cap {b.get('cap', '')[:8]} {b.get('args', {})}"
    if ev.verb == ATTEST:
        return f"attest {b.get('target_cell', '')[:8]} promote={b.get('promote')}"
    if ev.verb == RETRACT:
        return f"retract {b.get('cell', '')[:8]}"
    return ""


def timeline(weft, keyring=None, limit: int | None = None) -> list:
    """History lens: events in causal (seq) order — the transcript of the Weave."""
    evs = list(weft.events())
    if limit:
        evs = evs[-limit:]
    lines = []
    for ev in evs:
        who = keyring.name_of(ev.author) if keyring else ev.author[:8]
        lines.append(f"e{ev.seq:<3} {ev.verb:<7} {who:<9} {_summary(ev)}")
    return lines
