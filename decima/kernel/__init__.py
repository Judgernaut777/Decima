"""The Decima kernel — the trusted computing base (LOOM).

An independently-testable extraction of the reference kernel: canonical encoding,
content addressing, identity/signatures, the append-only signed Weft, the deterministic
fold (Weave), the capability + authorization model, Morta approvals, checkpoints, and the
Law-5 context fold. It verifies, authorizes, folds, and appends — and executes nothing
untrusted (effect execution lives in isolated workers, not here).

This package imports no network, subprocess, provider, MCP, or web-framework code; the
only third-party dependencies are real Ed25519 (`nacl`) behind the signing seam and the
SQLite storage backend behind the Weft store. `tests/architecture/` enforces that.

Extraction fidelity: modules were copied from the reference (`heartbeat/decima/`) with
only their intra-package import paths rewritten — semantics are unchanged and proven
equal to the reference on golden fixtures in `protocol/fixtures/` (see `tests/kernel/`).
"""

from decima.kernel import (  # noqa: F401  (re-export the kernel surface)
    authorization,
    capability,
    checkpoints,
    context_fold,
    crypto,
    hashing,
    inbox,
    keystore,
    lifecycle,
    model,
    receipts,
    rotation,
    snapshot,
    verifier,
    weave,
    weft,
)

__all__ = [
    "hashing",
    "model",
    "crypto",
    "keystore",
    "verifier",
    "rotation",
    "weft",
    "weave",
    "capability",
    "context_fold",
    "snapshot",
    "inbox",
    "receipts",
    "authorization",
    "lifecycle",
    "checkpoints",
]
