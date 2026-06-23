"""Principals and signing.

DEV-GRADE signing: a symmetric HMAC(blake2b) stand-in for ed25519. Each principal
has a secret derived from a persisted master seed, so identities and signatures
are stable across runs (a warm-started Weft still verifies). This is the SEAM for
production: swap for asymmetric ed25519 keypairs in an OS keystore — verifiers
then need no secrets. The Principal abstraction above this file does not change.

Why it matters at heartbeat stage: every Event is signed by its author, so the
Weft is tamper-evident AND each agent proves possession of its own key — knowing
a public Cell id buys nothing.
"""
import hashlib
import hmac
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    id: str          # public, stable identifier (= hash of name)
    name: str        # human-facing label
    kind: str        # "root" | "human" | "agent" | "executor" | "reckoner"


class Keyring:
    def __init__(self, seed: bytes | None = None):
        # One master seed; each principal's secret is derived from it + its id.
        self.master = seed or os.urandom(32)
        self.principals: dict[str, Principal] = {}

    def mint(self, name: str, kind: str = "agent") -> Principal:
        pid = hashlib.blake2b(name.encode(), digest_size=8).hexdigest()
        p = Principal(pid, name, kind)
        self.principals[pid] = p
        return p

    def _secret(self, pid: str) -> bytes:
        return hmac.new(self.master, pid.encode(), hashlib.blake2b).digest()

    def sign(self, pid: str, message: str) -> str:
        return hmac.new(self._secret(pid), message.encode(), hashlib.blake2b).hexdigest()

    def verify(self, pid: str, message: str, sig: str) -> bool:
        # Works for any principal under this master seed — even one minted in a
        # prior run and not re-minted this session.
        return hmac.compare_digest(self.sign(pid, message), sig)

    def name_of(self, pid: str) -> str:
        p = self.principals.get(pid)
        return p.name if p else pid[:8]
