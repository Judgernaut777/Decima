"""`doctor` + `diagnostic_export` (handoff §13).

Load-bearing properties: doctor DETECTS a tampered artifact (its content no longer
hashes to its own content-addressed name) and a stale / missing checkpoint; and a
support bundle NEVER carries a secret off the box.
"""
from __future__ import annotations

import json

from decima.kernel.checkpoints import make_checkpoint
from decima.kernel.crypto import Keyring
from decima.kernel.hashing import blob_id
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.services.data_layout import (
    ARTIFACTS,
    CHECKPOINTS,
    LOGS,
    DataDir,
)
from decima.services.diagnostics import diagnostic_export, doctor

_SEED = bytes(range(1, 33))
_SECRET = "supersecret_deadbeefcafebabe0123456789abcdef"


def _install(base: str, *, with_checkpoint: bool = True) -> tuple[DataDir, Keyring, str]:
    dd = DataDir(base).ensure()
    kr = Keyring(seed=_SEED)
    author = kr.mint("decima", "root").id
    weft = Weft(dd.weft_db, kr)
    plan = cells.create_plan(weft, author, objective="ship", creator_principal=author)
    cells.create_step(weft, author, plan_id=plan, description="A")

    data = b"artifact one"
    with open(dd.path(ARTIFACTS, blob_id(data)), "wb") as fh:
        fh.write(data)

    if with_checkpoint:
        weave = Weave.fold(weft)
        ckpt = make_checkpoint(weft, weave, kr, author, protocol_version="0.1")
        with open(dd.path(CHECKPOINTS, "frontier.json"), "w", encoding="utf-8") as fh:
            json.dump(ckpt, fh)
    return dd, kr, author


def _check(report: dict, code_prefix: str) -> dict:
    for c in report["checks"]:
        if str(c["code"]).startswith(code_prefix):
            return c
    raise AssertionError(f"no check with code prefix {code_prefix!r} in {report}")


def test_doctor_healthy_install_is_ok(tmp_path):
    base = str(tmp_path / "install")
    _dd, kr, _ = _install(base)
    report = doctor(base, keyring=kr)
    assert report["status"] in ("ok", "warn")
    assert _check(report, "weft")["status"] == "ok"
    assert _check(report, "artifacts")["status"] == "ok"


def test_doctor_detects_tampered_artifact(tmp_path):
    base = str(tmp_path / "install")
    dd, kr, _ = _install(base)
    name = dd.list_files(ARTIFACTS)[0]
    with open(dd.path(ARTIFACTS, name), "wb") as fh:
        fh.write(b"corrupted - no longer matches its digest name")

    report = doctor(base, keyring=kr)
    artifact_check = _check(report, "artifact")
    assert artifact_check["status"] == "fail"
    assert name in artifact_check["corrupt"]
    assert report["status"] == "fail"


def test_doctor_detects_stale_checkpoint(tmp_path):
    base = str(tmp_path / "install")
    dd, kr, author = _install(base)
    # Advance the log PAST the checkpoint frontier without recording a new one.
    weft = Weft(dd.weft_db, kr)
    plan_cell = Weave.fold(weft).of_type("plan")[0]
    cells.create_step(weft, author, plan_id=plan_cell.id, description="B")

    report = doctor(base, keyring=kr)
    ckpt_check = _check(report, "checkpoint")
    assert ckpt_check["status"] == "warn"
    assert "stale" in ckpt_check["detail"]


def test_doctor_detects_missing_checkpoint(tmp_path):
    base = str(tmp_path / "install")
    _dd, kr, _ = _install(base, with_checkpoint=False)
    report = doctor(base, keyring=kr)
    ckpt_check = _check(report, "checkpoint")
    assert ckpt_check["status"] == "warn"
    assert ckpt_check["code"] == "checkpoint-missing"


def test_diagnostic_export_contains_no_secret(tmp_path):
    base = str(tmp_path / "install")
    dd, kr, _ = _install(base)
    # A log line naming a token, and a raw opaque blob on its own line.
    with open(dd.path(LOGS, "decima.log"), "w", encoding="utf-8") as fh:
        fh.write(f"ERROR auth api_token={_SECRET} rejected\n")
        fh.write(f"{_SECRET}\n")
        fh.write("INFO plan ship step A ready\n")

    bundle = diagnostic_export(base, keyring=kr)
    serialized = json.dumps(bundle)

    assert _SECRET not in serialized, "the support bundle leaked a secret token"
    assert _SEED.hex() not in serialized, "the support bundle leaked the master seed"
    # It still carries useful, non-sensitive signal.
    assert bundle["kind"] == "decima-diagnostic-export"
    assert "[REDACTED]" in serialized
    assert any("ready" in ln for lines in bundle["logs"].values() for ln in lines)
