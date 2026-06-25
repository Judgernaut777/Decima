"""Capability Inspector + the Constellation — exact projections of authority.

Two read-only folds over the Weave (no core edits, no new events): "who holds what
authority, and along which delegation path" (the Inspector, `CAPABILITY_MAP` A2 —
the Fuchsia-validated capability inspection), and "what skills/capabilities exist,
with what lineage and promotion state" (the Constellation, D1 — the Skyrim-style
skill tree). Both are EXACT folds, never heuristic: they read the same envelope +
grant-chain truth that `capability.authorize` enforces, so the inspector can never
disagree with the gate.

The Constellation here is the data model behind the eventual GUI; it renders as
display lines now (the shape `kernel.task_tree` / `workspace` use).
"""
from __future__ import annotations


# ── Capability Inspector ────────────────────────────────────────────────────
def capability_holders(weave, cap_id: str) -> list:
    """Every agent that TRULY holds `cap_id`. Possession is envelope membership AND
    the grant naming that agent's principal as grantee — exactly `authorize`'s test,
    so an impostor that merely copied the public id into its envelope is EXCLUDED
    (a capability id is not a bearer token). Exact fold over agent + capability Cells."""
    cap = weave.get(cap_id)
    grantee = cap.content.get("grantee") if cap else None
    holders = []
    for agent in weave.of_type("agent"):
        env = agent.content.get("envelope", [])
        if cap_id in env and (grantee is None or grantee == agent.content.get("principal")):
            holders.append(agent)
    return holders


def delegation_chain(weave, cap_id: str) -> list[dict]:
    """The downhill grant chain from `cap_id` up to its root, leaf-first. Each hop
    is the real `capability.parent` link, so the attenuation is visible (caveats
    tighten, grantee changes) — never inferred. Cycle-guarded."""
    chain, seen = [], set()
    cap = weave.get(cap_id)
    while cap is not None and cap.id not in seen:
        seen.add(cap.id)
        c = cap.content
        chain.append({
            "cap": cap.id,
            "name": c.get("name"),
            "effect": c.get("effect"),
            "granter": c.get("granter"),
            "grantee": c.get("grantee"),
            "caveats": c.get("caveats", {}),
            "quarantined": bool(c.get("quarantined", False)),
            "retracted": bool(cap.retracted),
        })
        parent = c.get("parent")
        cap = weave.get(parent) if parent else None
    return chain


def inspect(weave, cap_id: str) -> dict:
    """Holders + the full delegation chain for one capability."""
    cap = weave.get(cap_id)
    return {
        "cap": cap_id,
        "name": cap.content.get("name") if cap else None,
        "effect": cap.content.get("effect") if cap else None,
        "holders": [{"agent": a.id,
                     "principal": a.content.get("principal"),
                     "objective": a.content.get("objective"),
                     "sandbox": bool(a.content.get("sandbox", False))}
                    for a in capability_holders(weave, cap_id)],
        "chain": delegation_chain(weave, cap_id),
    }


def render_inspection(weave, cap_id: str) -> list[str]:
    info = inspect(weave, cap_id)
    lines = [f"capability {info['name']} ({cap_id[:8]}) · effect {info['effect']}"]
    held = [f"{h['principal'][:8]}{'(sandbox)' if h['sandbox'] else ''}" for h in info["holders"]]
    lines.append(f"  holders: {', '.join(held) or '(none)'}")
    lines.append("  delegation chain (leaf → root):")
    for hop in info["chain"]:
        cav = hop["caveats"]
        budget = f" budget≤{cav['budget']}" if "budget" in cav else ""
        approval = " +approval" if cav.get("requires_approval") else ""
        state = " [quarantined]" if hop["quarantined"] else ""
        lines.append(f"    {hop['cap'][:8]} {hop['name']} "
                     f"granter={(hop['granter'] or '—')[:8]} grantee={(hop['grantee'] or '—')[:8]}"
                     f"{budget}{approval}{state}")
    return lines


# ── The Constellation (forged-skills / capability tree) ─────────────────────
def _node(weave, cap, children_map) -> dict:
    c = cap.content
    return {
        "cap": cap.id,
        "name": c.get("name"),
        "effect": c.get("effect"),
        # Promotion state is the quarantine flag the Reckoner toggles on promotion.
        "state": "quarantined" if c.get("quarantined") else "promoted",
        "grantee": c.get("grantee"),
        "caveats": c.get("caveats", {}),
        "children": [_node(weave, ch, children_map)
                     for ch in sorted(children_map.get(cap.id, []),
                                      key=lambda x: (x.content.get("name", ""), x.id))],
    }


def constellation(weave) -> dict:
    """Project the live capability set into a domain-grouped lineage forest. A root
    is a capability with no live parent grant; attenuations hang under their parent
    (the delegation lineage). Grouped by `effect` (the domain). Retracted caps are
    already absent (`of_type`). This is the Constellation's data model."""
    caps = list(weave.of_type("capability"))
    by_id = {c.id: c for c in caps}
    children_map: dict[str, list] = {}
    roots = []
    for c in caps:
        parent = c.content.get("parent")
        if parent and parent in by_id:
            children_map.setdefault(parent, []).append(c)
        else:
            roots.append(c)        # root, or orphaned by a retracted parent
    domains: dict[str, list] = {}
    for r in roots:
        domains.setdefault(r.content.get("effect", "?"), []).append(_node(weave, r, children_map))
    for d in domains:
        domains[d].sort(key=lambda n: (n["name"], n["cap"]))
    return {"domains": domains}


def walk(nodes):
    """Depth-first iterator over a constellation forest (nodes + their children)."""
    for node in nodes:
        yield node
        yield from walk(node["children"])


def all_nodes(con: dict) -> list[dict]:
    return [n for nodes in con["domains"].values() for n in walk(nodes)]


def render_constellation(weave) -> list[str]:
    con = constellation(weave)
    glyph = {"promoted": "★", "quarantined": "☆"}
    lines = []
    for domain in sorted(con["domains"]):
        lines.append(f"◇ {domain}")
        for node in con["domains"][domain]:
            _render_node(node, 1, lines, glyph)
    return lines


def _render_node(node, depth, lines, glyph):
    cav = node["caveats"]
    budget = f" ≤{cav['budget']}" if "budget" in cav else ""
    lines.append(f"{'  ' * depth}{glyph.get(node['state'], '·')} "
                 f"{node['name']}{budget} [{node['state']}]")
    for child in node["children"]:
        _render_node(child, depth + 1, lines, glyph)
