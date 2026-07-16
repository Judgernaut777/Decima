"""The approval inbox — the surface that replaces the inline REPL for gated effects.

Phase 2 (Go live). Until now a Morta-gated (`requires_approval`) effect blocked
INLINE: the REPL invoked the capability, `capability.authorize` denied it at the
Morta gate, and the shell just printed "awaiting approval". There was nowhere for a
human decision to LAND — the proposed effect evaporated with the turn.

`ApprovalInbox` makes the gate a DURABLE queue instead of an inline dead-end. When a
turn would fire an outward/irreversible (`requires_approval`) effect, the inbox
ENQUEUES a pending item on the Weft — the proposed capability, its args, a
human-readable description, provenance to the request that raised it, and the exact
operation nonce the kernel needs to enact it later — and the effect does NOT run. A
human then LISTs pending items, INSPECTs one, and APPROVEs or DENYs:

  • approve — carries the human's decision to the gate and NOTHING more. It approves
    EXACTLY this operation via the kernel's `approve_invocation` seam (cap+args+nonce,
    single-use) and then enacts it with `invoke(..., nonce=<pinned>)`. The effect thus
    runs under the SAME `capability.authorize` / Morta spine as any invoke — so a
    revoked or ungranted capability still fails CLOSED at the gate. The inbox confers
    no authority; it only decides WHETHER to present the human's approval to the gate.
  • deny — records a denial Cell (with the human as approver of record) and the effect
    NEVER runs.

Every item and every disposition is a Weft event (Law 4 provenance): the queue folds
from the Log, survives restart, time-travels, and is auditable. It fails CLOSED — an
unknown, or already-decided, item cannot be approved, and nothing auto-approves.

Laws upheld: Morta-gate outward effects (the gate, now durable); NO AMBIENT AUTHORITY
(the inbox is a carrier of a human decision, never a grant — enactment still runs the
full ocap spine); provenance on the Weft; ints-not-floats (no numeric content here);
offline + deterministic (pure stdlib over the same kernel seams the checks drive).
"""

from __future__ import annotations

import os
from typing import Any, Protocol, cast

from decima.kernel.crypto import Principal
from decima.kernel.hashing import content_id
from decima.kernel.model import assert_content, assert_edge
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Weft


class _KernelLike(Protocol):
    """The minimal Kernel surface the inbox reads/writes through — a structural seam
    so this module types against the shape it needs without depending on the
    (out-of-kernel) reference Kernel class."""

    weft: Weft
    approvals: set[str]
    human: Principal

    def weave(self, upto_seq: int | None = ...) -> Weave: ...

    def invoke(
        self, agent_cell: Cell, cap_id: str, args: dict[str, Any], nonce: str | None = ...
    ) -> dict[str, Any]: ...

    def approve_invocation(self, cap_id: str, args: dict[str, Any], nonce: str) -> object: ...

    def principal_for(self, agent_cell: Cell) -> str: ...


class InboxError(Exception):
    """A fail-closed inbox refusal: an unknown or already-decided item, etc."""


ITEM = "inbox_item"  # a pending Morta decision queued for a human
DECISION = "inbox_decision"  # the human's disposition of an item (approved | denied)


