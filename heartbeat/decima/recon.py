"""RECON1 — authorized recon / enumeration, the FIRST stage of the red-team kill
chain (CAPABILITY_MAP Part C: "RED-TEAM (recon → reporting)" — scanning/enum maps a
target's attack surface). It composes RED1 (the engagement-scope / authorize / Morta /
sandbox / finding-emit contract); it edits no module.

Recon is an OUTWARD reconnaissance action — pointing tooling at a host to map its
services/ports is itself an offensive act against that host — so it is governed by the
*same* rules as any RED1 probe:

  - `engagement` — the SCOPE (rules of engagement): the set/glob of targets this grant
    authorizes. Enumerating an OUT-OF-SCOPE host is a rules-of-engagement VIOLATION and
    is refused *before* anything is invoked;
  - `requires_approval` — Morta: an outward recon sweep is gated; it does not run until
    a human (or a Morta policy) approves it (forced on via RED1's Morta floor);
  - `sandbox` — an SB1 profile (network-denied): the reference enumerator is a
    DETERMINISTIC STUB. It contacts NO real host, sends NO packet, runs NO real scan.
    It maps a FIXED fake "attack surface" derived purely from the target string, so the
    whole module is reproducible and harm-free. It is a contract, NOT a scanner.

A sweep that clears every gate — IN-scope target, AUTHORIZED holder (ocap), and
APPROVED (Morta) — maps the target's stub surface (a deterministic set of fake
services/ports) and emits one `finding` Cell PER exposed service in **exactly the shape
DET1 emits** (`detection`/`rule`/`severity`/`source`/`excerpt` + a `found_in` edge),
each pointing at an `asset` Cell for the target — so the blue-team TRIAGE1 layer
correlates a recon finding into an incident with NO new wiring.

Composes PUBLIC APIs only: red.authorize_engagement / red.in_scope (the engagement
contract), kernel.invoke / approve / weave, model, executor.register, capability. No
edits to red.py, detection.py, triage.py, or any core file.
"""
from decima.capability import capability_content, with_morta_floor
from decima.hashing import content_id
from decima import model, executor, red

# The recon effect name — distinct from RED1's `redteam` effect, registered below as
# its OWN deterministic stub. Recon enumerates a surface; it fires no exploit.
EFFECT = "recon"

# A network-denied sandbox profile (SB1), scoped to THIS effect: a real enumerator
# reaching the network would be refused at the executor boundary. Recon is pure compute.
NO_NET_SANDBOX = {"effects": [EFFECT], "network": False}

# The fixed catalogue the stub maps from. Real recon would discover open ports; this
# derives a DETERMINISTIC subset of these from the target string alone — same target,
# same surface, every time, contacting nothing. (port, service, severity)
_SURFACE_CATALOGUE = [
    (22, "ssh", "medium"),
    (80, "http", "low"),
    (443, "https", "low"),
    (3306, "mysql", "high"),
    (6379, "redis", "high"),
    (8080, "http-alt", "medium"),
]


def _stub_enumerate(impl, args):
    """Deterministic recon stub (executor handler). Contacts NO real host and sends NO
    packet — it derives "what enumerating this target WOULD have surfaced" purely from a
    stable hash of the target string, selecting a fixed subset of the catalogue. The same
    target always yields the same surface; nothing leaves the sandbox."""
    target = str(args.get("target", ""))
    # A stable, integer-only selector over the target (ints, not floats — Law). The
    # content_id is the canonical content hash; fold it to a small int bitmask.
    seed = int(content_id({"recon_seed": target})[:8], 16)
    # NB: the module-level public `enumerate` shadows the builtin, so index explicitly.
    surface = [
        {"port": port, "service": svc, "severity": sev}
        for i in range(len(_SURFACE_CATALOGUE))
        for (port, svc, sev) in [_SURFACE_CATALOGUE[i]]
        if (seed >> i) & 1
    ]
    if not surface:                       # a reachable host always exposes SOMETHING
        port, svc, sev = _SURFACE_CATALOGUE[seed % len(_SURFACE_CATALOGUE)]
        surface = [{"port": port, "service": svc, "severity": sev}]
    return {"out": {"target": target, "surface": surface}}


# Register the handler at import time (idempotent — register overrides in place).
executor.register(EFFECT, _stub_enumerate)


