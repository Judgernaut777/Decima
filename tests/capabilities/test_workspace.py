"""Isolated repository workspace — worker confinement, reviewable diff, durable artifacts.

Load-bearing properties pinned here:
  * a workspace worker CANNOT read files outside its workspace (chroot jail — composes
    the workers adversarial guarantee);
  * a generated diff is REVIEWABLE before it is applied;
  * a restart does not lose the produced diff (it is a durable Weft artifact);
  * path traversal on mount/edit is refused.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from decima.capabilities.workspace import (
    WorkspaceError,
    create_workspace,
    get_diff_artifact,
)
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.workers.protocol import SUCCEEDED

REPO = {
    "app.py": "def add(a, b):\n    return a + b\n",
    "README.md": "# Demo\nA tiny repo.\n",
}


def test_mount_inspect_edit(weft, author):
    ws = create_workspace(weft, author, name="demo")
    ws.mount_repo(REPO)
    assert ws.list_files() == ["README.md", "app.py"]
    assert "def add" in ws.read_file("app.py")
    ws.edit_file("app.py", "def add(a, b):\n    return a + b + 0\n")
    assert "+ 0" in ws.read_file("app.py")


def test_mount_rejects_path_traversal(weft, author):
    ws = create_workspace(weft, author, name="demo")
    with pytest.raises(WorkspaceError):
        ws.mount_repo({"../escape.txt": "nope"})
    with pytest.raises(WorkspaceError):
        ws.edit_file("/etc/passwd", "nope")


def test_worker_cannot_read_files_outside_its_workspace(weft, author):
    ws = create_workspace(weft, author, name="jailed")
    ws.mount_repo(REPO)

    # Plant a real host secret OUTSIDE the workspace and probe for it from the worker.
    secret_dir = tempfile.mkdtemp(prefix="host-secret-")
    secret_path = os.path.join(secret_dir, "id_rsa")
    with open(secret_path, "w", encoding="utf-8") as handle:
        handle.write("PRIVATE KEY MATERIAL")

    resp = ws.run_in_worker(
        effect="probe",
        probe_paths=[secret_path, "/etc/passwd", "/etc/hostname", os.path.join(ws.root, "app.py")],
    )
    assert resp.status == SUCCEEDED
    # The chroot jail means NONE of the host paths (not even our own host workspace
    # dir) are reachable from inside the worker.
    assert resp.receipt_data["output"]["readable_outside"] == []
    # It DID materialize the mounted files into its own jail (composes, doesn't break).
    assert set(resp.receipt_data["output"]["written"]) == {"README.md", "app.py"}


def test_declared_test_runs_in_worker_and_reports_outcome(weft, author):
    ws = create_workspace(weft, author, name="checks")
    ws.mount_repo(REPO)
    check = (
        "def check(files):\n"
        "    ns = {}\n"
        "    exec(files['app.py'], ns)\n"
        "    ok = ns['add'](2, 3) == 5\n"
        "    return {'passed': 1 if ok else 0, 'failed': 0 if ok else 1}\n"
    )
    resp = ws.run_in_worker(effect="unit", check_source=check, check_entrypoint="check")
    assert resp.status == SUCCEEDED
    assert resp.receipt_data["output"]["passed"] == 1
    assert resp.receipt_data["output"]["failed"] == 0


def test_generated_diff_is_reviewable_before_apply(weft, author):
    ws = create_workspace(weft, author, name="review")
    ws.mount_repo(REPO)
    ws.edit_file("app.py", "def add(a, b):\n    return a + b  # documented\n")

    diff = ws.diff()
    # The diff is available for review BEFORE anything is applied.
    assert "--- a/app.py" in diff and "+++ b/app.py" in diff
    assert "# documented" in diff
    # Reviewing did not adopt the change: the baseline is still the mounted original.
    assert ws.baseline["app.py"] == REPO["app.py"]

    # Apply is a separate, explicit step — only then does the baseline move.
    ws.apply()
    assert ws.baseline["app.py"] == "def add(a, b):\n    return a + b  # documented\n"
    assert ws.diff() == ""  # nothing left to review after apply


def test_restart_does_not_lose_the_produced_diff(weft_env):
    weft, author, db, kr = weft_env
    ws = create_workspace(weft, author, name="durable")
    ws.mount_repo(REPO)
    ws.edit_file("README.md", "# Demo\nA tiny repo, now documented.\n")
    artifact_id = ws.produce_diff_artifact()

    # The artifact is on the log immediately.
    assert get_diff_artifact(weft, artifact_id) is not None

    # SIMULATE A RESTART: reopen the Weft from the same db with a fresh keyring/fold.
    reopened = Weft(db, Keyring(seed=bytes(32)))
    recovered = get_diff_artifact(reopened, artifact_id)
    assert recovered is not None, "the produced diff must survive a restart"
    assert "now documented" in recovered["diff"]
    assert recovered["instruction_eligible"] is False


def test_test_artifact_is_durable(weft_env):
    weft, author, db, kr = weft_env
    ws = create_workspace(weft, author, name="artifacts")
    ws.mount_repo(REPO)
    resp = ws.run_in_worker(effect="noop")
    artifact_id = ws.produce_test_artifact(resp)
    # Survives a reopen — the recorded outcome is evidence for later review.
    reopened = Weft(db, Keyring(seed=bytes(32)))
    cell = Weave.fold(reopened).get(artifact_id)
    assert cell is not None
    assert cell.content["status"] == SUCCEEDED
    assert cell.content["instruction_eligible"] is False
