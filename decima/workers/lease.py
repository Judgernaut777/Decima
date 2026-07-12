"""Lease validation — an effect runs only under a fresh, unexpired, unreplayed lease.

A lease (decima.runtime.cells, DEC-042) is the bounded authority + window for ONE attempt
of a step. Before a worker executes anything, the lease is validated against the current
logical frontier `now`:

  - shape: the runtime lease fields must be present and INTEGER where they are clocks
    (issued_frontier / expiry / attempt) — no floats, no bools masquerading as ints
    (invariant 6: determinism, ints-not-floats);
  - not yet valid: `now < issued_frontier` fails closed;
  - EXPIRED: `now > expiry` fails closed — a stale lease is never honored;
  - REPLAYED: a `LeaseGuard` remembers each (idempotency_key, attempt) it has consumed;
    presenting the same lease twice fails closed on the second use.

Everything here is pure and deterministic — logical time only, never a wall clock.
"""

from __future__ import annotations

from typing import Any

_INT_FIELDS = ("issued_frontier", "expiry", "attempt")
_REQUIRED_FIELDS = ("step_id", "worker", "issued_frontier", "expiry", "attempt", "idempotency_key")


class LeaseError(Exception):
    """The lease was malformed, expired, not yet valid, replayed, or bound to a different
    step than the one being executed. Fail closed — the effect never runs."""


def _as_int(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise LeaseError(f"lease field {name!r} must be an int (ints, not floats), got {value!r}")
    return value


def validate_lease(
    lease: dict[str, Any],
    *,
    now: int,
    expected_step_id: str | None = None,
) -> dict[str, Any]:
    """Validate a lease at logical time `now`; return it unchanged or raise LeaseError.

    This checks shape, integer clocks, the validity window (not-yet-valid / expired), and,
    if given, that the lease is bound to `expected_step_id`. Replay is NOT checked here (a
    single lease dict is inherently replayable) — use `LeaseGuard.consume` for that."""
    if not isinstance(lease, dict):
        raise LeaseError("lease must be a dict")
    if not isinstance(now, int) or isinstance(now, bool):
        raise LeaseError(f"now must be an int (logical frontier), got {now!r}")
    missing = [f for f in _REQUIRED_FIELDS if f not in lease]
    if missing:
        raise LeaseError(f"lease missing required field(s): {missing}")
    issued = _as_int("issued_frontier", lease["issued_frontier"])
    expiry = _as_int("expiry", lease["expiry"])
    _as_int("attempt", lease["attempt"])
    if not isinstance(lease["idempotency_key"], str) or not lease["idempotency_key"]:
        raise LeaseError("lease idempotency_key must be a non-empty str")
    if expiry < issued:
        raise LeaseError(f"lease expiry {expiry} precedes issue {issued} — malformed window")
    if now < issued:
        raise LeaseError(f"lease not yet valid: now={now} < issued_frontier={issued}")
    if now > expiry:
        raise LeaseError(f"lease expired: now={now} > expiry={expiry} — a stale lease fails closed")
    if expected_step_id is not None and lease["step_id"] != expected_step_id:
        raise LeaseError(f"lease is bound to step {lease['step_id']!r}, not {expected_step_id!r}")
    return lease


class LeaseGuard:
    """Remembers consumed leases so a REPLAYED lease fails closed on its second use.

    One guard tracks a stream of dispatches (e.g. one supervisor process). Each lease is
    identified by (idempotency_key, attempt): a distinct step attempt mints a distinct
    lease, so re-presenting the exact same lease — the classic replay — is refused."""

    def __init__(self) -> None:
        self._consumed: set[tuple[str, int]] = set()

    def consume(
        self,
        lease: dict[str, Any],
        *,
        now: int,
        expected_step_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate the lease (window + shape), then mark it consumed. Raises LeaseError if
        the lease is invalid/expired OR has already been consumed by this guard."""
        validate_lease(lease, now=now, expected_step_id=expected_step_id)
        key = (lease["idempotency_key"], int(lease["attempt"]))
        if key in self._consumed:
            raise LeaseError(f"replayed lease: {key} was already consumed — a lease is single-use")
        self._consumed.add(key)
        return lease

    def consumed(self, lease: dict[str, Any]) -> bool:
        """Whether this guard has already consumed the given lease (for inspection)."""
        return (lease.get("idempotency_key"), int(lease.get("attempt", 0))) in self._consumed
