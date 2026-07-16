"""Authorization service — machine-readable decisions over the ocap primitive (DEC-016).

The reference `capability.authorize` returns `(bool, str)`: correct, but the string is a
human sentence, not something downstream code (a scheduler, the Shell, an audit view) can
branch on. This facade wraps `capability.authorize_detail` in a typed
`AuthorizationDecision` carrying a STABLE `reason_code` — computed at the denial site
inside the primitive, never re-derived from the sentence. (The first version of this
module substring-matched the human prose to recover a code, so any rewording silently
degraded classification to `DENIED`; `authorize_detail` exists to make that class of
drift impossible.)

Handoff §2.4: models propose; deterministic code authorizes. This is deterministic code.
No network, no provider, no clock reads beyond the caller-supplied logical `now`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from decima.kernel import capability

if TYPE_CHECKING:
    from decima.kernel.weave import Cell, Weave

# The stable, machine-readable authorization vocabulary. The values are owned by the
# trusted primitive (`capability.DenialCode`) so the code a denial site produces and the
# code downstream branches on are, by construction, the same object. Re-exported here
# under the name the rest of the system has always imported.
ReasonCode = capability.DenialCode


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
    weave: Weave,
    agent_cell: Cell,
    cap_id: str,
    args: dict[str, Any],
    acting_principal: str,
    *,
    spent: float = 0.0,
    approvals: set[str] | None = None,
    now: int | None = None,
    prior_uses: int = 0,
) -> AuthorizationDecision:
    """Authorize an invocation and return a typed, machine-readable decision.

    Delegates the actual ocap check to `capability.authorize_detail` (the trusted
    primitive); the verdict AND its classification come from the same denial site.
    `matched_grant_id` is the capability the invocation was checked against;
    `required_approval` is True exactly when the denial is the Morta approval gate
    (the one denial a human can clear).
    """
    allowed, reason, code = capability.authorize_detail(
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
    return AuthorizationDecision(
        allowed=allowed,
        reason_code=code,
        reason=reason,
        matched_grant_id=cap_id,
        required_approval=(code == ReasonCode.APPROVAL_REQUIRED),
    )
