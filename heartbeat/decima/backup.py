"""BACKUP / RESTORE — a portable, verifiable, tamper-evident backup of the whole Weft.

Law 1 (everything on the Log) says the entire truth of a Weft is its ordered set of
signed events — not a folded cache. So a REAL backup is not a snapshot of state
(`snapshot.py` caches a *fold*, and is explicitly "never truth"); it is the causal
event log itself, portably serialized, with an integrity root over it so tampering
is detectable BEFORE a single byte is trusted, and a RESTORE that replays every event
back through `Weft.ingest` — the WEFT §2 acceptance gate (id recompute, signature
verify, parents-present, honest lamport) — so a restored world is never a *softer*
world than the one that produced the backup. A corrupted or forged backup is
REJECTED, never silently accepted into a fresh database (fail closed, Law 5).

  - `backup(k)`        → a portable manifest: every signed event in causal (seq)
                          order, serialized in the exact wire-row shape `sync.py`
                          already uses `(id, payload_text, author, sig)`, plus a
                          deterministic hash-chain `root` over the ordered ids.
  - `verify(blob)`      → PURE (no kernel, no keyring, no db): recomputes every
                          event's content id from its own bytes and recomputes the
                          root, and fails closed on the first mismatch. This is the
                          "cheap distrust" half — a caller can reject a bad backup
                          before ever opening a database.
  - `restore(blob, db_path, keyring=...)` → replays every row through a FRESH
                          `Weft.ingest`, in the manifest's own causal order, so each
                          event re-earns its place the same way a sync peer would:
                          integrity, authenticity, causal completeness, honest
                          lamport. Any row that fails ingest RAISES — restore never
                          fabricates a partial, silently-accepted world.

`root` is a sequential hash CHAIN (not a set digest), so it is sensitive to the
exact ORDER of events too — a backup with the same events replayed out of order (or
with one dropped/duplicated) recomputes a different root than the original.
"""
import json

from decima.hashing import content_id
from decima.weft import Weft


class BackupError(Exception):
    """A backup failed verification, or a restore was refused mid-replay
    (fail closed — never a partial or tampered world)."""


def _weft_of(k):
    """Accept either a `Kernel` (via its `.weft`) or a bare `Weft`."""
    return k.weft if hasattr(k, "weft") else k


def _rows(weft) -> list:
    """Raw wire records `(id, payload_text, author, sig)` in causal (seq) order —
    the exact shape `sync.py`'s `_rows`/`feed` already put on the wire."""
    return weft.db.execute(
        "SELECT id, payload, author, sig FROM events ORDER BY seq").fetchall()


def _chain_root(ids: list) -> str:
    """A deterministic hash CHAIN over the ordered event ids: each link folds in the
    previous accumulator, so the root is sensitive to membership, content (via the
    id), AND order — reordering, dropping, duplicating, or substituting any single
    event changes the final root. Pure content-addressing (`hashing.content_id`),
    no new crypto."""
    acc = ""
    for eid in ids:
        acc = content_id({"acc": acc, "id": eid}, kind="backup-link")
    return content_id({"final": acc, "count": len(ids)}, kind="backup-root")


def backup(k) -> dict:
    """A portable manifest of the ENTIRE event log: `{events, root, count}`.
    `events` is every row this Weft holds, in causal order, in the wire-row shape
    `sync.py` uses `(id, payload_text, author, sig)`. Reading through `Weft.events()`
    first (not just a raw SELECT) means `backup()` itself refuses to certify a log
    that does not already pass read-time verification (id recompute + signature) —
    defense in depth before a single row is exported. All ints stay ints: the
    payload text is the exact canonical JSON `Weft.append`/`ingest` already wrote,
    never re-encoded here."""
    weft = _weft_of(k)
    verified_ids = [ev.id for ev in weft.events()]     # raises WeftError if the
                                                        # SOURCE log itself is unsound
    rows = _rows(weft)
    if [r[0] for r in rows] != verified_ids:
        raise BackupError("event log changed under backup — refusing an inconsistent snapshot")
    events = [list(r) for r in rows]
    ids = [r[0] for r in rows]
    return {"events": events, "root": _chain_root(ids), "count": len(events)}


