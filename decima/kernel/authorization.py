"""Authorization service — machine-readable decisions over the ocap primitive (DEC-016).

The reference `capability.authorize` returns `(bool, str)`: correct, but the string is a
human sentence, not something downstream code (a scheduler, the Shell, an audit view) can
branch on. This facade wraps it in a typed `AuthorizationDecision` carrying a STABLE
`reason_code`, the matched grant id, and whether the denial is an approval gate — without
changing the underlying decision. The deterministic authorization logic still lives in
`capability` (the trusted primitive); this only makes its result legible.

Handoff §2.4: models propose; deterministic code authorizes. This is deterministic code.
No network, no provider, no clock reads beyond the caller-supplied logical `now`.
"""

from __future__ import annotations

from dataclasses import dataclass

from decima.kernel import capability


class ReasonCode:
    """Stable, machine-readable authorization outcomes. Values are the contract other
    subsystems branch on; keep them stable across refactors."""

    OK = "OK"
    SIGNER_MISMATCH = "SIGNER_MISMATCH"
    NO_SUCH_CAPABILITY = "NO_SUCH_CAPABILITY"
    NOT_A_CAPABILITY = "NOT_A_CAPABILITY"
    REVOKED = "REVOKED"
    LEASE_FAILED = "LEASE_FAILED"
    QUARANTINED = "QUARANTINED"
    NO_ENVELOPE = "NO_ENVELOPE"
    WRONG_GRANTEE = "WRONG_GRANTEE"
    DELEGATION_INVALID = "DELEGATION_INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SANDBOX_ONLY = "SANDBOX_ONLY"
    DENIED = "DENIED"  # fallback for an unrecognized denial reason


# Ordered (marker-substring -> reason code) mapping over the frozen reference strings.
# First match wins; order matters where markers overlap.
_MARKERS: tuple[tuple[str, str], ...] = (
    ("possession proof failed", ReasonCode.SIGNER_MISMATCH),
    ("no such capability", ReasonCode.NO_SUCH_CAPABILITY),
    ("target is not a capability", ReasonCode.NOT_A_CAPABILITY),
    ("lease failed closed", ReasonCode.LEASE_FAILED),
    ("capability revoked", ReasonCode.REVOKED),
    ("quarantined", ReasonCode.QUARANTINED),
    ("no grant in envelope", ReasonCode.NO_ENVELOPE),
    ("different principal", ReasonCode.WRONG_GRANTEE),
    ("budget exceeded", ReasonCode.BUDGET_EXCEEDED),
    ("requires human approval", ReasonCode.APPROVAL_REQUIRED),
    ("sandbox_only", ReasonCode.SANDBOX_ONLY),
    # lease_status expiry/exhaustion sentences (single-use / time-locked)
    ("expired", ReasonCode.LEASE_FAILED),
    ("max_uses", ReasonCode.LEASE_FAILED),
    ("uses exhausted", ReasonCode.LEASE_FAILED),
    # verify_delegation failures
    ("delegation", ReasonCode.DELEGATION_INVALID),
    ("granter", ReasonCode.DELEGATION_INVALID),
    ("downhill", ReasonCode.DELEGATION_INVALID),
)


def _classify(reason: str) -> str:
    low = reason.lower()
    for marker, code in _MARKERS:
        if marker in low:
            return code
    return ReasonCode.DENIED


@dataclass(frozen=True)
class AuthorizationDecision:
    """The deterministic verdict for one attempted invocation."""

    allowed: bool
    reason_code: str
    reason: str
    matched_grant_id: str | None
    required_approval: bool

    def __bool__(self) -> bool:
        return self.allowed


def authorize_decision(
    weave: object,
    agent_cell: object,
    cap_id: str,
    args: dict,
    acting_principal: str,
    *,
    spent: float = 0.0,
    approvals: set | None = None,
    now: int | None = None,
    prior_uses: int = 0,
) -> AuthorizationDecision:
    """Authorize an invocation and return a typed, machine-readable decision.

    Delegates the actual ocap check to `capability.authorize` (the trusted primitive);
    the verdict is identical, only classified. `matched_grant_id` is the capability the
    invocation was checked against; `required_approval` is True exactly when the denial
    is the Morta approval gate (the one denial a human can clear).
    """
    allowed, reason = capability.authorize(
        weave,
        agent_cell,
        cap_id,
        args,
        acting_principal,
        spent=spent,
        approvals=approvals,
        now=now,
        prior_uses=prior_uses,
    )
    code = ReasonCode.OK if allowed else _classify(reason)
    return AuthorizationDecision(
        allowed=allowed,
        reason_code=code,
        reason=reason,
        matched_grant_id=cap_id,
        required_approval=(code == ReasonCode.APPROVAL_REQUIRED),
    )
