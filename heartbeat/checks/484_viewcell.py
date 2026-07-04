"""ACCRETING VIEWS — a user-defined view is a Cell, not one of four hardcoded lenses.

VISION promises an "accreting Shell", but until now the workspace had exactly FOUR
lenses baked into the source tree (notes/board/graph/timeline) — a user could not
define a new way of seeing the Weave; the workspace never grew. `workspace.define_view`
makes a view a CELL: a named, declarative lens spec (which cell type(s), which scope,
which edge/backlink relation — DATA, never code) recorded on the Weft; `render` folds
the Weave through that spec into display lines exactly like the built-ins; `views`
lists what has accreted.

This check proves, offline + deterministically (fresh Kernels over one tmp db, no
clock, no network):

  (a) A USER-DEFINED VIEW ACCRETES + RENDERS — a lens that did not exist before
      ("lab-ideas": type `idea`, scope `realm:lab`) is defined, matching cells render
      under it, non-matching cells (wrong scope / wrong type) do NOT, an edge-filtered
      view ("grounded") shows only cells with the named relation, `views(k)` lists the
      accreted lenses, and — crucially — a NEW Kernel reconstructed over the SAME
      weft.db folds the view back and renders it identically (the view is a durable
      Cell: the workspace accreted);
  (b) PURE PROJECTION — rendering appends ZERO events (a Law-5 lens, nothing stored),
      a re-render is bit-identical, a non-declarative spec (code strings under unknown
      keys, callables, floats, empty/non-dict) is refused LOUD, a built-in lens name
      cannot be shadowed, and an unknown view FAILS CLOSED;
  (c) BUILT-INS UNAFFECTED — notes/board/graph/timeline still render, and
      `render(k, "notes")` is exactly `workspace.notes(...)` (one dispatcher, five+
      lenses).

Mutation-resistance (the load-bearing line): neuter the `assert_content(...)` append
inside `define_view` and the view never lands on the Weft — `render("lab-ideas")`
fails closed immediately after defining it and (a) goes RED: the workspace no longer
accretes.

Contract: run(k, line). Fail loud (assert / expected ViewError). Owns fresh Kernels
over its own tmp db; registers no effects and touches no shared state.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import workspace
from decima.model import assert_content, assert_edge
from decima.hashing import content_id


def _idea(k, text, scope):
    """Assert a small `idea` Cell (untrusted content is data) and return its id."""
    cid = content_id({"idea": text, "scope": scope})
    assert_content(k.weft, k.human.id, cid, "idea", {"text": text, "scope": scope})
    return cid


def run(k, line):
    line("\n== ACCRETING VIEWS — a user-defined lens is a Cell, folded from the log ==")

    # ── (a) A USER-DEFINED VIEW ACCRETES + RENDERS. ───────────────────────────────────
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kk = Kernel(db, fresh=True)
    try:
        workspace.render(kk, "lab-ideas")
        raise AssertionError("a not-yet-defined view must fail closed (nothing to render)")
    except workspace.ViewError:
        pass
    vid = workspace.define_view(kk, "lab-ideas", {"types": ["idea"], "scope": "realm:lab"})
    hit1 = _idea(kk, "solar kiln", "realm:lab")
    hit2 = _idea(kk, "weft-native mail", "realm:lab")
    miss_scope = _idea(kk, "yacht", "realm:other")            # right type, wrong scope
    miss_type = content_id({"note": "lab journal"})            # right scope, wrong type
    assert_content(kk.weft, kk.human.id, miss_type, "note",
                   {"text": "lab journal", "scope": "realm:lab"})
    lines = workspace.render(kk, "lab-ideas")
    shown = "\n".join(lines)
    assert lines[0] == "[lab-ideas] (2)", \
        f"the view header must carry the EXACT int match count: {lines[0]!r}"
    assert hit1[:8] in shown and hit2[:8] in shown, "both matching cells must render"
    assert "solar kiln" in shown and "weft-native mail" in shown, "titles render like notes"
    assert miss_scope[:8] not in shown and miss_type[:8] not in shown, \
        "non-matching cells (wrong scope / wrong type) must NOT render"
    # an edge/backlink filter is part of the declarative surface: only grounded ideas.
    assert_edge(kk.weft, kk.human.id, hit1, "supported_by", miss_type)
    workspace.define_view(kk, "grounded", {"types": ["idea"], "rel": "supported_by"})
    grounded = workspace.render(kk, "grounded")
    gshown = "\n".join(grounded)
    assert grounded[0] == "[grounded] (1)" and hit1[:8] in gshown and hit2[:8] not in gshown, \
        f"the rel-filtered view must show ONLY cells bearing the relation: {grounded}"
    assert any(f"→ supported_by {miss_type[:8]}" in ln for ln in grounded), \
        "the filtered relation renders as an edge line (like the notes lens)"
    catalogue = workspace.views(kk)
    assert any("lab-ideas" in ln for ln in catalogue) and any("grounded" in ln for ln in catalogue), \
        f"views(k) must list the accreted lenses: {catalogue}"
    assert kk.weave().get(vid).type == workspace.VIEW, "a view definition is a Cell (Law 3)"
    line("  accretes: a lens that did not exist before ('lab-ideas': idea × realm:lab) now "
         "renders exactly the 2 matching cells (never the wrong-scope / wrong-type ones), an "
         "edge-filtered 'grounded' view works, and views(k) lists both ✓")

    # durable: a NEW Kernel over the SAME log folds the view back (the workspace grew).
    k2 = Kernel(db, fresh=False)
    assert workspace.render(k2, "lab-ideas") == lines, \
        "a reconstructed Kernel must fold the user view back and render it IDENTICALLY"
    assert any("lab-ideas" in ln for ln in workspace.views(k2)), \
        "the accreted view must survive reconstruction (it is a durable Cell)"
    line("  durable: a NEW Kernel over the same weft.db folds 'lab-ideas' back and renders "
         "it bit-identically — the view is a Cell on the Weft, not a source-tree privilege ✓")

    # ── (b) PURE PROJECTION — a Law-5 lens: declarative data, zero appends, fail closed. ─
    before = kk.weft.count()
    r1 = workspace.render(kk, "lab-ideas")
    r2 = workspace.render(kk, "lab-ideas")
    _ = workspace.views(kk)
    assert kk.weft.count() == before, \
        "rendering + listing must append ZERO events (a view is a projection, not storage)"
    assert r1 == r2 == lines, "a render is a deterministic pure fold (bit-identical re-render)"
    for bad in ({"types": ["idea"], "exec": "os.system('rm -rf /')"},   # unknown key = no code seam
                {"types": [lambda c: True]},                            # a callable is not data
                {"types": ["idea"], "scope": 3.5},                      # no float enters content
                {"scope": "realm:lab"},                                 # a lens must name its types
                {}, "not-a-dict"):
        try:
            workspace.define_view(kk, "evil", bad)
            raise AssertionError(f"a non-declarative spec must be refused loud: {bad!r}")
        except workspace.ViewError:
            pass
    try:
        workspace.define_view(kk, "notes", {"types": ["idea"]})
        raise AssertionError("a built-in lens name must be reserved (no shadowing)")
    except workspace.ViewError:
        pass
    try:
        workspace.render(kk, "nonesuch")
        raise AssertionError("an unknown view must FAIL CLOSED")
    except workspace.ViewError:
        pass
    assert kk.weft.count() == before, "every refused definition must land NOTHING on the Weft"
    line("  pure: rendering appends ZERO events and re-renders bit-identically; a spec is "
         "declarative DATA (code-shaped keys, callables, floats, typeless/empty specs all "
         "refused loud, landing nothing); built-in names reserved; unknown view fails closed ✓")

    # ── (c) BUILT-INS UNAFFECTED — one graph, many lenses, the original four intact. ─────
    notes_lines = workspace.notes(kk.weave())
    assert any("lab journal" in ln for ln in notes_lines), "the notes lens still renders"
    assert workspace.render(kk, "notes") == notes_lines, \
        "render('notes') must BE the notes lens (one dispatcher, built-ins included)"
    assert isinstance(workspace.board(kk), list), "the board lens still renders"
    assert isinstance(workspace.graph(kk.weave()), list), "the graph lens still renders"
    assert workspace.timeline(kk.weft, kk.keyring, limit=5), "the timeline lens still renders"
    assert workspace.render(kk, "timeline") == workspace.timeline(kk.weft, kk.keyring, limit=30)
    line("  built-ins: notes/board/graph/timeline unchanged, and render() dispatches to them "
         "by name — user views sit beside them, never in place of them ✓")

    line("  → the workspace now ACCRETES: a user-defined view is a Cell on the Weft — a "
         "declarative lens (types/scope/edges, data never code) that renders like the "
         "built-ins, folds back on every future Kernel over the same log, appends nothing "
         "when read, confers no authority, and fails closed when unknown.")
