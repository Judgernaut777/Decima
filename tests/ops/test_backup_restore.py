"""Backup / restore round-trip + corruption rejection (handoff §12).

The load-bearing property: a restore that replays the backed-up event log through the
kernel's own acceptance gate folds to the SAME `state_root` as the original — and a
backup whose bytes were tampered is REJECTED before it can produce a world at all.
"""

from __future__ import annotations

import json
import os

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.services.backup import BackupError, backup_create, backup_verify, restore_apply
from decima.services.data_layout import (
    ARTIFACTS,
    CONFIG,
    KEYS,
    PROJECTIONS,
    DataDir,
)

_SEED = bytes(range(32))


def _build_base(base: str) -> tuple[DataDir, Keyring, str]:
    """A small, real install: a plan DAG on the Weft, a content-addressed artifact, a
    public config file, plus a disposable projection + a secret key (which a backup must
    NOT capture)."""
    from decima.kernel.hashing import blob_id

    dd = DataDir(base).ensure()
    kr = Keyring(seed=_SEED)
    author = kr.mint("decima", "root").id
    weft = Weft(dd.weft_db, kr)
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    a = cells.create_step(weft, author, plan_id=plan, description="A")
    cells.create_step(weft, author, plan_id=plan, description="B", dependency_ids=[a])

    data = b"the durable artifact bytes"
    with open(dd.path(ARTIFACTS, blob_id(data)), "wb") as fh:
        fh.write(data)
    with open(dd.path(CONFIG, "budgets.json"), "w", encoding="utf-8") as fh:
        json.dump({"token_budget": 1000}, fh)
    # Disposable + secret — neither may be backed up.
    with open(dd.path(PROJECTIONS, "cache.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    with open(dd.path(KEYS, "master.seed"), "wb") as fh:
        fh.write(_SEED)
    return dd, kr, author


def test_backup_restore_round_trip_preserves_state_root(tmp_path):
    base = str(tmp_path / "install")
    dd, kr, _author = _build_base(base)
    original_root = Weave.fold(Weft(dd.weft_db, kr)).state_root()

    dest = str(tmp_path / "backup")
    manifest = backup_create(base, dest, keyring=kr)
    assert manifest["state_root"] == original_root

    ok, reason = backup_verify(dest)
    assert ok, reason

    # Restore into a FRESH base with a fresh (seed-equal) keyring — proving restore
    # needs only the original seed, not the live in-memory keyring.
    restored_base = str(tmp_path / "restored")
    result = restore_apply(dest, restored_base, keyring=Keyring(seed=_SEED))

    assert result["state_root"] == original_root
    refold = Weave.fold(Weft(DataDir(restored_base).weft_db, Keyring(seed=_SEED))).state_root()
    assert refold == original_root, "folded state_root after restore must equal the original"


def test_backup_excludes_projections_and_keys(tmp_path):
    base = str(tmp_path / "install")
    _build_base(base)
    dest = str(tmp_path / "backup")
    backup_create(base, dest, keyring=Keyring(seed=_SEED))
    # Rebuildable + secret directories never enter the backup.
    assert not os.path.isdir(os.path.join(dest, PROJECTIONS)) or not os.listdir(
        os.path.join(dest, PROJECTIONS)
    )
    assert not os.path.isdir(os.path.join(dest, KEYS))


def test_corrupted_event_payload_is_rejected(tmp_path):
    base = str(tmp_path / "install")
    _build_base(base)
    dest = str(tmp_path / "backup")
    backup_create(base, dest, keyring=Keyring(seed=_SEED))

    manifest_path = os.path.join(dest, "MANIFEST.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    # Flip one byte inside a signed event payload — the recomputed content id no longer
    # matches, so verification must fail closed.
    payload = manifest["weft"]["events"][0][1]
    manifest["weft"]["events"][0][1] = payload.replace("ship", "shıp", 1)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)

    ok, reason = backup_verify(dest)
    assert not ok
    assert "content id" in reason or "root" in reason

    with pytest.raises(BackupError):
        restore_apply(dest, str(tmp_path / "restored"), keyring=Keyring(seed=_SEED))


def test_corrupted_artifact_is_rejected(tmp_path):
    base = str(tmp_path / "install")
    _build_base(base)
    dest = str(tmp_path / "backup")
    backup_create(base, dest, keyring=Keyring(seed=_SEED))

    art_dir = os.path.join(dest, ARTIFACTS)
    name = os.listdir(art_dir)[0]
    with open(os.path.join(art_dir, name), "wb") as fh:
        fh.write(b"tampered bytes")  # filename (a digest) no longer matches content

    ok, reason = backup_verify(dest)
    assert not ok
    assert "corrupted" in reason or "digest" in reason


def test_restore_preserves_a_rollback_copy(tmp_path):
    base = str(tmp_path / "install")
    _build_base(base)
    dest = str(tmp_path / "backup")
    backup_create(base, dest, keyring=Keyring(seed=_SEED))

    # Restore over an EXISTING base → the prior contents are moved aside, never deleted.
    result = restore_apply(dest, base, keyring=Keyring(seed=_SEED))
    assert result["rollback"] is not None
    assert os.path.isdir(result["rollback"])