def authorize_recon(k, name, scope, *, severity="medium", sandbox=None):
    """Stand up an AUTHORIZED RECON ENGAGEMENT: an attenuated capability whose caveats
    ARE the rules of engagement (scope + Morta floor + SB1 sandbox), held by a sandboxed
    recon agent (ocap — only that principal can wield it).

    Built directly on RED1's engagement contract (`red.authorize_engagement`), then
    re-pointed at the `recon` effect so RED1's offensive `redteam` stub is NOT what runs
    — RECON1's network-denied enumeration stub is. Returns (recon_agent_cell, cap_id)."""
    # Reuse RED1 verbatim to forge the engagement (scope, Morta floor, sandbox agent,
    # ocap grant). This gives us the exact rules-of-engagement contract RED1 proves.
    recon_agent, _ = red.authorize_engagement(
        k, name, scope, technique="enumerate", severity=severity, sandbox=sandbox)

    # RED1's grant targets its own `redteam` effect; recon is a DIFFERENT effect, so we
    # forge a sibling capability for THIS effect under the SAME engagement caveats. The
    # Morta floor + scope + SB1 profile carry over unchanged; only the effect differs.
    cav = red.engagement_caveats(scope, severity=severity, sandbox=sandbox or NO_NET_SANDBOX)
    # Belt-and-braces: the floor is already shell-class; re-floor is idempotent.
    cav = with_morta_floor("shell", cav)
    impl = {"op": "enumerate", "technique": "enumerate"}
    cap_id = content_id({"reconcap": name, "scope": list(scope), "effect": EFFECT})
    principal = recon_agent.content["principal"]
    cap = capability_content(name=f"recon.{name}", effect=EFFECT, target="*",
                             caveats=cav, impl=impl,
                             grantee=principal, granter=k.root.id)
    from decima.weft import ASSERT
    k.weft.append(k.root.id, ASSERT,
                  {"cell": cap_id, "type": "capability", "content": cap})

    # Re-mint the recon agent's envelope to hold the recon cap (so the SAME sandboxed
    # principal wields it). Content-addressed by name → idempotent re-assert.
    agent_id = content_id({"agent": f"redteam:{name}"})
    k.weft.append(k.root.id, ASSERT, {
        "cell": agent_id, "type": "agent",
        "content": {"principal": principal,
                    "objective": f"authorized recon engagement: {name}",
                    "envelope": [cap_id], "budget": 0, "sandbox": True},
    })
    return k.weave().get(agent_id), cap_id


def in_scope(k, cap_id, target) -> bool:
    """Rules-of-engagement check, delegated to RED1: is `target` within the grant's
    engagement scope? An empty/missing scope authorizes NOTHING (fail closed)."""
    return red.in_scope(k, cap_id, target)


def enumerate(k, recon_agent, cap_id, target, *, cost=0) -> dict:
    """Enumerate `target`'s attack surface under the recon engagement `cap_id`.

    The gate order mirrors RED1 deliberately:
      1. RULES OF ENGAGEMENT — an out-of-scope target is REFUSED here, before any invoke
         is written. Authorized tooling pointed at an unauthorized host is the cardinal
         red-team sin; it never reaches the executor.
      2. ocap + Morta + SB1 — delegated to `k.invoke`: an UNAUTHORIZED principal is
         DENIED (no grant / wrong grantee); an UNAPPROVED outward recon is DENIED until
         `k.approve(cap_id)`; the network-denied sandbox bounds what the stub may touch.
      3. On success — map the (deterministic, fake) surface and emit one `finding` Cell
         PER exposed service in DET1's exact shape, each with a `found_in` provenance
         edge back to an `asset` Cell for the target — so TRIAGE1 consumes them unchanged.

    Returns {"refused": reason} for a ROE violation, {"denied": reason} for an
    ocap/Morta/sandbox denial, or, on success, a dict with the surface, the emitted
    finding ids, the asset id, and the audit trail (invoke event / receipt / signer)."""
    if not in_scope(k, cap_id, target):
        return {"refused": f"target {target!r} is out of recon engagement scope "
                           f"(rules-of-engagement violation)"}

    res = k.invoke(recon_agent, cap_id, {"target": target, "cost": cost})
    if "denied" in res:
        return {"denied": res["denied"]}

    out = res["ok"]["out"]
    surface = out["surface"]
    cap = k.weave().get(cap_id)
    name = cap.content["name"]
    default_sev = cap.content.get("caveats", {}).get("severity", "medium")
    signer = k.principal_for(recon_agent)

    # Record the target as an asset Cell — the provenance source the findings point at,
    # mirroring how DET1 findings reference an observed Cell (and how RED1 records assets).
    asset_id = content_id({"asset": target})
    model.assert_content(k.weft, signer, asset_id, "asset", {"target": target})

    # One finding PER exposed service, each in DET1's EXACT shape so triage.correlate
    # consumes it unchanged. `rule` is this recon engagement cap; `source` is the asset.
    # Each finding inherits its service's severity (a mysql/redis exposure is high).
    findings = []
    for svc in surface:
        sev = svc.get("severity", default_sev)
        excerpt = (f"enumerate@{target}: {svc['service']} exposed on port {svc['port']} "
                   f"(stub-mapped surface; no live host contacted)")
        fid = content_id({"finding": cap_id, "in": asset_id, "port": svc["port"]})
        model.assert_content(k.weft, signer, fid, "finding", {
            "detection": name, "rule": cap_id, "severity": sev,
            "source": asset_id, "excerpt": excerpt,
            "port": svc["port"], "service": svc["service"],
        })
        model.assert_edge(k.weft, signer, fid, "found_in", asset_id)
        findings.append(fid)

    return {"surface": surface, "findings": findings, "asset": asset_id,
            "receipt": res["result_cell"], "invoke_event": res["invoke_event"],
            "signer": res["signer"]}
