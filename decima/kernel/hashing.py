"""Content addressing — Law 4: identity is content + cause.

An object's id IS the hash of its bytes. Same content, same id, everywhere,
forever. Dedup, provenance, and reproducibility all fall out of this.

Durable Weft Protocol v0.1 §1 (adopt-durable-protocol re-freeze, waves 1 + 3):
  - hash: BLAKE3-256 (was BLAKE2b-128 in the stdlib profile) — 256-bit digest,
    so ids carry 128-bit collision resistance, matching Ed25519's security level. [D1]
  - identifiers: base32-lower, kind-prefixed — `evt_`/`cell_`/`bdy_`/`blob_`/`prn_`/
    `cap_` (was an undifferentiated hex digest). An id now names its own kind, and the
    text is the shorter, case-insensitive base32 form. [D3]
  - canonical bytes: sorted-key JSON in UTF-8 (the durable protocol's deterministic
    CBOR with integer field numbers is the remaining re-freeze wave, D2).
  - domain separation: IMPLEMENTED — digest = BLAKE3-256("decima:v0.1:" || kind
    || 0x00 || bytes), so the event-id space and cell-id space are disjoint.
Takes one dependency (BLAKE3) — see docs/design/adopt-durable-protocol.md, §7 D1.
"""

from __future__ import annotations

import base64
import json
import unicodedata
from typing import Any

import blake3

_DOMAIN = b"decima:v0.1:"

# Kind → identifier text prefix (Weft Protocol v0.1 §1). An id is `<prefix>_<base32>`, so
# it is self-describing. Unmapped kinds fall back to the kind string itself as the prefix.
_PREFIX = {
    "event": "evt",
    "cell": "cell",
    "body": "bdy",
    "blob": "blob",
    "principal": "prn",
    "capability": "cap",
}


def _b32(raw: bytes) -> str:
    """base32-lower, unpadded — the durable protocol's identifier encoding."""
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def nfc_deep(obj: object) -> object:
    """Recursively NFC-normalize EVERY string — dict keys and values, list items,
    nested arbitrarily deep — so canonical bytes are Unicode-normalized throughout, not
    just at the `say` boundary (Weft Protocol §1: text is UTF-8, NFC). Non-string
    scalars (int/bool/None) pass through untouched; a tuple becomes a list (JSON has no
    tuples — byte-identical to the previous encoding). Idempotent: already-NFC content
    (all ASCII, and anything that already came through `nfc()`) is returned unchanged, so
    this pins the normalization form WITHOUT changing any existing id."""
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, dict):
        return {nfc_deep(k): nfc_deep(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [nfc_deep(v) for v in obj]
    return obj


def canonical(payload: dict[str, Any]) -> bytes:
    """Deterministic byte encoding so a payload's hash is stable.
    UTF-8, sorted keys, no whitespace, and NFC-normalized text throughout (every
    nested string). (No floats — see PROFILE.md.)"""
    return json.dumps(
        nfc_deep(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(kind: str, data: bytes) -> str:
    raw = blake3.blake3(_DOMAIN + kind.encode() + b"\x00" + data).digest()
    return f"{_PREFIX.get(kind, kind)}_{_b32(raw)}"


def content_id(payload: dict[str, Any], kind: str = "cell") -> str:
    """The content-address of a structured payload. `kind` domain-separates the
    id space ("event" for Weft events, "cell" for everything in the Weave)."""
    return _digest(kind, canonical(payload))


def blob_id(data: bytes, kind: str = "blob") -> str:
    """The content-address of raw bytes (an image, a file, an impl)."""
    return _digest(kind, data)


def nfc(text: str) -> str:
    """NFC-normalize human text before it enters the Weft (protocol §1)."""
    return unicodedata.normalize("NFC", text)
