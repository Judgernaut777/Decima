"""E2E scenario G — BACKUP + RESTORE on the durable stack.

Build a real install: notes/documents (knowledge), a project plan with tasks, and a
content-addressed artifact on disk. Back it up; restore into a FRESH base with a
seed-equal keyring; rebuild every projection from the restored Weft; and prove the
restored world is the SAME world — equal folded state_root, equal per-projection
state_roots, and a verified (digest-checked) artifact.

Load-bearing property: a backup is the signed event log plus content-addressed blobs —
NOT a projection dump. Restore replays the log through the kernel's own acceptance gate,
so the restored fold is bit-identical (state_root-equal) to the original, and every
disposable projection re-derives from it. A tampered event or artifact is refused before
it can produce a world.
"""

from __future__ import annotations

import json

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.hashing import blob_id
from decima.kernel.model import assert_content, assert_edge
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.projections.engine import ProjectionDriver
from decima.projections.knowledge import KnowledgeProjection
from decima.projections.projects import ProjectsProjection
from decima.projections.tasks import TasksProjection
from decima.runtime import cells
from decima.services.backup import BackupError, backup_create, backup_verify, restore_apply
from decima.services.data_layout import ARTIFACTS, DataDir

_SEED = bytes(range(32))
_FACTORIES = (TasksProjection, ProjectsProjection, KnowledgeProjection)


def _build_install(base: str) -> tuple[DataDir, str, bytes]:
    dd = DataDir(base).ensure()
    kr = Keyring(seed=_SEED)
    author = kr.mint("decima", "root").id
    weft = Weft(dd.weft_db, kr)

    # Knowledge: a trusted note referencing a document.
    assert_content(weft, author, "note:plan", "note",
                   {"text": "the release plan", "instruction_eligible": True})
    assert_content(weft, author, "doc:spec", "document",
                   {"title": "spec", "text": "the canonical spec"})
    assert_edge(weft, author, "note:plan", "references", "doc:spec")

    # A project plan with tasks.
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    a = cells.create_step(weft, author, plan_id=plan, description="A")
    cells.create_step(weft, author, plan_id=plan, description="B", dependency_ids=[a])

    # A content-addressed artifact on disk.
    art = b"the durable artifact bytes for scenario G"
    with open(dd.path(ARTIFACTS, blob_id(art)), "wb") as fh:
        fh.write(art)
    return dd, author, art


def _projection_roots(weft: Weft) -> dict[str, str]:
    driver = ProjectionDriver(weft)
    for factory in _FACTORIES:
        driver.register(factory())
    return {name: driver.get(name).state_root() for name in driver.names()}


def test_backup_restore_rebuilds_an_identical_world(tmp_path):
    base = str(tmp_path / "install")
    dd, _author, art = _build_install(base)

    original_root = Weave.fold(Weft(dd.weft_db, Keyring(seed=_SEED))).state_root()
    original_projections = _projection_roots(Weft(dd.weft_db, Keyring(seed=_SEED)))

    dest = str(tmp_path / "backup")
    manifest = backup_create(base, dest, keyring=Keyring(seed=_SEED))
    assert manifest["state_root"] == original_root
    ok, reason = backup_verify(dest)
    assert ok, reason

    # Restore into a FRESH base with a seed-equal keyring (only the seed is needed).
    restored_base = str(tmp_path / "restored")
    result = restore_apply(dest, restored_base, keyring=Keyring(seed=_SEED))
    assert result["state_root"] == original_root

    # Fold + rebuild every projection from the restored Weft.
    restored_weft = Weft(DataDir(restored_base).weft_db, Keyring(seed=_SEED))
    assert Weave.fold(restored_weft).state_root() == original_root, "folded root must match"
    assert _projection_roots(restored_weft) == original_projections, (
        "every disposable projection must re-derive identically after restore"
    )

    # The artifact restored byte-identically and verifies against its digest name.
    restored_art = DataDir(restored_base).path(ARTIFACTS, blob_id(art))
    with open(restored_art, "rb") as fh:
        assert fh.read() == art


def test_tampered_backup_is_refused_before_producing_a_world(tmp_path):
    base = str(tmp_path / "install")
    _build_install(base)
    dest = str(tmp_path / "backup")
    backup_create(base, dest, keyring=Keyring(seed=_SEED))

    manifest_path = str(tmp_path / "backup" / "MANIFEST.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    # Flip a byte inside the first signed event payload (the "release plan" note); the
    # recomputed content id no longer matches, so verification must fail closed.
    payload = manifest["weft"]["events"][0][1]
    assert "release" in payload, "expected the tampered token in the first event payload"
    manifest["weft"]["events"][0][1] = payload.replace("release", "releasx", 1)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)

    ok, _reason = backup_verify(dest)
    assert not ok, "a tampered event log must not verify"
    with pytest.raises(BackupError):
        restore_apply(dest, str(tmp_path / "nope"), keyring=Keyring(seed=_SEED))
