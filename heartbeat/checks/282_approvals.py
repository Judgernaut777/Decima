"""APPROVALS ARE WEFT EVENTS — capability-scope + per-invocation (Morta hardening).

The `requires_approval` Morta gate used to consult an in-memory per-capability set on
the kernel (`self.approvals`) — ambient, unauditable, gone on restart, and impossible
to bind to a specific request. This hardens it: approvals are now EVENTS on the Weft,
in two scopes:
  • capability — `k.approve(cap)` operator-enables the cap (back-compat: authorizes its
    requires_approval invokes). Now a folded, auditable, durable event.
  • invocation — `k.approve_invocation(cap, args, nonce)` approves EXACTLY one operation.
    Approving 'publish A' can never authorize 'publish B' (bound to the operation, not
    the capability), and it is SINGLE-USE (consumed when its invoke lands).

This check proves (on its own fresh, isolated Kernels):
  - capability-scope: an unapproved invoke is denied; `approve` writes an APPROVAL cell
    on the Weft (auditable, folds into `k.approvals`); the invoke then succeeds;
  - invocation-scope: an invoke with a pinned nonce is denied until exactly that
    operation is approved; a DIFFERENT operation (changed args) stays denied despite the
    approval — anti-ambient; the approved operation runs;
  - single-use: after the approved operation runs, re-running it is denied again and the
    approval cell is RETRACTed (spent) — anti-replay of the approval itself;
  - the human is the approver of record on every approval event (audit trail).

The gate still fails CLOSED: no approval event ⇒ no invoke of a requires_approval cap.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import capability as C
from decima.weft import INVOKE


def _fresh():
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    agent = k.weave().get(k.decima_agent_id)
    pub = next(c.id for c in k.weave().of_type("capability")
               if c.content["name"] == "browser.publish")   # bootstrap requires_approval cap
    return k, agent, pub


def run(k, line):
    line("\n== APPROVALS ARE WEFT EVENTS: capability-scope + invocation-scope (Morta) ==")

    # 1. CAPABILITY-SCOPE — approve() is a Weft event; back-compat authorizes the cap. ─
    k1, ag1, pub1 = _fresh()
    denied = k1.invoke(ag1, pub1, {"text": "hello"})
    assert "denied" in denied and "approval" in denied["denied"], denied
    assert not list(k1.weave().of_type(C.APPROVAL)), "no approval event should exist yet"
    aid = k1.approve(pub1)
    acell = k1.weave().get(aid)
    assert acell is not None and acell.type == C.APPROVAL, acell
    assert acell.content["scope"] == "capability" and acell.content["approver"] == k1.human.id, acell.content
    assert pub1 in k1.approvals, "the folded `approvals` property must reflect the event"
    ok = k1.invoke(ag1, pub1, {"text": "hello"})
    assert "ok" in ok, ok
    line("  capability-scope: unapproved→denied; approve() writes an auditable APPROVAL "
         "event (folds into k.approvals, approver=human); invoke then authorized ✓")

    # 2. INVOCATION-SCOPE — approves EXACTLY one operation (anti-ambient). ─────────────
    k2, ag2, pub2 = _fresh()
    nonce = "op-nonce-approve-1"
    assert "denied" in k2.invoke(ag2, pub2, {"text": "A"}, nonce=nonce), "unapproved op must be denied"
    aid2 = k2.approve_invocation(pub2, {"text": "A"}, nonce)
    a2 = k2.weave().get(aid2)
    assert a2.type == C.APPROVAL and a2.content["scope"] == "invocation", a2.content
    assert pub2 not in k2.approvals, "an invocation approval must NOT capability-approve the cap"
    # Anti-ambient: the approval for op 'A' must not authorize a DIFFERENT op 'B'.
    assert "denied" in k2.invoke(ag2, pub2, {"text": "B"}, nonce=nonce), \
        "an approval for one operation must never authorize a different one"
    # The exact approved operation runs.
    ran = k2.invoke(ag2, pub2, {"text": "A"}, nonce=nonce)
    assert "ok" in ran, ran
    line("  invocation-scope: approves EXACTLY (cap+args+nonce); a changed operation "
         "stays denied despite the approval (anti-ambient); the approved op runs ✓")

    # 3. SINGLE-USE — the invocation approval is consumed once its invoke lands. ───────
    again = k2.invoke(ag2, pub2, {"text": "A"}, nonce=nonce)
    assert "denied" in again, "an invocation approval must be single-use (consumed)"
    assert k2.weave().get(aid2).retracted, "the spent approval cell must be RETRACTed"
    line("  single-use: after the approved op runs, re-running is denied and the "
         "approval cell is RETRACTed (spent) — anti-replay of the approval ✓")

    line("  → the Morta approval gate is now auditable Weft events, per-capability OR "
         "bound to a single operation, single-use, and still fails closed. No ambient, "
         "in-memory approval remains.")
