"""WS2 clean-install / first-run / backup-restore rehearsal — the gate-runnable form.

The heavy lifting lives in `rehearsal_core`, which is ALSO run end to end inside a
systemd clean-room container (`rehearse_clean_install.sh`). Here we drive the same
public seams socket-free so the full lifecycle is part of the normal `pytest` gate:
first-run + idempotency, doctor, the Shell's 200 / unauth-401 / strict-CSP surface,
representative data through the real authenticated API, a backup → move-aside → restore
state-root round-trip, and the fault matrix (corrupt backup, missing identity, occupied
port, non-loopback refusal, Python floor, rollback preservation).
"""

from __future__ import annotations

import pytest

from tests.install import rehearsal_core as rc


@pytest.fixture()
def rehearsal(tmp_path):
    return rc.run_full_rehearsal(str(tmp_path))


def test_full_lifecycle_all_checks_pass(rehearsal):
    failed = [c for c in rehearsal.checks if c["status"] != "ok"]
    assert not failed, f"failed checks: {failed}"
    assert len(rehearsal.checks) >= 50


def test_first_run_and_restart_idempotent(rehearsal):
    codes = {c["code"] for c in rehearsal.checks}
    assert "restart::first-run-refuses-clobber" in codes
    assert "restart::identity-persists" in codes


def test_shell_surface_gated(rehearsal):
    by_code = {c["code"]: c for c in rehearsal.checks}
    assert by_code["shell::root-200"]["status"] == "ok"
    assert by_code["shell::unauth-api-401"]["status"] == "ok"
    assert by_code["shell::csp-present"]["status"] == "ok"
    assert by_code["shell::csp-no-unsafe"]["status"] == "ok"


def test_backup_restore_state_root_roundtrips(rehearsal):
    assert rehearsal.facts["backup_state_root"] == rehearsal.facts["restored_state_root"]
    assert (
        rehearsal.facts["record_counts_before_backup"]
        == rehearsal.facts["record_counts_after_restore"]
    )


def test_no_secret_ever_leaks(rehearsal):
    leaks = [
        c for c in rehearsal.checks if c["code"].startswith("no-secret::") and c["status"] != "ok"
    ]
    assert not leaks


def test_backup_excludes_secret_and_disposable(rehearsal):
    by_code = {c["code"]: c for c in rehearsal.checks}
    assert by_code["backup::excludes-keys"]["status"] == "ok"
    assert by_code["restore::backup-carried-no-seed"]["status"] == "ok"


def test_fault_matrix_each_explicit_and_recoverable(rehearsal):
    by_code = {c["code"]: c for c in rehearsal.checks}
    for code in (
        "fault::corrupt-backup-verify-false",
        "fault::corrupt-backup-restore-refused",
        "fault::backup-without-identity-exit-1",
        "fault::restore-without-identity-exit-1",
        "fault::restore-preserves-rollback",
        "fault::rollback-retains-prior-data",
        "fault::occupied-port-raises",
        "fault::nonloopback-bind-refused",
        "fault::python-floor-guard",
    ):
        assert by_code[code]["status"] == "ok", code
