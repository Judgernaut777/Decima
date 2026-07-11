"""Architecture guard: the trusted computing base imports nothing outward (DEC-006).

The kernel process verifies, authorizes, folds, and appends — and executes nothing
untrusted (handoff §2.6, §4.3). This test FAILS THE BUILD if any TCB module's imports
cross that line: no HTTP clients, sockets, subprocess, provider SDKs, MCP, or web
frameworks may appear in the trusted core.

Enforcement target:
  * Once Phase 2 extracts ``decima/kernel/`` this test scans that package.
  * Until then it scans the *current* TCB modules in ``heartbeat/decima/`` (the modules
    designated in docs/architecture/trust-boundaries.md). The rule and the assertions are
    identical, so the guard transfers verbatim when the package moves.

Scope note: Phase 1 enforces the third-party / stdlib forbidden set (the clear §2.9
rule), which is checkable on the current entangled tree. Transitive enforcement and the
decima-internal service-module boundary become enforceable once the kernel package is
cleanly separated in Phase 2; extend this file then.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# Top-level import roots the trusted core must never pull in (handoff §2.9 / §4.3).
FORBIDDEN_ROOTS: frozenset[str] = frozenset(
    {
        # network / IO egress
        "requests",
        "urllib",
        "http",
        "socket",
        "ssl",
        "asyncio",
        # process / code execution
        "subprocess",
        "ctypes",
        "multiprocessing",
        # model provider SDKs
        "anthropic",
        "openai",
        # tool / agent transports, web frameworks
        "mcp",
        "fastapi",
        "flask",
        "django",
        "starlette",
        "uvicorn",
    }
)

# Permitted third-party roots, only through the declared kernel seams
# (Signer/Verifier/WeftStore): real crypto and the storage backend.
ALLOWED_THIRD_PARTY: frozenset[str] = frozenset({"nacl", "sqlite3"})

# The trusted computing base, as designated in docs/architecture/trust-boundaries.md.
# When decima/kernel/ exists these names map to it; until then they are the current
# reference modules under heartbeat/decima/.
TCB_MODULE_NAMES: tuple[str, ...] = (
    "weft",  # append-only signed log (WeftStore)
    "weave",  # canonical encoding + fold + Cells
    "kernel",  # authorize / Morta gate / lifecycle (boot-wiring straddles → runtime)
    "executor",  # authorize→dispatch boundary (execution half moves to workers, Phase 5)
    "capability",  # grants, attenuation, invocation proofs
    "identity",  # principals
    "crypto",  # Ed25519 signing / verification
    "keystore",  # signing key custody
    "verifier",  # signature verification helpers
    "hashing",  # canonical content IDs
    "inbox",  # ApprovalInbox / Morta
    "manifest",  # capability manifests (grant nothing)
    "snapshot",  # signed checkpoints
    "context_fold",  # Law-5 window fold
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _tcb_files() -> list[pathlib.Path]:
    """Locate the TCB source files: prefer the extracted package, else the reference."""
    kernel_pkg = _REPO_ROOT / "decima" / "kernel"
    if kernel_pkg.is_dir():
        return sorted(p for p in kernel_pkg.rglob("*.py") if p.name != "__init__.py")
    ref = _REPO_ROOT / "heartbeat" / "decima"
    return [ref / f"{name}.py" for name in TCB_MODULE_NAMES]


def _imported_roots(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_tcb_files_exist() -> None:
    files = _tcb_files()
    missing = [str(p) for p in files if not p.is_file()]
    assert not missing, f"designated TCB modules not found: {missing}"


@pytest.mark.parametrize("path", _tcb_files(), ids=lambda p: p.stem)
def test_tcb_module_has_no_forbidden_import(path: pathlib.Path) -> None:
    if not path.is_file():
        pytest.skip(f"{path} not present yet")
    forbidden = _imported_roots(path) & FORBIDDEN_ROOTS
    assert not forbidden, (
        f"TCB module {path.name} imports forbidden root(s) {sorted(forbidden)} — "
        f"the trusted core must not reach the network, spawn processes, or load "
        f"provider/web transports (see docs/architecture/trust-boundaries.md)"
    )


def test_third_party_imports_are_declared_seams() -> None:
    """Any third-party (non-stdlib) import in the TCB must be an allowed kernel seam."""
    import sys

    stdlib = set(sys.stdlib_module_names)
    offenders: dict[str, list[str]] = {}
    for path in _tcb_files():
        if not path.is_file():
            continue
        third_party = {
            r
            for r in _imported_roots(path)
            if r not in stdlib and r != "decima" and r not in ALLOWED_THIRD_PARTY
        }
        if third_party:
            offenders[path.name] = sorted(third_party)
    assert not offenders, (
        f"undeclared third-party imports in the TCB: {offenders}; only "
        f"{sorted(ALLOWED_THIRD_PARTY)} are permitted, and only behind kernel seams"
    )
