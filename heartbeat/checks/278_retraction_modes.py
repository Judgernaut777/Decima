"""C278 — retraction MODES: SUPERSEDE + TERMINATE (WEFT §5 / FOLD §10).

RETRACT carries a `mode` (WEFT §5). Two hardening modes on top of WITHDRAW/REDACT:

  - SUPERSEDE — tombstone a cell AND record the `replacement` that took its place
    (an event id or cell id). Unlike REDACT the payload is NOT erased (still readable
    via the events); unlike a capability WITHDRAW it does NOT cascade by default. A
    superseded version drops out of `of_type` but simply points forward.

  - TERMINATE — a hard shutdown that fails closed the whole lease/authority tree
    descending from the cell (default cascade LEASE_TREE). The targeted cell becomes a
    `cascade_root` (mode LEASE_TREE) and every grant/lease derived from it fails closed
    at the fold — the SAME derived, order-independent, idempotent pass DERIVED_AUTHORITY
    uses. The payload is NOT erased.

Existing WITHDRAW / REDACT / DERIVED_AUTHORITY behavior is unchanged. Contract:
run(k, line). Fail loud.
"""
from decima import model
from decima.hashing import content_id


def run(k, line):
    line("\n== RETRACTION MODES: SUPERSEDE + TERMINATE (WEFT §5) ==")
    wf, root = k.weft, k.root.id

    # 1. SUPERSEDE — v1 replaced by v2. Tombstone but NOT erased; no cascade.
    v1 = content_id({"doc": "policy", "v": 1})
    v2 = content_id({"doc": "policy", "v": 2})
    model.assert_content(wf, root, v1, "doc", {"title": "policy", "rev": 1, "body": "draft"})
    model.assert_content(wf, root, v2, "doc", {"title": "policy", "rev": 2, "body": "final"})
    live = {c.id for c in k.weave().of_type("doc")}
    assert v1 in live and v2 in live, "both versions must be live pre-supersede"

    before = k.weave().state_root()
    k.supersede(v1, replacement=v2)          # point v1 forward at v2
    w = k.weave()
    c1 = w.get(v1)
    assert c1.retracted, "superseded v1 must be retracted"
    assert not c1.redacted, "SUPERSEDE must NOT redact"
    assert c1.content.get("body") == "draft", f"payload must survive supersede: {c1.content}"
    assert c1.superseded_by == v2, f"superseded_by must point to the replacement: {c1.superseded_by}"
    assert not c1.cascade_root and c1.cascade_mode is None, "SUPERSEDE must not cascade by default"
    live = {c.id for c in w.of_type("doc")}
    assert v1 not in live, "superseded v1 must drop out of of_type"
    assert v2 in live and not w.get(v2).retracted, "replacement v2 must stay live"
    after = k.weave().state_root()
    assert before != after, "supersede must change the state_root"
    line(f"  SUPERSEDE: v1 tombstoned (payload intact body={c1.content['body']!r}), "
         f"superseded_by→{v2[:8]}; v2 live; state_root {before[:8]}→{after[:8]}")

    # 2. TERMINATE with LEASE_TREE cascade — parent + derived child fail closed.
    #    A budgetless READ echo cap (forge what you need); delegate it downhill.
    parent_cap = k.integrate_tool(
        "term.echo", lambda impl, args: {"out": args.get("text", "")},
        caveats={"effect_class": "READ"})
    decima = k.weave().get(k.decima_agent_id)
    child_id, child_grant, _ = k.spawn(decima, "TerminateChild", parent_cap,
                                       {}, "run under a delegated grant")
    w = k.weave()
    child = w.get(child_id)
    assert "ok" in k.invoke(decima, parent_cap, {"text": "hi"}), "parent must invoke pre-terminate"
    assert "ok" in k.invoke(child, child_grant, {"text": "hi"}), "child must invoke pre-terminate"
    line("  pre-terminate: parent + delegated child both invoke OK")

    k.terminate(parent_cap)                  # default cascade LEASE_TREE
    w = k.weave()
    p = w.get(parent_cap)
    assert p.retracted and p.cascade_root, "terminated parent must be retracted + cascade_root"
    assert p.cascade_mode == "LEASE_TREE", f"cascade_mode must be LEASE_TREE: {p.cascade_mode}"
    assert not p.redacted, "TERMINATE must NOT redact the payload"
    cg = w.get(child_grant)
    assert cg.retracted and cg.cascaded, "derived child grant must fail closed via LEASE_TREE cascade"
    live_caps = {c.id for c in w.of_type("capability")}
    assert parent_cap not in live_caps and child_grant not in live_caps, "both must leave of_type"
    assert "denied" in k.invoke(child, child_grant, {"text": "hi"}), "child must fail closed post-terminate"
    line(f"  TERMINATE: parent cascade_root (mode={p.cascade_mode}, payload intact) → "
         f"child grant fails closed (retracted+cascaded)")

    # 3. Sanity — WITHDRAW / REDACT / DERIVED_AUTHORITY defaults unchanged.
    wd = content_id({"doc": "withdraw-me"})
    model.assert_content(wf, root, wd, "doc", {"body": "x"})
    k.revoke(wd)                             # WITHDRAW (non-capability): tombstone only
    wc = k.weave().get(wd)
    assert wc.retracted and not wc.redacted and wc.superseded_by is None, "WITHDRAW must be unchanged"

    rd = content_id({"doc": "redact-me"})
    model.assert_content(wf, root, rd, "doc", {"body": "secret"})
    k.redact(rd)
    rc = k.weave().get(rd)
    assert rc.retracted and rc.redacted and rc.content == {}, "REDACT must be unchanged"

    da = k.integrate_tool("da.echo", lambda impl, args: {"out": ""},
                          caveats={"effect_class": "READ"})
    k.revoke(da)                             # capability WITHDRAW → DERIVED_AUTHORITY default
    dc = k.weave().get(da)
    assert dc.cascade_root and dc.cascade_mode == "DERIVED_AUTHORITY", \
        "a capability WITHDRAW must still default to the DERIVED_AUTHORITY cascade"
    line("  sanity: WITHDRAW / REDACT / DERIVED_AUTHORITY defaults all unchanged")

    # 4. Determinism — two folds → identical state_root (cascade is a pure derived pass).
    r1, r2 = k.weave().state_root(), k.weave().state_root()
    assert r1 == r2, "two folds must give an identical state_root"
    line(f"  determinism: two folds → identical state_root ({r1[:12]}…)")

    line("  → SUPERSEDE points a tombstone forward without erasing or cascading; "
         "TERMINATE fails closed the whole lease tree — both at the fold.")
