"""Principals and signing — REAL Ed25519 (libsodium via PyNaCl).

Signatures are the one place Decima takes a dependency, on purpose. The dependency
policy is: recreate the design in pure stdlib, but WRAP the real engine for the domains
where rolling your own is the liability — and cryptography is the ultimate
"never roll your own." Python's stdlib has no asymmetric signatures (only symmetric
hashlib/hmac), and a hand-written pure-Python Ed25519 would be both unaudited and slow.
So the Weft is signed with **Ed25519 from libsodium** (PyNaCl) — audited, constant-time,
C-fast. (This replaces the earlier dev-grade HMAC-BLAKE2b stand-in.)

Every Event is signed by its author, so the Weft is tamper-evident AND each agent proves
possession of its own key — knowing a public Cell id buys nothing (Law 2). Verification
uses the PUBLIC key, so a verifier needs no secret.

Key management (heartbeat profile): each principal's Ed25519 keypair is DERIVED
deterministically from one persisted master seed + the principal id, so identities and
signatures are stable across runs — a warm-started Weft still verifies, and a principal
minted in a prior run can still be verified this run. `public_key(pid)` exposes the
verify key. The production step beyond this profile is per-principal keys held in an OS
keystore with only public keys distributed to verifiers (true keyless-verifier
multi-party); the Principal/Keyring interface above this file does not change.
"""
import hashlib
import os
from dataclasses import dataclass

import nacl.signing
import nacl.exceptions


@dataclass(frozen=True)
class Principal:
    id: str          # public, stable identifier (= hash of name)
    name: str        # human-facing label
    kind: str        # "root" | "human" | "agent" | "executor" | "reckoner"


