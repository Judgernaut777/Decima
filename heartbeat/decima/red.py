"""RED1 — red-team capability scoped to an AUTHORIZED ENGAGEMENT (CAPABILITY_MAP
Part C, the offensive half of the security flagship; the *purple loop's* red end).

An offensive action — a probe / exploit-attempt against a target — is just a
`capability` (Law 2: authority is data). Its caveats carry the **rules of
engagement**:

  - `engagement` — the SCOPE: the set/glob of targets this grant authorizes. An
    attempt against an out-of-scope target is a rules-of-engagement VIOLATION and
    is refused *before* anything is invoked (the engagement bounds the blast
    radius the way a budget bounds spend);
  - `requires_approval` — Morta: an offensive action is outward/irreversible, so it
    is gated; it does not run until a human (or a Morta policy) approves it;
  - `sandbox` — an SB1 profile (network-denied by default here): defense-in-depth
    under ocap. The reference probe is a DETERMINISTIC STUB — it contacts no real
    target, runs no real payload, does no real harm. It is a contract, modelled
    like the payment rail / browser / inference stubs, NOT a weapon.

A probe that clears every gate — IN-scope target, AUTHORIZED holder (ocap), and
APPROVED (Morta) — emits a `finding` Cell in **exactly the shape DET1 emits**
(`detection`/`rule`/`severity`/`source`/`excerpt` + a `found_in` edge), so the
blue-team TRIAGE1 layer can correlate a red-team finding into an incident with no
new wiring. That is the **PURPLE LOOP**: a red-team finding becomes a blue-team
fixture.

Composes PUBLIC APIs only (kernel.invoke / grant, capability, model, weft,
executor.register) — no edits to any core file, detection.py, triage.py, or
smoke.py.
"""
import fnmatch

from decima.weft import ASSERT
from decima.capability import capability_content, with_morta_floor
from decima.hashing import content_id
from decima import model, executor

# The offensive effect name. Registered as a DETERMINISTIC stub below: it records
# the attempt and returns a finding-shaped result without touching the world.
EFFECT = "redteam"

# A network-denied sandbox profile (SB1): the reference probe is pure compute, so a
# real handler reaching the network would be refused at the executor boundary.
NO_NET_SANDBOX = {"effects": [EFFECT], "network": False}


def _stub_probe(impl, args):
    """Deterministic offensive-action stub (executor handler). Contacts NO real
    target and runs NO real payload — it derives a fixed "what a probe of this
    technique WOULD have found" record purely from its inputs, so the whole module
    is reproducible and harm-free. The same (target, technique) always yields the
    same result; nothing is sent anywhere."""
    target = str(args.get("target", ""))
    technique = (impl or {}).get("technique", "probe")
    # A stand-in for an attacker-observed signal. Deterministic, non-actionable.
    excerpt = f"{technique}@{target}: stub-observed exposure (no live target contacted)"
    return {"out": {"target": target, "technique": technique, "excerpt": excerpt}}


# Register the handler at import time (idempotent — register overrides in place).
executor.register(EFFECT, _stub_probe)


def engagement_caveats(scope, severity="high", sandbox=None):
    """Build the caveats for an offensive grant: the engagement SCOPE (rules of
    engagement — a list of target globs), an SB1 sandbox profile, and a severity
    the findings inherit. `requires_approval` is forced on via the Morta floor — an
    outward/irreversible offensive action is never ungated, and that floor cannot be
    attenuated away."""
    cav = {
        "engagement": list(scope),                 # the authorized targets (globs)
        "sandbox": sandbox or NO_NET_SANDBOX,      # SB1 profile (network-denied)
        "severity": severity,                      # severity carried into findings
        "requires_approval": True,                 # Morta gate (also the floor below)
    }
    # Floor it as an outward/irreversible class so the gate is permanent, like
    # `shell`/`financial` — narrowing can only ever strengthen it.
    return with_morta_floor("shell", cav)


