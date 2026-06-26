"""OFFICE1 — Office / docs editing: editable office documents as Cells.

An *office document* is a Cell (Law 3): a `kind` (doc / sheet / slides), a `title`,
and a `body`. This is the EDITABLE-office sibling of `doc.py` — and they are
distinct on purpose: a `doc.py` DOCUMENT is a piece of *knowledge* (title+body,
searchable, citable, trust-typed for the recall-vs-instruct law); an `office_doc`
is a *workspace artifact you edit* — a wordprocessor doc, a spreadsheet, a slide
deck — whose value is its evolving content + version history, not its citeability.

Two laws, cleanly split (the OFFICE1 invariant):

  • EDITING IS LOCAL. `create` and `edit` are plain ASSERTs on the Weft — no Morta,
    no outward effect. Editing your own document never leaves the box, so it needs
    no gate. Each `edit` asserts a NEW CONTENT version of the SAME cell id (LWW): on
    the linear log the latest version is what `get` materializes (`cell.version`
    counts the revisions), while EVERY prior version stays on the Weft as its own
    ASSERT event — `history()` reconstructs them by folding the log at each seq
    (Law 5: state is a fold; provenance + versioning on the Weft).

  • PUBLISH / SHARE IS AN OUTWARD EFFECT. Sending a document OUT of the box (publish
    it, share it with a peer) is irreversible egress, so it composes the same safety
    primitives the payments / messaging rails do (PAY1 / MSG1 pattern): an effect
    registered via the public `executor.register` (through `kernel.integrate_tool`),
    a capability carrying **Morta** (`requires_approval` — DENIED until a human/policy
    approves) and an **SB1 sandbox** profile (only this effect may run; network on, to
    the publish rail). Every publish lands a full EffectReceipt on the Weft (audit).

Identity (content-addressed by title+kind, so a doc keeps ONE identity across edits)
and the integer discipline (a `sheet`'s cell values and any aggregate are ints, never
floats — exact, replayable arithmetic) are both Weave laws applied here.

Pure composition: public `executor` / `kernel` / `model` / `hashing` API only. Edits
no core file and no other module.
"""
from __future__ import annotations

from decima import executor
from decima.hashing import content_id, nfc
from decima.model import assert_content
from decima.weave import Weave

OFFICE_DOC = "office_doc"          # the editable-document Cell type
KINDS = ("doc", "sheet", "slides")  # the three document kinds

PUBLISH = "PUBLISH"                 # the effect_class for an outward publish/share
PUBLISH_EFFECT = "office.publish"   # the registered outbound effect name
RESULT = "result"                   # the EffectReceipt cell type the kernel asserts


# -- identity ----------------------------------------------------------------
def doc_id(kind: str, title: str) -> str:
    """Content-address an office document by its (kind, title), so edits to the same
    document land on one cell id (stable identity; LWW versions accrete on it)."""
    return content_id({"office_doc": nfc(title), "kind": nfc(kind)})


# -- (local) create & edit — no gate, editing never leaves the box ------------
def create(k, kind: str, title: str, *, author: str | None = None,
           body: dict | None = None) -> str:
    """Create an `office_doc` Cell (version 1) and return its cell id.

    `kind` is one of doc / sheet / slides. A fresh `doc`/`slides` carries a text
    `body` (a {"text": "..."} map by default); a `sheet` carries `cells` — a
    {coord: int} map (the cell values), integers only. Editing is LOCAL — a plain
    ASSERT on the Weft, no Morta."""
    author = author or k.decima_agent_id
    kind = nfc(kind)
    if kind not in KINDS:
        raise ValueError(f"office_doc kind must be one of {KINDS}, got {kind!r}")
    cid = doc_id(kind, title)
    if body is None:
        body = {"cells": {}} if kind == "sheet" else {"text": ""}
    body = _normalize_body(kind, body)
    assert_content(k.weft, author, cid, OFFICE_DOC, {
        "kind": kind, "title": nfc(title), "body": body, "published": False,
    })
    return cid


def edit(k, doc: str, patch: dict, *, author: str | None = None) -> str:
    """Apply a `patch` to an office document as a NEW LWW version (history on the Log).

    The prior version is NOT overwritten on the Weft: it remains its own ASSERT event
    (see `history`). The materialized cell's `version` bumps to reflect the revision
    count. The merge is shallow over the `body`:
      - doc / slides: `patch` keys (e.g. {"text": ...}) overwrite body keys;
      - sheet: a `patch` of {coord: int} updates those cells, leaving others; a cell
        set to None is deleted. Sheet values MUST be ints (exact, replayable sums).
    Returns the (unchanged) cell id."""
    author = author or k.decima_agent_id
    cur = k.weave().get(doc)
    if cur is None or cur.type != OFFICE_DOC:
        raise ValueError(f"no office_doc {doc!r} to edit")
    kind = cur.content["kind"]
    body = dict(cur.content.get("body", {}))
    if kind == "sheet":
        cells = dict(body.get("cells", {}))
        for coord, val in (patch or {}).items():
            coord = nfc(str(coord))
            if val is None:
                cells.pop(coord, None)
                continue
            if not isinstance(val, int) or isinstance(val, bool):
                raise ValueError(f"sheet cell {coord!r} must be an int, got {val!r}")
            cells[coord] = int(val)
        body["cells"] = cells
    else:
        for key, val in (patch or {}).items():
            body[nfc(str(key))] = nfc(val) if isinstance(val, str) else val
    assert_content(k.weft, author, doc, OFFICE_DOC, {
        "kind": kind, "title": cur.content["title"], "body": body,
        "published": bool(cur.content.get("published", False)),
    })
    return doc


