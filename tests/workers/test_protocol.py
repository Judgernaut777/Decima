"""The worker IPC is versioned and refuses to carry private keys."""

from __future__ import annotations

from typing import Any

import pytest

from decima.workers import protocol
from decima.workers.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    WorkerRequest,
    WorkerResponse,
)


def _lease() -> dict:
    return {
        "step_id": "s1",
        "worker": "w1",
        "capability_ids": ["c1"],
        "issued_frontier": 0,
        "expiry": 100,
        "attempt": 1,
        "idempotency_key": "idem-1",
    }


def _request(**kw: Any) -> WorkerRequest:
    base: dict[str, Any] = dict(
        invocation_id="inv-1",
        job_id="job-1",
        effect="pure_compute",
        implementation_digest="deadbeef",
        arguments={"x": 1},
        lease=_lease(),
        capability_proof={"grant_id": "g1"},
    )
    base.update(kw)
    return WorkerRequest(**base)


def test_request_round_trips_through_the_wire():
    req = _request()
    back = protocol.decode_request(protocol.encode_request(req))
    assert back == req


def test_response_round_trips_through_the_wire():
    resp = WorkerResponse(
        invocation_id="inv-1",
        status=protocol.SUCCEEDED,
        output_refs=["cell-a"],
        receipt_data={"output": 2},
        diagnostics={"isolation": {"engaged": True}},
    )
    back = protocol.decode_response(protocol.encode_response(resp))
    assert back == resp


def test_decode_rejects_unknown_protocol_version():
    wire = protocol.encode_request(_request())
    bumped = wire.replace(
        f'"protocol_version":{PROTOCOL_VERSION}', f'"protocol_version":{PROTOCOL_VERSION + 1}'
    )
    with pytest.raises(ProtocolError, match="unsupported protocol_version"):
        protocol.decode_request(bumped)


def test_encode_refuses_private_key_material_in_capability_proof():
    req = _request(capability_proof={"grant_id": "g1", "signing_key": "AAAA...secret"})
    with pytest.raises(ProtocolError, match="private-key material"):
        protocol.encode_request(req)


def test_encode_refuses_nested_private_key_material():
    req = _request(arguments={"payload": {"nested": {"private_key": "zzz"}}})
    with pytest.raises(ProtocolError, match="private-key material"):
        protocol.encode_request(req)


def test_decode_refuses_smuggled_private_key():
    # A hand-crafted wire form that tries to slip a key past a naive consumer.
    wire = (
        f'{{"protocol_version":{PROTOCOL_VERSION},"invocation_id":"i","job_id":"j",'
        '"effect":"e","implementation_digest":"d","arguments":{},'
        '"lease":{"secret_seed":"..."},"capability_proof":{"g":1}}'
    )
    with pytest.raises(ProtocolError, match="private-key material"):
        protocol.decode_request(wire)


def test_request_rejects_empty_required_fields():
    with pytest.raises(ProtocolError):
        _request(effect="")


def test_response_rejects_unknown_status():
    with pytest.raises(ProtocolError, match="status must be one of"):
        WorkerResponse(invocation_id="i", status="MAYBE")
