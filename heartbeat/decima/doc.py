"""Documents / knowledge-base — first-class knowledge Cells with provenance.

A *document* is a Cell (Law 3): a `title`, a `body`, and the trust of its source.
This is the knowledge-base sibling of `memory.py` (a claim is a Cell; a doc is a
Cell). The same trust law the browser receipt and memory obey applies here:
content from an UNTRUSTED source is DATA — it is written `instruction_eligible=False`
and the brain never treats a doc's body as an instruction just because it recalled
it. A trusted doc (authored by the user / Decima itself) may be instruction-eligible.

Identity & history (LWW, the Weave default for an untagged type):
  - A doc Cell is content-addressed by TITLE, so it keeps ONE identity across edits.
  - `update_doc` asserts a NEW CONTENT version of that same cell id. On the linear
    log LWW means the latest version is what `get` materializes (`cell.version`
    counts the revisions), while EVERY prior version stays on the Weft as its own
    ASSERT event — `history()` reconstructs them by folding the log at each seq.

Edges (typed relations, model.assert_edge):
  - document —references→ document   (a doc cites another doc)
  - document —about→ entity          (a doc is about a thing)

Search reuses the retrieval seam (`memory.Retriever`): the default is a lexical
token match over title+body, but any Retriever (vector/graph) drops in behind the
same interface. No core file is touched — this composes the public model/weave API.
"""
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc
from decima import memory

DOCUMENT = "document"
ENTITY = "entity"

# Typed relations a document participates in.
REFERENCES = "references"   # document → document
ABOUT = "about"             # document → entity


def doc_id(title: str) -> str:
    """Content-address a document by its title, so edits to the same titled doc
    land on one cell id (stable identity; LWW versions accrete on it)."""
    return content_id({"document": nfc(title)})


def _permissions(instruction_eligible: bool, recallable: bool, citable: bool) -> dict:
    return {
        "recallable": bool(recallable),
        "citable": bool(citable),
        "instruction_eligible": bool(instruction_eligible),
    }


def create_doc(k, title: str, body: str, *, trusted: bool = True,
               author: str | None = None, about: str | None = None,
               source: str | None = None,
               instruction_eligible: bool | None = None,
               recallable: bool = True, citable: bool = True) -> str:
    """Create a `document` Cell (version 0) and return its cell id.

    `trusted` records whether the SOURCE is trusted. An untrusted-sourced doc is
    stored as DATA: its body is never instruction-eligible (the recall-vs-instruct
    law), regardless of what a caller passes. A trusted doc may be made
    instruction-eligible explicitly; by default it is not (DATA-by-default — a doc
    is knowledge to read, not an order to obey).

    `about` optionally links the doc to an entity it concerns (document—about→entity).
    """
    author = author or k.decima_agent_id
    title, body = nfc(title), nfc(body)
    # Untrusted source ⇒ DATA, full stop. Trusted ⇒ honor caller (default False).
    if not trusted:
        eligible = False
    else:
        eligible = bool(instruction_eligible) if instruction_eligible is not None else False
    cid = doc_id(title)
    content = {
        "title": title,
        "body": body,
        "trusted": bool(trusted),
        "source": nfc(source) if source is not None else None,
        **_permissions(eligible, recallable, citable),
    }
    assert_content(k.weft, author, cid, DOCUMENT, content)
    if about is not None:
        eid = memory.entity_id(about)
        assert_content(k.weft, author, eid, ENTITY, {"name": nfc(about)})
        assert_edge(k.weft, author, cid, ABOUT, eid)
    return cid


