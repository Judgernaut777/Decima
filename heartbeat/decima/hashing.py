"""Content addressing — Law 4: identity is content + cause.

An object's id IS the hash of its bytes. Same content, same id, everywhere,
forever. Dedup, provenance, and reproducibility all fall out of this.

Heartbeat profile (see heartbeat/PROFILE.md) of Weft Protocol v0.1 §1:
  - hash: BLAKE2b-128 (the durable protocol uses BLAKE3-256 — not in stdlib)
  - canonical bytes: sorted-key JSON in UTF-8 (the durable protocol uses
    deterministic CBOR with integer field numbers — not in stdlib)
  - domain separation: IMPLEMENTED — digest = HASH("decima:v0.1:" || kind
    || 0x00 || bytes), so the event-id space and cell-id space are disjoint.
Pure stdlib. No external crypto.
"""
import hashlib
import json
import unicodedata

_DOMAIN = b"decima:v0.1:"


def canonical(payload: dict) -> bytes:
    """Deterministic byte encoding so a payload's hash is stable.
    UTF-8, sorted keys, no whitespace. (No floats — see PROFILE.md.)"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _digest(kind: str, data: bytes) -> str:
    return hashlib.blake2b(_DOMAIN + kind.encode() + b"\x00" + data,
                           digest_size=16).hexdigest()


def content_id(payload: dict, kind: str = "cell") -> str:
    """The content-address of a structured payload. `kind` domain-separates the
    id space ("event" for Weft events, "cell" for everything in the Weave)."""
    return _digest(kind, canonical(payload))


def blob_id(data: bytes, kind: str = "blob") -> str:
    """The content-address of raw bytes (an image, a file, an impl)."""
    return _digest(kind, data)


def nfc(text: str) -> str:
    """NFC-normalize human text before it enters the Weft (protocol §1)."""
    return unicodedata.normalize("NFC", text)
