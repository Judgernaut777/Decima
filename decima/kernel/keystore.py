"""Key custody seam — a CUSTODIAN owns signing keys; the raw private key never leaves.

The heartbeat profile derives every principal's Ed25519 keypair in-process from one
master seed (`crypto.Keyring`). That is convenient and reproducible, but it fuses two
jobs that production must split: *holding a private key* and *using it*. This module
carves out the seam. A **custodian** (`KeyStore`) OWNS the private keys and exposes only:

  • ``has(pid)``          — do I hold a signing key for this principal?
  • ``public_key(pid)``   — the Ed25519 verify key (hex) — what a verifier needs, all it needs
  • ``sign(pid, message)``— an Ed25519 signature (hex) produced INSIDE the custodian

The raw private key / seed NEVER crosses this boundary — you get a public key or a
signature, never the secret. This mirrors CRED1 (`secrets.py`): the broker *dispenses,
it does not disclose*. A custodian is the crypto analogue — sign on behalf of a
principal without ever handing out the key.

Two implementations:

  • ``DerivedKeyStore`` — the DEFAULT. Reproduces today's exact behavior: each key is
    ``blake2b(master + pid, person="decima:ed255")`` → a 32-byte Ed25519 seed. Byte-for-
    byte identical to the pre-seam `Keyring._signing_key`, so a warm-started Weft and
    every existing signature still verify. Derivation always yields a key (``has`` is
    True), matching the old fail-closed-by-wrong-key verify fallback.

  • ``DirectoryKeyStore`` — an ALTERNATIVE. Per-principal 32-byte seeds persisted as
    ``<dir>/<pid>.seed`` (0600), a stand-in for an OS keystore / HSM directory: keys live
    OUTSIDE the Keyring and outlive it. A key is provisioned explicitly via ``create``;
    an un-provisioned principal FAILS CLOSED (``sign``/``public_key`` raise ``KeyError``,
    ``has`` is False). A fresh keystore over the same dir re-loads the persisted keys.

`crypto.Keyring` delegates its key-storage internals (``sign`` / ``public_key`` /
verify-key lookup) to a custodian (default: ``DerivedKeyStore(master)``); its public
interface is unchanged and the keybook (foreign public keys) is untouched.
"""

import hashlib
import os
import warnings

import nacl.signing


class KeyStore:
    """Custodian interface. Owns Ed25519 signing keys; the raw private key NEVER leaves
    it — callers get only a public key (hex) or a signature (hex)."""

    def has(self, pid: str) -> bool:
        """Whether this custodian holds a signing key for `pid`."""
        raise NotImplementedError

    def public_key(self, pid: str) -> str:
        """The principal's Ed25519 verify key, hex. Fails closed if not held."""
        raise NotImplementedError

    def sign(self, pid: str, message: str) -> str:
        """Ed25519-sign `message` inside the custodian; return the signature hex.
        Fails closed if the key is not held."""
        raise NotImplementedError

    def adopt(self, pid: str, seed: bytes) -> str:
        """Take custody of an EXPLICIT 32-byte Ed25519 seed under `pid`, overriding any
        derivation for that pid, and return the PUBLIC key (hex). The seed enters the
        custodian and never leaves. Needed for keys whose pid is NOT derivable from the
        pid itself — e.g. a self-certifying `mint_keyed` principal (pid = blake2b(pubkey),
        so the custodian cannot re-derive it and must be handed the key once)."""
        raise NotImplementedError


