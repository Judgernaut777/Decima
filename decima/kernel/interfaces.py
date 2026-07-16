"""Kernel seam contracts — the typed Protocols the TCB is written against (DEC-010/011/013).

These are the interfaces that separate *policy* from *mechanism* at the kernel's edges, so
an alternative backend (a directory-backed key custodian, a different Weft store) or the
eventual Rust port can be swapped in behind a stable contract. They describe the surface
the extracted reference implementations already expose today; a conformance test
(`tests/kernel/test_interfaces.py`) asserts the real objects satisfy them, catching drift.

Scope note (0.3): the codec choice is separated from event semantics (a `CanonicalCodec`
is a value→bytes function, not tied to the JSON profile), and identity/storage sit behind
`Signer`/`Verifier`/`WeftStore`. Richer store methods the handoff envisions (get/contains/
frontier/iter) are provided by an adapter as the runtime needs them (Phase 4); this file
pins the surface that exists now.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CanonicalCodec(Protocol):
    """Deterministic value ↔ bytes + content addressing (Weft Protocol §1, Law 4).

    The heartbeat profile is sorted-key NFC JSON + BLAKE2b-128 with domain separation;
    the durable protocol swaps in CBOR + BLAKE3. Either satisfies this contract."""

    def canonical(self, payload: dict[str, Any]) -> bytes: ...

    def content_id(self, payload: dict[str, Any], kind: str = ...) -> str: ...

    def blob_id(self, data: bytes, kind: str = ...) -> str: ...


@runtime_checkable
class Signer(Protocol):
    """Signing over held keys (Law 4). The raw private key never leaves the custodian;
    a principal id addresses which key signs. Errors fail closed. (Key existence/custody
    lives on the underlying key store, not this signing seam.)"""

    def sign(self, pid: str, message: str) -> str: ...

    def public_key(self, pid: str) -> str: ...


@runtime_checkable
class Verifier(Protocol):
    """Signature verification, rotation-aware at the Weft. A foreign author with no known
    key does not verify (fail closed)."""

    def verify(self, pid: str, message: str, sig: str) -> bool: ...


@runtime_checkable
class WeftStore(Protocol):
    """The append-only, signed, content-addressed log (Law 1). No UPDATE, no DELETE — only
    append and the acceptance-validated ingest of foreign events; reads verify on the way
    out. `events()` yields in causal (seq) order; `ingest()` returns a status string."""

    def append(
        self,
        author_pid: str,
        verb: str,
        body: dict[str, Any],
        authorized: str | None = ...,
        parents: list[Any] | None = ...,
    ) -> object: ...

    def events(self, upto_seq: int | None = ..., from_seq: int | None = ...) -> object: ...

    def count(self) -> int: ...

    def ingest(self, row: object) -> str: ...
