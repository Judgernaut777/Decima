"""APPROVAL INBOX — the durable Morta gate that replaces the inline REPL (Phase 2).

Before this lane, a Morta-gated (`requires_approval`) outward/irreversible effect
died INLINE: the REPL invoked the capability, `capability.authorize` denied it at the
gate, and the shell printed a notice — the human decision had nowhere to land, and the
proposed effect evaporated with the turn. `decima/inbox.py` makes the gate a DURABLE
queue: a gated effect ENQUEUES a pending item on the Weft and does NOT run; a human
LISTs/INSPECTs and APPROVEs (carrying the decision to the SAME gate) or DENYs it.

This check is an adversarial detector against the REAL kernel spine (the same
`approve_invocation` / `invoke` / `authorize` path any turn drives):

  (0) THREAT/BASELINE — browser.publish is genuinely Morta-gated: invoked directly
      with no approval it is DENIED at the gate (so the inbox is guarding a real gate);
  (a) a `requires_approval` effect submitted to the inbox ENQUEUES a pending item and
      DOES NOT run — no INVOKE, no receipt names the capability until approval, and the
      item carries provenance (a `requested_by` edge) to the request that raised it;
  (b) APPROVE enacts it THROUGH the kernel gate — exactly one INVOKE with the pinned
      nonce, a SUCCEEDED receipt, and a disposition Cell recording the human approver;
      after which the item is no longer pending;
  (c) DENY records a denial Cell (human approver) and the effect NEVER runs (no INVOKE,
      no receipt) — the item leaves the pending queue as denied;
  (d) NO AMBIENT AUTHORITY — approving an item whose capability is REVOKED, or was
      never GRANTED to the agent, still fails CLOSED at the gate: the effect does not
      run and the inbox conferred nothing;
  (e) FAIL CLOSED — an unknown item, and an already-decided item (approved OR denied),
      cannot be approved; nothing auto-approves.

Deterministic + offline: fresh Kernels, the offline RuleBrain path, no network, no
clocks. Contract: run(k, line). Fail loud (assert / expected InboxError).
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.inbox import ApprovalInbox, InboxError, ITEM, DECISION
from decima import executor


def _fresh():
    """A fresh, isolated Kernel + Decima agent + the bootstrap Morta-gated cap
    (browser.publish carries requires_approval) + an inbox over it."""
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    agent = k.weave().get(k.decima_agent_id)
    pub = next(c.id for c in k.weave().of_type("capability")
               if c.content["name"] == "browser.publish")
    return k, agent, pub, ApprovalInbox(k)


def _invokes_of(k, cap_id):
    return [i for i in k.weave().invocations if i.cap == cap_id]


def _receipts_for_cap(k, cap_id):
    """Effect receipts produced by invoking cap_id — matched via the INVOKE events
    that named it (a receipt `of` an INVOKE of this cap)."""
    inv_ids = {i.event for i in _invokes_of(k, cap_id)}
    return [c for c in k.weave().of_type("result") if c.content.get("of") in inv_ids]


def run(k, line):
    line("\n== APPROVAL INBOX — the durable Morta gate (enqueue → human → gate) ==")

    # 0. BASELINE — the cap the inbox guards is REALLY Morta-gated. ────────────────────
    k0, ag0, pub0, _ib0 = _fresh()
    direct = k0.invoke(ag0, pub0, {"text": "no approval"})
    assert "denied" in direct and "approval" in direct["denied"], \
        f"browser.publish must be Morta-gated for this check to prove anything: {direct}"
    assert not _receipts_for_cap(k0, pub0), "a denied gated invoke must run no effect"
    line("  baseline: browser.publish invoked directly (no approval) is DENIED at the "
         "Morta gate — the inbox guards a real gate ✓")

    # (a) ENQUEUE — a gated effect is queued, does NOT run, and carries provenance. ────
    k1, ag1, pub1, ib1 = _fresh()
    # a provenance anchor (the "request" that raised the effect), so the item links back
    from decima.hashing import content_id
    from decima.weft import ASSERT
    uid = content_id({"utterance": "publish the report", "lamport": k1.weft.lamport})
    k1.weft.append(k1.human.id, ASSERT,
                   {"cell": uid, "type": "utterance", "content": {"text": "publish the report"}})
    assert ib1.is_gated(pub1), "the inbox must route a requires_approval cap into the queue"
    sub = ib1.submit(ag1, pub1, {"text": "the report"}, description="publish the report",
                     provenance=uid)
    assert "queued" in sub and "ran" not in sub, f"a gated submit must ENQUEUE, not run: {sub}"
    item = sub["queued"]
    cell = k1.weave().get(item)
    assert cell is not None and cell.type == ITEM, "an inbox_item Cell must land on the Weft"
    assert cell.content["capability"] == pub1 and cell.content["status"] == "pending"
    assert cell.content["args"] == {"text": "the report"} and cell.content.get("nonce")
    # provenance: a requested_by edge ties the item to the request that raised it.
    assert any(e["rel"] == "requested_by" and e["dst"] == uid
               for e in cell.edges_out), "the queued item must link to its request (Law 4)"
    # it is PENDING and, crucially, NOTHING ran.
    assert [c.id for c in ib1.pending()] == [item], "the item must be the sole pending one"
    assert not _invokes_of(k1, pub1), "a queued effect must NOT invoke until approved"
    assert not _receipts_for_cap(k1, pub1), "a queued effect must produce NO receipt yet"
    line("  enqueue: a requires_approval effect is queued (Weft inbox_item + requested_by "
         "provenance), stays pending, and runs NOTHING before approval ✓")

    # (b) APPROVE — enacts through the SAME gate; audited with the human approver. ─────
    res = ib1.approve(item)
    assert "ok" in res, f"approving a held, gated item must enact it: {res}"
    assert res["ok"]["out"] == "published: the report", "the effect must actually run"
    invs = _invokes_of(k1, pub1)
    assert len(invs) == 1, "approval must enact EXACTLY ONE invoke (the pinned operation)"
    assert invs[0].args == {"text": "the report"}, "the enacted op must be the queued one"
    rcpts = _receipts_for_cap(k1, pub1)
    assert len(rcpts) == 1 and rcpts[0].content["status"] == executor.SUCCEEDED, \
        "the approved effect must leave one SUCCEEDED receipt"
    # the disposition is on the Weft, with the human as approver of record.
    dec = next((c for c in k1.weave().of_type(DECISION)
                if c.content.get("item") == item), None)
    assert dec is not None and dec.content["decision"] == "approved", "a decision Cell must land"
    assert dec.content["approver"] == k1.human.id, "the human is the approver of record"
    assert any(e["rel"] == "decides" and e["dst"] == item for e in dec.edges_out)
    assert item not in [c.id for c in ib1.pending()], "an approved item leaves the queue"
    line("  approve: enacts EXACTLY the pinned op through authorize/Morta → one INVOKE + "
         "SUCCEEDED receipt + a decision Cell (approver=human); item leaves the queue ✓")

    # (c) DENY — records a denial; the effect never runs. ─────────────────────────────
    k2, ag2, pub2, ib2 = _fresh()
    item2 = ib2.enqueue(ag2, pub2, {"text": "leak the seed"}, description="deny me")
    did = ib2.deny(item2, reason="not sanctioned")
    d2 = k2.weave().get(did)
    assert d2 is not None and d2.type == DECISION and d2.content["decision"] == "denied"
    assert d2.content["approver"] == k2.human.id and d2.content["ran"] is False
    assert not _invokes_of(k2, pub2), "a denied effect must NEVER invoke"
    assert not _receipts_for_cap(k2, pub2), "a denied effect must produce NO receipt"
    assert item2 not in [c.id for c in ib2.pending()], "a denied item leaves the pending queue"
    line("  deny: records a denial Cell (approver=human, ran=false); no INVOKE, no "
         "receipt — the effect never runs ✓")

    # (d) NO AMBIENT AUTHORITY — the inbox confers nothing; the gate still decides. ────
    # d1. REVOKED capability: approve fails closed at the gate, effect does not run.
    k3, ag3, pub3, ib3 = _fresh()
    item3 = ib3.enqueue(ag3, pub3, {"text": "x"})
    k3.revoke(pub3)                                   # Morta withdraws the capability
    r3 = ib3.approve(item3)
    assert "denied" in r3 and "ok" not in r3, f"a revoked cap must fail closed at the gate: {r3}"
    assert not _receipts_for_cap(k3, pub3), "no effect may run through a revoked grant"
    assert item3 in [c.id for c in ib3.pending()], "a gate-refused item stays pending (undecided)"
    # d2. UNGRANTED capability: an item for a cap the agent never held fails closed too.
    k4, ag4, _pub4, ib4 = _fresh()
    ungranted = k4._assert_cap("secret.publish", "browser",
                               caveats={"requires_approval": True}, impl={"op": "publish"})
    item4 = ib4.enqueue(ag4, ungranted, {"text": "y"})    # never granted into the envelope
    r4 = ib4.approve(item4)
    assert "denied" in r4 and "envelope" in r4["denied"], \
        f"an ungranted cap must be denied for lack of a grant: {r4}"
    assert not _receipts_for_cap(k4, ungranted), "no effect may run for an ungranted cap"
    line("  no ambient authority: approving a REVOKED or UNGRANTED item still fails "
         "CLOSED at the ocap/Morta gate — the effect never runs, the inbox grants nothing ✓")

    # (e) FAIL CLOSED — unknown / already-decided items cannot be approved. ────────────
    # e1. unknown id.
    try:
        ib1.approve("00" * 32)
        raise AssertionError("an unknown item was approved (must fail closed)")
    except InboxError:
        pass
    # e2. already-APPROVED (item from part b) cannot be approved again — no double-enact.
    before = len(_invokes_of(k1, pub1))
    try:
        ib1.approve(item)
        raise AssertionError("an already-approved item was approved again")
    except InboxError:
        pass
    assert len(_invokes_of(k1, pub1)) == before, "a blocked re-approve must enact nothing"
    # e3. already-DENIED (item from part c) cannot be approved.
    try:
        ib2.approve(item2)
        raise AssertionError("an already-denied item was approved")
    except InboxError:
        pass
    assert not _invokes_of(k2, pub2), "a denied item must remain un-enacted"
    line("  fail closed: an unknown item, an already-approved item, and an already-denied "
         "item all refuse approval — nothing auto-approves, nothing double-enacts ✓")

    line("  → the Morta gate is now a DURABLE inbox: gated effects queue with provenance, "
         "a human decision is carried to the SAME authorize/Morta spine (never a grant of "
         "its own), and unknown/decided/revoked/ungranted all fail closed.")
