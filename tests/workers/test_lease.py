"""Lease validation: expired, replayed, malformed, or mis-bound leases fail closed."""

from __future__ import annotations

import os
import tempfile

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.workers.lease import LeaseError, LeaseGuard, validate_lease


def _real_lease() -> dict:
    """A lease built by the actual runtime cells API, so the test tracks its real shape."""
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    weft = Weft(db, kr)
    plan = cells.create_plan(weft, author, objective="o", creator_principal=author)
    step = cells.create_step(weft, author, plan_id=plan, description="A")
    lid = cells.create_lease(
        weft,
        author,
        step_id=step,
        worker=author,
        capability_ids=[],
        issued_frontier=5,
        expiry=15,
        attempt=1,
        idempotency_key="idem-A",
    )
    lease = Weave.fold(weft).get(lid).content
    return dict(lease)


def test_valid_lease_inside_window_passes():
    lease = _real_lease()
    assert validate_lease(lease, now=10) is lease


def test_expired_lease_fails_closed():
    lease = _real_lease()  # expiry=15
    with pytest.raises(LeaseError, match="expired"):
        validate_lease(lease, now=16)


def test_not_yet_valid_lease_fails_closed():
    lease = _real_lease()  # issued_frontier=5
    with pytest.raises(LeaseError, match="not yet valid"):
        validate_lease(lease, now=4)


def test_wrong_step_binding_fails_closed():
    lease = _real_lease()
    with pytest.raises(LeaseError, match="bound to step"):
        validate_lease(lease, now=10, expected_step_id="some-other-step")


def test_float_clock_is_rejected_determinism():
    lease = _real_lease()
    lease["expiry"] = 15.0  # a float clock would break determinism (invariant 6)
    with pytest.raises(LeaseError, match="ints, not floats"):
        validate_lease(lease, now=10)


def test_replayed_lease_fails_closed_on_second_use():
    lease = _real_lease()
    guard = LeaseGuard()
    guard.consume(lease, now=10)  # first use ok
    assert guard.consumed(lease)
    with pytest.raises(LeaseError, match="replayed lease"):
        guard.consume(lease, now=11)  # same lease again → refused


def test_distinct_attempts_are_not_replays():
    lease1 = _real_lease()
    lease2 = dict(lease1)
    lease2["attempt"] = 2  # a genuinely new attempt mints a distinct lease
    guard = LeaseGuard()
    guard.consume(lease1, now=10)
    guard.consume(lease2, now=10)  # different (idem, attempt) — allowed
