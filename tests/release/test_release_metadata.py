"""Tests for the single-source release metadata and its drift guard.

Covers:
  1. ``decima.__version__`` equals the canonical ``decima._release.VERSION``.
  2. ``pyproject.toml`` sources its version dynamically from that module (no hard-coded
     second version).
  3. ``scripts/check_release_metadata.py`` passes on the real tree.
  4. Adversarial: the drift guard actually FAILS on injected version / count / spec drift
     (a guard that can't fail is worthless).
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_release_metadata.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_release_metadata", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────── single-source version ───────────────────────────


def test_runtime_version_matches_canonical():
    import decima
    import decima._release as release

    assert decima.__version__ == release.VERSION


def test_release_module_exposes_name():
    import decima._release as release

    assert isinstance(release.VERSION, str) and release.VERSION
    assert isinstance(release.RELEASE_NAME, str) and release.RELEASE_NAME


def test_pyproject_version_is_dynamic_from_release_module():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    # No hard-coded static version — that was the two-place drift.
    assert "version" not in project
    assert "version" in project.get("dynamic", [])
    attr = data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    assert attr == "decima._release.VERSION"


# ─────────────────────────── the guard passes on the tree ───────────────────────────


def test_check_release_metadata_passes_on_tree():
    mod = _load_checker()
    problems, facts = mod.run_checks()
    assert problems == [], f"unexpected release-metadata drift: {problems}"
    assert facts["canonical_version"] == mod.canonical_version()
    # The derived counts are the ground truth the docs are checked against.
    assert facts["pytest_collected"] == mod.real_pytest_total()
    assert facts["spec_files"] == len(mod.real_spec_files())
    assert facts["spec_cases"] >= facts["spec_files"] >= 1


# ─────────────────────────── adversarial: the guard must fail on drift ──────────────


def _fixture_tree(tmp: Path, *, version="7.7.7", passed=500, skipped=20, files=9, cases=13):
    """Build a minimal, internally-consistent metadata tree under ``tmp``."""
    (tmp / "decima").mkdir(parents=True)
    (tmp / "decima" / "_release.py").write_text(
        f'VERSION = "{version}"\nRELEASE_NAME = "Test"\n', encoding="utf-8"
    )
    (tmp / "decima" / "__init__.py").write_text(
        "from decima._release import VERSION as __version__\n", encoding="utf-8"
    )
    (tmp / "pyproject.toml").write_text(
        "[project]\n"
        'name = "decima"\n'
        'dynamic = ["version"]\n'
        "[tool.setuptools.dynamic]\n"
        'version = { attr = "decima._release.VERSION" }\n',
        encoding="utf-8",
    )
    (tmp / "CHANGELOG.md").write_text(f"## [Unreleased]\n## [{version}] — Test\n", encoding="utf-8")
    (tmp / "README.md").write_text(
        f"{passed} passed / {skipped} skipped plus {cases} Playwright specs across {files} files\n",
        encoding="utf-8",
    )
    (tmp / "RELEASE-READINESS.md").write_text(
        f"gate {passed} passed, {skipped} skipped across {files} spec files\n",
        encoding="utf-8",
    )
    releases = tmp / "docs" / "releases"
    releases.mkdir(parents=True)
    (releases / f"{version}.md").write_text(
        f"Package version: `{version}`\n{cases} specs/{files}\n", encoding="utf-8"
    )
    specs = tmp / "tests" / "browser" / "specs"
    specs.mkdir(parents=True)
    per_file = [1] * files
    for extra in range(cases - files):
        per_file[extra % files] += 1
    for i, n in enumerate(per_file):
        body = "".join(f"test('case {i}-{j}', async () => {{}});\n" for j in range(n))
        (specs / f"s{i}.spec.js").write_text(body, encoding="utf-8")
    return tmp


def _point_module_at(mod, tmp: Path, *, total: int):
    """Repoint the checker's path globals + pytest count at a fixture tree."""
    mod.ROOT = tmp
    mod.CHANGELOG = tmp / "CHANGELOG.md"
    mod.README = tmp / "README.md"
    mod.RELEASE_READINESS = tmp / "RELEASE-READINESS.md"
    mod.RELEASES_DIR = tmp / "docs" / "releases"
    mod.SPECS_DIR = tmp / "tests" / "browser" / "specs"
    mod.real_pytest_total = lambda: total


def test_guard_accepts_consistent_fixture(tmp_path):
    mod = _load_checker()
    _fixture_tree(tmp_path, passed=500, skipped=20)
    _point_module_at(mod, tmp_path, total=520)
    problems, _ = mod.run_checks()
    assert problems == [], problems


def test_guard_catches_pytest_count_drift(tmp_path):
    mod = _load_checker()
    _fixture_tree(tmp_path, passed=500, skipped=20)  # docs claim 520 total
    _point_module_at(mod, tmp_path, total=521)  # reality collects one more
    problems, _ = mod.run_checks()
    assert any("collect" in p for p in problems), problems


def test_guard_catches_spec_file_count_drift(tmp_path):
    mod = _load_checker()
    _fixture_tree(tmp_path, files=9, cases=13)
    _point_module_at(mod, tmp_path, total=520)
    # Remove one spec file so the docs' "9 files" claim is now wrong (8 on disk).
    next(iter(sorted((tmp_path / "tests" / "browser" / "specs").glob("*.spec.js")))).unlink()
    problems, _ = mod.run_checks()
    assert any("spec-file" in p for p in problems), problems


def test_guard_catches_version_doc_drift(tmp_path):
    mod = _load_checker()
    _fixture_tree(tmp_path, version="7.7.7")
    _point_module_at(mod, tmp_path, total=520)
    # Corrupt the CHANGELOG heading so it disagrees with _release.py.
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n## [6.6.6] — Test\n", encoding="utf-8")
    problems, _ = mod.run_checks()
    assert any("CHANGELOG" in p for p in problems), problems


def test_guard_catches_hardcoded_pyproject_version(tmp_path):
    mod = _load_checker()
    _fixture_tree(tmp_path, version="7.7.7")
    _point_module_at(mod, tmp_path, total=520)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "decima"\n'
        'version = "7.7.7"\n'
        'dynamic = ["version"]\n'
        "[tool.setuptools.dynamic]\n"
        'version = { attr = "decima._release.VERSION" }\n',
        encoding="utf-8",
    )
    problems, _ = mod.run_checks()
    assert any("hard-code" in p for p in problems), problems


def test_guard_catches_missing_release_notes(tmp_path):
    mod = _load_checker()
    _fixture_tree(tmp_path, version="7.7.7")
    _point_module_at(mod, tmp_path, total=520)
    (tmp_path / "docs" / "releases" / "7.7.7.md").unlink()
    problems, _ = mod.run_checks()
    assert any("missing" in p for p in problems), problems


@pytest.mark.parametrize("bad_cases", [12, 14])
def test_guard_catches_spec_case_drift(tmp_path, bad_cases):
    mod = _load_checker()
    # Docs claim `bad_cases` specs, but the fixture files actually contain 13.
    _fixture_tree(tmp_path, files=9, cases=13)
    (tmp_path / "docs" / "releases" / "7.7.7.md").write_text(
        f"Package version: `7.7.7`\n{bad_cases} specs/9\n", encoding="utf-8"
    )
    _point_module_at(mod, tmp_path, total=520)
    problems, _ = mod.run_checks()
    assert any("spec-case" in p for p in problems), problems
