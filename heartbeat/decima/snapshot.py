"""Snapshots — a verifiable cache of materialized state at a frontier.

Realizes the first increment of `specs/SNAPSHOTS.md`. A snapshot is **never truth**
(Law 5: the Weft is the only truth); it just lets a fold start from a checkpoint
instead of genesis. The whole discipline is that a snapshot can be *cheaply
distrusted* — recomputed and compared — so a forged or stale one is rejected, never
silently served.

What this module does, using ONLY the public Weave/Weft API (no core edits):

  - `snapshot(weft, upto_seq, store)` folds to a frontier and emits a
    `SnapshotManifest`: the `state_root` (Weave's own digest — the authoritative
    root), the folded `CellState` chunked into content-addressed blobs, plus a
    `reducer_set` (which code produced it) and an optional signature.
  - `restore(manifest, store)` fetches each chunk, **verifies its hash**, reassembles
    the CellState, and requires the **recomputed `state_root` == the manifest's** —
    rejecting on any mismatch (bad reducer set, bad signature, corrupted chunk,
    root mismatch). A snapshot is never partially trusted.
  - The contract (`SNAPSHOTS.md` §7, FOLD §11.1): fold-from-snapshot to a frontier
    == genesis fold to that frontier, byte-for-byte `state_root`. The check proves it.

Deferred to a core cycle (noted per the brief): **incremental fold-from-base** —
restoring the base state and applying only the delta above the frontier. That is
the actual performance win and needs a core change to fold onto a restored base;
here restore reconstructs the captured frontier state and we verify it equals the
genesis fold, which is the correctness half.
"""
import dataclasses
import json

from decima.weave import Weave, Cell
from decima.hashing import content_id

# The reducer identity (FOLD §9 / SNAPSHOTS §9): a snapshot is valid only for the
# reducer set that built it. Bump the version when the fold/leaf format changes —
# a mismatch must force a REBUILD, never a fold with mismatched reducers.
REDUCER_SET = [{"name": "heartbeat-weave", "version": 1}]
_PROTOCOL = 1
_REALM = "heartbeat"
_DEFAULT_CHUNK = 32   # CellState leaves per chunk (cell-id-range partitioning, §4)


class SnapshotError(Exception):
    """Any verification failure rejects the snapshot (SNAPSHOTS §6). Callers fall
    back to an older verified snapshot or to genesis — never partial trust."""


class BlobStore:
    """A content-addressed blob store (in-memory for the heartbeat; the seam where
    object storage / a CAS would slot in). `blob_id == hash(bytes)`, so addressing
    and integrity coincide and unchanged chunks dedup across snapshot generations."""

    def __init__(self):
        self.blobs: dict[str, str] = {}

    def put(self, data: dict) -> tuple[str, str]:
        raw = json.dumps(data, sort_keys=True)
        h = _hash_bytes(raw)
        self.blobs[h] = raw
        return h, h          # (blob_id, hash) — content-addressed: the id IS the hash

    def get(self, blob_id: str) -> str | None:
        return self.blobs.get(blob_id)


def _hash_bytes(raw: str) -> str:
    return content_id({"bytes": raw}, kind="snapshot")


def _leaf(cell: Cell) -> dict:
    """Canonical CellState leaf for a Cell (SNAPSHOTS §3). We capture EVERY Cell
    field generically (dataclasses.asdict), so the leaf — and thus restore — stays
    correct as the merge layer adds fields (e.g. M1's content_heads/in_conflict)
    without this module needing to know about them."""
    return dataclasses.asdict(cell)


def _manifest_id(manifest: dict) -> str:
    """Content id over the manifest-unsigned bytes (everything but the signature)."""
    unsigned = {k: v for k, v in manifest.items() if k != "signature"}
    return content_id(unsigned, kind="snapshot")


def _frontier(weft, upto_seq: int | None) -> tuple[list, int]:
    """The captured causal frontier (event ids) and the event count folded in.
    Linear profile: the frontier is the highest-seq event at/under `upto_seq`."""
    last_id, count = None, 0
    for ev in weft.events(upto_seq):
        last_id, count = ev.id, count + 1
    return ([last_id] if last_id else []), count


def snapshot(weft, upto_seq: int | None = None, store: BlobStore | None = None,
             *, created_by: str = "executor", keyring=None,
             chunk_size: int = _DEFAULT_CHUNK) -> tuple[dict, BlobStore]:
    """Fold to `upto_seq` and emit a verifiable SnapshotManifest + its blob store.

    The `state_root` recorded is the Weave's own digest — the authoritative root
    the §11 replay-determinism check already uses — so a restore that reassembles
    the same CellState recomputes the same root, and that root equals a genesis
    fold to this frontier."""
    store = store if store is not None else BlobStore()
    w = Weave.fold(weft, upto_seq)
    leaves = sorted((_leaf(c) for c in w.cells.values()), key=lambda d: d["id"])

    chunks = []
    for i in range(0, len(leaves), chunk_size):
        part = leaves[i:i + chunk_size]
        data = {"cell_range": [part[0]["id"], part[-1]["id"]], "leaves": part}
        blob_id, h = store.put(data)
        chunks.append({"cell_range": data["cell_range"], "blob_id": blob_id,
                       "hash": h, "count": len(part)})

    frontier, count = _frontier(weft, upto_seq)
    manifest = {
        "protocol": _PROTOCOL,
        "realm": _REALM,
        "frontier": frontier,
        "event_count": count,
        "state_root": w.state_root(),
        "reducer_set": REDUCER_SET,
        "schema_frontier": [],            # no schema/type-policy events captured yet
        "chunks": chunks,
        "created_by": created_by,
        "created_at": None,               # no clock in a pure fold (FOLD §2); informational anyway
        "signature": None,
    }
    if keyring is not None:
        manifest["signature"] = keyring.sign(created_by, _manifest_id(manifest))
    return manifest, store


def restore(manifest: dict, store: BlobStore, *, reducer_set=None, keyring=None) -> Weave:
    """Reassemble + VERIFY the captured CellState, returning it as a Weave (the base
    state ready for a delta fold — the delta itself is the deferred increment).

    Rejects on any failure (SNAPSHOTS §6): protocol/reducer mismatch, bad signature,
    a chunk whose bytes don't match its hash, or a recomputed root != the manifest's.
    """
    reducer_set = reducer_set if reducer_set is not None else REDUCER_SET
    if manifest.get("protocol") != _PROTOCOL:
        raise SnapshotError("protocol mismatch")
    # Reducer-version coupling (§9): a snapshot is valid ONLY for the reducer set
    # that built it. Never fold with mismatched reducers — rebuild instead.
    if manifest.get("reducer_set") != reducer_set:
        raise SnapshotError("reducer-set mismatch — rebuild, do not fold")
    if keyring is not None:
        if not keyring.verify(manifest.get("created_by", ""), _manifest_id(manifest),
                              manifest.get("signature") or ""):
            raise SnapshotError("manifest signature invalid")

    leaves = []
    for ch in manifest["chunks"]:
        raw = store.get(ch["blob_id"])
        if raw is None:
            raise SnapshotError(f"missing chunk blob {ch['blob_id'][:8]}")
        if _hash_bytes(raw) != ch["hash"]:
            raise SnapshotError(f"chunk hash mismatch — corrupted blob {ch['blob_id'][:8]}")
        leaves.extend(json.loads(raw)["leaves"])

    base = Weave()
    for leaf in leaves:
        base.cells[leaf["id"]] = Cell(**leaf)
    if base.state_root() != manifest["state_root"]:
        raise SnapshotError("state_root mismatch — restored leaves do not match the manifest")
    return base
