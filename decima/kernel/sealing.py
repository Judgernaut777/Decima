"""Encrypted-at-rest payloads + key destruction — REAL byte-erasure for REDACT (FOLD §10.3).

Until now REDACT was erasure at the FOLD only: `weave._redact` wiped the payload out of
every projection, but the plaintext still sat in `events.payload` in the SQLite file
forever. FOLD §10.3 names the shipping mechanism explicitly:

    - Encrypt blobs with per-object or per-erasure-domain data keys.
    - Destroy the data key first (cryptographic erasure).
    - Preserve a minimal signed event skeleton unless law/policy requires its removal.

This module is that mechanism, and it is built so THE EVENT ID AND SIGNATURE ARE
UNAFFECTED BY ERASURE:

  * a redactable ASSERT stores an ENVELOPE (`{"$sealed": {...}}`) in place of its
    `content`, and the envelope — the ciphertext — is what the event id is computed over
    and what the author signs. The plaintext is NEVER canonicalized into a signed struct,
    never written to the log, and never leaves this module except to the fold;
  * erasure destroys the DATA KEY, which lives OUTSIDE the log in a `PayloadVault`. The
    stored bytes are untouched, so `content_id(payload) == eid` still recomputes and the
    Ed25519 signature over `eid` still verifies — verify-on-read passes for a redacted
    event exactly as before (tamper-evidence preserved, FOLD §10 "the event skeleton
    remains"). Only the *readability* of the payload is destroyed.

Deliberate choices:

  * NO plaintext hash / commitment is stored. FOLD §10.3 warns that "content addressing
    creates a privacy trap if raw hashes are globally guessable" — a low-entropy payload
    ("dx: HIV+") would be recoverable from its digest by guessing. The ciphertext IS the
    commitment, and Poly1305 authenticates it on unseal.
  * ONE FRESH KEY PER SEALED PAYLOAD (`os.urandom(32)`), so the ciphertext of the same
    plaintext differs every time: there is no cross-payload — hence no cross-realm —
    deduplication, which §10.3 forbids by default. A caller MAY pass an explicit key
    (deterministic tests, or a shared per-erasure-domain key); identical (key, plaintext)
    yields an identical envelope and therefore a SHARED erasure domain, by construction.
  * The nonce is DERIVED, not stored: `blake3("decima:v0.1:seal-nonce" || key)[:24]`. A
    data key is single-use, so a per-key constant nonce cannot be reused across
    plaintexts; deriving it keeps the envelope smaller and fully deterministic given
    (key, plaintext), with no randomness in the hashed bytes beyond the key itself.
  * Envelope fields are JSON-safe ASCII (base32-lower, the protocol's identifier
    encoding) and INTs — the Weft stores payloads as `json.dumps(...)` text, which cannot
    hold raw bytes, and no float ever enters signed content.

Determinism note: sealing a payload with a fresh random key makes THAT APPEND's event id
unreproducible across runs (the key is new secret material — the same is true of any
nonce). Replay determinism is untouched: the fold reads the stored ciphertext, so the
same log always folds to the same `state_root`. Pass `key=` for a byte-reproducible seal.

Third-party surface: `nacl` (XSalsa20-Poly1305 SecretBox), `blake3`, `cbor2` — all
already-declared kernel seams (tests/architecture ALLOWED_THIRD_PARTY).
"""

from __future__ import annotations

import base64
import os
from typing import Any

import blake3
import cbor2
import nacl.exceptions
import nacl.secret

from decima.kernel.hashing import blob_id, canonical

# The envelope marker. A body whose `content` is a dict with this single key holds
# ciphertext, not plaintext.
SEALED = "$sealed"
# Envelope algorithm tag: XSalsa20-Poly1305 (NaCl SecretBox) over deterministic-CBOR
# plaintext, with a BLAKE3-derived per-key nonce. Versioned so a future scheme is a
# new tag rather than a silent reinterpretation of old bytes.
ALG = "xsalsa20poly1305/cbor/blake3-nonce/v1"
_NONCE_DOMAIN = b"decima:v0.1:seal-nonce\x00"
KEY_BYTES = 32


class SealError(Exception):
    """A sealed payload could not be opened: malformed envelope, unknown algorithm, or a
    failed Poly1305 authentication (wrong key / corrupted ciphertext). Always fail closed
    — never fall back to treating ciphertext as content."""


def new_key() -> bytes:
    """A fresh 32-byte data key. One per sealed payload (no dedup across payloads)."""
    return os.urandom(KEY_BYTES)


def _nonce(key: bytes) -> bytes:
    """The per-key nonce, DERIVED not stored. A data key is single-use, so a constant
    per-key nonce cannot be reused across plaintexts."""
    return bytes(
        blake3.blake3(_NONCE_DOMAIN + key).digest(length=nacl.secret.SecretBox.NONCE_SIZE)
    )


def _b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def _unb32(text: str) -> bytes:
    padded = text.upper() + "=" * (-len(text) % 8)
    return base64.b32decode(padded.encode("ascii"))


def seal(plaintext: dict[str, Any], key: bytes | None = None) -> tuple[dict[str, Any], bytes]:
    """Seal a payload dict. Returns `(envelope, key)` — the envelope is what goes on the
    log (and into the event id), the key is what the caller must hand to a vault and
    later DESTROY to erase the payload. Deterministic given (plaintext, key)."""
    if key is None:
        key = new_key()
    if len(key) != KEY_BYTES:
        raise SealError(f"data key must be {KEY_BYTES} bytes")
    raw = canonical(plaintext)  # deterministic CBOR + NFC — the protocol's canonical bytes
    ct = nacl.secret.SecretBox(key).encrypt(raw, _nonce(key)).ciphertext
    env: dict[str, Any] = {
        SEALED: {
            "v": 1,
            "alg": ALG,
            "ct": _b32(ct),
            # The vault handle is DERIVED from the ciphertext (`blob_id`), so it is
            # content-addressed like everything else and needs no random identifier.
            "key_ref": blob_id(ct),
        }
    }
    return env, key


