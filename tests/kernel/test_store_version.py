"""Clean-break store-version guard (adopt-durable-protocol §7 D2 / R4).

The durable Weft v0.1 changed the content-address scheme (BLAKE3-256 ids, deterministic
CBOR, base32 kind-prefixed ids), so a store written by the old stdlib profile is not
upgraded in place. Weft stamps a fresh store, verifies a stamped one, and rejects an
incompatible store on open with a clear message rather than a cryptic id-mismatch fold
error. The stamp lives in a side table and never enters any signed/hashed content.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.weft import WEFT_STORE_VERSION, Weft, WeftError

_EVENTS_DDL = (
    "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, "
    "payload TEXT NOT NULL, author TEXT NOT NULL, sig TEXT NOT NULL)"
)


def _db() -> str:
    return os.path.join(tempfile.mkdtemp(), "weft.db")


def test_fresh_store_is_stamped_with_the_current_version() -> None:
    path = _db()
    Weft(path, Keyring(seed=bytes(32)))
    row = (
        sqlite3.connect(path)
        .execute("SELECT value FROM meta WHERE key = 'store_version'")
        .fetchone()
    )
    assert row is not None and row[0] == WEFT_STORE_VERSION


def test_reopening_a_stamped_store_verifies_and_does_not_raise() -> None:
    path = _db()
    kr = Keyring(seed=bytes(32))
    Weft(path, kr)
    Weft(path, kr)  # warm start speaks the same version — no error


def test_unstamped_store_with_events_is_rejected_clean_break() -> None:
    """A pre-versioning (old-profile) store — events present, no version stamp — is refused
    on open rather than silently misread under the new content-address scheme."""
    path = _db()
    db = sqlite3.connect(path)
    db.execute(_EVENTS_DDL)
    db.execute("INSERT INTO events (id, payload, author, sig) VALUES ('old', '{}', 'a', 's')")
    db.commit()
    db.close()
    with pytest.raises(WeftError, match="clean break"):
        Weft(path, Keyring(seed=bytes(32)))


def test_mismatched_store_version_is_rejected() -> None:
    path = _db()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO meta (key, value) VALUES ('store_version', 'decima-weft/0.0')")
    db.commit()
    db.close()
    with pytest.raises(WeftError, match="0.0"):
        Weft(path, Keyring(seed=bytes(32)))
