"""Per-principal key custody on the operations paths (T1.1).

Covers the custody seam itself and the two operational paths that are NOT the API daemon:

  * a PROVISIONED install (`first_run`) custodies root's own key 0600 in a 0700 dir and
    publishes that key (not a master-derived one) as its fingerprint;
  * the layout mapping: an install keeps its keys in the SECRETS partition
    (`<base>/keys/principals`), an ad-hoc db keeps them beside itself;
  * FAIL CLOSED: an author with no key in custody does not verify — the derived store's
    "always yields a key" fallback is gone;
  * MIGRATION: an install whose history was signed under the DEV-ONLY derived custodian
    keeps verifying, because exactly those authors are adopted into per-principal custody.
"""

from __future__ import annotations

import os
import stat

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.keystore import DirectoryKeyStore, derived_public_key
from decima.kernel.weft import ASSERT, Weft, WeftError
from decima.services.custody import (
    adopt_legacy_authors,
    custody_dir,
    ensure_custody,
    install_keyring,
)
from decima.services.data_layout import KEYS, DataDir
from decima.services.provision import first_run

_SEED = bytes(range(5, 37))
_OTHER_SEED = bytes(range(40, 72))


def _mode(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_custody_dir_for_an_install_is_inside_the_secrets_partition(tmp_path):
    dd = DataDir(str(tmp_path / "install"))
    assert custody_dir(dd.weft_db) == os.path.abspath(dd.path(KEYS, "principals"))
    # An ad-hoc database keeps its keys beside itself.
    ad_hoc = str(tmp_path / "scratch" / "other.db")
    assert custody_dir(ad_hoc) == os.path.abspath(ad_hoc) + ".keys.d"


def test_provisioned_install_custodies_root_key_per_principal(tmp_path):
    base = str(tmp_path / "install")
    summary = first_run(base, seed=_SEED)
    dd = DataDir(base)

    keys_dir = custody_dir(dd.weft_db)
    assert summary["key_custody"] == "per-principal"
    assert summary["key_custody_dir"] == keys_dir
    assert _mode(keys_dir) == 0o700

    key_file = os.path.join(keys_dir, summary["principal"] + ".seed")
    assert _mode(key_file) == 0o600
    assert summary["public_key"] != derived_public_key(_SEED, summary["principal"])

    # Warm start: re-opening the install reuses the SAME key (no re-mint).
    with open(dd.master_seed, "rb") as fh:
        reopened = install_keyring(dd.weft_db, seed=fh.read())
    assert isinstance(reopened.custodian, DirectoryKeyStore)
    assert reopened.public_key(summary["principal"]) == summary["public_key"]
    assert ensure_custody(reopened, (summary["principal"],)) == []


def test_an_author_without_custody_fails_closed(tmp_path):
    """A foreign author is not silently derived into existence: no key, no verification."""
    db = str(tmp_path / "weft.db")
    with pytest.warns(UserWarning):  # the DEV-ONLY derived custodian announces itself
        foreign = Keyring(seed=_OTHER_SEED)
    author = foreign.mint("stranger", "agent").id
    event = Weft(db, foreign).append(author, ASSERT, {"kind": "CONTENT", "text": "hello"})

    keyring = install_keyring(db, seed=_SEED)  # a DIFFERENT master seed
    assert not keyring.custodian.has(author), "a stranger's key must not be conjured"
    assert keyring.verify(author, event.id, event.sig) is False
    with pytest.raises(WeftError):
        list(Weft(db, keyring).events())  # the verifying read refuses, it does not accept


def test_legacy_derived_history_is_adopted_and_keeps_verifying(tmp_path):
    """An install written under the derived custodian still verifies after the flip: its
    authors' keys are imported into per-principal custody, proven by a signature check."""
    db = str(tmp_path / "weft.db")
    with pytest.warns(UserWarning):
        legacy = Keyring(seed=_SEED)
    author = legacy.mint("decima", "root").id
    event = Weft(db, legacy).append(author, ASSERT, {"kind": "CONTENT", "text": "legacy"})

    with pytest.warns(UserWarning, match="per-principal custody"):
        keyring = install_keyring(db, seed=_SEED)

    store = keyring.custodian
    assert isinstance(store, DirectoryKeyStore)
    assert store.has(author)
    assert _mode(os.path.join(custody_dir(db), author + ".seed")) == 0o600
    # Custody now owns the key the history was signed with, so the log still verifies…
    assert [e.id for e in Weft(db, keyring).events()] == [event.id]
    # …and a second open is a no-op (the migration runs once).
    assert adopt_legacy_authors(db, keyring) == []
