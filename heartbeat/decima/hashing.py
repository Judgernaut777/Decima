"""Content addressing — Law 4: identity is content + cause.

An object's id IS the hash of its bytes. Same content, same id, everywhere,
forever. Dedup, provenance, and reproducibility all fall out of this.
Pure stdlib (blake2b). No external crypto.
"""
import hashlib
import json


def canonical(payload: dict) -> bytes:
    """Deterministic byte encoding of a payload so its hash is stable."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def content_id(payload: dict) -> str:
    """The content-address of a structured payload (an Event's hashed body)."""
    return hashlib.blake2b(canonical(payload), digest_size=16).hexdigest()


def blob_id(data: bytes) -> str:
    """The content-address of raw bytes (an image, a file, an impl)."""
    return hashlib.blake2b(data, digest_size=16).hexdigest()
