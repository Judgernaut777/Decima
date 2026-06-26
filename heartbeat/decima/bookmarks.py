"""BOOKMARKS1 — links + tags + archive as Cells (a bookmarks / read-later / archive
capability that COMPOSES the gated egress, the disposition router, and the model).

A bookmark is just a saved pointer to the outside world, and the outside world is
UNTRUSTED. So BOOKMARKS1 invents no new trust path and no ambient authority:

  - add() lands a `bookmark` Cell whose title/tags/url are IMPORTED METADATA = DATA
    (`instruction_eligible=False`). A title scraped from a page or supplied by an
    importer is a label Decima holds, never an order it obeys;
  - archive() FETCHES the page through the EGRESS1 gated capability — the rule of
    egress holds: a non-allowlisted host FAILS CLOSED (refused, recorded, the request
    never leaves the box). Only an allowlisted fetch runs (sandboxed + audited), and
    its response body — the ARCHIVE SNAPSHOT — is stored as UNTRUSTED DATA via the
    PARSE1 firewall / DISP1 disposition: remembered, never obeyed. The snapshot Cell
    is linked to its bookmark with an `archived_from` edge (provenance on the Weft);
  - by_tag() / read_later() / search() are walks over the log, not a side table.

The same recall-vs-instruct law memory, the browser receipt, the feed and disposition
all obey: imported bookmark metadata is DATA, and an archived page snapshot is DATA —
neither can elevate itself to a task, an invoke, or a policy. Public egress /
disposition / model / kernel API only — no core edit, no real network.
"""
from __future__ import annotations

from decima import egress, disposition
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

BOOKMARK = "bookmark"
SNAPSHOT = "bookmark_snapshot"      # an archived page snapshot — UNTRUSTED DATA
ARCHIVED_FROM = "archived_from"     # snapshot —archived_from→ bookmark
TAGGED = "tagged"                   # bookmark —tagged→ tag entity
TAG = "tag"


def bookmark_id(url: str) -> str:
    """Content-address a bookmark by its URL (nfc) so re-adding the same link
    re-versions one Cell and a bookmark keeps a single identity."""
    return content_id({"bookmark": nfc(url)})


def tag_id(tag: str) -> str:
    return content_id({"tag": nfc(tag).lower()})


def _norm_tags(tags) -> list[str]:
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [tags]
    seen, out = set(), []
    for t in tags:
        t = nfc(str(t)).strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def add(k, url, *, title=None, tags=None, read_later=False) -> str:
    """Save a `bookmark` Cell and return its id.

    `url`/`title`/`tags` are IMPORTED METADATA — stored as DATA
    (`instruction_eligible=False`): a scraped or imported title is a label, never an
    instruction. `read_later` marks the read-later queue. Each tag also lands a `tag`
    entity Cell with a `tagged` edge so `by_tag` is a walk over the log. Re-adding the
    same url re-versions the same Cell (content-addressed by url)."""
    url = nfc(str(url))
    bid = bookmark_id(url)
    norm = _norm_tags(tags)
    assert_content(k.weft, k.decima_agent_id, bid, BOOKMARK, {
        "url": url,
        "title": nfc(str(title)) if title else url,
        "tags": norm,
        "read_later": bool(read_later),
        "origin": "bookmark",
        "instruction_eligible": False,   # imported bookmark metadata is DATA
    })
    for t in norm:
        tid = tag_id(t)
        assert_content(k.weft, k.decima_agent_id, tid, TAG, {"name": t})
        assert_edge(k.weft, k.decima_agent_id, bid, TAGGED, tid)
    return bid


