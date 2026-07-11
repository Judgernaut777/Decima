"""A portable, verifiable, tamper-evident backup of a local Decima install.

Law 1 (nothing off the Log) says the whole truth of a Weft is its ordered set of
signed events — not a folded cache. So a REAL backup is not a snapshot of state
(that is a disposable projection); it is the causal event log itself, serialized in
the exact wire-row shape the sync path uses `(id, payload, author, sig)`, wrapped
with an integrity root so tampering is detectable BEFORE a single byte is trusted,
plus the durable byte-artifacts attached to the log (artifacts / checkpoints / public
config). Projections are excluded (rebuildable from the fold), and keys are excluded
(a plaintext secret in a backup is a second place authority could leak from).

Three operations, each fail-closed:

  - ``backup_create(base, dest, *, keyring)`` — reads the canonical Weft, VERIFIES it
    by reading through ``Weft.events`` (id recompute + signature), records its authoritative
    fold ``state_root``, and copies the durable artifacts with per-file digests. The manifest
    carries a hash-chain ``root`` over the ordered event ids and a ``backup_root`` binding the
    log root, the fold root, and every file digest.
  - ``backup_verify(path)`` — PURE (no kernel fold, no keyring, no db): recomputes every
    event's content id from its own bytes, recomputes the hash-chain + file digests, and
    fails closed on the first mismatch. This is the "cheap distrust" half: a bad backup is
    rejected offline, before it is ever restored.
  - ``restore_apply(dest, base, *, keyring)`` — verifies, preserves a rollback copy of any
    existing base, replays every event through a FRESH ``Weft.ingest`` (the WEFT §2 acceptance
    gate: id recompute, signature verify, parents-present, honest lamport), restores the
    artifacts (re-checking each digest), rebuilds NOTHING canonical, and confirms the folded
    ``state_root`` equals the one the backup certified. A corrupted backup is REJECTED.
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Any

from decima.kernel.hashing import blob_id, content_id
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.services.data_layout import (
    BACKUP_DIRS,
    WEFT,
    WEFT_DB,
    DataDir,
)

_SCHEMA = 1
_MANIFEST = "MANIFEST.json"


class BackupError(Exception):
    """A backup failed verification, or a restore was refused (fail closed — never a
    partial, softened, or tampered world)."""


# ── integrity roots ──────────────────────────────────────────────
def _chain_root(ids: list[str]) -> str:
    """A hash CHAIN over the ordered event ids: each link folds in the previous
    accumulator, so the root is sensitive to membership, content (via each id), AND
    order — reordering, dropping, duplicating, or substituting any event changes it."""
    acc = ""
    for eid in ids:
        acc = content_id({"acc": acc, "id": eid}, kind="backup-link")
    return content_id({"final": acc, "count": len(ids)}, kind="backup-root")


def _backup_root(weft_root: str, state_root: str, files: dict[str, list[dict]]) -> str:
    """Bind the log root, the certified fold root, and every file digest into one root,
    so tampering with ANY captured byte (log, artifact, checkpoint, or config) is
    detectable from the manifest alone."""
    file_index = {cat: sorted((f["name"], f["digest"]) for f in entries)
                  for cat, entries in files.items()}
    return content_id({"weft_root": weft_root, "state_root": state_root,
                       "files": file_index}, kind="backup-manifest")


def _raw_rows(db_path: str) -> list[list[str]]:
    """Wire rows `(id, payload, author, sig)` in causal (seq) order, read straight from
    the Weft's SQLite store. Integrity is re-established from the bytes themselves (the
    id recomputes from the payload), so this read needs no keyring."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, payload, author, sig FROM events ORDER BY seq").fetchall()
    finally:
        conn.close()
    return [list(r) for r in rows]


