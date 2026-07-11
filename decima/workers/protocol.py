"""The versioned local worker IPC (handoff §5).

A worker call is a request/response pair of plain data, serialized as canonical JSON.
The protocol is VERSIONED: a decoder that meets an unknown `protocol_version` fails
closed rather than guessing a wire shape it does not understand.

The request carries everything the worker needs to run ONE bounded effect and nothing
more: the invocation/job identity, the effect name, the `implementation_digest` that
binds which code may run (execution.py enforces the binding), the effect arguments, the
runtime `lease` (bounded authority + window), and a `capability_proof` (the token that
the trusted parent already authorized — its mere presence is what forbids an effect with
no authority at all). The response reports the outcome the worker observed:
SUCCEEDED / FAILED / UNKNOWN, output references, receipt data, and diagnostics.

A hard rule (handoff §5): a raw private signing key NEVER crosses this boundary. The
encoders scan every field for private-key-shaped material and refuse to serialize it —
a worker is handed proofs and digests, never the authority to mint new signatures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

# Response outcome codes (mirrors the executor's EffectReceipt.status, WEFT §8.2).
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"
_STATUSES = frozenset({SUCCEEDED, FAILED, UNKNOWN})

# Substrings that mark a field as raw private-key material. Anything carrying one of
# these keys (at any depth) is refused at the serialization boundary — a worker is
# NEVER handed a signing key. Matching is case-insensitive on the key name.
_FORBIDDEN_KEY_MARKERS = (
    "private_key",
    "privatekey",
    "privkey",
    "secret_key",
    "secretkey",
    "signing_key",
    "signingkey",
    "secret_seed",
    "key_seed",
    "seed_bytes",
)


class ProtocolError(Exception):
    """The wire form was malformed, carried an unknown protocol version, or tried to
    smuggle private-key material across the boundary. Fail closed — nothing is
    serialized or dispatched."""


def _assert_no_private_keys(where: str, value: Any) -> None:
    """Refuse (ProtocolError) if `value` contains a private-key-shaped key at any depth.

    This is the structural guarantee that a worker never receives raw signing keys: the
    check runs on both encode and decode, so neither a producer nor a consumer can move
    key material through the IPC."""
    if isinstance(value, dict):
        for key, sub in value.items():
            low = str(key).lower()
            if any(marker in low for marker in _FORBIDDEN_KEY_MARKERS):
                raise ProtocolError(
                    f"refusing to transfer private-key material: field {where}.{key!r} "
                    "looks like a raw signing key — workers receive proofs, never keys"
                )
            _assert_no_private_keys(f"{where}.{key}", sub)
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            _assert_no_private_keys(f"{where}[{i}]", sub)


def _require_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{name} must be a non-empty str, got {value!r}")
    return value


@dataclass(frozen=True)
class WorkerRequest:
    """A request to run ONE bounded effect in an isolated worker."""

    invocation_id: str
    job_id: str
    effect: str
    implementation_digest: str
    arguments: dict[str, Any] = field(default_factory=dict)
    lease: dict[str, Any] = field(default_factory=dict)
    capability_proof: dict[str, Any] = field(default_factory=dict)
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_str("invocation_id", self.invocation_id)
        _require_str("job_id", self.job_id)
        _require_str("effect", self.effect)
        _require_str("implementation_digest", self.implementation_digest)
        if not isinstance(self.arguments, dict):
            raise ProtocolError("arguments must be a dict")
        if not isinstance(self.lease, dict):
            raise ProtocolError("lease must be a dict")
        if not isinstance(self.capability_proof, dict):
            raise ProtocolError("capability_proof must be a dict")
        if not isinstance(self.protocol_version, int) or isinstance(self.protocol_version, bool):
            raise ProtocolError("protocol_version must be an int")


@dataclass(frozen=True)
class WorkerResponse:
    """The outcome the worker observed for one dispatched effect."""

    invocation_id: str
    status: str
    output_refs: list[str] = field(default_factory=list)
    receipt_data: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_str("invocation_id", self.invocation_id)
        if self.status not in _STATUSES:
            raise ProtocolError(f"status must be one of {sorted(_STATUSES)}, got {self.status!r}")
        if not isinstance(self.output_refs, list):
            raise ProtocolError("output_refs must be a list")
        if not isinstance(self.receipt_data, dict):
            raise ProtocolError("receipt_data must be a dict")
        if not isinstance(self.diagnostics, dict):
            raise ProtocolError("diagnostics must be a dict")


def encode_request(request: WorkerRequest) -> str:
    """Serialize a request to canonical JSON, refusing any private-key material."""
    body = {
        "protocol_version": request.protocol_version,
        "invocation_id": request.invocation_id,
        "job_id": request.job_id,
        "effect": request.effect,
        "implementation_digest": request.implementation_digest,
        "arguments": request.arguments,
        "lease": request.lease,
        "capability_proof": request.capability_proof,
    }
    _assert_no_private_keys("request", body)
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def decode_request(wire: str) -> WorkerRequest:
    """Parse a request, failing closed on an unknown protocol version or key material."""
    try:
        body = json.loads(wire)
    except ValueError as exc:
        raise ProtocolError(f"request is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ProtocolError("request must be a JSON object")
    version = body.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol_version {version!r} (this worker speaks {PROTOCOL_VERSION})"
        )
    _assert_no_private_keys("request", body)
    return WorkerRequest(
        invocation_id=body.get("invocation_id", ""),
        job_id=body.get("job_id", ""),
        effect=body.get("effect", ""),
        implementation_digest=body.get("implementation_digest", ""),
        arguments=body.get("arguments", {}),
        lease=body.get("lease", {}),
        capability_proof=body.get("capability_proof", {}),
        protocol_version=version,
    )


def encode_response(response: WorkerResponse) -> str:
    """Serialize a response to canonical JSON, refusing any private-key material."""
    body = {
        "protocol_version": response.protocol_version,
        "invocation_id": response.invocation_id,
        "status": response.status,
        "output_refs": response.output_refs,
        "receipt_data": response.receipt_data,
        "diagnostics": response.diagnostics,
    }
    _assert_no_private_keys("response", body)
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def decode_response(wire: str) -> WorkerResponse:
    """Parse a response, failing closed on an unknown protocol version."""
    try:
        body = json.loads(wire)
    except ValueError as exc:
        raise ProtocolError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ProtocolError("response must be a JSON object")
    version = body.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol_version {version!r} (this worker speaks {PROTOCOL_VERSION})"
        )
    _assert_no_private_keys("response", body)
    return WorkerResponse(
        invocation_id=body.get("invocation_id", ""),
        status=body.get("status", ""),
        output_refs=body.get("output_refs", []),
        receipt_data=body.get("receipt_data", {}),
        diagnostics=body.get("diagnostics", {}),
        protocol_version=version,
    )
