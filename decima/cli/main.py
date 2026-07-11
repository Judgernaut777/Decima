"""Console entry points for Decima 0.3.

The interactive Shell, server, and worker remain thin honest stubs (their epics wire
them later); the OPERATIONS commands — doctor / backup / restore / rebuild — are wired
to their real implementations in ``decima.services`` (handoff §12-13).

  decima          interactive Shell / conversation client   (DEC-090+, stub)
  decima-server   the local Shell backend API + runtime      (DEC-044/084, stub)
  decima-worker   an isolated effect worker                  (DEC-051..053, stub)
  decima-doctor   operational diagnostics                    (WIRED)
  decima-rebuild  drop & rebuild disposable projections      (WIRED)
  decima-backup   backup canonical state                     (WIRED)
  decima-restore  restore + rebuild + verify state root      (WIRED)

Argument parsing is deliberately minimal and self-documenting (`--help` on each). A
`--base` names the install's data directory; commands that touch the signed log
(`backup`, `restore`, and doctor's fold-based checks) load the master seed from
`<base>/keys/master.seed` to construct the verifying keyring.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_NOT_YET = "not yet implemented in the 0.3 scaffold"

_DEFAULT_BASE = os.environ.get("DECIMA_HOME", os.path.expanduser("~/.local/share/decima"))


def _stub(command: str, role: str, epic: str) -> int:
    print(f"decima {command}: {role}")
    print(f"  status: {_NOT_YET} (implemented in {epic})")
    print("  the runnable reference implementation currently lives in ./heartbeat")
    print("  run it with:  cd heartbeat && python3 run.py")
    return 1


def main(argv: list[str] | None = None) -> int:
    return _stub("shell", "interactive Shell / conversation client", "DEC-090+")


def server(argv: list[str] | None = None) -> int:
    return _stub("server", "local Shell backend API + durable runtime", "DEC-044 / DEC-084")


def worker(argv: list[str] | None = None) -> int:
    return _stub("worker", "isolated capability-bound effect worker", "DEC-051..053")


# ── operations: real implementations ────────────────────────────────
def _load_keyring(base: str):
    """Construct the verifying keyring from the install's master seed, if present.
    Returns None when no identity has been provisioned (doctor still runs, in
    keyring-free integrity mode)."""
    from decima.services.data_layout import DataDir

    seed_path = DataDir(base).master_seed
    if not os.path.exists(seed_path):
        return None
    from decima.kernel.crypto import Keyring

    with open(seed_path, "rb") as fh:
        return Keyring(seed=fh.read())


def doctor(argv: list[str] | None = None) -> int:
    from decima.services.diagnostics import diagnostic_export as _export
    from decima.services.diagnostics import doctor as _doctor

    parser = argparse.ArgumentParser(prog="decima-doctor", description="operational diagnostics")
    parser.add_argument("--base", default=_DEFAULT_BASE, help="install data directory")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--export", action="store_true",
                        help="emit a scrubbed support bundle instead of the report")
    args = parser.parse_args([] if argv is None else argv)

    keyring = _load_keyring(args.base)
    if args.export:
        report = _export(args.base, keyring=keyring)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    report = _doctor(args.base, keyring=keyring)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"decima doctor — {args.base}")
        print(f"  overall: {report['status'].upper()}")
        for check in report["checks"]:
            extra = "  ".join(f"{k}={v}" for k, v in check.items()
                              if k not in ("status", "code"))
            print(f"  [{check['status']:>4}] {check['code']:<22} {extra}")
    return 0 if report["status"] != "fail" else 1


def rebuild(argv: list[str] | None = None) -> int:
    """Drop the DISPOSABLE projection cache. Projections are rebuildable from the fold
    (invariant 2), so deleting them destroys nothing canonical; the next fold repopulates
    them on demand. This deliberately never touches `weft/`, `artifacts/`, `checkpoints/`,
    `config/`, or `keys/`."""
    from decima.services.data_layout import PROJECTIONS, DataDir

    parser = argparse.ArgumentParser(prog="decima-rebuild",
                                     description="drop & rebuild disposable projections")
    parser.add_argument("--base", default=_DEFAULT_BASE, help="install data directory")
    args = parser.parse_args([] if argv is None else argv)

    proj = DataDir(args.base).path(PROJECTIONS)
    removed = 0
    if os.path.isdir(proj):
        for name in os.listdir(proj):
            path = os.path.join(proj, name)
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
            elif os.path.isdir(path):
                import shutil

                shutil.rmtree(path)
                removed += 1
    os.makedirs(proj, exist_ok=True)
    print(f"decima rebuild: cleared {removed} projection entries under {proj}")
    print("  projections are disposable — they rebuild from the fold on demand.")
    return 0


def backup(argv: list[str] | None = None) -> int:
    from decima.services.backup import BackupError, backup_create

    parser = argparse.ArgumentParser(prog="decima-backup",
                                     description="backup canonical Weft + artifacts")
    parser.add_argument("--base", default=_DEFAULT_BASE, help="install data directory")
    parser.add_argument("--dest", required=True, help="destination backup directory")
    args = parser.parse_args([] if argv is None else argv)

    keyring = _load_keyring(args.base)
    if keyring is None:
        print(f"decima backup: no identity at {args.base} (run first-run first)", file=sys.stderr)
        return 1
    try:
        manifest = backup_create(args.base, args.dest, keyring=keyring)
    except BackupError as exc:
        print(f"decima backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"decima backup: {manifest['weft']['count']} events + "
          f"{sum(len(v) for v in manifest['files'].values())} files → {args.dest}")
    print(f"  state_root: {manifest['state_root']}")
    return 0


def restore(argv: list[str] | None = None) -> int:
    from decima.services.backup import BackupError, restore_apply

    parser = argparse.ArgumentParser(prog="decima-restore",
                                     description="restore + verify canonical state")
    parser.add_argument("--dest", required=True, help="backup directory to restore from")
    parser.add_argument("--base", default=_DEFAULT_BASE,
                        help="install data directory to restore into")
    parser.add_argument("--identity", default=None,
                        help="base directory holding keys/master.seed that authored the log "
                             "(the seed is excluded from backups by design; default: --base)")
    args = parser.parse_args([] if argv is None else argv)

    # The master seed is a SECRET and is never inside a backup — an operator restores it
    # from their own key custody. Point --identity at wherever that seed lives.
    keyring = _load_keyring(args.identity or args.base)
    if keyring is None:
        print("decima restore: need the original install's keyring — pass --identity "
              "<base-with-keys/master.seed> (the seed is not stored in the backup)",
              file=sys.stderr)
        return 1
    try:
        result = restore_apply(args.dest, args.base, keyring=keyring)
    except BackupError as exc:
        print(f"decima restore refused: {exc}", file=sys.stderr)
        return 1
    print(f"decima restore: {result['events']} events → {result['base']}")
    print(f"  state_root: {result['state_root']} (verified)")
    if result["rollback"]:
        print(f"  previous base preserved at: {result['rollback']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