class Keyring:
    def __init__(self, seed: bytes | None = None):
        # One master seed; each principal's Ed25519 keypair is derived from it + its id,
        # so warm start (same seed) reproduces every key, and any past principal verifies.
        self.master = seed or os.urandom(32)
        self.principals: dict[str, Principal] = {}
        self._keys: dict[str, nacl.signing.SigningKey] = {}   # pid -> signing key (cache)
        # Keybook: foreign principals' PUBLIC keys learned from other peers (multi-party
        # trust). A pid in here is verified against the registered public key — NOT
        # re-derived from our master — so peers with DIFFERENT master seeds can verify
        # each other once they've exchanged keys. Fail closed: a foreign author we have
        # no key for does not verify.
        self.keybook: dict[str, nacl.signing.VerifyKey] = {}

    def mint(self, name: str, kind: str = "agent") -> Principal:
        pid = hashlib.blake2b(name.encode(), digest_size=8).hexdigest()
        p = Principal(pid, name, kind)
        self.principals[pid] = p
        return p

    # ── Key-based (self-certifying) identity ─────────────────────────────────────
    # `mint` sets pid = blake2b(NAME): two INDEPENDENT peers must coordinate distinct
    # names or their pids COLLIDE (same name → same pid, even with different master
    # seeds and different keys). `mint_keyed` inverts the dependency — it derives the
    # keypair FIRST and sets pid = blake2b(PUBLIC KEY). The id is then a COMMITMENT to
    # the key: globally unique with ZERO name coordination (two peers may mint the same
    # name and never collide) AND self-certifying — a verifier handed only the public
    # key can both verify signatures AND confirm pid == blake2b(pubkey). Purely
    # additive: named `mint` above is untouched (every existing check uses it).

    @staticmethod
    def keyed_pid(public_key) -> str:
        """The self-certifying principal id for an Ed25519 public key: blake2b(pubkey),
        8-byte hex — the same digest shape as a named pid. Accepts raw 32 bytes, a hex
        string, or a VerifyKey, so a verifier can recompute the id from whatever form
        the keybook handed it and confirm it commits to the key it was given."""
        if isinstance(public_key, nacl.signing.VerifyKey):
            raw = public_key.encode()
        elif isinstance(public_key, str):
            raw = bytes.fromhex(public_key)
        else:
            raw = bytes(public_key)
        return hashlib.blake2b(raw, digest_size=8).hexdigest()

    def mint_keyed(self, name: str, kind: str = "agent") -> Principal:
        """Mint a SELF-CERTIFYING principal: derive the keypair FIRST, then set
        pid = blake2b(public_key). Because the id commits to the key, two Keyrings with
        DIFFERENT master seeds may mint the SAME name without their pids colliding —
        identity is globally unique with no name coordination. The signing key is
        derived deterministically from (master, name), DOMAIN-SEPARATED from
        `_signing_key`'s (master, pid) derivation, and cached under the resulting pid so
        `sign`/`public_key`/`verify` work unchanged (they read the same `_keys` cache)
        and a warm start (same seed + name) reproduces the same pid. This is a new
        minting PATH only — it does not alter named `mint` or the `_signing_key`
        derivation used by every existing principal."""
        seed = hashlib.blake2b(self.master + name.encode(), digest_size=32,
                               person=b"decima:keyid").digest()
        sk = nacl.signing.SigningKey(seed)
        pid = self.keyed_pid(sk.verify_key)
        self._keys[pid] = sk                    # cache so _signing_key returns this key
        p = Principal(pid, name, kind)
        self.principals[pid] = p
        return p

    def verify_keyed(self, pid: str, message: str, sig: str, public_key: str) -> bool:
        """FAIL-CLOSED verification for a KEY-BASED principal. Two independent checks,
        BOTH required: (a) the presented public key self-certifies the claimed id —
        blake2b(public_key) == pid — so an event that claims a key-derived pid but
        carries a key that hashes elsewhere is REJECTED (a public key confers
        verifiability, never a free identity); and (b) the signature verifies under that
        public key. A verifier needs only the public key (from the keybook) — no secret,
        no shared master. Any mismatch / forgery / malformed input returns False, never
        raises."""
        try:
            if self.keyed_pid(public_key) != pid:
                return False                     # id is not a commitment to this key
            vk = nacl.signing.VerifyKey(bytes.fromhex(public_key))
            vk.verify(message.encode(), bytes.fromhex(sig))
            return True
        except (nacl.exceptions.BadSignatureError, ValueError, TypeError):
            return False

    def _signing_key(self, pid: str) -> nacl.signing.SigningKey:
        sk = self._keys.get(pid)
        if sk is None:
            # 32-byte Ed25519 seed, deterministic from (master, pid) — domain-separated.
            seed = hashlib.blake2b(self.master + pid.encode(), digest_size=32,
                                   person=b"decima:ed255").digest()
            sk = nacl.signing.SigningKey(seed)
            self._keys[pid] = sk
        return sk

    def sign(self, pid: str, message: str) -> str:
        """Ed25519-sign `message` with the principal's private key. Returns the 64-byte
        signature as hex."""
        return self._signing_key(pid).sign(message.encode()).signature.hex()

    def trust(self, pid: str, public_key_hex: str) -> None:
        """Register a FOREIGN principal's public (verify) key — learned from another
        peer (a keybook exchange). Afterward this keyring can verify that principal's
        signatures WITHOUT sharing its master seed. Registering a key confers no
        authority (that is still the capability layer's job) — it only lets us check
        that an event really came from that principal."""
        self.keybook[pid] = nacl.signing.VerifyKey(bytes.fromhex(public_key_hex))

    def _verify_key(self, pid: str) -> nacl.signing.VerifyKey:
        """The public key to verify `pid` with. A LOCALLY-minted principal always uses
        its own (master-derived) key — so a peer's own events never get shadowed by a
        same-named foreign key. A FOREIGN principal uses its keybook entry if we have
        one; otherwise we derive (which fails closed for an unknown author)."""
        if pid in self.principals:                    # our own — never shadowed
            return self._signing_key(pid).verify_key
        vk = self.keybook.get(pid)                    # foreign — learned public key
        return vk if vk is not None else self._signing_key(pid).verify_key

    def verify(self, pid: str, message: str, sig: str) -> bool:
        """Verify with the principal's PUBLIC key. For a FOREIGN principal we hold a
        keybook entry for, that entry is used (multi-party: no shared master needed);
        otherwise the key is re-derived from our master seed (a local principal, incl.
        one minted in a prior run). Any bad/forged/malformed/unknown signature returns
        False, never raises."""
        try:
            self._verify_key(pid).verify(message.encode(), bytes.fromhex(sig))
            return True
        except (nacl.exceptions.BadSignatureError, ValueError, TypeError):
            return False

    def public_key(self, pid: str) -> str:
        """The principal's Ed25519 public (verify) key, hex — what a verifier needs and
        all it needs (no secret). The seam for distributing public keys to peers."""
        return self._signing_key(pid).verify_key.encode().hex()

    def name_of(self, pid: str) -> str:
        p = self.principals.get(pid)
        return p.name if p else pid[:8]