class DerivedKeyStore(KeyStore):
    """Default custodian — derive each keypair from one master seed + the pid, exactly
    as the pre-seam `Keyring` did. Deterministic: same master → same keys → same
    signatures across runs, so warm start and all prior signatures verify unchanged."""

    def __init__(self, master: bytes):
        # DEV-ONLY custody: one master seed derives EVERY principal's key, so a single
        # secret is the whole trust root — it collapses the split-custody posture the
        # ocap + Morta model assumes (see SECURITY.md, "Key custody"). Production must
        # pass an explicit split-custody custodian (e.g. DirectoryKeyStore).
        warnings.warn(
            "DerivedKeyStore derives all signing keys from ONE master seed — DEV-ONLY: "
            "it collapses split custody and the ocap+Morta trust model. Provision a "
            "DirectoryKeyStore (per-principal 0600 keys) and pass it to "
            "Keyring(custodian=...) in production.",
            stacklevel=2,
        )
        self._master = master
        self._cache: dict[str, nacl.signing.SigningKey] = {}

    def _sk(self, pid: str) -> nacl.signing.SigningKey:
        sk = self._cache.get(pid)
        if sk is None:
            # 32-byte Ed25519 seed, deterministic from (master, pid) — domain-separated.
            # Identical to the original crypto.Keyring._signing_key derivation.
            seed = hashlib.blake2b(
                self._master + pid.encode(), digest_size=32, person=b"decima:ed255"
            ).digest()
            sk = nacl.signing.SigningKey(seed)
            self._cache[pid] = sk
        return sk

    def has(self, pid: str) -> bool:
        # Derivation always yields a key (heartbeat profile). This preserves the old
        # verify fallback for an unknown foreign author: a derived (wrong) key that
        # fails the signature check — i.e. fail-closed-by-mismatch, never by exception.
        return True

    def public_key(self, pid: str) -> str:
        return self._sk(pid).verify_key.encode().hex()

    def sign(self, pid: str, message: str) -> str:
        return self._sk(pid).sign(message.encode()).signature.hex()

    def adopt(self, pid: str, seed: bytes) -> str:
        """Cache an explicit seed under `pid`, overriding derivation for that pid (a
        keyed pid is not derivable from itself). Later `sign`/`public_key` use this key."""
        if len(seed) != 32:
            raise ValueError("Ed25519 seed must be 32 bytes")
        sk = nacl.signing.SigningKey(seed)
        self._cache[pid] = sk
        return sk.verify_key.encode().hex()


class DirectoryKeyStore(KeyStore):
    """Alternative custodian — per-principal seeds persisted under a directory, proving
    keys can live OUTSIDE the Keyring (a stand-in for an OS keystore). Keys are minted or
    imported explicitly via `create`; the raw seed is written to disk and cached, but
    never returned. An un-provisioned principal fails closed."""

    def __init__(self, path: str):
        self._dir = path
        os.makedirs(path, mode=0o700, exist_ok=True)
        self._cache: dict[str, nacl.signing.SigningKey] = {}

    def _path(self, pid: str) -> str:
        return os.path.join(self._dir, pid + ".seed")

    def create(self, pid: str, seed: bytes | None = None) -> str:
        """Provision a principal's key in custody — mint a fresh 32-byte seed (or import
        the given one), persist it 0600, and return the PUBLIC key (hex). The seed never
        leaves the custodian."""
        seed = seed if seed is not None else os.urandom(32)
        if len(seed) != 32:
            raise ValueError("Ed25519 seed must be 32 bytes")
        path = self._path(pid)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, seed)
        finally:
            os.close(fd)
        sk = nacl.signing.SigningKey(seed)
        self._cache[pid] = sk
        return sk.verify_key.encode().hex()

    def _sk(self, pid: str) -> nacl.signing.SigningKey:
        sk = self._cache.get(pid)
        if sk is None:
            path = self._path(pid)
            if not os.path.exists(path):
                raise KeyError(f"no signing key in custody for {pid}")
            with open(path, "rb") as f:
                sk = nacl.signing.SigningKey(f.read())
            self._cache[pid] = sk
        return sk

    def has(self, pid: str) -> bool:
        return pid in self._cache or os.path.exists(self._path(pid))

    def public_key(self, pid: str) -> str:
        return self._sk(pid).verify_key.encode().hex()

    def sign(self, pid: str, message: str) -> str:
        return self._sk(pid).sign(message.encode()).signature.hex()
