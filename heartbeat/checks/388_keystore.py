"""KEY CUSTODY — a custodian OWNS signing keys; the raw private key never leaves it.

Today every principal's key is DERIVED in-process from one master seed. Production wants
keys held by a custodian (an OS keystore) with only public keys distributed to verifiers.
This lane builds the SEAM (`decima.keystore`): a `KeyStore` custodian exposing only
`sign(pid, message)` / `public_key(pid)` / `has(pid)` — never the raw key — and makes
`Keyring` DELEGATE to one. Mirrors CRED1: dispense a signature/handle, never the secret.

Proves:
  - the DEFAULT custodian (derive-from-master) signs & verifies BYTE-IDENTICAL to the
    pre-seam behavior (so every existing check stays green);
  - an ALTERNATIVE custodian (a directory-backed keystore, keys persisted OUTSIDE the
    Keyring) also signs & verifies, and survives a fresh keystore over the same dir;
  - the Keyring API never exposes a raw private key — only public keys (hex) + signatures;
  - a verifier holding ONLY the public key (keybook) verifies custodian-made signatures
    without any access to the signing key (keyless verifier);
  - fail closed: a missing/unknown key → sign/public_key raise, verify returns False.

Runs on its OWN fresh Keyrings (independent of `k`). Contract: run(k, line). Fail loud.
"""
import hashlib
import os
import tempfile

import nacl.signing

from decima.crypto import Keyring
from decima.keystore import KeyStore, DerivedKeyStore, DirectoryKeyStore


def run(k, line):
    line("\n== KEY CUSTODY — custodian owns the key; only public keys + signatures leave ==")

    # 1. DEFAULT custodian is byte-identical to the pre-seam derive-from-master. ────────
    S = b"seed-default-32-bytes-exactly!!!"
    assert len(S) == 32
    kr = Keyring(seed=S)                                  # default = DerivedKeyStore(S)
    assert isinstance(kr.custodian, DerivedKeyStore)
    p = kr.mint("custody-agent", "agent")
    msg = "the loom is signed"
    # Reconstruct the ORIGINAL derivation independently and compare — proves the seam
    # changed nothing on the wire.
    ref_seed = hashlib.blake2b(S + p.id.encode(), digest_size=32,
                               person=b"decima:ed255").digest()
    ref_sk = nacl.signing.SigningKey(ref_seed)
    assert kr.public_key(p.id) == ref_sk.verify_key.encode().hex(), "public key must match pre-seam"
    assert kr.sign(p.id, msg) == ref_sk.sign(msg.encode()).signature.hex(), "sig must be byte-identical"
    assert kr.verify(p.id, msg, kr.sign(p.id, msg)) is True
    line("  default custodian: public key + signature byte-identical to derive-from-master ✓")

    # 2. ALTERNATIVE custodian — keys persisted OUTSIDE the Keyring, in a directory. ────
    kdir = tempfile.mkdtemp()
    store = DirectoryKeyStore(kdir)
    krC = Keyring(seed=b"C" * 32, custodian=store)
    pc = krC.mint("filed-agent", "agent")
    pub = store.create(pc.id)                             # provision a key in custody
    assert os.path.exists(os.path.join(kdir, pc.id + ".seed")), "seed persisted to disk"
    assert pub == krC.public_key(pc.id)
    sigC = krC.sign(pc.id, msg)
    assert krC.verify(pc.id, msg, sigC) is True, "alternative custodian signs & verifies"
    assert krC.verify(pc.id, msg, "00" * 64) is False, "a forged sig still fails"
    # A brand-new keystore over the SAME directory re-loads the persisted key (custody
    # outlives the Keyring / process).
    krC2 = Keyring(seed=b"D" * 32, custodian=DirectoryKeyStore(kdir))
    pc2 = krC2.mint("filed-agent", "agent")              # same name → same pid
    assert pc2.id == pc.id
    assert krC2.public_key(pc.id) == pub, "reloaded custodian yields the same public key"
    assert krC2.verify(pc.id, msg, sigC) is True, "sig verifies after reload from disk"
    line("  alternative (directory) custodian: keys live outside the Keyring, persist & reload ✓")

    # 3. The Keyring API never exposes a raw private key — only public keys + sigs. ─────
    for ring in (kr, krC):
        assert not any(isinstance(v, nacl.signing.SigningKey) for v in vars(ring).values()), \
            "no SigningKey is held on the Keyring itself"
    assert not hasattr(Keyring, "_signing_key"), "the raw-key accessor is gone from Keyring"
    assert isinstance(kr.public_key(p.id), str) and len(kr.public_key(p.id)) == 64
    assert isinstance(kr.sign(p.id, msg), str) and len(kr.sign(p.id, msg)) == 128
    # The custodian surface returns only hex strings — never a key object.
    assert isinstance(store.public_key(pc.id), str) and isinstance(store.sign(pc.id, msg), str)
    assert isinstance(store, KeyStore)
    line("  Keyring exposes only public-key hex + signatures — the private key stays in custody ✓")

    # 4. A verifier holding ONLY the public key (keybook) verifies — keyless verifier. ──
    verifier = Keyring(seed=b"Z" * 32)                    # different seed; no key for pc
    assert verifier.verify(pc.id, msg, sigC) is False, "unknown author does not verify"
    verifier.trust(pc.id, krC.public_key(pc.id))         # learn ONLY the public key
    assert verifier.verify(pc.id, msg, sigC) is True, "public key alone verifies custodian sigs"
    assert verifier.verify(pc.id, "tampered", sigC) is False, "wrong message still fails"
    line("  a verifier with only the public key (keybook) verifies custodian signatures ✓")

    # 5. Fail closed — a missing / unknown key. ────────────────────────────────────────
    empty = DirectoryKeyStore(tempfile.mkdtemp())
    unknown = "deadbeefdeadbeef"
    assert empty.has(unknown) is False, "no key held for an un-provisioned principal"
    for op in (lambda: empty.sign(unknown, msg), lambda: empty.public_key(unknown)):
        try:
            op()
            assert False, "expected fail-closed on a missing key"
        except KeyError:
            pass
    # Through the Keyring, an unknown author (no local key, no keybook entry) → verify
    # returns False, never raises.
    krE = Keyring(seed=b"E" * 32, custodian=DirectoryKeyStore(tempfile.mkdtemp()))
    assert krE.verify(unknown, msg, sigC) is False, "fail closed: unknown author does not verify"
    line("  missing/unknown key: sign/public_key raise, verify returns False — fail closed ✓")

    line("  → key custody: a custodian owns the private key and only signs/serves public "
         "keys; the Keyring (default OR alternative) never lets the raw key out.")