# ── create ────────────────────────────────────────────────────
def backup_create(base: str, dest: str, *, keyring: Any) -> dict:
    """Back up the canonical Weft + artifacts + checkpoints + public config from
    `base` into a fresh `dest` directory, returning the manifest.

    NOT captured: projections (rebuildable from the fold) and keys (secret). The
    `keyring` is used to VERIFY the source log on the way out (defense in depth) and to
    fold its authoritative `state_root`, which restore later re-confirms."""
    src = DataDir(base)
    if not os.path.exists(src.weft_db):
        raise BackupError(f"no canonical Weft at {src.weft_db} — nothing to back up")

    # Verified read (raises on a tampered source) + the authoritative fold root.
    weft = Weft(src.weft_db, keyring)
    verified_ids = [ev.id for ev in weft.events()]
    state_root = Weave.fold(weft).state_root()

    rows = _raw_rows(src.weft_db)
    if [r[0] for r in rows] != verified_ids:
        raise BackupError("event log changed under backup — refusing an inconsistent capture")

    os.makedirs(dest, exist_ok=True)
    dst = DataDir(dest)

    # Copy the durable byte-artifacts with per-file digests.
    files: dict[str, list[dict]] = {}
    for category in BACKUP_DIRS:
        os.makedirs(dst.path(category), exist_ok=True)
        entries: list[dict] = []
        for name in src.list_files(category):
            with open(src.path(category, name), "rb") as fh:
                data = fh.read()
            with open(dst.path(category, name), "wb") as out:
                out.write(data)
            entries.append({"name": name, "digest": blob_id(data)})
        files[category] = sorted(entries, key=lambda e: e["name"])

    ids = [r[0] for r in rows]
    weft_root = _chain_root(ids)
    manifest = {
        "schema": _SCHEMA,
        "realm": "decima",
        "weft": {"events": rows, "count": len(rows), "root": weft_root},
        "state_root": state_root,
        "files": files,
        "backup_root": _backup_root(weft_root, state_root, files),
    }
    with open(dst.path(_MANIFEST), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, sort_keys=True)
    return manifest


