"""Seam conformance: the extracted implementations satisfy the kernel Protocols.

Runtime-checkable Protocols verify method presence, so these tests catch drift if a seam
loses a method the TCB (or the Rust port) relies on.
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel import hashing
from decima.kernel.crypto import Keyring
from decima.kernel.interfaces import CanonicalCodec, Signer, Verifier, WeftStore
from decima.kernel.weft import Weft


def test_hashing_module_is_a_canonical_codec():
    assert isinstance(hashing, CanonicalCodec)


def test_keyring_is_a_signer_and_verifier():
    kr = Keyring(seed=bytes(32))
    kr.mint("p", "human")
    assert isinstance(kr, Signer)
    assert isinstance(kr, Verifier)


def test_weft_is_a_weft_store():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    weft = Weft(db, Keyring(seed=bytes(32)))
    assert isinstance(weft, WeftStore)