def verify(blob) -> tuple:
    """PURE, fail-closed verification: no kernel, no keyring, no signatures — just
    "do these bytes still say what they claim to say". Recomputes each event's
    content id from its own payload bytes (WEFT §2 integrity check) and recomputes
    the hash-chain root over the ids IN THE ORDER THEY APPEAR; any mismatch —
    malformed row, a tampered payload whose id no longer matches, a wrong count, or
    a root that does not recompute — returns `(False, reason)`. Never raises on bad
    input; a malformed blob is just another kind of failure to report."""
    if not isinstance(blob, dict):
        return False, "blob is not a mapping"
    events = blob.get("events")
    if not isinstance(events, list):
        return False, "missing/malformed events list"
    ids = []
    for i, row in enumerate(events):
        if not (isinstance(row, (list, tuple)) and len(row) == 4):
            return False, f"malformed row at index {i}"
        eid, payload_text, author, sig = row
        if not all(isinstance(x, str) for x in (eid, payload_text, author, sig)):
            return False, f"non-string field in row at index {i}"
        try:
            payload = json.loads(payload_text)
        except (ValueError, TypeError):
            return False, f"unparseable payload at index {i} (id {eid[:8]})"
        if not isinstance(payload, dict):
            return False, f"payload is not an object at index {i} (id {eid[:8]})"
        if content_id(payload, kind="event") != eid:
            return False, f"content id mismatch at index {i} — tampered payload (claimed {eid[:8]})"
        ids.append(eid)
    if blob.get("count") != len(events):
        return False, f"count mismatch: manifest says {blob.get('count')}, has {len(events)}"
    root = _chain_root(ids)
    if root != blob.get("root"):
        return False, "root mismatch — event set/order does not match the manifest"
    return True, "ok"


def restore(blob, db_path: str, *, keyring=None) -> Weft:
    """Replay a backup manifest into a FRESH database through `Weft.ingest` — the
    WEFT §2 acceptance gate — so every event re-earns its place exactly as a sync
    peer's foreign event would: id recompute, signature verify under `keyring`,
    every parent already present, an honest lamport. `keyring` MUST be one that
    holds (or trusts) the public keys of every event's author — normally the same
    keyring that authored the original log, since restore mints no new identity and
    confers no new authority; it only re-admits events that already proved
    themselves once.

    FAILS CLOSED at every step:
      - a `blob` that does not `verify()` is refused before touching the database;
      - any row `Weft.ingest` does not accept ("orphan" or "rejected:*") raises
        immediately — restore never continues past a row it cannot admit, so a
        tampered event is never partially or silently woven into the restored log;
      - a restored count that disagrees with the manifest raises too (belt + brace:
        the replay must have admitted exactly what was promised, no more, no less).

    Returns the fresh `Weft` over `db_path` — the caller folds it (`Weave.fold`) to
    get materialized state, exactly like any other Weft."""
    if keyring is None:
        raise BackupError("restore requires a keyring that can verify the backed-up "
                          "events' authors (normally the original log's own keyring)")
    ok, reason = verify(blob)
    if not ok:
        raise BackupError(f"refusing to restore an unverifiable backup: {reason}")
    weft = Weft(db_path, keyring)
    for row in blob["events"]:
        status = weft.ingest(tuple(row))
        if status not in ("ingested", "duplicate"):
            raise BackupError(f"restore refused row {row[0][:8]}: {status} — "
                              "fail closed, no partial world")
    if weft.count() != blob["count"]:
        raise BackupError(
            f"restored {weft.count()} events, manifest promised {blob['count']}")
    return weft