def archive(k, agent, bookmark, *, egress_cap) -> dict:
    """Archive a bookmark: fetch its page via the EGRESS1 gated capability and store
    the page SNAPSHOT as UNTRUSTED DATA linked to the bookmark.

    The rule of egress holds end-to-end: `egress.fetch` REFUSES a non-allowlisted host
    (fail closed — no effect runs, the request never leaves, a refusal is recorded),
    and an allowlisted fetch runs sandboxed + audited. The response body is the
    snapshot — routed through PARSE1 / DISP1 as untrusted intake and stored
    `instruction_eligible=False`, never obeyed. On success a `bookmark_snapshot` Cell
    is asserted and linked to the bookmark with an `archived_from` edge.

    Returns the egress result dict, augmented on success with `bookmark`, `snapshot`
    (the snapshot Cell id) and `archived` (True); on a refusal it carries the egress
    `refused`/`reason`/`refusal` fields unchanged (and `archived` False)."""
    bm = k.weave().get(bookmark)
    assert bm is not None and bm.type == BOOKMARK, f"not a bookmark: {bookmark}"
    url = bm.content["url"]

    # ── fetch through the gated egress: non-allowlisted host fails closed ──────
    r = egress.fetch(k, agent, egress_cap, url)
    if not r.get("ok"):
        # refused (or sandbox/exec denial) — nothing archived, the request never left.
        return {**r, "bookmark": bookmark, "archived": False}

    # ── allowlisted: store the response body as an UNTRUSTED snapshot (DATA) ───
    # egress already routed the body through PARSE1/DISP1 (instruction_eligible=False);
    # we mint a `bookmark_snapshot` Cell over that provenance and tie it to the bookmark.
    sid = content_id({"bookmark_snapshot": bookmark, "of": r["receipt"]})
    assert_content(k.weft, k.decima_agent_id, sid, SNAPSHOT, {
        "bookmark": bookmark,
        "url": url,
        "host": r["host"],
        "receipt": r["receipt"],            # the audited EffectReceipt (egress fetch)
        "fetch_cell": r.get("fetch_cell"),
        "disposition": r.get("disposition"),
        "action": r.get("action"),          # remember (DATA) — never invoke/task/policy
        "body": r["body"],
        "instruction_eligible": False,      # an archive snapshot is DATA, full stop
    })
    assert_edge(k.weft, k.decima_agent_id, sid, ARCHIVED_FROM, bookmark)
    assert_edge(k.weft, k.decima_agent_id, sid, "fetched_via", r["receipt"])
    return {**r, "bookmark": bookmark, "snapshot": sid, "archived": True}


def get(k, bookmark) -> dict | None:
    bm = k.weave().get(bookmark)
    return bm.content if (bm is not None and bm.type == BOOKMARK) else None


def all_bookmarks(k) -> list:
    """Every `bookmark` Cell (as DATA cells), most-recently-added not guaranteed."""
    return [c for c in k.weave().of_type(BOOKMARK)]


def by_tag(k, tag) -> list[str]:
    """The bookmark ids tagged `tag` — a walk over the `tagged` edges into the tag
    entity (provenance on the log, not a side index)."""
    w = k.weave()
    tid = tag_id(tag)
    if w.get(tid) is None:
        return []
    return sorted({e["src"] for e in w.edges_to(tid, TAGGED)})


def read_later(k) -> list[str]:
    """The bookmark ids flagged read-later (the read-later queue)."""
    return sorted(c.id for c in all_bookmarks(k)
                  if c.content.get("read_later"))


def search(k, query: str) -> list[str]:
    """Substring search over bookmark title/url/tags (DATA, case-insensitive).
    A seam: a real semantic index wraps in behind the same signature later."""
    q = nfc(str(query)).lower()
    out = []
    for c in all_bookmarks(k):
        hay = " ".join([
            c.content.get("title", ""),
            c.content.get("url", ""),
            " ".join(c.content.get("tags", [])),
        ]).lower()
        if q in hay:
            out.append(c.id)
    return sorted(out)


def snapshots(k, bookmark) -> list[str]:
    """The archive snapshot Cell ids for a bookmark — a walk over the
    `archived_from` edges into the bookmark (provenance on the Weft)."""
    w = k.weave()
    return sorted({e["src"] for e in w.edges_to(bookmark, ARCHIVED_FROM)})
