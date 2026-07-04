"""Nona's FORGE — FORGE-IF-MISSING, the last resort of plug-in-or-forge.

Decima's discovery layer (`discovery.py`) tries, in strict order, to (1) find an
EXISTING capability in the manifest registry that fits a goal, then (2) plug in a
candidate from an injected research seam. Only when BOTH miss does the policy say
FORGE — grow a new organ. This module is that organ-grower: it is the FORGE Fate's
lane (Nona forges; Decima routes; Morta revokes/gates).

FORGE IS NOW AN ADAPTER OVER THE REAL PIPELINE. The forge-real loop (intent →
codegen → sandboxed test → scan → attested promotion → versioning) exists as
`candidate.py` / `reckoner.py` / `promotion.py`; a discovery-triggered forge routes
THROUGH it instead of returning a decorative stub:

  1. AUTHOR — `candidate.author_candidate` turns the goal into an ExtensionCandidate
     via the codegen seam. The candidate is BORN QUARANTINED (§3: sandbox_only ·
     no_outward_effects · network_allow([])), its build content-addressed (Law 4),
     its source recorded as DATA — never trusted, never exec'd at authoring time.
  2. EVALUATE — `reckoner.evaluate` runs the full verifier hierarchy (§5):
     deterministic seeded cases, SANDBOXED hostile-input containment, seeded
     property/fuzz testing, and the static/security source scan. The verdict is
     deterministic EVIDENCE on the Weft.
  3. PROMOTE — `promotion.promote` turns that evidence into a governed lifecycle
     transition through the ATTESTED, tiered trust gate (§7). A candidate that FAILS
     evaluation is REFUSED — `PromotionBlocked`, fail closed, no capability exposed
     and NO stub fallback. A promoted capability is then registered as a discoverable
     manifest (a re-request FINDS it, the second time is a plug-in, not a forge) and
     granted to Decima exactly like an integrated tool.

The CODEGEN seam decides which path can run. With an injected deterministic codegen
(tests) or a live egress-bound model (production), forge runs the REAL pipeline,
strictly — an evaluation failure refuses, it never degrades to a stub. Only when NO
codegen exists at all (the default `candidate.model_codegen` fails CLOSED offline)
does forge fall back to the LEGACY HONEST STUB: a placeholder that says so plainly
(`stub=True`, `promoted=False`, `fallback="codegen-unavailable"`, effect_class STUB)
in its descriptor, manifest and every receipt — a truthful placeholder, never a
fabricated success and never passed off as a promoted organ.

A forged capability GRANTS NOTHING extra: both paths route the same ocap spine —
`authorize` gates every INVOKE, Morta can revoke it, exactly like any other
capability. Promotion is the ONLY authority path for the real organ (the tiered,
root-anchored trust gate lifts quarantine); forging creates a tool, not authority.

Deterministic given the same goal + codegen (same name, same content-addressed
build, same evidence) and content-addressed, so re-forging the identical goal is
idempotent. Pure stdlib; composes the public `manifest`/`candidate`/`reckoner`/
`promotion`/`kernel` APIs only — no core edit.
"""
import re

from decima import candidate as C
from decima import manifest as M
from decima import promotion as P
from decima import reckoner as R
from decima.hashing import nfc

_TOKEN = re.compile(r"[a-z0-9]+")

# Verbs whose presence in a goal marks it as an OUTWARD action (an EFFECT archetype)
# rather than a pure read/transform (COMPUTE). Deterministic, lexical — no model.
_EFFECT_VERBS = frozenset({
    "send", "charge", "pay", "payout", "transfer", "email", "message", "sms", "text",
    "post", "delete", "remove", "create", "launch", "provision", "book", "file", "sign",
    "buy", "sell", "ship", "order", "publish", "deploy", "notify", "page", "call",
    "submit", "issue", "grant", "revoke", "schedule", "cancel", "refund", "withdraw",
})

# The honest effect_class of a forged stub: it does nothing outward yet, and both the
# gate and every receipt should say so plainly.
STUB_EFFECT_CLASS = "STUB"

# Re-exported so a discovery-forge caller can catch the refusal without a second import.
PromotionBlocked = P.PromotionBlocked


def _tokens(goal: str) -> list:
    return _TOKEN.findall(nfc(str(goal)).lower())


def slug(goal: str, *, max_tokens: int = 8) -> str:
    """A deterministic capability name derived from the goal: `forged_` + the goal's
    first tokens joined by `_`. Same goal → same name, so a re-forge is idempotent."""
    toks = _tokens(goal) or ["capability"]
    return "forged_" + "_".join(toks[:max_tokens])


