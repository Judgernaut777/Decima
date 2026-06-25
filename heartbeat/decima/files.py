"""Files / storage — content-addressed blobs as first-class Cells.

A *file* is a Cell (Law 3): a `path`, its `content`, the content's hash, and the
trust of its source. This is the storage sibling of `doc.py` — but where a doc is
a piece of *knowledge* (title+body, searchable, citable), a file is an arbitrary
blob addressed by a *path* (a stored object: an attachment, an asset, a config,
a scraped page). The same trust law every receipt obeys binds here too: content
from an UNTRUSTED source is DATA — written `instruction_eligible=False`, never an
instruction, no matter what the caller asks. A trusted file may be made
instruction-eligible explicitly; by default it is not (DATA-by-default).

Identity & history (LWW, the Weave default for an untagged type):
  - A file Cell is content-addressed by PATH, so it keeps ONE identity across
    writes. `put` to an existing path asserts a NEW CONTENT version of that same
    cell id. On the linear log LWW means the latest version is what `get`
    materializes (`cell.version` counts the writes), while EVERY prior version
    stays on the Weft as its own ASSERT event — `versions()` reconstructs them by
    folding the log at each seq (Law 5: state is a fold).

Content addressing (Law 4: identity is content + cause):
  - Each version records a `content_hash` = the blob-id of its bytes. Identical
    content yields the SAME hash everywhere, forever (dedup/provenance fall out of
    this). A `put` whose bytes match the current head is therefore detectable as a
    no-change rewrite — the hash is the version's content-address on the Weft.

No core file is touched — this composes the public model/weave/hashing API.
"""
from __future__ import annotations

from decima.model import assert_content
from decima.hashing import content_id, blob_id, nfc
from decima.weave import Weave

FILE = "file"


def file_id(path: str) -> str:
    """Content-address a file by its path, so writes to the same path land on one
    cell id (stable identity; LWW versions accrete on it)."""
    return content_id({"file": nfc(path)})


def _content_hash(content) -> str:
    """The content-address of a file's bytes (Law 4). `content` may be bytes or a
    str; a str is encoded UTF-8 so identical text yields one hash."""
    data = content if isinstance(content, (bytes, bytearray)) else str(content).encode("utf-8")
    return blob_id(bytes(data))


def put(k, path: str, content, *, trusted: bool = True,
        author: str | None = None, source: str | None = None,
        instruction_eligible: bool | None = None) -> str:
    """Write a `file` Cell at `path` and return its cell id (stable across writes).

    The first write materializes version 1; a later `put` to the same path asserts
    a NEW CONTENT version of the SAME cell id (LWW) — the prior version is NOT
    overwritten on the Log, it remains its own ASSERT event (see `versions`). Each
    version records `content_hash` (the blob-id of its bytes): identical content
    yields the same hash, so an update with new bytes changes the hash.

    `trusted` records whether the SOURCE is trusted. An untrusted-sourced file is
    stored as DATA: its content is never instruction-eligible (the recall-vs-
    instruct law), regardless of what a caller passes. A trusted file may be made
    instruction-eligible explicitly; by default it is not (a file is a blob to
    read, not an order to obey)."""
    author = author or k.decima_agent_id
    path = nfc(path)
    # store text NFC-normalized so identical human text content-addresses to one hash
    stored = nfc(content) if isinstance(content, str) else content
    chash = _content_hash(stored)
    # Untrusted source ⇒ DATA, full stop. Trusted ⇒ honor caller (default False).
    if not trusted:
        eligible = False
    else:
        eligible = bool(instruction_eligible) if instruction_eligible is not None else False
    body = {
        "path": path,
        "content": stored,
        "content_hash": chash,
        "trusted": bool(trusted),
        "source": nfc(source) if source is not None else None,
        "instruction_eligible": bool(eligible),
    }
    assert_content(k.weft, author, file_id(path), FILE, body)
    return file_id(path)


def get(k, path: str):
    """The latest version of the file at `path` as a Cell (LWW head), or None."""
    cell = k.weave().get(file_id(path))
    if cell is None or cell.type != FILE or cell.retracted:
        return None
    return cell


def versions(k, path: str) -> list:
    """Reconstruct every version of the file at `path` from the Log (oldest →
    newest). Each prior version is recovered by folding the Weft up to the seq of
    the ASSERT event that wrote it — Law 5: state is a fold, so history is just
    folding at earlier points. Returns {seq, version, content, content_hash}."""
    cid = file_id(path)
    out = []
    for ev in k.weft.events():
        b = ev.body or {}
        if b.get("cell") == cid and b.get("kind") == "CONTENT":
            cell = Weave.fold(k.weft, upto_seq=ev.seq).get(cid)
            if cell is not None:
                out.append({"seq": ev.seq, "version": cell.version,
                            "content": cell.content.get("content"),
                            "content_hash": cell.content.get("content_hash")})
    return out


def list(k, prefix: str = "") -> list:
    """File Cells whose path starts with `prefix` (latest version of each), sorted
    by path. An empty prefix lists every live file."""
    prefix = nfc(prefix)
    out = [c for c in k.weave().of_type(FILE)
           if c.content.get("path", "").startswith(prefix)]
    return sorted(out, key=lambda c: c.content.get("path", ""))