def update_doc(k, title: str, body: str, *, trusted: bool | None = None,
               author: str | None = None, source: str | None = None,
               instruction_eligible: bool | None = None,
               recallable: bool | None = None, citable: bool | None = None) -> str:
    """Assert a NEW version of an existing doc (LWW) — same cell id, fresh CONTENT.

    The prior version is NOT overwritten on the Log: it remains as its own ASSERT
    event (see `history`). The materialized cell's `version` bumps to reflect the
    revision count. Fields not given are carried forward from the current head, so
    a body-only edit keeps the doc's trust/source/permissions. Trust still binds:
    an untrusted doc can never become instruction-eligible via an update.
    """
    author = author or k.decima_agent_id
    cid = doc_id(title)
    cur = k.weave().get(cid)
    if cur is None or cur.type != DOCUMENT:
        raise ValueError(f"no document titled {title!r} to update")
    prev = cur.content
    trusted_v = bool(prev.get("trusted", True)) if trusted is None else bool(trusted)
    source_v = prev.get("source") if source is None else (nfc(source) if source is not None else None)
    recallable_v = prev.get("recallable", True) if recallable is None else bool(recallable)
    citable_v = prev.get("citable", True) if citable is None else bool(citable)
    if not trusted_v:
        eligible = False
    elif instruction_eligible is None:
        eligible = bool(prev.get("instruction_eligible", False))
    else:
        eligible = bool(instruction_eligible)
    content = {
        "title": nfc(title),
        "body": nfc(body),
        "trusted": trusted_v,
        "source": source_v,
        **_permissions(eligible, recallable_v, citable_v),
    }
    assert_content(k.weft, author, cid, DOCUMENT, content)
    return cid


def history(k, title: str) -> list:
    """Reconstruct every version of a doc from the Log (oldest → newest).

    Each prior version is recovered by folding the Weft up to the seq of the ASSERT
    event that wrote it — Law 5: state is a fold, so history is just folding at
    earlier points. Returns a list of {seq, version, content} for the doc cell."""
    from decima.weave import Weave

    cid = doc_id(title)
    out = []
    for ev in k.weft.events():
        body = ev.body or {}
        if body.get("cell") == cid and body.get("kind") == "CONTENT":
            cell = Weave.fold(k.weft, upto_seq=ev.seq).get(cid)
            if cell is not None:
                out.append({"seq": ev.seq, "version": cell.version,
                            "content": dict(cell.content)})
    return out


def link_doc(k, src: str, rel: str, dst: str, *, author: str | None = None) -> None:
    """Assert a typed edge from one doc to another doc or to an entity.

    `rel` is REFERENCES (doc→doc) or ABOUT (doc→entity); any nfc string is allowed
    (edges are free-form relations), but those two are the document vocabulary."""
    author = author or k.decima_agent_id
    assert_edge(k.weft, author, src, rel, dst)


def references(weave, src: str) -> list:
    """Doc ids that `src` references (document—references→document)."""
    return [e["dst"] for e in weave.edges_from(src, REFERENCES)]


def referenced_by(weave, dst: str) -> list:
    """Doc ids that reference `dst`."""
    return [e["src"] for e in weave.edges_to(dst, REFERENCES)]


# -- search seam -------------------------------------------------------------
class DocRetriever(memory.Retriever):
    """Lexical token match over a document's title + body, honoring `recallable`
    and an optional `scope` filter. The default search engine; swap any
    `memory.Retriever` (vector/graph) in behind the same interface."""

    def search(self, weave, query: str, scope: str | None = None,
               memory_types: tuple[str, ...] | None = None) -> list:
        from decima import retrieval

        qtok = retrieval.tokens(query)
        if not qtok:
            return []
        out = []
        for c in weave.of_type(DOCUMENT):
            if not c.content.get("recallable", True):
                continue
            if scope is not None and c.content.get("scope") != scope:
                continue
            hay = f"{c.content.get('title', '')} {c.content.get('body', '')}"
            if qtok & retrieval.tokens(hay):
                out.append(c)
        return out


_DEFAULT_RETRIEVER = DocRetriever()


def search_docs(k, query: str, *, scope: str | None = None,
                retriever: memory.Retriever | None = None) -> list:
    """Return document Cells matching `query` over title+body — as DATA.

    Recall never confers instruction authority: an untrusted doc comes back too
    (with `instruction_eligible=False`) for the caller to READ, never to obey."""
    engine = retriever or _DEFAULT_RETRIEVER
    return engine.search(k.weave(), query, scope, (DOCUMENT,))
