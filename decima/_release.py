"""Single canonical source of Decima release metadata.

Historically the version lived in two places — ``pyproject.toml`` and
``decima/__init__.py`` — which drifted (a real pre-release finding). This module is now
the *one* authoritative definition:

- ``pyproject.toml`` reads ``VERSION`` via setuptools dynamic metadata
  (``[tool.setuptools.dynamic] version = {attr = "decima._release.VERSION"}``), so the
  built wheel/sdist version comes from here.
- ``decima/__init__.py`` re-exports it as ``decima.__version__``.
- ``scripts/check_release_metadata.py`` asserts every other mention of the version in the
  docs agrees with this value.

Keep this module import-light and side-effect-free: setuptools imports it *at build time*
via ``attr =`` before the package's dependencies are guaranteed to be installed, so it
must not import anything beyond the standard library (ideally nothing at all).
"""

# The single authoritative version string. Bump here and nowhere else.
VERSION = "0.3.0"

# Human-facing release name for the current version (used in release notes / banners).
RELEASE_NAME = "Local Daily Driver"

__all__ = ["RELEASE_NAME", "VERSION"]
