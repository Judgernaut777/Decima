"""The workspace — projections of the Weave (Law 5: views are derived, not stored).

The workspace is **not new storage**. It is read-only lenses over the same Cells:
the very same graph shows up as a document outline, a task board, a knowledge
graph, and a timeline. A `claim` Cell appears in `notes` (its proposition), in
`graph` (a node with edges), and its asserting events in `timeline` — one Weave,
many lenses. Nothing here is canonical; every view rebuilds from the log.

Each function returns a list of display lines (like `kernel.task_tree`), so the
Shell and the smoke test render them the same way.
"""
from decima.weft import ASSERT, RETRACT, INVOKE, ATTEST

_DOC_TYPES = ("note", "claim", "entity")


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
