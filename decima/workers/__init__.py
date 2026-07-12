"""Decima workers — isolated effect execution (Phase 5, handoff §5).

Effect execution NEVER inherits the parent process authority (invariant 7): a bounded
effect runs in a fresh child process with a scrubbed environment, a dedicated tmp
working directory, resource limits, no inherited file descriptors, and — on this
aarch64 Linux box — real Linux namespace isolation (a user + mount namespace with a
chroot into the scratch jail, and, for a network-denied worker, a network namespace).
The child receives NO signing keys, NO home directory, and NO parent secrets.

This package is built ON `decima.kernel` (for content-address digests) and
`decima.runtime` (for the lease Cell shape). It holds no trusted-core logic: the
kernel authorizes (models propose, deterministic code authorizes), and only an
already-authorized, digest-bound implementation reaches a worker. The worker's own
job is CONTAINMENT and HONEST provenance — never inventing authority, never claiming
an isolation layer that did not actually engage.

Public surface:
  - protocol.py   — the versioned local worker IPC (request/response), which refuses
                    to serialize raw private key material.
  - lease.py      — validate a runtime lease before executing; expired or replayed
                    leases fail closed.
  - execution.py  — run one bounded effect in the isolated child; digest binding, the
                    layered confinement, and an honest in-child-verified manifest.
  - profiles.py   — worker profiles (PURE at minimum; WORKSPACE / PROVIDER as
                    structure).
"""

from __future__ import annotations

from decima.workers.execution import (
    CONTAINMENT_MATRIX_VERSION,
    DEFAULT_LIMITS,
    DigestMismatch,
    IsolationError,
    WorkerError,
    WorkerTimeout,
    compute_digest,
    containment_report,
    run_worker,
)
from decima.workers.lease import LeaseError, LeaseGuard, validate_lease
from decima.workers.profiles import PROVIDER, PURE, WORKSPACE, WorkerProfile
from decima.workers.protocol import (
    FAILED,
    PROTOCOL_VERSION,
    SUCCEEDED,
    UNKNOWN,
    ProtocolError,
    WorkerRequest,
    WorkerResponse,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)

__all__ = [
    "CONTAINMENT_MATRIX_VERSION",
    "DEFAULT_LIMITS",
    "DigestMismatch",
    "FAILED",
    "IsolationError",
    "LeaseError",
    "LeaseGuard",
    "PROTOCOL_VERSION",
    "PROVIDER",
    "PURE",
    "ProtocolError",
    "SUCCEEDED",
    "UNKNOWN",
    "WORKSPACE",
    "WorkerError",
    "WorkerProfile",
    "WorkerRequest",
    "WorkerResponse",
    "WorkerTimeout",
    "compute_digest",
    "containment_report",
    "decode_request",
    "decode_response",
    "encode_request",
    "encode_response",
    "run_worker",
    "validate_lease",
]