def _envelope(obj: object) -> dict[str, Any] | None:
    """The inner envelope record iff `obj` is a well-formed sealed envelope (a dict whose
    ONLY key is the marker, holding a dict) — else None. One narrowing point, so nothing
    downstream has to re-check the shape."""
    if not isinstance(obj, dict) or set(obj) != {SEALED}:
        return None
    inner = obj[SEALED]
    return inner if isinstance(inner, dict) else None


def is_sealed(obj: object) -> bool:
    """True iff `obj` is a sealed envelope, i.e. ciphertext rather than content."""
    return _envelope(obj) is not None


def key_ref_of(env: object) -> str | None:
    """The vault handle of a sealed envelope, or None if it is not one / is malformed."""
    e = _envelope(env)
    if e is None:
        return None
    ref = e.get("key_ref")
    return ref if isinstance(ref, str) else None


def unseal(env: object, key: bytes) -> dict[str, Any]:
    """Open a sealed envelope with its data key. Fails closed (SealError) on a malformed
    envelope, an unknown algorithm, or a failed authentication — a wrong key or a flipped
    ciphertext byte NEVER yields content."""
    e = _envelope(env)
    if e is None:
        raise SealError("not a sealed envelope")
    if e.get("alg") != ALG or e.get("v") != 1:
        raise SealError(f"unsupported sealed envelope {e.get('alg')!r} v{e.get('v')!r}")
    ct_text = e.get("ct")
    if not isinstance(ct_text, str):
        raise SealError("sealed envelope has no ciphertext")
    try:
        raw = nacl.secret.SecretBox(key).decrypt(_unb32(ct_text), _nonce(key))
    except (nacl.exceptions.CryptoError, ValueError, TypeError) as exc:
        raise SealError("sealed payload failed authentication (wrong key or tampered)") from exc
    opened = cbor2.loads(raw)
    if not isinstance(opened, dict):
        raise SealError("sealed payload did not decode to a content dict")
    return opened


class PayloadVault:
    """Custody of PAYLOAD data keys — the erasure mechanism, kept strictly OUTSIDE the
    append-only log so destroying a key is possible at all (the log has no DELETE).

    Same discipline as `keystore.KeyStore`: the vault holds secret bytes and hands them
    out only to the kernel's read path. `destroy` is the whole point — it is the one
    irreversible operation in Decima, and it is irreversible BY DESIGN (FOLD §10.3
    "destroy the data key first")."""

    def put(self, ref: str, key: bytes) -> None:
        raise NotImplementedError

    def get(self, ref: str) -> bytes | None:
        """The data key for `ref`, or None if it was destroyed / never held."""
        raise NotImplementedError

    def destroy(self, ref: str) -> bool:
        """Destroy the data key. True iff a key was actually destroyed (idempotent:
        destroying an already-erased payload is a no-op returning False)."""
        raise NotImplementedError


class MemoryPayloadVault(PayloadVault):
    """In-process vault (tests, ephemeral realms). Destruction overwrites the key
    material in place before dropping the reference."""

    def __init__(self) -> None:
        self._keys: dict[str, bytearray] = {}

    def put(self, ref: str, key: bytes) -> None:
        self._keys[ref] = bytearray(key)

    def get(self, ref: str) -> bytes | None:
        k = self._keys.get(ref)
        return bytes(k) if k is not None else None

    def destroy(self, ref: str) -> bool:
        k = self._keys.pop(ref, None)
        if k is None:
            return False
        for i in range(len(k)):
            k[i] = 0
        return True


class DirectoryPayloadVault(PayloadVault):
    """Durable vault — one 0600 key file per payload under a 0700 directory (the same
    shape as `keystore.DirectoryKeyStore`, and it belongs in the install's `keys/` tree,
    which `data_layout.EXCLUDED_FROM_BACKUP` keeps out of every backup and support
    bundle. That exclusion is LOAD-BEARING for erasure: if data keys rode along in
    backups, restoring an old backup would resurrect a payload the owner erased, and
    REDACT would be a lie. The cost is deliberate — a sealed payload is unreadable from a
    restored backup unless the operator separately preserved its keys.)

    `destroy` overwrites the key bytes in place, flushes to the device, then unlinks —
    best-effort physical erasure on a conventional filesystem (a copy-on-write or
    journaling FS may retain the old block; a full-disk-encryption realm is the standard
    mitigation and the reason erasure is layered, not single-shot)."""

    def __init__(self, path: str) -> None:
        self._dir = path
        os.makedirs(path, mode=0o700, exist_ok=True)

    def _path(self, ref: str) -> str:
        if not ref or "/" in ref or ref.startswith("."):
            raise SealError(f"unsafe key ref {ref!r}")  # never escape the vault directory
        return os.path.join(self._dir, ref + ".key")

    def put(self, ref: str, key: bytes) -> None:
        fd = os.open(self._path(ref), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)

    def get(self, ref: str) -> bytes | None:
        path = self._path(ref)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as fh:
            return fh.read()

    def destroy(self, ref: str) -> bool:
        path = self._path(ref)
        if not os.path.exists(path):
            return False
        size = os.path.getsize(path)
        fd = os.open(path, os.O_WRONLY)
        try:
            os.write(fd, b"\x00" * size)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.unlink(path)
        return True
