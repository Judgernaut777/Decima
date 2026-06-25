"""TIMELINE1 — the user-facing activity feed over the signed Weft.

Where AUDIT1 (audit.py) draws a *compliance* lens (financial gates, denials,
outward effects), this module draws the *human* lens: "what happened lately?"
It is the activity timeline / digest you would show a person — an ordered feed of
recent events rendered as readable activity entries (who did what, to which cell),
and a grouped "what happened" summary with counts.

It is a pure, read-only consumer of the PUBLIC read APIs:
  - `k.weft.events()` — yields events in causal (seq) order, recomputing each
    event's content id and verifying the author's signature ON READ (weft.py
    §events; specs/WEFT_PROTOCOL.md §8). Reaching the end of the iterator is itself
    the tamper-evidence: a single altered byte raises `WeftError`, so the feed fails
    loud rather than presenting a tampered history as a clean timeline.
  - `k.weave()` — the fold, used only to name the *type* of the cell each event
    touched (Law 3: everything is a Cell). Read-only; never mutated.
  - `k.keyring.name_of(...)` — a human name for a principal id.

It edits no core file and never appends to the Weft: the Weft stays append-only and
its tamper-evidence is untouched. Ints, not floats. Every structure is a plain dict
AND has a human-readable `summary(...)`.

Provenance is preserved end to end: every entry carries the signing author, the
capability that authorized the act (provenance of power), and the event id (the
provenance handle), exactly as the signed Event records them.
"""
from decima.weft import WeftError, ASSERT, RETRACT, INVOKE, ATTEST


# -- human-facing vocabulary -------------------------------------------------
# A short, person-readable verb for each Weft instruction. The Weft's verb set is
# four (ASSERT | RETRACT | INVOKE | ATTEST); this is only the display label.
_VERB_WORD = {
    ASSERT: "asserted",
    RETRACT: "retracted",
    INVOKE: "invoked",
    ATTEST: "attested",
}


def _name(k, principal_id: str) -> str:
    """Human name for a principal id, via the keyring (falls back to the id)."""
    try:
        n = k.keyring.name_of(principal_id)
    except Exception:
        n = None
    return n or principal_id


def _touched_cell(ev) -> str | None:
    """The cell id this event acted on, read from the body by verb shape.

    An ASSERT/RETRACT names its `cell` (or, for an EDGE assertion, its `src`); an
    INVOKE names the `cap` it exercised; an ATTEST names its `target_cell`. Returns
    None when the event references no single cell (e.g. an edge-only assertion has
    src+dst rather than one cell)."""
    b = ev.body if isinstance(ev.body, dict) else {}
    for key in ("cell", "target_cell", "cap", "src"):
        v = b.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _describe(ev, verb_word: str, cell_type: str | None) -> str:
    """A short human description of the activity, e.g. 'asserted a capability' or
    'invoked an effect through a capability'. Purely cosmetic — the structured
    fields (verb, cell, cell_type) carry the machine-readable truth."""
    if ev.verb == INVOKE:
        return "invoked an effect through a capability"
    if ev.verb == ATTEST:
        return f"attested a {cell_type or 'cell'}"
    thing = cell_type or "cell"
    if ev.verb == RETRACT:
        return f"retracted a {thing}"
    return f"asserted a {thing}"


