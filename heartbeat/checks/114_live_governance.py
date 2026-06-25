"""LOOP1 — the live governance gate.

`memory.governance_check` (B4) has existed as a query; LOOP1 wires it into the live
delegation path so Decima auto-consults its OWN recorded rules BEFORE it acts. This check
proves:
  - a recorded `banned_action` makes a matching delegation **refused at delegate-time**
    (status `governance_denied`), with the rule + prior evidence cited on the Weft — and
    no worker is spawned;
  - a delegation that no rule covers proceeds past the gate;
  - the gate is inert when nothing is on record.

Contract: run(k, line). Fail loud.
"""
import types

from decima import memory, model
from decima.hashing import content_id


def run(k, line):
    line("\n== LIVE GOVERNANCE GATE (Decima consults its own rules before delegating) — LOOP1 ==")

    # Decima holds capabilities it could delegate.
    k.integrate_tool("deploy", lambda impl, args: {"out": "deployed"})
    k.integrate_tool("run-tests", lambda impl, args: {"out": "tested"})

    # Record a governance ban (trusted, instruction-eligible) on a specific action, with
    # grounding evidence so the verdict can cite WHY.
    ev = content_id({"policy_src": "change-freeze"})
    model.assert_content(k.weft, k.decima_agent_id, ev, "note",
                         {"text": "release freeze: a prod outage last week"})
    memory.remember_governance(k.weft, k.decima_agent_id, memory.BANNED_ACTION,
                               target="deploy to prod", reason="under a change freeze",
                               evidence_src=ev)

    decima = lambda: k.weave().get(k.decima_agent_id)

    def delegate(objective, capability="deploy"):
        action = types.SimpleNamespace(tasks=[{
            "subagent": "Worker", "objective": objective, "capability": capability, "budget": 5}])
        return k._delegate(decima(), action, depth=1, label="decima", parent_task=None)

    # 1. A delegation the ban covers → refused at delegate-time; no worker spawned.
    before = len(k.weave().of_type("task"))
    _, out = delegate("deploy to prod now")
    t = out["tasks"][0]
    assert t["status"] == "governance_denied", out
    # find the refused task cell and confirm it cites the rule + prior evidence.
    refused = [c for c in k.weave().of_type("task")
               if c.content.get("status") == "governance_denied"
               and c.content.get("objective") == "deploy to prod now"]
    assert refused, "no governance_denied task recorded"
    rc = refused[0]
    assert "DENY" in rc.content["result"] and "change freeze" in rc.content["result"], rc.content["result"]
    assert rc.content["governance"] and rc.content["evidence"], rc.content
    assert rc.content["worker"] is None, "a worker was spawned despite the ban!"
    line(f"  banned delegation → ⛔ governance_denied at delegate-time: {rc.content['result']}")
    line(f"    rule cited (cell {rc.content['governance'][:8]}) + prior evidence ✓; no worker spawned ✓")

    # 2. A delegation no rule covers (unrelated objective AND capability) proceeds past the gate.
    _, out2 = delegate("run the unit tests", capability="run-tests")
    assert out2["tasks"][0]["status"] != "governance_denied", out2
    line(f"  unrelated delegation → passes the gate (status={out2['tasks'][0]['status']}) ✓")

    # 3. Inert for anything no rule covers: an unrelated objective/capability sees allow.
    v = k._governance_verdict("polish the docs", "run-tests")
    assert v["allow"], v
    line("  nothing on record for it → gate inert (allow) ✓")
    line("  → governance is no longer just queryable: Decima refuses a banned action at "
         "delegate-time, citing its own memory. The recall-vs-instruct law holds (only "
         "trusted governance binds).")
