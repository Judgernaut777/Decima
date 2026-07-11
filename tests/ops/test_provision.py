"""First-run provisioning (handoff §12-13, deploy flow).

Load-bearing property: first-run stands up a usable LOCAL install — data layout, a
minted identity with the master seed custodied 0600, an initialized empty Weft, and
PUBLIC default budgets — and refuses to clobber an existing identity. It mints no
authority and touches no network.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.services.data_layout import ALL_DIRS, CONFIG, DataDir
from decima.services.provision import first_run

_SEED = bytes(range(2, 34))


def test_first_run_creates_local_install(tmp_path):
    base = str(tmp_path / "install")
    summary = first_run(base, seed=_SEED, token_budget=5000, monetary_budget=0)

    dd = DataDir(base)
    for name in ALL_DIRS:
        assert os.path.isdir(dd.path(name)), f"missing subdir {name}"

    # Master seed is custodied and private (0600); it is never returned.
    assert os.path.exists(dd.master_seed)
    mode = stat.S_IMODE(os.stat(dd.master_seed).st_mode)
    assert mode == 0o600
    assert "seed" not in summary and summary["network"] == "none"

    # Public config only: budgets are ints; identity carries the public key, not the seed.
    with open(dd.path(CONFIG, "budgets.json"), encoding="utf-8") as fh:
        budgets = json.load(fh)
    assert budgets["token_budget"] == 5000
    assert isinstance(budgets["token_budget"], int)
    with open(dd.path(CONFIG, "identity.json"), encoding="utf-8") as fh:
        identity = json.load(fh)
    assert identity["public_key"] == Keyring(seed=_SEED).public_key(summary["principal"])
    assert _SEED.hex() not in json.dumps(identity)

    # The empty canonical Weft exists and folds cleanly (genesis-only).
    weave = Weave.fold(Weft(dd.weft_db, Keyring(seed=_SEED)))
    assert weave.state_root()  # deterministic root over an empty install


def test_first_run_refuses_to_clobber_identity(tmp_path):
    base = str(tmp_path / "install")
    first_run(base, seed=_SEED)
    with pytest.raises(FileExistsError):
        first_run(base, seed=_SEED)