class ApprovalInbox:
    """A durable queue of Morta decisions, folded from the Weft.

    Construct with a `Kernel`; every method reads/writes only through the kernel's
    existing seams (`weave`, `weft.append`, `approve_invocation`, `invoke`,
    `approvals`), so the inbox adds a surface, never a new authority path."""

    def __init__(self, k: _KernelLike) -> None:
        self.k = k

    # -- gate detection ----------------------------------------------------
    def is_gated(self, cap: str | Cell | None) -> bool:
        """True iff invoking `cap` (a cap cell or its id) would hit the Morta gate:
        it carries `requires_approval` and is NOT already capability-approved. An
        ungated effect — or one an operator already enabled — is not queued; it runs
        as before. This is the ONLY predicate that routes a turn into the inbox."""
        if isinstance(cap, str):
            cap = self.k.weave().get(cap)
        if cap is None or cap.type != "capability":
            return False
        caveats = cap.content.get("caveats", {})
        return bool(caveats.get("requires_approval")) and cap.id not in self.k.approvals

    # -- enqueue: a gated turn lands here instead of blocking inline --------
    def submit(
        self,
        agent_cell: Cell,
        cap_id: str,
        args: dict[str, Any],
        *,
        description: str | None = None,
        provenance: str | None = None,
    ) -> dict[str, Any]:
        """Route an effect through the inbox. A Morta-gated (`requires_approval`)
        effect is ENQUEUED as a pending item and does NOT run — the human decides
        later. An ungated (or already operator-approved) effect runs immediately,
        exactly as a bare invoke would. Returns {'queued': item_id} or {'ran': res}."""
        cap = self.k.weave().get(cap_id)
        if cap is None:
            return {"denied": "no such capability"}
        if not self.is_gated(cap):
            return {"ran": self.k.invoke(agent_cell, cap_id, args)}
        return {
            "queued": self.enqueue(
                agent_cell, cap_id, args, description=description, provenance=provenance
            )
        }

    def enqueue(
        self,
        agent_cell: Cell,
        cap_id: str,
        args: dict[str, Any],
        *,
        description: str | None = None,
        provenance: str | None = None,
    ) -> str:
        """Record a pending inbox item on the Weft, WITHOUT running the effect. The
        item captures everything the kernel needs to enact it later: the capability,
        its args, and a fresh operation `nonce` that PINS exactly the operation the
        human will approve (via `approve_invocation`) — so approving 'publish A' can
        never enact 'publish B'. `provenance` (e.g. the utterance/intake that raised
        the effect) is linked with a `requested_by` edge (Law 4).

        A queued item is DATA, not authority — its description/args may quote
        untrusted content (a catalog manifest, an inbound message), so the item is
        stamped `instruction_eligible: False` (Law: untrusted content is data): it
        can DESCRIBE the proposed effect for a human, never instruct an agent."""
        cap = self.k.weave().get(cap_id)
        if cap is None or cap.type != "capability":
            raise InboxError(f"cannot enqueue: no such capability {cap_id!r}")
        principal = self.k.principal_for(agent_cell)
        nonce = os.urandom(16).hex()  # pins the exact operation to approve later
        desc = description or f"{cap.content.get('name')}({args})"
        item_id = content_id(
            {
                "inbox_item": cap_id,
                "args": args,
                "nonce": nonce,
                "agent": agent_cell.id,
                "at": self.k.weft.head,
            }
        )
        assert_content(
            self.k.weft,
            principal,
            item_id,
            ITEM,
            {
                "capability": cap_id,
                "capability_name": cap.content.get("name"),
                "effect": cap.content.get("effect"),
                "args": args,
                "nonce": nonce,  # the kernel enacts THIS exact operation
                "description": desc,
                "agent": agent_cell.id,
                "principal": principal,
                "provenance": provenance,
                "status": "pending",
                "instruction_eligible": False,  # a queued item DESCRIBES, never instructs
            },
        )
        if provenance is not None:
            assert_edge(self.k.weft, principal, item_id, "requested_by", provenance)
        return item_id

    # -- projections -------------------------------------------------------
    def item(self, item_id: str) -> Cell | None:
        """The item Cell for `item_id` (accepts a prefix), or None if unknown."""
        c = self.k.weave().get(item_id)
        return c if (c is not None and c.type == ITEM) else None

    def _decision_of(self, item_id: str) -> Cell | None:
        """The live disposition Cell for an item, or None while it is still pending.
        This is the fail-closed guard: an already-decided item has a decision here."""
        for c in self.k.weave().of_type(DECISION):
            if c.content.get("item") == item_id:
                return c
        return None

    def pending(self) -> list[Cell]:
        """The pending items — enqueued and not yet decided — folded from the Weft."""
        w = self.k.weave()
        decided = {c.content.get("item") for c in w.of_type(DECISION)}
        return [c for c in w.of_type(ITEM) if c.id not in decided]

    def inspect(self, item_id: str) -> dict[str, Any]:
        """Inspect one item: its content plus its current disposition. Fails CLOSED
        on an unknown id (there is nothing for a human to act on)."""
        c = self.item(item_id)
        if c is None:
            raise InboxError(f"unknown inbox item {item_id!r}")
        d = self._decision_of(c.id)
        return {
            "item": c,
            "id": c.id,
            "status": d.content["decision"] if d is not None else "pending",
            "decision": d,
        }

    # -- the human decision, carried to the gate ---------------------------
    def approve(self, item_id: str, agent_cell: Cell | None = None) -> dict[str, Any]:
        """Approve a pending item and enact its effect THROUGH the kernel gate.

        Fails CLOSED on an unknown or already-decided item (nothing auto-approves; no
        item is decided twice). The inbox confers NO authority: it approves EXACTLY
        this operation (`approve_invocation`, cap+args+nonce, single-use) and then
        enacts it with the PINNED nonce, so the effect runs under the full ocap /
        Morta spine. If that gate refuses — the capability was revoked or was never
        granted to this agent — the effect does NOT run, the item stays pending, and
        the denial is returned (fail closed at the gate). On success the disposition
        is recorded on the Weft with the human as approver of record (Law 4)."""
        c = self.item(item_id)
        if c is None:
            raise InboxError(f"cannot approve: unknown inbox item {item_id!r}")
        if self._decision_of(c.id) is not None:
            raise InboxError(f"cannot approve: item {c.id[:8]} is already decided")
        content = c.content
        cap_id, args, nonce = content["capability"], content["args"], content["nonce"]
        agent = agent_cell or self.k.weave().get(content["agent"])
        # Carry the human's decision to the gate — and nothing more. Approve exactly
        # this operation, then invoke with the SAME pinned nonce so the approval matches.
        self.k.approve_invocation(cap_id, args, nonce)
        # `agent` is None only if `content["agent"]` no longer resolves to a live cell
        # (pre-existing possible edge case, unchanged here) — cast preserves the exact
        # runtime pass-through to `invoke` rather than adding a new guard.
        res = self.k.invoke(cast(Cell, agent), cap_id, args, nonce=nonce)
        if "ok" not in res:  # the gate refused (revoked/ungranted) — fail closed
            return res
        did = content_id({"inbox_approved": c.id, "invoke": res.get("invoke_event")})
        assert_content(
            self.k.weft,
            self.k.human.id,
            did,
            DECISION,
            {
                "item": c.id,
                "decision": "approved",
                "approver": self.k.human.id,
                "capability": cap_id,
                "nonce": nonce,
                "ran": True,
                "invoke": res.get("invoke_event"),
                "result_cell": res.get("result_cell"),
            },
        )
        assert_edge(self.k.weft, self.k.human.id, did, "decides", c.id)
        return res

    def deny(self, item_id: str, reason: str = "") -> str:
        """Deny a pending item: record a denial Cell (human as approver of record) and
        the effect NEVER runs. Fails CLOSED on an unknown or already-decided item."""
        c = self.item(item_id)
        if c is None:
            raise InboxError(f"cannot deny: unknown inbox item {item_id!r}")
        if self._decision_of(c.id) is not None:
            raise InboxError(f"cannot deny: item {c.id[:8]} is already decided")
        did = content_id({"inbox_denied": c.id, "at": self.k.weft.head})
        assert_content(
            self.k.weft,
            self.k.human.id,
            did,
            DECISION,
            {
                "item": c.id,
                "decision": "denied",
                "approver": self.k.human.id,
                "capability": c.content.get("capability"),
                "reason": reason,
                "ran": False,
            },
        )
        assert_edge(self.k.weft, self.k.human.id, did, "decides", c.id)
        return did
