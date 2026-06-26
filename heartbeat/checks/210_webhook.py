"""WEBHOOK1 — synchronous real-time approval callbacks (the Stripe 2-second gate).

Proves the synchronous Morta gate at the instant money moves (CAPABILITY_MAP D3.4):
an inbound authorization request is UNTRUSTED DATA — the decision is DECIMA's,
bounded by a DEADLINE (an int tick budget, not wall-clock), FAIL CLOSED if policy
denies OR the deadline elapses, audited as an `auth_decision` Cell with provenance.

Runs on its OWN fresh Kernel (it writes governance + auth decisions): contract
run(k, line). Fail loud.
"""
import os
import tempfile

from decima import webhook, memory
from decima.kernel import Kernel


def run(_k, line):
    line("\n== WEBHOOK (synchronous real-time auth · UNTRUSTED request · deadline · fail-closed) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    author = k.decima_agent_id

    # A deterministic policy: spend ceiling 100, merchant allowlist, governance on.
    handler = webhook.register_handler(k, "card-auth", policy={
        "spend_ceiling": 100,
        "merchant_allowlist": ["acme", "globex"],
        "governance": True,
    })
    line("  registered handler: ceiling=100, allowlist=[acme,globex], governance=on")

    # ---- (1) within policy + approved → approve:true -----------------------
    ok = webhook.authorize_request(k, handler, {"amount": 80, "merchant": "acme"},
                                   deadline=5, approved=True)
    assert ok["approve"] is True, ok
    dec = k.weave().get(ok["decision"])
    assert dec.type == webhook.AUTH_DECISION and dec.content["approve"] is True
    assert dec.content["decided_by"] == "decima"             # Decima's call, not the request's
    assert any(e["dst"] == ok["request"]                     # provenance: decision ← request
               for e in k.weave().edges_from(ok["decision"], "decided_from"))
    line(f"  in-policy: $80 @acme within 5 ticks → approve:TRUE — {ok['reason']} ✓")

    # ---- (2) exceeding policy → approve:false (fail closed) ----------------
    over = webhook.authorize_request(k, handler, {"amount": 250, "merchant": "acme"},
                                     deadline=5, approved=True)
    assert over["approve"] is False and "ceiling" in over["reason"], over
    line(f"  over-ceiling: $250 @acme → approve:FALSE (fail closed) — {over['reason']} ✓")

    # ---- off-allowlist merchant → approve:false ----------------------------
    offlist = webhook.authorize_request(k, handler, {"amount": 10, "merchant": "darkpool"},
                                        deadline=5, approved=True)
    assert offlist["approve"] is False and "allowlist" in offlist["reason"], offlist
    line(f"  off-allowlist: $10 @darkpool → approve:FALSE — {offlist['reason']} ✓")

    # ---- governance-banned merchant → approve:false, with cited evidence ---
    iid = memory.remember(k.weft, author, "globex flagged for fraud",
                          evidence_src=k.weft.head or author, instruction_eligible=False)
    memory.remember_governance(k.weft, author, memory.BANNED_ACTION,
                               target="globex", reason="fraud chargebacks",
                               evidence_src=iid)
    banned = webhook.authorize_request(k, handler, {"amount": 10, "merchant": "globex"},
                                       deadline=5, approved=True)
    assert banned["approve"] is False and "governance" in banned["reason"], banned
    line(f"  governance: $10 @globex (banned) → approve:FALSE — {banned['reason']} ✓")

    # ---- (3) misses the deadline → approve:false (fail closed) -------------
    # A slow inbound forces more work (cost) than the deadline's tick budget allows.
    slow = webhook.authorize_request(k, handler, {"amount": 80, "merchant": "acme",
                                                  "cost": 9}, deadline=3)
    assert slow["approve"] is False and "deadline" in slow["reason"], slow
    assert slow["elapsed"] > slow["deadline"], slow
    line(f"  deadline: $80 @acme but {slow['elapsed']} ticks > deadline {slow['deadline']} "
         f"→ approve:FALSE (fail closed) ✓")

    # ---- approval withheld → approve:false (the synchronous Morta gate) ----
    held = webhook.authorize_request(k, handler, {"amount": 80, "merchant": "acme"},
                                     deadline=5, approved=False)
    assert held["approve"] is False and "approval" in held["reason"], held
    line(f"  Morta: $80 @acme but approval withheld → approve:FALSE ✓")

    # ---- the request is DATA: a malicious self-approving field is ignored ---
    evil = webhook.authorize_request(k, handler,
                                     {"amount": 250, "merchant": "acme", "approve": True},
                                     deadline=5, approved=True)
    assert evil["approve"] is False, evil                    # its own approve:true never binds
    req = k.weave().get(evil["request"])
    assert req.content["instruction_eligible"] is False      # captured as untrusted DATA
    line("  untrusted: request carrying approve:true is DATA — Decima still denies $250 ✓")

    # ---- every decision is recorded with provenance on the Weft ------------
    decisions = k.weave().of_type(webhook.AUTH_DECISION)
    assert len(decisions) == 7, len(decisions)
    for d in decisions:
        assert k.weave().edges_from(d.id, "decided_from"), d.id   # each grounded in its request
    line(f"  audit: {len(decisions)} auth_decision Cells, each grounded in its request ✓")
