"""Capabilities — Law 2: no ambient authority.

A capability is a Cell (authority is data). Authority is NOT the public Cell id —
ids are content hashes that appear all over the log and graph. Authority is a
*signed grant* to a specific principal, plus that principal proving possession of
its key on each request. Before any INVOKE is written, the kernel verifies, in
order: the signer is the acting agent, the agent holds a grant whose grantee is
that principal, the delegation path is downhill and granter-held, then every
caveat (budget, approval, sandbox).

Authority only ever flows DOWNHILL: `attenuate` narrows, never widens. A
compromised or prompt-injected agent's blast radius is exactly its grants — and
knowing a capability id buys nothing, because the id is not a bearer token.
"""
from decima.weft import ASSERT


def capability_content(name, effect, target="*", caveats=None, delegable=True,
                       impl=None, quarantined=False, parent=None,
                       grantee=None, granter=None):
    return {
        "name": name,
        "effect": effect,             # echo | shell | transform | forge
        "target": target,
        "caveats": caveats or {},     # budget, expires, rate, requires_approval, sandbox_only
        "delegable": delegable,
        "impl": impl,                 # for authored caps: how the effect is realized
        "quarantined": quarantined,   # born True for forged caps until Nona promotes
        "parent": parent,             # the cap this was attenuated from, if any
        "grantee": grantee,           # the principal this grant was issued TO
        "granter": granter,           # the principal that issued this grant
    }


def envelope_holds(weave, agent_cell, cap_id) -> bool:
    """True if the agent holds cap_id directly — a grant edge in its envelope."""
    return cap_id in set(agent_cell.content.get("envelope", []))


def _caveats_downhill(child: dict, parent: dict) -> bool:
    """Child caveats must be at least as strict as the parent's."""
    pc, cc = parent.get("caveats", {}), child.get("caveats", {})
    if "budget" in pc:                                  # budget may only shrink
        if "budget" not in cc or float(cc["budget"]) > float(pc["budget"]):
            return False
    for k, v in pc.items():                             # parent constraints must persist
        if k == "budget":
            continue
        if v and not cc.get(k):
            return False
    return True


def verify_delegation(weave, cap) -> tuple[bool, str]:
    """Walk the grant chain to its root, checking each hop is downhill and that
    the granter actually held what it delegated (granter == parent's grantee)."""
    seen = set()
    while cap.content.get("parent"):
        if cap.id in seen:
            return False, "cyclic delegation"
        seen.add(cap.id)
        parent = weave.get(cap.content["parent"])
        if parent is None or parent.type != "capability":
            return False, "broken delegation: parent grant missing"
        if parent.retracted:
            return False, "delegation path revoked upstream (Morta)"
        if cap.content.get("granter") != parent.content.get("grantee"):
            return False, "granter did not hold the parent grant"
        if not _caveats_downhill(cap.content, parent.content):
            return False, "attenuation widened authority (not downhill)"
        cap = parent
    return True, "ok"


def authorize(weave, agent_cell, cap_id, args, acting_principal,
              spent: float = 0.0, approvals=None) -> tuple[bool, str]:
    """The ocap check performed before every INVOKE is written to the Weft.

    `acting_principal` is the principal that will SIGN the INVOKE. The id being
    public is exactly why this — not id-possession — is the gate.
    """
    approvals = approvals or set()

    # 0. Possession proof: you act as yourself. The signer must be the agent.
    if acting_principal != agent_cell.content.get("principal"):
        return False, "signer is not the acting agent (possession proof failed)"

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

    # 1. The grant must be in the agent's envelope...
    if not envelope_holds(weave, agent_cell, cap_id):
        return False, "no grant in envelope (no ambient authority)"
    # 2. ...and that grant must name THIS principal as its grantee.
    grantee = cap.content.get("grantee")
    if grantee is not None and grantee != acting_principal:
        return False, "grant issued to a different principal (id is public, not a bearer token)"
    # 3. The delegation path must be downhill and granter-held.
    ok, why = verify_delegation(weave, cap)
    if not ok:
        return False, why

    # 4. Caveats.
    caveats = cap.content.get("caveats", {})
    budget = caveats.get("budget")
    if budget is not None and spent + float(args.get("cost", 0)) > float(budget):
        return False, f"budget exceeded (grant budget {budget}, spent {spent})"
    if caveats.get("requires_approval") and cap_id not in approvals:
        return False, "requires human approval (Morta gate)"
    if caveats.get("sandbox_only") and not agent_is_sandbox:
        return False, "sandbox_only: not runnable outside a sandbox principal"
    return True, "ok"


def attenuate(parent_content: dict, stricter: dict, parent_id: str,
              grantee: str, granter: str) -> dict:
    """Derive a weaker capability granted to `grantee` by `granter`.
    Caveats can only get tighter."""
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
        grantee=grantee,
        granter=granter,
    )
