"""Capabilities — Law 2: no ambient authority.

A capability is a Cell (authority is data). To act, an agent must hold the
capability's id in its envelope. Authority only ever flows DOWNHILL:
`attenuate` can narrow a capability but never widen it. A compromised or
prompt-injected agent's blast radius is exactly its envelope — there is no
escalation path to inject toward, because root does not exist.
"""
from decima.weft import ASSERT


def capability_content(name, effect, target="*", caveats=None, delegable=True,
                       impl=None, quarantined=False, parent=None):
    return {
        "name": name,
        "effect": effect,             # echo | shell | transform | forge
        "target": target,
        "caveats": caveats or {},     # budget, expires, rate, requires_approval, sandbox_only
        "delegable": delegable,
        "impl": impl,                 # for authored caps: how the effect is realized
        "quarantined": quarantined,   # born True for forged caps until Nona promotes
        "parent": parent,             # the cap this was attenuated from, if any
    }


def envelope_holds(weave, agent_cell, cap_id) -> bool:
    """True if the agent holds cap_id directly, or holds an ancestor it was
    attenuated from (downhill authority is still authority)."""
    env = set(agent_cell.content.get("envelope", []))
    if cap_id in env:
        return True
    cap = weave.get(cap_id)
    seen = set()
    while cap and cap.content.get("parent") and cap.id not in seen:
        seen.add(cap.id)
        parent = cap.content["parent"]
        if parent in env:
            return True
        cap = weave.get(parent)
    return False


def authorize(weave, agent_cell, cap_id, args, spent: float = 0.0,
              approvals=None) -> tuple[bool, str]:
    """The ocap check performed before every INVOKE is written to the Weft."""
    approvals = approvals or set()
    cap = weave.get(cap_id)
    if cap is None:
        return False, "no such capability"
    if cap.type != "capability":
        return False, "target is not a capability"
    if cap.retracted:
        return False, "capability revoked (RETRACTed)"
    agent_is_sandbox = agent_cell.content.get("sandbox", False)
    if cap.content.get("quarantined") and not agent_is_sandbox:
        return False, "capability quarantined (not promoted by Nona)"
    if not envelope_holds(weave, agent_cell, cap_id):
        return False, "not in agent envelope (no ambient authority)"
    caveats = cap.content.get("caveats", {})
    budget = caveats.get("budget")
    if budget is not None and spent + float(args.get("cost", 0)) > float(budget):
        return False, f"budget exceeded (cap budget {budget})"
    if caveats.get("requires_approval") and cap_id not in approvals:
        return False, "requires human approval (Morta gate)"
    if caveats.get("sandbox_only") and not agent_is_sandbox:
        return False, "sandbox_only: not runnable outside a sandbox principal"
    return True, "ok"


def attenuate(parent_content: dict, stricter: dict, parent_id: str) -> dict:
    """Derive a weaker capability. Caveats can only get tighter."""
    caveats = dict(parent_content.get("caveats", {}))
    for k, v in stricter.items():
        if k == "budget":
            caveats["budget"] = min(float(v), float(caveats.get("budget", v)))
        else:
            caveats[k] = v  # adding a constraint (e.g. requires_approval) only narrows
    return capability_content(
        name=parent_content["name"] + "·att",
        effect=parent_content["effect"],
        target=parent_content["target"],
        caveats=caveats,
        delegable=parent_content["delegable"],
        impl=parent_content.get("impl"),
        quarantined=parent_content.get("quarantined", False),
        parent=parent_id,
    )