# -- the timeline ------------------------------------------------------------
def timeline(k, *, last: int | None = None, principal: str | None = None,
             cell_type: str | None = None) -> dict:
    """The most recent events as human-facing activity entries, newest LAST in
    causal (seq) order — the same order `weft.events()` yields.

    Each entry names: `seq` (the position), `author` + `author_name` (who),
    `verb` + `verb_word` (what kind of act), `description` (a readable summary),
    `cell` + `cell_type` (the cell/effect touched), `authorized_by` (the capability
    that permitted it — provenance of power), and `provenance` (the event id).

    Filters (both optional, and composable):
      - `principal` — only events authored by that principal id;
      - `cell_type` — only events whose touched cell is of that type.
    `last=N` keeps the N most recent entries AFTER filtering (None = all).

    `verifiable` is True iff the whole history re-derived cleanly on read; on tamper
    it is False, `error` says where, and `entries` is empty — the feed never presents
    a tampered log as a clean timeline."""
    try:
        evs = list(k.weft.events())            # verifies id + sig per event (Law 1/4)
        ok, err = True, None
    except WeftError as e:                      # tampered history — fail closed
        evs, ok, err = [], False, str(e)

    w = k.weave() if evs else None
    entries = []
    for ev in evs:                             # events() yields in causal (seq) order
        if principal is not None and ev.author != principal:
            continue
        cid = _touched_cell(ev)
        ctype = None
        if cid and w is not None:
            cell = w.cells.get(cid)
            ctype = cell.type if cell else None
        if cell_type is not None and ctype != cell_type:
            continue
        verb_word = _VERB_WORD.get(ev.verb, ev.verb.lower())
        entries.append({
            "seq": ev.seq,
            "author": ev.author,
            "author_name": _name(k, ev.author),
            "verb": ev.verb,
            "verb_word": verb_word,
            "description": _describe(ev, verb_word, ctype),
            "cell": cid,
            "cell_type": ctype,
            "authorized_by": ev.authorized,    # capability cell id, or None
            "provenance": ev.id,
        })

    if last is not None:
        entries = entries[-last:]              # the N most recent, newest last
    return {
        "entries": entries,
        "count": len(entries),
        "filters": {"principal": principal, "cell_type": cell_type, "last": last},
        "verifiable": ok,
        "error": err,
    }


# -- the digest --------------------------------------------------------------
def digest(k, *, last: int | None = None, principal: str | None = None,
           cell_type: str | None = None) -> dict:
    """A grouped "what happened" summary over the same recent activity. The exact
    entries `timeline(...)` would return are folded into counts by three lenses:

      - `by_verb`      — verb_word → count (what kinds of act happened);
      - `by_principal` — author_name → count (who was active);
      - `by_cell_type` — cell type → count (what kinds of thing were touched;
                         events touching no single cell count under 'effect').

    Carries the same `verifiable`/`error` tamper-evidence as the timeline it sums."""
    tl = timeline(k, last=last, principal=principal, cell_type=cell_type)
    by_verb: dict[str, int] = {}
    by_principal: dict[str, int] = {}
    by_cell_type: dict[str, int] = {}
    for e in tl["entries"]:
        by_verb[e["verb_word"]] = by_verb.get(e["verb_word"], 0) + 1
        by_principal[e["author_name"]] = by_principal.get(e["author_name"], 0) + 1
        bucket = e["cell_type"] or ("effect" if e["verb"] == INVOKE else "—")
        by_cell_type[bucket] = by_cell_type.get(bucket, 0) + 1
    return {
        "total": tl["count"],
        "by_verb": by_verb,
        "by_principal": by_principal,
        "by_cell_type": by_cell_type,
        "filters": tl["filters"],
        "verifiable": tl["verifiable"],
        "error": tl["error"],
    }


# -- human-readable summaries ------------------------------------------------
def summary(report: dict) -> list[str]:
    """Render a timeline or digest dict to human lines."""
    if "entries" in report:                    # a timeline
        flt = report["filters"]
        scope = []
        if flt.get("principal"):
            scope.append(f"by {flt['principal'][:12]}")
        if flt.get("cell_type"):
            scope.append(f"of {flt['cell_type']}")
        scope_s = (" (" + ", ".join(scope) + ")") if scope else ""
        out = [f"activity timeline{scope_s} — {report['count']} entry(ies), "
               f"verifiable={report['verifiable']}"]
        for e in report["entries"]:
            cap = e["authorized_by"][:8] if e["authorized_by"] else "—"
            cell = e["cell"][:10] if e["cell"] else "—"
            out.append(f"  e{e['seq']:<3} {e['author_name']:<10} {e['verb_word']:<9} "
                       f"{e['description']:<38} cell={cell} via {cap} "
                       f"prov={e['provenance'][:8]}")
        return out
    # a digest
    out = [f"activity digest — {report['total']} entry(ies), "
           f"verifiable={report['verifiable']}"]

    def _grp(title, d):
        line = f"  {title}: " + ", ".join(
            f"{kk}×{vv}" for kk, vv in sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))
        return line if d else f"  {title}: —"
    out.append(_grp("by verb", report["by_verb"]))
    out.append(_grp("by principal", report["by_principal"]))
    out.append(_grp("by cell type", report["by_cell_type"]))
    return out
