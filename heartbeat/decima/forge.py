"""Nona's FORGE — FORGE-IF-MISSING, the last resort of plug-in-or-forge.

Decima's discovery layer (`discovery.py`) tries, in strict order, to (1) find an
EXISTING capability in the manifest registry that fits a goal, then (2) plug in a
candidate from an injected research seam. Only when BOTH miss does the policy say
FORGE — grow a new organ. This module is that organ-grower: it is the FORGE Fate's
lane (Nona forges; Decima routes; Morta revokes/gates).

When asked to forge, Nona:
  1. SYNTHESIZES a `capability_manifest` from the goal — a real, validatable Cell
     (homoiconic, Law 3). The archetype is chosen deterministically from the goal
     (EFFECT if the goal reads like an outward action, else COMPUTE); the effect_class
     is the honest marker `STUB` so the gate and every receipt say plainly that this is
     a placeholder, not a finished tool.
  2. REGISTERS it (`manifest.register`) so it lands in the registry and becomes
     DISCOVERABLE — a re-request for the same intent now FINDS it instead of forging
     again (the second time is a plug-in, not a forge).
  3. WIRES a real, INVOCABLE handler via `kernel.integrate_tool` — one call turns the
     description into a live capability. The stub handler performs NO outward effect;
     it returns a receipt that is HONEST about being a stub (`stub=True`, `forged=True`,
     a note that it is to be made real later), so the forged organ is a truthful
     placeholder, never a fabricated success.

A forged capability GRANTS NOTHING extra: `kernel.integrate_tool` routes through the
same ocap spine — `authorize` gates every INVOKE, Morta can revoke it, exactly like any
other capability. Forging creates a tool; it does not create authority.

Deterministic given the same goal (same name, same manifest content, same handler
behavior) and content-addressed, so re-forging the identical goal is idempotent. Pure
stdlib; composes the public `manifest`/`kernel` APIs only — no core edit.
"""
import re

from decima import manifest as M
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
                        archetype: str | None = None) -> dict:
    """Synthesize a `capability_manifest` (a Cell) for `goal`. The description IS the
    goal, so a later re-request phrased the same way ranks this manifest at the top of
    the semantic index (found, not re-forged). effect_class is the honest `STUB` marker
    and the caveats/annotations flag it forged+stub so the gate and receipts stay
    truthful about being a placeholder."""
    goal = nfc(str(goal))
    name = name or slug(goal)
    archetype = archetype or archetype_for(goal)
    tags = _tokens(goal) + ["forged", "stub"]
    return M.capability_manifest(
        name,
        title=goal,
        description=goal,
        archetype=archetype,
        effect_class=STUB_EFFECT_CLASS,
        caveats={"forged": True, "stub": True},          # honest: a placeholder, not real yet
        annotations={"forged": True, "stub": True, "goal": goal},  # untrusted provenance
        source="forged",
        tags=tags,
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


def forge(k, goal: str, *, name: str | None = None, archetype: str | None = None,
          author: str | None = None) -> dict:
    """FORGE a real, invocable capability for `goal` and return a descriptor.

    Synthesizes + registers a manifest (now discoverable), then wires an honest stub
    handler as a live capability via `kernel.integrate_tool` (still ocap-gated). Returns
    {"action":"forged", "name", "goal", "manifest": <cell id>, "cap": <cap id>,
     "archetype", "stub": True}. Deterministic + idempotent for the same goal.

    This is the seam `discovery.discover(..., forge=forge)` calls as its LAST resort —
    only after the registry and the research seam both miss."""
    goal = nfc(str(goal))
    m = synthesize_manifest(goal, name=name, archetype=archetype)
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
    }