def archetype_for(goal: str) -> str:
    """Choose the ONEX archetype for a goal, deterministically and lexically: EFFECT if
    the goal reads like an outward action, else COMPUTE (a pure read/transform)."""
    return "EFFECT" if (set(_tokens(goal)) & _EFFECT_VERBS) else "COMPUTE"


def synthesize_manifest(goal: str, *, name: str | None = None,
                        archetype: str | None = None, stub: bool = True) -> dict:
    """Synthesize a `capability_manifest` (a Cell) for `goal`. The description IS the
    goal, so a later re-request phrased the same way ranks this manifest at the top of
    the semantic index (found, not re-forged).

    `stub=True` (the legacy honest placeholder): effect_class is the honest `STUB`
    marker and the caveats/annotations flag it forged+stub so the gate and receipts
    stay truthful about being a placeholder. `stub=False` (the REAL forged organ): the
    manifest describes a PROMOTED generated capability — source `promoted`, caveats/
    annotations say plainly it is forged AND real (stub=False) — registered only AFTER
    the candidate cleared evaluation + the attested promotion gate."""
    goal = nfc(str(goal))
    name = name or slug(goal)
    archetype = archetype or archetype_for(goal)
    if stub:
        return M.capability_manifest(
            name,
            title=goal,
            description=goal,
            archetype=archetype,
            effect_class=STUB_EFFECT_CLASS,
            caveats={"forged": True, "stub": True},          # honest: a placeholder, not real yet
            annotations={"forged": True, "stub": True, "goal": goal},  # untrusted provenance
            source="forged",
            tags=_tokens(goal) + ["forged", "stub"],
        )
    return M.capability_manifest(
        name,
        title=goal,
        description=goal,
        archetype=archetype,
        effect_class="READ",                                 # a promoted generated COMPUTE organ
        caveats={"forged": True, "stub": False},             # honest: forged AND real
        annotations={"forged": True, "stub": False, "goal": goal},  # untrusted provenance
        source="promoted",
        tags=_tokens(goal) + ["forged", "promoted", "generated"],
    )


def _stub_handler(name: str, goal: str):
    """Build the INVOCABLE stub handler `(impl, args) -> dict`. It performs NO outward
    effect; it returns a receipt payload that is HONEST about being a forged stub, so
    invoking a freshly-grown organ never fabricates a real outcome — it plainly reports
    that it is a placeholder to be made real later."""
    def handler(_impl, args):
        return {
            "out": None,
            "forged": True,
            "stub": True,                                # the receipt says: not real yet
            "capability": name,
            "goal": goal,
            "note": "forged stub — placeholder, not yet implemented (to be made real later)",
        }
    return handler


def _forge_stub(k, goal: str, *, name=None, archetype=None, author=None) -> dict:
    """The LEGACY HONEST-STUB fallback — taken ONLY when no codegen seam exists at all
    (the default model seam fails closed offline). Synthesizes + registers the honest
    STUB manifest and wires the honest stub handler via `kernel.integrate_tool`. The
    descriptor says so plainly: `stub=True`, `promoted=False`, `fallback` names why —
    a truthful placeholder, never passed off as a promoted real organ."""
    m = synthesize_manifest(goal, name=name, archetype=archetype, stub=True)
    name = m["name"]
    mid = M.register(k, m, author=author)
    cap_id = k.integrate_tool(name, _stub_handler(name, goal), caveats=dict(m["caveats"]))
    return {
        "action": "forged",
        "name": name,
        "goal": goal,
        "manifest": mid,
        "cap": cap_id,
        "archetype": m["archetype"],
        "stub": True,
        "promoted": False,                               # honest: quarantine-grade placeholder
        "fallback": "codegen-unavailable",               # why the real pipeline could not run
    }


