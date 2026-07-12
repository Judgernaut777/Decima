"""The approvals read-model — a disposable view over the Morta approval inbox.

Reads the kernel ``ApprovalInbox``'s Cells (``inbox_item`` requests and
``inbox_decision`` dispositions, ``decima.kernel.inbox``) from the fold and buckets
every gated effect by its lifecycle state:

  * ``pending``  — an item enqueued and not yet decided;
  * ``approved`` — a decision approved it;
  * ``denied``   — a decision denied it;
  * ``consumed`` — an approved item whose effect actually RAN (``ran: True``);
  * ``expired``  — a still-pending item whose ``expires_at`` is past the logical
                   frontier (``max lamport`` folded so far — deterministic, never
                   wall-clock). An expired item is NOT approvable; the buckets are
                   the human/audit lens the inbox itself fails closed on.

It asserts nothing and is rebuildable from the Weft. Deterministic: each bucket is
sorted by item id.
"""

from __future__ import annotations

from dataclasses import dataclass

from decima.kernel.inbox import DECISION, ITEM
from decima.projections.engine import BaseProjection

PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"
CONSUMED = "consumed"
EXPIRED = "expired"
BUCKETS = (PENDING, APPROVED, DENIED, CONSUMED, EXPIRED)


@dataclass(frozen=True)
class ApprovalView:
    item: str
    capability: str | None
    description: str | None
    state: str
    ran: bool
    decision: str | None
    approver: str | None
    expires_at: int | None

    def as_dict(self) -> dict:
        return {
            "item": self.item,
            "capability": self.capability,
            "description": self.description,
            "state": self.state,
            "ran": self.ran,
            "decision": self.decision,
            "approver": self.approver,
            "expires_at": self.expires_at,
        }


class ApprovalsProjection(BaseProjection):
    name = "approvals"
    version = 1

    def _decisions_by_item(self) -> dict[str, object]:
        return {
            c.content.get("item"): c
            for c in self.fold.of_type(DECISION)
            if c.content.get("item") is not None
        }

    def approvals(self) -> list[ApprovalView]:
        decisions = self._decisions_by_item()
        frontier = self.fold.frontier_lamport
        out: list[ApprovalView] = []
        for item in self.fold.of_type(ITEM):
            d = decisions.get(item.id)
            expires_at = item.content.get("expires_at")
            if d is None:
                if (
                    isinstance(expires_at, int)
                    and not isinstance(expires_at, bool)
                    and expires_at < frontier
                ):
                    state, ran, verdict, approver = EXPIRED, False, None, None
                else:
                    state, ran, verdict, approver = PENDING, False, None, None
            else:
                verdict = d.content.get("decision")
                ran = bool(d.content.get("ran", False))
                approver = d.content.get("approver")
                if verdict == "approved":
                    state = CONSUMED if ran else APPROVED
                elif verdict == "denied":
                    state = DENIED
                else:
                    state = verdict or PENDING
            out.append(
                ApprovalView(
                    item=item.id,
                    capability=item.content.get("capability"),
                    description=item.content.get("description"),
                    state=state,
                    ran=ran,
                    decision=verdict,
                    approver=approver,
                    expires_at=expires_at
                    if isinstance(expires_at, int) and not isinstance(expires_at, bool)
                    else None,
                )
            )
        return sorted(out, key=lambda v: v.item)

    def by_state(self, state: str) -> list[ApprovalView]:
        return [a for a in self.approvals() if a.state == state]

    def pending(self) -> list[ApprovalView]:
        return self.by_state(PENDING)

    def counts(self) -> dict[str, int]:
        buckets = {b: 0 for b in BUCKETS}
        for a in self.approvals():
            buckets[a.state] = buckets.get(a.state, 0) + 1
        return buckets

    def view(self) -> object:
        return [a.as_dict() for a in self.approvals()]
