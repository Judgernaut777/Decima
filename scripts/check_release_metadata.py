#!/usr/bin/env python3
"""Release-metadata drift guard.

Fails (exit 1) if any of the following disagree, so version/count drift can never reach a
tag again (the two-place version drift and the stale test/spec counts were both real
pre-0.3 findings):

Version consistency
  - ``decima/_release.py::VERSION`` is the single source of truth.
  - ``pyproject.toml`` sources its version from it (``dynamic = ["version"]`` +
    ``[tool.setuptools.dynamic] version = {attr = "decima._release.VERSION"}``) and does
    NOT hard-code a second ``version = "..."``.
  - ``decima.__version__`` re-exports that same value.
  - The docs that name the package version agree with it (CHANGELOG released heading,
    ``docs/releases/<VERSION>.md`` filename + its ``Package version:`` line).

Count consistency (derived deterministically, offline — no live model, no browser)
  - The documented full pytest gate ("<passed> passed, <skipped> skipped") sums to the
    number pytest actually collects (``pytest --collect-only -q``).
  - The documented Playwright spec-file count matches ``tests/browser/specs/*.spec.js``.
  - The documented Playwright spec-case count matches the ``test(...)`` calls in them.

Deterministic and offline by construction: it only parses files and runs pytest in
*collect-only* mode (no test bodies execute, no network, no credentials).

Usage: ``python3 scripts/check_release_metadata.py`` (add ``-v`` for the full report).
Exit 0 = all consistent; exit 1 = drift (details printed to stderr).
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Docs that carry release metadata (relative to repo root).
CHANGELOG = ROOT / "CHANGELOG.md"
README = ROOT / "README.md"
RELEASE_READINESS = ROOT / "docs" / "RELEASE-READINESS.md"
RELEASES_DIR = ROOT / "docs" / "releases"
SPECS_DIR = ROOT / "tests" / "browser" / "specs"

# A documented "N passed, M skipped" line describes the *full* pytest gate (which we can
# check against collect-only) only when N is this large; smaller ones are the bounded
# live-provider suite (e.g. "9 passed, 1 skipped") and are intentionally not summed here.
FULL_GATE_MIN_PASSED = 50


class Drift(Exception):
    """A metadata inconsistency worth failing the build over."""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ─────────────────────────── version ───────────────────────────


def canonical_version() -> str:
    """The single source of truth, parsed from decima/_release.py WITHOUT importing it."""
    text = _read(ROOT / "decima" / "_release.py")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise Drift('decima/_release.py: could not find `VERSION = "..."`')
    return m.group(1)


def check_pyproject_version(version: str, problems: list[str]) -> None:
    data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    project = data.get("project", {})

    if "version" in project:
        problems.append(
            "pyproject.toml [project] hard-codes `version` "
            f"({project['version']!r}); it must be dynamic and sourced from "
            "decima/_release.py"
        )
    if "version" not in project.get("dynamic", []):
        problems.append('pyproject.toml [project].dynamic must include "version"')

    attr = (
        data.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get("version", {}).get("attr")
    )
    expected = "decima._release.VERSION"
    if attr != expected:
        problems.append(
            f"pyproject.toml [tool.setuptools.dynamic].version.attr is {attr!r}, "
            f"expected {expected!r}"
        )


def check_runtime_version(version: str, problems: list[str]) -> None:
    """decima.__version__ must equal the canonical VERSION (parse, don't require install)."""
    init = _read(ROOT / "decima" / "__init__.py")
    if "from decima._release import VERSION as __version__" not in init:
        problems.append(
            "decima/__init__.py must re-export the canonical version "
            "(`from decima._release import VERSION as __version__`)"
        )


def _is_dev(version: str) -> bool:
    """True for in-development versions (PEP 440 `X.Y.Z.devN`)."""
    return ".dev" in version


def _unreleased_body(changelog: str) -> str:
    """The text between `## [Unreleased]` and the next `## ` heading (or EOF)."""
    m = re.search(r"^##\s+\[Unreleased\]\s*$(.*?)(?=^##\s|\Z)", changelog, re.M | re.S)
    return m.group(1).strip() if m else ""


def check_doc_versions(version: str, problems: list[str]) -> None:
    changelog = _read(CHANGELOG)
    # CHANGELOG: the first non-Unreleased released heading must be the current version
    # (for a dev version, the *base* it is working toward has not shipped, so the top
    # released heading must be an older, already-tagged version — never the dev version).
    headings = re.findall(r"^##\s+\[([^\]]+)\]", changelog, re.M)
    released = [h for h in headings if h.lower() != "unreleased"]
    if not released:
        problems.append("CHANGELOG.md has no released `## [X.Y.Z]` heading")
    elif not _is_dev(version) and released[0] != version:
        problems.append(
            f"CHANGELOG.md top released heading is [{released[0]}], expected [{version}]"
        )
    elif _is_dev(version) and released[0] == version:
        problems.append(
            f"CHANGELOG.md lists dev version [{version}] as released — dev versions must "
            "never appear as a released heading"
        )

    # Unreleased-section discipline: a dev version means work is in flight (the section
    # must say what), and a release version means main IS the release (the section must
    # be empty — accumulating unreleased work under a release version is exactly the
    # 0.3.0-era drift this guard exists to prevent).
    unreleased = _unreleased_body(changelog)
    if _is_dev(version) and not unreleased:
        problems.append(
            f"VERSION {version} is a dev version but CHANGELOG.md `## [Unreleased]` is "
            "empty — describe the in-flight work, or cut the release"
        )
    elif not _is_dev(version) and unreleased:
        problems.append(
            f"VERSION {version} claims to be a release but CHANGELOG.md `## [Unreleased]` "
            "is non-empty — bump to the next `X.Y.Z.dev0` (decima/_release.py) so main "
            "stops identifying itself as the released artifact"
        )

    # docs/releases/<VERSION>.md must exist and state the same package version. Dev
    # versions have no release notes yet, by definition.
    if _is_dev(version):
        return
    notes = RELEASES_DIR / f"{version}.md"
    if not notes.exists():
        existing = sorted(p.name for p in RELEASES_DIR.glob("*.md"))
        problems.append(
            f"docs/releases/{version}.md is missing (found: {existing}); the release-notes "
            "filename must match the canonical version"
        )
    else:
        m = re.search(r"Package version:\s*`([^`]+)`", _read(notes))
        if m and m.group(1) != version:
            problems.append(
                f"docs/releases/{version}.md 'Package version: `{m.group(1)}`' "
                f"disagrees with canonical {version}"
            )


# ─────────────────────────── counts ───────────────────────────


def real_pytest_total() -> int:
    """Number of tests pytest collects — deterministic, offline (no bodies run)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = proc.stdout + proc.stderr
    matches = re.findall(r"(\d+)\s+tests?\s+collected", out)
    if not matches:
        raise Drift(
            "could not parse a 'N tests collected' summary from "
            f"`pytest --collect-only -q` (exit {proc.returncode}):\n{out[-2000:]}"
        )
    if proc.returncode != 0:
        raise Drift(
            f"`pytest --collect-only -q` exited {proc.returncode} (collection error?):\n"
            f"{out[-2000:]}"
        )
    return int(matches[-1])


def real_spec_files() -> list[Path]:
    return sorted(SPECS_DIR.glob("*.spec.js"))


def real_spec_cases(files: list[Path]) -> int:
    """Count top-level test(...) calls across the Playwright spec files."""
    total = 0
    for f in files:
        total += len(re.findall(r"(?:^|[^\w.])test\s*\(", _read(f)))
    return total


def _count_docs(version: str) -> dict[Path, str]:
    """The docs that carry count metadata, read once (skip any that are absent).

    For a dev version only the README (the one doc that describes *current main*) is held
    to the current tree's counts; RELEASE-READINESS, CHANGELOG released sections and
    docs/releases/*.md describe a tagged qualification and must stop being rewritten to
    track main (the 0.3.0-era habit that made release evidence mutable).
    """
    if _is_dev(version):
        candidates = [README]
    else:
        candidates = [README, RELEASE_READINESS, RELEASES_DIR / f"{version}.md", CHANGELOG]
    return {p: _read(p) for p in candidates if p.exists()}


def check_gate_counts(version: str, problems: list[str]) -> None:
    total = real_pytest_total()
    files = real_spec_files()
    n_files = len(files)
    n_cases = real_spec_cases(files)

    docs = _count_docs(version)

    # Full pytest gate: every documented "N passed[,/] M skipped" with a large N must sum
    # to the real collected total. A drift like 498 -> 497 makes 497+25 != 523 -> fail.
    gate_seen = False
    for path, text in docs.items():
        for pas, skip in re.findall(r"(\d+)\s+passed\s*[,/]\s*(\d+)\s+skipped", text):
            passed, skipped = int(pas), int(skip)
            if passed < FULL_GATE_MIN_PASSED:
                continue  # bounded live-provider suite, not the full gate
            gate_seen = True
            if passed + skipped != total:
                problems.append(
                    f"{path.relative_to(ROOT)}: documented full gate "
                    f"'{passed} passed, {skipped} skipped' sums to {passed + skipped}, "
                    f"but pytest collects {total}"
                )
    if not gate_seen:
        problems.append(
            "no documented full pytest gate ('<N> passed, <M> skipped') found in the "
            "release docs to validate against the real count"
        )

    # Playwright spec-file count: "across N files" / "across N spec files" / "specs/N".
    file_seen = False
    file_patterns = (
        r"across\s+(\d+)\s+files",
        r"across\s+(\d+)\s+spec\s+files",
        r"specs?/(\d+)\b",
    )
    for path, text in docs.items():
        for pat in file_patterns:
            for m in re.findall(pat, text):
                file_seen = True
                if int(m) != n_files:
                    problems.append(
                        f"{path.relative_to(ROOT)}: documented Playwright spec-file count "
                        f"{m} != actual {n_files} (tests/browser/specs/*.spec.js)"
                    )
    if not file_seen:
        problems.append("no documented Playwright spec-file count found in the release docs")

    # Playwright spec-case count: "N Playwright specs" / "N specs/".
    case_patterns = (
        r"(\d+)\s+Playwright\s+specs\b",
        r"(\d+)\s+specs/\d+",
    )
    for path, text in docs.items():
        for pat in case_patterns:
            for m in re.findall(pat, text):
                if int(m) != n_cases:
                    problems.append(
                        f"{path.relative_to(ROOT)}: documented Playwright spec-case count "
                        f"{m} != actual {n_cases} (test(...) calls in the spec files)"
                    )


# ─────────────────────────── main ───────────────────────────


def run_checks() -> tuple[list[str], dict[str, object]]:
    problems: list[str] = []
    version = canonical_version()
    check_pyproject_version(version, problems)
    check_runtime_version(version, problems)
    check_doc_versions(version, problems)
    check_gate_counts(version, problems)

    files = real_spec_files()
    facts: dict[str, object] = {
        "canonical_version": version,
        "pytest_collected": real_pytest_total(),
        "spec_files": len(files),
        "spec_cases": real_spec_cases(files),
    }
    return problems, facts


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    try:
        problems, facts = run_checks()
    except Drift as exc:
        print(f"release-metadata check FAILED:\n  - {exc}", file=sys.stderr)
        return 1

    if verbose or problems:
        print("release-metadata facts:")
        for k, v in facts.items():
            print(f"  {k} = {v}")

    if problems:
        print("\nrelease-metadata check FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print(
        f"release-metadata OK: version {facts['canonical_version']}, "
        f"{facts['pytest_collected']} tests, "
        f"{facts['spec_cases']} spec-cases across {facts['spec_files']} files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
