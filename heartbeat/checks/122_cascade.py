"""C12 — RETRACT/REDACT cascade to DERIVED authority (WEFT §5 cascade / FOLD §10.2).

Until now a RETRACT/REDACT failed closed only the targeted cell. The existing
revoke-fails-closed invariant (FOLD §11 #4) caught a *direct* child at the
`authorize` gate (verify_delegation walks `parent.retracted`), but the fold itself
left the child grant LIVE — still in `of_type("capability")`, still live in
`state_root()`. A grant/lease/cell that does not happen to re-walk the delegation
chain on every read would not have failed closed.

This proves the DERIVED_AUTHORITY cascade, now computed IN THE FOLD (weave.py):
when a capability/grant is RETRACTed, every authority that DESCENDS from it — a
delegated child grant attenuated from it (`content["parent"]`), and transitively any
grant whose delegation chain passes through it — is itself treated as retracted at
the post-retraction frontier. The descendant marking is a PURE derived pass over the
folded graph, so it is arrival-order independent and idempotent (FOLD §11.1/2/3).

  grant→delegate→invoke OK · RETRACT the parent · BOTH parent holder AND the child
  (and a grandchild) fail closed · prior asserts + the retract event still on the
  Log (weft.events) · two folds give the identical state_root (determinism).

Pre-retraction state (the live frontier) is recoverable by time-travel: folding
upto the pre-revoke seq still shows the chain live — the cascade is "after its
frontier", never a rewrite of history. Contract: run(k, line). Fail loud.
"""


def run(k, line):
    line("\n== RETRACTION CASCADE (DERIVED_AUTHORITY fails closed downhill) ==")

    # Forge a FRESH capability for this check (the shared kernel's `shell`/`echo`
    # budgets are already spent by earlier sections — README: forge what you need).
    # A no-cost echo handler, granted to Decima with a generous budget, is the parent
    # grant we will delegate then revoke.
    # No budget caveat: this check is about the CASCADE, not budgets — and the shared
    # kernel's per-agent spend ledger is already non-zero from earlier sections, which
    # a budget gate would trip. A budgetless READ cap keeps the focus on retraction.
    parent_cap = k.integrate_tool(
        "cascade.echo", lambda impl, args: {"out": args.get("text", "")},
        caveats={"effect_class": "READ"})

    w = k.weave()
    decima = w.get(k.decima_agent_id)

    # Delegate the parent grant downhill to a child, then the child re-delegates to a
    # grandchild — a two-hop derivation chain (parent → child → grandchild). Empty
    # `stricter` keeps the (budgetless) scope; attenuation can only narrow, never widen.
    child_id, child_grant, _ = k.spawn(decima, "CascadeChild", parent_cap,
                                       {}, "run under a delegated grant")
    w = k.weave()
    child = w.get(child_id)
    grand_id, grand_grant, _ = k.spawn(child, "CascadeGrand", child_grant,
                                       {}, "run under a re-delegated grant")

    # 1. Everyone can invoke through their grant BEFORE the retraction.
    w = k.weave()
    child, grand = w.get(child_id), w.get(grand_id)
    pre_parent = k.invoke(decima, parent_cap, {"text": "hi"})
    pre_child = k.invoke(child, child_grant, {"text": "hi"})
    pre_grand = k.invoke(grand, grand_grant, {"text": "hi"})
    assert "ok" in pre_parent, f"parent should invoke pre-retract: {pre_parent}"
    assert "ok" in pre_child, f"child should invoke pre-retract: {pre_child}"
    assert "ok" in pre_grand, f"grandchild should invoke pre-retract: {pre_grand}"
    line("  pre-retract: parent + delegated child + grandchild all invoke OK")

    # The frontier just BEFORE the retraction — for the time-travel check below.
    frontier = k.weft.count()
    pre_asserts = {child_grant, grand_grant}      # grant events that must survive

    # 2. RETRACT the PARENT grant (Morta). A capability RETRACT cascades to derived
    #    authority by default (FOLD §10.2 names capability revocation as this case).
    k.revoke(parent_cap)
    w = k.weave()

    # The targeted cell carries the cascade frontier; descendants are marked retracted
    # BY the cascade (cascaded=True), not by their own RETRACT event.
    assert w.get(parent_cap).retracted and w.get(parent_cap).cascade_root, \
        "parent must be retracted and a cascade root"
    cg, gg = w.get(child_grant), w.get(grand_grant)
    assert cg.retracted and cg.cascaded, "child grant must fail closed via cascade"
    assert gg.retracted and gg.cascaded, "grandchild grant must fail closed via cascade (transitive)"
    line("  cascade: parent retracted (root) → child + grandchild grants marked retracted")

    # 3. The cascade shows up in EVERY projection, not just the authorize gate:
    #    the cascaded grants drop out of of_type (the fold treats them as retracted).
    live_caps = {c.id for c in w.of_type("capability")}
    assert parent_cap not in live_caps, "retracted parent must leave of_type"
    assert child_grant not in live_caps, "cascaded child grant must leave of_type"
    assert grand_grant not in live_caps, "cascaded grandchild grant must leave of_type"

    # 4. The cascade — BOTH the parent holder AND the derived holders fail closed.
    post_parent = k.invoke(decima, parent_cap, {"text": "hi"})
    post_child = k.invoke(child, child_grant, {"text": "hi"})
    post_grand = k.invoke(grand, grand_grant, {"text": "hi"})
    assert "denied" in post_parent, f"parent must fail closed: {post_parent}"
    assert "denied" in post_child, f"child must fail closed (cascade): {post_child}"
    assert "denied" in post_grand, f"grandchild must fail closed (cascade): {post_grand}"
    line(f"  post-retract DENIED — parent: {post_parent['denied']}")
    line(f"                       child:  {post_child['denied']}")
    line(f"                       grand:  {post_grand['denied']}")

    # 5. History is intact: the prior grant asserts AND the retract event still read
    #    back from the Log (Law 1 — RETRACT withdraws effect, never erases history).
    eids = list(k.weft.events())
    asserted = {ev.body.get("cell") for ev in eids if ev.verb == "ASSERT"}
    assert pre_asserts <= asserted, "delegated grant asserts must remain on the Log"
    retract_seen = any(ev.verb == "RETRACT" and ev.body.get("cell") == parent_cap
                       for ev in eids)
    assert retract_seen, "the RETRACT event must remain on the Log"
    line("  history intact: grant asserts + the RETRACT event still in weft.events()")

    # 6. Time-travel: at the pre-revoke frontier the chain is STILL live — the cascade
    #    is strictly after its frontier, not a rewrite of the past.
    past = k.weave(upto_seq=frontier)
    assert not past.get(parent_cap).retracted, "parent live at the pre-revoke frontier"
    assert not past.get(child_grant).retracted, "child live at the pre-revoke frontier"
    assert not past.get(grand_grant).retracted, "grandchild live at the pre-revoke frontier"
    line(f"  time-travel: at seq ≤ {frontier} the whole chain folds live (cascade is post-frontier)")

    # 7. Determinism: two independent folds → identical state_root, and the cascade is
    #    the same regardless of fold path (arrival-order independent, idempotent — the
    #    derived pass is a pure function of the folded graph).
    r1, r2 = k.weave().state_root(), k.weave().state_root()
    assert r1 == r2, "two folds must give an identical state_root (cascade is deterministic)"
    line(f"  determinism: two folds → identical state_root ({r1[:12]}…)")

    line("  → RETRACT cascades to derived authority: revoke a parent grant and every "
         "grant attenuated from it (transitively) fails closed at the fold.")
