"""Shared fixtures for the daily-driver capability tests.

A real Weft (the sole canonical store) seeded through the public kernel seams, plus a
deterministic model provider — no live API, no ambient authority.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.weft import Weft
from decima.models.providers import DeterministicProvider


def _new_weft() -> tuple[Weft, str, str, Keyring]:
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(db, kr), author, db, kr


@pytest.fixture
def weft_env() -> tuple[Weft, str, str, Keyring]:
    """(weft, author, db_path, keyring) — a fresh signed log."""
    return _new_weft()


@pytest.fixture
def weft(weft_env):
    return weft_env[0]


@pytest.fixture
def author(weft_env):
    return weft_env[1]


@pytest.fixture
def provider() -> DeterministicProvider:
    """The offline, reproducible provider every test uses (models PROPOSE)."""
    return DeterministicProvider()
