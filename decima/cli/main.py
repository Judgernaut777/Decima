"""Console entry points for Decima 0.3.

Phase 1 ships these as thin, honest stubs so that a clean clone installs with one command
and every documented command resolves and runs (handoff §1.3 acceptance criterion). Each
command prints its planned role and the epic that implements it, then exits non-zero to
make clear it is not yet wired — never pretending success.

Filling these in is later-epic work:
  decima          interactive Shell / conversation client   (DEC-090+)
  decima-server   the local Shell backend API + runtime      (DEC-044, DEC-084)
  decima-worker   an isolated effect worker                  (DEC-051..053)
  decima-doctor   operational diagnostics                    (DEC-123)
  decima-rebuild  drop & rebuild disposable projections      (DEC-070)
  decima-backup   backup canonical state                     (DEC-121)
  decima-restore  restore + rebuild + verify state root      (DEC-122)
"""

from __future__ import annotations

import sys

_NOT_YET = "not yet implemented in the 0.3 scaffold"


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


def doctor(argv: list[str] | None = None) -> int:
    return _stub("doctor", "operational diagnostics", "DEC-123")


def rebuild(argv: list[str] | None = None) -> int:
    return _stub("rebuild", "drop & rebuild disposable projections", "DEC-070")


def backup(argv: list[str] | None = None) -> int:
    return _stub("backup", "backup canonical Weft + artifacts", "DEC-121")


def restore(argv: list[str] | None = None) -> int:
    return _stub("restore", "restore + rebuild + verify state root", "DEC-122")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
