"""A SERVED instance holds PER-PRINCIPAL keys — the default custody posture (T1.1).

What these pin, at the real daemon-construction path (`build_application`):

  * the Keyring is backed by a `DirectoryKeyStore` — one 0600 seed per principal in a
    0700 directory — and NOT by the DEV-ONLY derived custodian (asserted twice: by type,
    and by the absence of the derived store's construction `UserWarning`);
  * each principal's public key is its OWN, not the key one master seed derives for it;
  * a RESTART re-loads the same keys, so the events the previous run signed still verify
    (a full verifying read + fold) and the fold reaches the same `state_root`.
"""

from __future__ import annotations

import os
import stat
import tempfile
import warnings

from decima.kernel.keystore import DerivedKeyStore, DirectoryKeyStore, derived_public_key
from decima.kernel.weave import Weave
from decima.services.api.server import build_application
from decima.services.custody import custody_dir

_SEED = bytes(32)  # the seed tests/api/conftest.py builds its app with


def _mode(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _state_root(app) -> str:
    """Fold the whole log — a VERIFYING read (Weft.events checks every signature)."""
    return Weave.fold(app.weft).state_root()


def test_served_instance_uses_per_principal_directory_custody(env):
    app, identity = env["app"], env["identity"]
    store = app.weft.keyring.custodian
    assert isinstance(store, DirectoryKeyStore)
    assert not isinstance(store, DerivedKeyStore)

    keys_dir = custody_dir(env["db"])
    assert keys_dir == os.path.abspath(env["db"]) + ".keys.d"
    assert _mode(keys_dir) == 0o700, "the custody directory must be private"

    for pid in (identity.app, identity.human):
        key_file = os.path.join(keys_dir, pid + ".seed")
        assert os.path.isfile(key_file), f"no per-principal key provisioned for {pid}"
        assert _mode(key_file) == 0o600, "a signing key must not be group/other-readable"
        # The principal's OWN key — not the one a single master seed derives for it.
        assert app.weft.keyring.public_key(pid) != derived_public_key(_SEED, pid)


def test_building_a_served_instance_emits_no_dev_only_custody_warning():
    """`DerivedKeyStore.__init__` warns; a real daemon path must never construct one."""
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any custody warning becomes a failure
        app, _identity = build_application(db, seed=_SEED, secure_cookie=True)
    assert isinstance(app.weft.keyring.custodian, DirectoryKeyStore)


def test_restart_reuses_the_same_keys_and_prior_events_still_verify(env, client):
    """Warm start: the same custody directory, the same keys, and the log the previous
    process signed still verifies and folds to the same state_root."""
    app, identity = env["app"], env["identity"]
    r = client.request("POST", "/api/v1/notes", body={"text": "custody survives a restart"})
    assert r.status == 201, r.json()  # a durable mutation reports 201 Created

    before_root = _state_root(app)
    before_keys = {p: app.weft.keyring.public_key(p) for p in (identity.app, identity.human)}

    app2, identity2 = build_application(env["db"], seed=_SEED, secure_cookie=True)

    assert (identity2.app, identity2.human) == (identity.app, identity.human)
    assert {p: app2.weft.keyring.public_key(p) for p in before_keys} == before_keys, (
        "a restart must re-load the persisted per-principal keys, not mint new ones"
    )
    assert _state_root(app2) == before_root  # verifying read of the prior run's signatures
    assert isinstance(app2.weft.keyring.custodian, DirectoryKeyStore)
