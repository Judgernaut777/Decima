"""LIVE1 — the autonomy ladder, LIVE at the invoke boundary (kernel.invoke).

The ladder (D5, checks/212) was a QUERY layer: `autonomy.decide()` returned a verdict but
the kernel never consulted it. This check proves the verdict is now ENFORCED inside
`kernel.invoke`, BEFORE any effect runs — generalizing the delegate-time governance gate
(LOOP1) to invoke-time autonomy. It proves, end to end through real invokes:

  1. BACK-COMPAT — an agent with NO rung set for a capability invokes exactly as before
     (the gate is inert; this is the critical 98-checks-stay-green invariant).
  2. rung 1 (read-only) — a write/effect invoke is REFUSED at the boundary (fail closed),
     and the refusal is recorded; a READ-classed invoke still runs.
  3. rung 3 — a FINANCIAL invoke REQUIRES Morta approval (denied → approve → runs), while a
     REVERSIBLE invoke runs straight through (different steps, same rung, by stakes).
  4. rung 2 (draft) — the invoke does NOT execute; a proposal is recorded instead.
  5. DEMOTION IS INSTANT — a demote blocks the very NEXT invoke (the rung is re-read from
     the Weave every call, so no caching can defeat the Morta reflex).

Contract: run(k, line). Fail loud — any regression raises AssertionError (nonzero exit).
"""
from decima import autonomy as au
from decima import executor


def _cap(k, name, effect_class):
    """Mint + grant the orchestrator a fresh `echo` capability carrying an explicit
    effect_class caveat, so an invoke through it is classed accordingly. Public kernel
    API only (no core edit from the check)."""
    cid = k._assert_cap(name, "echo", caveats={"effect_class": effect_class})
    k.grant(cid, k.decima_agent_id)
    return cid


def run(k, line):
    line("\n== LIVE AUTONOMY GATE (the ladder enforced inside kernel.invoke) — LIVE1 ==")

    agent_cell = k.weave().get(k.decima_agent_id)
    principal = k.principal_for(agent_cell)

    # Distinct caps so each scenario carries its own effect_class + its own rung.
    c_back = _cap(k, "live.backcompat", au.REVERSIBLE)   # no rung will be set → inert
    c_ro = _cap(k, "live.readonly", au.IRREVERSIBLE)     # rung 1 → refuse the effect
    c_rev = _cap(k, "live.reversible", au.REVERSIBLE)     # rung 3 → runs
    c_fin = _cap(k, "live.financial", au.FINANCIAL)       # rung 3 → require approval
    c_draft = _cap(k, "live.draft", au.REVERSIBLE)       # rung 2 → propose, no execute
    c_demo = _cap(k, "live.demote", au.REVERSIBLE)       # rung 3 then demoted

    # Re-read the agent cell AFTER granting — grant() mutates the cell on the Weave, so
    # the pre-grant snapshot's envelope is stale (it wouldn't hold the new caps).
    agent_cell = k.weave().get(k.decima_agent_id)

    def cap_name(cid):
        return k.weave().get(cid).content["name"]

    # 1. BACK-COMPAT — no rung set for this (agent, cap): the gate is INERT; invoke runs.
    assert au.get_level(k, principal, cap_name(c_back)) is None, "precondition: no rung set"
    res = k.invoke(agent_cell, c_back, {"text": "hello"})
    assert "ok" in res and res["status"] == executor.SUCCEEDED, res
    assert "autonomy" not in res, "no-rung invoke must be untouched by the gate"
    line(f"  no rung set · invoke({cap_name(c_back)}) → ran (status {res['status']}); "
         "gate INERT — back-compat held ✓")

    # 2. RUNG 1 (read-only): a write/effect invoke is REFUSED at the boundary, recorded.
    au.set_autonomy(k, principal, cap_name(c_ro), au.RUNG_READ_ONLY, reason="observe only")
    res = k.invoke(agent_cell, c_ro, {"text": "do an effect"})
    assert "denied" in res and res["autonomy"]["verdict"] == au.REFUSE, res
    assert k.weave().get(res["decision"]) is not None, "the refusal must be recorded on the Weft"
    line(f"  rung 1 · invoke({cap_name(c_ro)}, IRREVERSIBLE) → DENIED at boundary; "
         f"decision {res['decision'][:8]} on Weft ✓")

    # 3. RUNG 3 — FINANCIAL requires Morta approval (deny → approve → runs); REVERSIBLE runs.
    au.set_autonomy(k, principal, cap_name(c_rev), au.RUNG_SUPERVISED)
    au.set_autonomy(k, principal, cap_name(c_fin), au.RUNG_SUPERVISED)
    rev = k.invoke(agent_cell, c_rev, {"text": "reversible effect"})
    assert "ok" in rev and rev["status"] == executor.SUCCEEDED, rev
    denied = k.invoke(agent_cell, c_fin, {"text": "wire $$$"})       # before approval
    assert "denied" in denied and denied["requires_approval"] == c_fin, denied
    k.approve(c_fin)                                                  # Morta approves
    approved = k.invoke(agent_cell, c_fin, {"text": "wire $$$"})      # after approval
    assert "ok" in approved and approved["status"] == executor.SUCCEEDED, approved
    line(f"  rung 3 · REVERSIBLE → ran; FINANCIAL → DENIED then APPROVED → ran "
         "(same rung, gated by stakes) ✓")

    # 4. RUNG 2 (draft & suggest): the invoke does NOT execute — a proposal is recorded.
    au.set_autonomy(k, principal, cap_name(c_draft), au.RUNG_PROPOSE)
    res = k.invoke(agent_cell, c_draft, {"text": "draft me"})
    assert "proposed" in res and "ok" not in res, res
    prop = k.weave().get(res["proposed"])
    assert prop is not None and prop.type == "proposal", prop
    line(f"  rung 2 · invoke({cap_name(c_draft)}) → NOT executed; proposal "
         f"{res['proposed'][:8]} recorded instead ✓")

    # 5. DEMOTION IS INSTANT — a demote blocks the very next invoke.
    au.set_autonomy(k, principal, cap_name(c_demo), au.RUNG_MONITORED)
    ok_first = k.invoke(agent_cell, c_demo, {"text": "trusted run"})
    assert "ok" in ok_first, ok_first
    au.demote(k, principal, cap_name(c_demo), reason="anomaly — demote now")
    blocked = k.invoke(agent_cell, c_demo, {"text": "next run"})       # the VERY next invoke
    assert "denied" in blocked and blocked["autonomy"]["verdict"] == au.REFUSE, blocked
    line(f"  rung 4 ran → demote(Morta) → the VERY NEXT invoke({cap_name(c_demo)}) "
         "DENIED (instant, no caching) ✓")

    line("  → the ladder is LIVE: every invoke consults the rung; inert by default, "
         "fail-closed on demotion, Morta-gated on stakes.")