def _normalize_body(kind: str, body: dict) -> dict:
    """Coerce a caller-supplied body to the kind's shape, enforcing the int law for
    a sheet (values are ints, never floats — exact, replayable arithmetic)."""
    if kind == "sheet":
        cells = {}
        for coord, val in (body.get("cells", body) or {}).items():
            if coord == "cells":
                continue
            if not isinstance(val, int) or isinstance(val, bool):
                raise ValueError(f"sheet cell {coord!r} must be an int, got {val!r}")
            cells[nfc(str(coord))] = int(val)
        return {"cells": cells}
    out = dict(body)
    if "text" in out and isinstance(out["text"], str):
        out["text"] = nfc(out["text"])
    return out


def get(k, doc: str):
    """The latest version of an office document as a Cell (LWW head), or None."""
    cell = k.weave().get(doc)
    if cell is None or cell.type != OFFICE_DOC or cell.retracted:
        return None
    return cell


def history(k, doc: str) -> list:
    """Reconstruct every version of an office document from the Log (oldest → newest).

    Each prior version is recovered by folding the Weft up to the seq of the ASSERT
    event that wrote it — Law 5: state is a fold, so history is just folding at earlier
    points. Returns {seq, version, body} per revision."""
    out = []
    for ev in k.weft.events():
        b = ev.body or {}
        if b.get("cell") == doc and b.get("kind") == "CONTENT":
            cell = Weave.fold(k.weft, upto_seq=ev.seq).get(doc)
            if cell is not None:
                out.append({"seq": ev.seq, "version": cell.version,
                            "body": dict(cell.content.get("body", {}))})
    return out


# -- compute — a deterministic integer aggregation over a sheet ---------------
def compute(k, sheet: str) -> int:
    """A deterministic integer aggregation over a `sheet`'s cells (SUM of values).

    Pure over the sheet's current head: same cells → same int, every replay (the
    values are ints by the edit law, so the sum is exact — no float drift). Raises if
    `sheet` is not a sheet."""
    cell = k.weave().get(sheet)
    if cell is None or cell.type != OFFICE_DOC:
        raise ValueError(f"no office_doc {sheet!r}")
    if cell.content["kind"] != "sheet":
        raise ValueError(f"compute is for a sheet, not a {cell.content['kind']!r}")
    cells = cell.content.get("body", {}).get("cells", {})
    total = 0
    for v in cells.values():
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"non-int sheet value {v!r}")
        total += int(v)
    return int(total)


# -- (outward) publish / share — a Morta-gated, sandboxed, audited effect ------
def _publish_handler(args: dict) -> dict:
    """The publish rail — a deterministic stub standing in for an external publish/
    share provider (Google Drive share, a CMS, an email of the export…). A real
    handler calls the provider over the network-to-rail-only sandbox; here it confirms
    deterministically. A bad request (missing doc/target) raises ExecError → a FAILED
    receipt: a definite no-effect, nothing left the box."""
    doc = nfc(str(args.get("doc", "")))
    to = nfc(str(args.get("to", "")))
    if not doc:
        raise executor.ExecError("publish requires a document id")
    if not to:
        raise executor.ExecError("publish requires a target")
    return {"out": f"published {doc} to {to}", "doc": doc, "to": to}


def install_rail(k, *, name: str = PUBLISH_EFFECT) -> str:
    """Register the outward `office.publish` effect and forge a PUBLISH capability
    granted to Decima: Morta `requires_approval` (DENIED until approved) + an SB1
    sandbox profile that allows only this effect (network on, to the rail). Returns
    the capability id. Editing needs no rail; only PUBLISH/SHARE is gated."""
    caveats = {
        "effect_class": PUBLISH,
        "requires_approval": True,          # Morta gate — denied until approved
        # SB1 sandbox: only this effect may run under the cap; network on (to the
        # rail). The durable form pins egress to the provider host.
        "sandbox": {"effects": [name], "network": True},
    }
    return k.integrate_tool(name, lambda _impl, args: _publish_handler(args),
                            caveats=caveats)


def publish(k, agent_cell, cap_id, doc, to, *, author: str | None = None) -> dict:
    """Publish / share an office document OUT of the box through the Morta-gated,
    sandboxed `office.publish` capability. DENIED until the capability is approved
    (Morta); on success the kernel emits an EffectReceipt (audit) and the document is
    marked `published` (a new local version recording it left the box).

    Returns {status, result_cell, denied?, doc}."""
    author = author or k.decima_agent_id
    res = k.invoke(agent_cell, cap_id, {"doc": nfc(str(doc)), "to": nfc(str(to))})
    out = {"status": res.get("status"), "result_cell": res.get("result_cell"),
           "doc": doc}
    if "denied" in res:                                     # Morta / sandbox refusal
        out["denied"] = res["denied"]
        return out

    # The publish ran: record that the document left the box (a new local version).
    cur = k.weave().get(doc)
    if cur is not None and cur.type == OFFICE_DOC:
        assert_content(k.weft, author, doc, OFFICE_DOC, {
            "kind": cur.content["kind"], "title": cur.content["title"],
            "body": cur.content.get("body", {}), "published": True,
            "published_to": nfc(str(to)), "receipt": res["result_cell"],
        })
    return out
