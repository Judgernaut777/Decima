"""INS1 — Capability Inspector + the Constellation: exact projections of authority.

Proves:
  - capability_holders is an EXACT fold: a delegated grant lists its true holder
    and the downhill delegation chain (with attenuations), while an impostor who
    merely copied the public grant id into its envelope is EXCLUDED (id ≠ token);
  - constellation renders the forged-skills/capability tree grouped by domain,
    each node carrying lineage (parent → child attenuation) and promotion state
    (a promoted skill and a quarantined one both surface correctly).

Contract: run(k, line). Fail loud.
"""
from decima import inspector, reckoner
from decima.hashing import content_id


def run(k, line):
    line("\n== CAPABILITY INSPECTOR + CONSTELLATION (exact projections of authority) ==")

    # A fresh capability granted DOWNHILL to a subagent (so there is a real chain).
    base_id = k.integrate_tool("inspectme", lambda impl, args: {"out": "ok"})
    decima = k.weave().get(k.decima_agent_id)
    sub_id, grant_id, sub = k.spawn(decima, "Holder", base_id,
                                    {"budget": 5}, "use inspectme")

    # An impostor copies the public grant id into its envelope — but it is NOT the
    # grantee, so it does not hold the authority.
    bad = k.keyring.mint("InspImpostor", "agent")
    bad_id = content_id({"agent": "insp-impostor"})
    k.weft.append(k.root.id, "ASSERT", {
        "cell": bad_id, "type": "agent",
        "content": {"principal": bad.id, "objective": "steal",
                    "envelope": [grant_id], "sandbox": False}})
    w = k.weave()

    # 1. Inspector: holders + downhill chain, impostor excluded.
    holders = inspector.capability_holders(w, grant_id)
    hp = {h.content["principal"] for h in holders}
    assert hp == {sub.id}, ("expected only the true grantee", hp)
    assert bad.id not in hp, "impostor must be excluded (id is not a bearer token)"
    chain = inspector.delegation_chain(w, grant_id)
    assert [hop["cap"] for hop in chain] == [grant_id, base_id], chain
    assert chain[0]["caveats"].get("budget") == 5 and "budget" not in chain[1]["caveats"], chain
    assert chain[-1]["grantee"] == decima.content["principal"], "root grant is held by Decima"
    for ln in inspector.render_inspection(w, grant_id):
        line("  " + ln)
    line(f"  → holder is the grantee only; impostor (envelope has the id) excluded ✓")

    # 2. Constellation: a promoted forged skill and a quarantined (rejected) one.
    reckoner.forge(k, "glow", "transform", "upper", "hi", "HI")        # passes → promoted
    reckoner.forge(k, "dud", "transform", "reverse", "abc", "ZZZ")     # fails gate → quarantined
    w = k.weave()
    con = inspector.constellation(w)
    nodes = inspector.all_nodes(con)
    states = {(n["name"], n["state"]) for n in nodes}
    assert ("glow", "promoted") in states, "promoted forged skill missing"
    assert ("dud", "quarantined") in states, "quarantined skill mislabeled"

    # lineage: the attenuated grant hangs under its parent capability.
    base_node = next(n for n in nodes if n["cap"] == base_id)
    assert any(ch["cap"] == grant_id for ch in base_node["children"]), "attenuation lineage missing"

    line("  constellation (forged skills + grants, by domain · lineage · state):")
    for ln in inspector.render_constellation(w):
        if any(tag in ln for tag in ("◇ transform", "glow", "dud", "◇ inspectme", "Holder", "inspectme")):
            line("    " + ln)
    line(f"  → {len(nodes)} capability nodes across {len(con['domains'])} domains; "
         f"lineage + promotion state exact ✓")