def authorize_engagement(k, name, scope, technique, *, severity="high", sandbox=None):
    """Stand up an AUTHORIZED ENGAGEMENT: forge the offensive capability Cell (its
    caveats = the rules of engagement) and a SANDBOXED red-team agent that holds it.
    Returns (red_agent_cell, cap_id). The grant names the red agent's principal as
    grantee, so only that principal can wield it (ocap)."""
    red = k.keyring.mint(f"redteam:{name}", "agent")
    impl = {"op": "probe", "technique": technique}
    cap_id = content_id({"redcap": name, "technique": technique, "scope": list(scope)})
    cav = engagement_caveats(scope, severity=severity, sandbox=sandbox)
    cap = capability_content(name=f"red.{name}", effect=EFFECT, target="*",
                             caveats=cav, impl=impl,
                             grantee=red.id, granter=k.root.id)
    k.weft.append(k.root.id, ASSERT,
                  {"cell": cap_id, "type": "capability", "content": cap})

    agent_id = content_id({"agent": f"redteam:{name}"})
    k.weft.append(k.root.id, ASSERT, {
        "cell": agent_id, "type": "agent",
        "content": {"principal": red.id,
                    "objective": f"authorized engagement: {name}",
                    "envelope": [cap_id], "budget": 0,
                    "sandbox": True},          # offensive work runs in a sandbox principal
    })
    return k.weave().get(agent_id), cap_id


def in_scope(k, cap_id, target) -> bool:
    """Rules-of-engagement check: is `target` within the grant's engagement scope?
    The scope is a list of globs; a target matching ANY of them is in-scope. An
    empty/missing scope authorizes NOTHING (fail closed)."""
    cap = k.weave().get(cap_id)
    scope = (cap.content.get("caveats", {}) if cap else {}).get("engagement", [])
    return any(fnmatch.fnmatch(str(target), pat) for pat in scope)


def probe(k, red_agent, cap_id, target, *, cost=0):
    """Run one offensive action against `target` under the engagement `cap_id`.

    The gate order is deliberate:
      1. RULES OF ENGAGEMENT — an out-of-scope target is refused here, before any
         invoke is written. Authorized tooling pointed at an unauthorized target is
         the cardinal red-team sin; it never reaches the executor.
      2. ocap + Morta + SB1 — delegated to `k.invoke`: an unauthorized principal is
         DENIED (no grant / wrong grantee), an unapproved outward action is DENIED
         until `k.approve(cap_id)`, and the sandbox bounds what the stub may touch.
      3. On success — emit a `finding` Cell in DET1's exact shape (so TRIAGE1 can
         correlate it) with a `found_in` provenance edge back to the target.

    Returns a dict: {"refused": reason} for a ROE violation, {"denied": reason} for
    an ocap/Morta/sandbox denial, or {"finding": fid, "receipt": rid} on success.
    """
    if not in_scope(k, cap_id, target):
        return {"refused": f"target {target!r} is out of engagement scope "
                           f"(rules-of-engagement violation)"}

    res = k.invoke(red_agent, cap_id, {"target": target, "cost": cost})
    if "denied" in res:
        return {"denied": res["denied"]}

    out = res["ok"]["out"]
    cap = k.weave().get(cap_id)
    severity = cap.content.get("caveats", {}).get("severity", "high")
    name = cap.content["name"]

    # Record the target as an asset Cell so the finding has a real source to point
    # at (its provenance), mirroring how DET1 findings reference an observed Cell.
    asset_id = content_id({"asset": target})
    model.assert_content(k.weft, k.principal_for(red_agent), asset_id, "asset",
                         {"target": target})

    # The finding: SAME shape DET1 (detection.detect) emits, so triage.correlate
    # consumes it unchanged. `rule` is this engagement cap; `source` is the asset.
    fid = content_id({"finding": cap_id, "in": asset_id})
    model.assert_content(k.weft, k.principal_for(red_agent), fid, "finding", {
        "detection": name, "rule": cap_id, "severity": severity,
        "source": asset_id, "excerpt": out["excerpt"],
    })
    model.assert_edge(k.weft, k.principal_for(red_agent), fid, "found_in", asset_id)
    return {"finding": fid, "receipt": res["result_cell"], "asset": asset_id,
            "invoke_event": res["invoke_event"], "signer": res["signer"]}
