"""Principals and signing.

DEV-GRADE signing: a symmetric HMAC(blake2b) stand-in for ed25519. The kernel
holds every principal's secret in one in-process Keyring, so it can both sign
and verify. This is the SEAM for production: swap `sign`/`verify` for asymmetric
ed25519 so verifiers never need secrets. The Principal abstraction stays
identical — nothing above this file changes.

Why it still matters at heartbeat stage: every Event is signed by its author,
so the Weft is tamper-evident. Flip a byte in the log and verification fails.
"""
import hashlib
import hmac
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    id: str          # public, stable identifier
    name: str        # human-facing label
    kind: str        # "root" | "human" | "agent" | "executor" | "reckoner"


class Keyring:
    def __init__(self):
        self._secrets: dict[str, bytes] = {}
        self.principals: dict[str, Principal] = {}

    def mint(self, name: str, kind: str = "agent") -> Principal:
        secret = os.urandom(32)
        pid = hashlib.blake2b(name.encode() + secret, digest_size=8).hexdigest()
        self._secrets[pid] = secret
        p = Principal(pid, name, kind)
        self.principals[pid] = p
        return p

    def sign(self, pid: str, message: str) -> str:
        return hmac.new(self._secrets[pid], message.encode(), hashlib.blake2b).hexdigest()

    def verify(self, pid: str, message: str, sig: str) -> bool:
        if pid not in self._secrets:
            return False
        return hmac.compare_digest(self.sign(pid, message), sig)

    def name_of(self, pid: str) -> str:
        p = self.principals.get(pid)
        return p.name if p else pid[:8]