# ── verify (pure) ─────────────────────────────────────────────
def _load_manifest(path: str) -> tuple[dict | None, str]:
    mpath = os.path.join(path, _MANIFEST)
    if not os.path.isfile(mpath):
        return None, f"no {_MANIFEST} at {path}"
    try:
        with open(mpath, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, f"unreadable manifest: {exc}"
    if not isinstance(manifest, dict):
        return None, "manifest is not a mapping"
    if manifest.get("schema") != _SCHEMA:
        return None, "manifest schema mismatch"
    return manifest, "ok"


def backup_verify(path: str) -> tuple[bool, str]:
    """PURE, fail-closed integrity check of a backup directory — no kernel fold, no
    keyring, no signatures: "do these bytes still say what they claim to say".

    Recomputes each event's content id from its own payload bytes (WEFT §2 integrity),
    recomputes the hash-chain root, recomputes every captured file's digest, and
    recomputes `backup_root`. Any mismatch returns `(False, reason)`. Never raises on a
    malformed backup — that is just another failure to report."""
    manifest, reason = _load_manifest(path)
    if manifest is None:
        return False, reason

    weft = manifest.get("weft")
    if not isinstance(weft, dict) or not isinstance(weft.get("events"), list):
        return False, "missing/malformed weft events"
    events = weft["events"]
    ids: list[str] = []
    for i, row in enumerate(events):
        if not (isinstance(row, (list, tuple)) and len(row) == 4):
            return False, f"malformed row at index {i}"
        eid, payload_text, author, sig = row
        if not all(isinstance(x, str) for x in (eid, payload_text, author, sig)):
            return False, f"non-string field in row at index {i}"
        try:
            payload = json.loads(payload_text)
        except (ValueError, TypeError):
            return False, f"unparseable payload at index {i}"
        if not isinstance(payload, dict) or content_id(payload, kind="event") != eid:
            return False, f"content id mismatch at index {i} — tampered payload"
        ids.append(eid)
    if weft.get("count") != len(events):
        return False, "weft count mismatch"
    weft_root = _chain_root(ids)
    if weft_root != weft.get("root"):
        return False, "weft root mismatch — event set/order does not match the manifest"

    files = manifest.get("files")
    if not isinstance(files, dict):
        return False, "missing/malformed files index"
    for category, entries in files.items():
        if not isinstance(entries, list):
            return False, f"malformed file entries for {category}"
        for entry in entries:
            fpath = os.path.join(path, category, entry["name"])
            if not os.path.isfile(fpath):
                return False, f"missing captured file {category}/{entry['name']}"
            with open(fpath, "rb") as fh:
                if blob_id(fh.read()) != entry["digest"]:
                    return False, f"digest mismatch — corrupted {category}/{entry['name']}"

    recomputed = _backup_root(weft_root, manifest.get("state_root", ""), files)
    if recomputed != manifest.get("backup_root"):
        return False, "backup_root mismatch — manifest binding does not recompute"
    return True, "ok"


# ── restore ──────────────────────────────────────────────────
def _rollback_path(base: str) -> str:
    """A non-clobbering sibling path for the rollback copy. Deterministic (no
    wall-clock): `<base>.rollback`, then `.rollback.1`, `.2`, … if taken."""
    candidate = base.rstrip("/") + ".rollback"
    n = 0
    while os.path.exists(candidate):
        n += 1
        candidate = base.rstrip("/") + f".rollback.{n}"
    return candidate


def restore_apply(dest: str, base: str, *, keyring: Any) -> dict:
    """Restore a verified backup at `dest` INTO `base`, fail-closed at every step.

    1. VERIFY the backup (pure); a corrupted backup is rejected before touching `base`.
    2. Preserve a ROLLBACK copy of any existing non-empty `base` (moved aside, never
       deleted) so a botched restore is recoverable.
    3. Replay every event through a FRESH `Weft.ingest` — the WEFT §2 acceptance gate —
       so each event re-earns its place (id, signature, parents-present, honest lamport).
       Any row not admitted RAISES (no partial world).
    4. Restore the artifacts / checkpoints / config, re-checking each digest.
    5. Rebuild NOTHING canonical (projections are rebuilt on demand from the fold).
    6. Confirm the folded `state_root` equals the one the backup certified — else RAISE.
    """
    ok, reason = backup_verify(dest)
    if not ok:
        raise BackupError(f"refusing to restore an unverifiable backup: {reason}")
    manifest, _ = _load_manifest(dest)
    assert manifest is not None  # backup_verify already accepted it

    rollback = None
    if os.path.isdir(base) and os.listdir(base):
        rollback = _rollback_path(base)
        shutil.move(base, rollback)

    dst = DataDir(base).ensure()

    # 3. Replay the canonical log through the acceptance gate.
    if os.path.exists(dst.weft_db):
        os.remove(dst.weft_db)
    weft = Weft(dst.weft_db, keyring)
    for row in manifest["weft"]["events"]:
        status = weft.ingest(tuple(row))
        if status not in ("ingested", "duplicate"):
            raise BackupError(f"restore refused event {row[0][:8]}: {status} — no partial world")
    if weft.count() != manifest["weft"]["count"]:
        raise BackupError(
            f"restored {weft.count()} events, manifest promised {manifest['weft']['count']}")

    # 4. Restore artifacts / checkpoints / config, re-checking each digest.
    for category, entries in manifest["files"].items():
        os.makedirs(dst.path(category), exist_ok=True)
        for entry in entries:
            with open(os.path.join(dest, category, entry["name"]), "rb") as fh:
                data = fh.read()
            if blob_id(data) != entry["digest"]:
                raise BackupError(f"restore refused {category}/{entry['name']}: digest mismatch")
            with open(dst.path(category, entry["name"]), "wb") as out:
                out.write(data)

    # 6. Confirm the fold matches what the backup certified (event integrity by folding).
    restored_root = Weave.fold(weft).state_root()
    if restored_root != manifest["state_root"]:
        raise BackupError(
            "restored state_root does not match the backup — event integrity check failed")

    return {
        "base": base,
        "events": weft.count(),
        "state_root": restored_root,
        "rollback": rollback,
    }


# A convenience name so callers can locate the canonical DB after a restore.
def weft_db_path(base: str) -> str:
    return os.path.join(base, WEFT, WEFT_DB)