def _forge_real(k, goal: str, codegen, *, name=None, archetype=None, author=None,
                declared_effect_class: str = "pure") -> dict:
    """The REAL forge: an adapter over candidate → reckoner → promotion.

    AUTHOR a born-quarantined ExtensionCandidate from the goal via `codegen`, EVALUATE
    it across the full verifier hierarchy (sandboxed deterministic tests + hostile-
    input containment + property/fuzz + static scan), then PROMOTE it through the
    attested, tiered trust gate. Raises `PromotionBlocked` (fail closed — nothing
    registered, nothing invocable, NO stub fallback) when the evidence gate refuses.
    On success the promoted organ is registered as a discoverable manifest (the
    description IS the goal, so a re-request FINDS it) and granted to Decima like any
    integrated tool — the ocap spine still gates every INVOKE."""
    name = name or slug(goal)
    P.install_trust_anchors(k)                       # idempotent §7 anchors (root-declared)

    # 1. AUTHOR — born quarantined (§3), content-addressed (Law 4), source-as-DATA.
    cand = C.author_candidate(k, goal, codegen, author=author, name=name,
                              declared_effect_class=declared_effect_class)

    # 2. EVALUATE — sandboxed test + hostile-input + property/fuzz + static scan (§5).
    outcome = R.evaluate(k, cand)

    # 3. PROMOTE — the attested, tiered gate is the ONLY authority path. A failing
    #    candidate raises PromotionBlocked here: refused, fail closed, no stub.
    result = P.promote(k, cand, outcome, tier=declared_effect_class)
    if not result.promoted:
        raise P.PromotionBlocked(
            f"promotion attested but the trust fold did not lift quarantine for "
            f"{name!r} (tier={result.tier}) — refusing, fail closed")

    # 4. Register the promoted organ as a DISCOVERABLE manifest (description == goal:
    #    a re-request finds it — the second time is a plug-in, not a forge) and grant
    #    it to Decima exactly like an integrated tool (a real, downhill grant).
    m = synthesize_manifest(goal, name=name, archetype=archetype, stub=False)
    mid = M.register(k, m, author=author or k.reckoner.id)
    P.grant_to(k, result.cap_id, k.decima_agent_id)

    return {
        "action": "forged",
        "name": name,
        "goal": goal,
        "manifest": mid,
        "cap": result.cap_id,
        "candidate": cand["cell"],
        "evaluation": outcome.result_cell,
        "implementation_digest": cand["implementation_digest"],
        "archetype": m["archetype"],
        "tier": result.tier,
        "to_state": result.to_state,
        "stub": False,                               # a REAL, tested, attested organ
        "promoted": True,
        "fallback": None,
    }


def forge(k, goal: str, *, name: str | None = None, archetype: str | None = None,
          author: str | None = None, codegen=None,
          declared_effect_class: str = "pure") -> dict:
    """FORGE a real capability for `goal` through the forge-real pipeline and return a
    descriptor: candidate (born quarantined) → reckoner (sandboxed test + property +
    hostile-input eval + scan) → promotion (attested, tiered gate). Returns
    {"action":"forged", "name", "goal", "manifest": <cell id>, "cap": <cap id>,
     "candidate", "evaluation", "tier", "stub": False, "promoted": True, ...} for a
    promoted organ; raises `PromotionBlocked` (fail closed, no stub fallback) when the
    candidate fails evaluation. Deterministic + idempotent for the same goal + codegen.

    `codegen` is the seam a MODEL authors source through — inject a deterministic
    callable in tests (`discover(..., forge=forge_with(my_codegen))`); production binds
    the live egress-gated model. With NO codegen at all (`codegen=None` and the default
    `candidate.model_codegen` failing closed offline), forge degrades to the LEGACY
    HONEST STUB — loudly marked `stub=True` / `promoted=False` / `fallback=
    "codegen-unavailable"` — a truthful placeholder, never passed off as real.

    This is the seam `discovery.discover(..., forge=forge)` calls as its LAST resort —
    only after the registry and the research seam both miss."""
    goal = nfc(str(goal))
    if codegen is None:
        try:
            return _forge_real(k, goal, C.model_codegen, name=name, archetype=archetype,
                               author=author, declared_effect_class=declared_effect_class)
        except C.CodegenUnavailable:
            # No codegen seam reachable (offline, no key, no bound egress): the honest,
            # loudly-marked placeholder — NEVER a fabricated promoted organ.
            return _forge_stub(k, goal, name=name, archetype=archetype, author=author)
    return _forge_real(k, goal, codegen, name=name, archetype=archetype,
                       author=author, declared_effect_class=declared_effect_class)


def forge_with(codegen, **defaults):
    """Bind a deterministic `codegen` (and any forge kwargs) into a `forge(k, goal)`
    callable shaped exactly for `discovery.discover(..., forge=...)` — the one-line
    wiring that routes discovery's forge-if-missing through the REAL pipeline."""
    def bound(k, goal):
        return forge(k, goal, codegen=codegen, **defaults)
    return bound
