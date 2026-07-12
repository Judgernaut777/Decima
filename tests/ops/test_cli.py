"""The wired operations CLI (handoff §12-13): doctor / backup / restore / rebuild.

Exercises the real entry points end to end over a provisioned install — the stubs are
gone; each command reads the master seed, does its real work, and returns a real code.
"""

from __future__ import annotations

import os

import pytest

from decima.cli import main as cli
from decima.services.data_layout import PROJECTIONS, DataDir
from decima.services.provision import first_run

_SEED = bytes(range(3, 35))


@pytest.mark.parametrize("command", ["doctor", "rebuild", "backup", "restore"])
def test_console_script_reads_process_args(command, tmp_path, monkeypatch, capsys):
    """As an installed console_script, each op is called with argv=None and MUST read
    ``sys.argv`` — regression for the bug where ``parse_args([] if argv is None …)``
    silently ignored every command-line flag (so ``decima-backup --dest X`` did nothing
    with X and ``decima-doctor --base X`` inspected the default dir instead of X)."""
    base = str(tmp_path / "install")
    first_run(base, seed=_SEED)
    dest = str(tmp_path / "bk")
    argmap = {
        "doctor": ["--base", base, "--json"],
        "rebuild": ["--base", base],
        "backup": ["--base", base, "--dest", dest],
        "restore": None,  # set below after a backup exists
    }
    if command == "restore":
        assert cli.backup(["--base", base, "--dest", dest]) == 0
        argmap["restore"] = ["--dest", dest, "--base", base, "--identity", base]
    # Simulate the console-script invocation: argv=None, real flags on sys.argv.
    monkeypatch.setattr("sys.argv", [f"decima-{command}", *argmap[command]])
    rc = getattr(cli, command)(None)
    assert rc == 0
    out = capsys.readouterr().out
    # The flags took effect: the command acted on the paths we passed, not the defaults.
    if command == "doctor":
        assert base in out  # doctor prints the base it inspected
    elif command == "backup":
        assert dest in out  # backup prints the dest it wrote to


def test_cli_backup_restore_rebuild_doctor(tmp_path, capsys):
    base = str(tmp_path / "install")
    first_run(base, seed=_SEED)

    # doctor over a fresh install: runs, no hard failure.
    assert cli.doctor(["--base", base]) == 0
    assert cli.doctor(["--base", base, "--json"]) == 0
    assert cli.doctor(["--base", base, "--export"]) == 0

    # backup → a destination directory.
    dest = str(tmp_path / "backup")
    assert cli.backup(["--base", base, "--dest", dest]) == 0
    assert os.path.isfile(os.path.join(dest, "MANIFEST.json"))

    # restore into a fresh base, pointing --identity at the seed that authored the log
    # (the seed is excluded from backups by design).
    restored = str(tmp_path / "restored")
    assert cli.restore(["--dest", dest, "--base", restored, "--identity", base]) == 0
    assert os.path.exists(DataDir(restored).weft_db)

    # rebuild clears the disposable projection cache (canonical dirs untouched).
    proj = DataDir(base).path(PROJECTIONS)
    with open(os.path.join(proj, "cache.bin"), "wb") as fh:
        fh.write(b"disposable")
    assert cli.rebuild(["--base", base]) == 0
    assert os.listdir(proj) == []
    assert os.path.exists(DataDir(base).weft_db)  # canonical store survives


def test_cli_backup_without_identity_fails(tmp_path):
    base = str(tmp_path / "empty")
    DataDir(base).ensure()
    # No master.seed → backup cannot verify/fold the log; fail closed with a non-zero code.
    assert cli.backup(["--base", base, "--dest", str(tmp_path / "b")]) == 1
