"""FEED1 — a news/RSS feed capability that COMPOSES the live intake loop.

A feed is a stream of items from the outside world — and the outside world is
UNTRUSTED. So FEED1 doesn't invent any new trust path: it captures a `feed_source`
Cell and then routes every polled item straight through the kernel's public
`ingest(source, text, trusted=False)` (INTAKE1 → DISP1). The disposition router
decides each item's fate, never the item:

  - deterministic noise / promo  → archive (nothing remembered);
  - a content/fact item          → remember as DATA (instruction_eligible=False);
  - an injection-laced item       → remember as flagged DATA — never invoke/policy.

Feed items are DATA, never instructions: the same recall-vs-instruct law memory,
the browser receipt and disposition all obey. An item can only be remembered as
DATA or archived — it can never elevate itself to a task/invoke/policy, because it
is fed with `trusted=False` and the kernel enforces that untrusted intake can't
elevate. No ambient authority: FEED1 grants nothing and proposes nothing.

Provenance lives on the Weft as EDGEs, not a side table: each item's intake Cell
is linked back to its `feed_source` with a `from_source` edge (folded onto both
endpoints), so `recent(source)` is a walk over the log, not a query against state
FEED1 keeps. Public model / kernel API only — no core edit.
"""
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

FEED_SOURCE = "feed_source"
FROM_SOURCE = "from_source"          # item.intake —from_source→ feed_source


def source_id(url: str) -> str:
    """Content-address a feed source by its URL (nfc) so re-adding the same feed
    is idempotent and a source keeps one identity across versions."""
    return content_id({"feed_source": nfc(url)})


def add_source(k, name: str, url: str) -> str:
    """Register a feed (RSS/news) as a `feed_source` Cell and return its id.

    The source is just metadata Decima holds — it carries NO trust: items polled
    from it are still ingested untrusted. Re-adding the same url re-versions the
    same Cell (content-addressed by url)."""
    sid = source_id(url)
    assert_content(k.weft, k.decima_agent_id, sid, FEED_SOURCE, {
        "name": nfc(name), "url": nfc(url), "origin": "feed",
    })
    return sid


def poll(k, source: str, items) -> list[dict]:
    """Poll a `feed_source`: route each item through the kernel's live intake loop.

    `items` is an iterable of feed items — each a dict with `title` and/or `body`
    (strings), or a bare string. Every item is UNTRUSTED data, so it goes through
    the PUBLIC `kernel.ingest(source=f"feed:{name}", text, trusted=False)`: the
    disposition router archives noise, remembers a fact as DATA, and keeps an
    injection as flagged DATA — the item never selects its own disposition.

    Each item's intake Cell is linked back to the source with a `from_source`
    edge. Returns the list of dispositions (one per item), in order."""
    src = k.weave().get(source)
    assert src is not None and src.type == FEED_SOURCE, f"not a feed_source: {source}"
    name = src.content["name"]
    feed_src = f"feed:{name}"

    out = []
    for item in items:
        text = _item_text(item)
        # UNTRUSTED: the kernel auto-disposes. trusted defaults to False; we are
        # explicit because that default IS the law here.
        disp = k.ingest(feed_src, text, trusted=False)
        # Provenance: link the captured intake back to its source on the Weft.
        assert_edge(k.weft, k.decima_agent_id, disp["intake"], FROM_SOURCE, source)
        out.append(disp)
    return out


def recent(k, source: str) -> list[dict]:
    """The dispositioned items polled from `source`, oldest-first.

    Walks the `from_source` edges into the source (provenance on the log), and for
    each intake returns its disposition: {intake, disposition, action, produced}."""
    w = k.weave()
    src = w.get(source)
    assert src is not None and src.type == FEED_SOURCE, f"not a feed_source: {source}"
    items = []
    for e in w.edges_to(source, FROM_SOURCE):
        intake = e["src"]
        disposed = w.edges_from(intake, "disposed_as")
        did = disposed[0]["dst"] if disposed else None
        dc = w.get(did).content if did else {}
        items.append({
            "intake": intake,
            "disposition": did,
            "action": dc.get("action"),
            "produced": dc.get("produced"),
        })
    return items


def _item_text(item) -> str:
    """Flatten a feed item (dict with title/body, or a bare string) to the text
    the disposition router classifies. Title and body are joined as DATA."""
    if isinstance(item, str):
        return nfc(item)
    title = (item.get("title") or "").strip()
    body = (item.get("body") or "").strip()
    text = (title + "\n" + body).strip() if title and body else (title or body)
    return nfc(text)
