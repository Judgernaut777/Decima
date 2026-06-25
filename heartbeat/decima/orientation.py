"""Orientation — "the Big O" (CAPABILITY_MAP D4). The lens the agent looks THROUGH
before it decides.

Generic AI is strong at Observe / Decide / Act and weak at **Orient** — the step
that interprets raw input through *who the user is and what they value* before any
decision is made. Decima already has the ingredients scattered across the Weave;
this module assembles them into one explicit object consulted before `decide`:

  • values / profile — the user's stated preferences (trusted memory Cells). Only
    TRUSTED preferences bind (the recall-vs-instruct law: a "preference" injected by
    a third party is visible as DATA but can never steer behavior);
  • governance — B4's banned/fragile/failed rules (`memory.governance_check`), so a
    request that conflicts with a rule is caught HERE, at orient-time, with the rule
    cited as evidence — before the brain proposes an action;
  • horizon — the slice of the world this agent may act within (its `horizon`
    field), so orientation is scoped to what the agent can legitimately see.

Non-linear OODA: an *oriented* situation (a known preference applies, or a rule
fires) is a fast path — the lens already resolves it; a novel situation with
neither falls through to deliberate decision-making. `Orientation.fast_path` flags
which.

Authority: orientation only *informs* and can only *refuse* — it never grants. A
block is the brain choosing not to act; `capability.authorize` still gates anything
it does choose to do. Pure read over the Weave + thin preference Cells; no core edit.
"""
from dataclasses import dataclass, field

from decima import memory
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

PREFERENCE = "preference"          # a user value/preference Cell (the profile)


# ── profile: trusted preference Cells ────────────────────────────────────────
def set_preference(weft, author: str, key: str, value: str, evidence_src: str,
                   *, instruction_eligible: bool = True,
                   scope: str = memory.DEFAULT_SCOPE) -> str:
    """Record a user preference/value as a Cell. Trusted by default (it is the
    user's own value, and its whole job is to steer) — but written through the same
    four-permission boundary as any memory: a preference authored from an UNTRUSTED
    source (`instruction_eligible=False`) is stored and visible, yet `preferences()`
    will not let it bind. `evidence_src` grounds where the preference came from."""
    key, value = nfc(key), nfc(value)
    cid = content_id({"preference": key, "scope": nfc(scope)})
    assert_content(weft, author, cid, PREFERENCE, {
        "key": key, "value": value, "text": f"{key}={value}",
        "scope": scope, "recallable": True, "citable": True,
        "instruction_eligible": bool(instruction_eligible),
    })
    assert_edge(weft, author, cid, "supported_by", evidence_src)
    return cid


def preferences(weave, scope: str | None = None) -> dict:
    """The BINDING preferences: trusted (instruction-eligible), recallable, in scope.
    Returns {key: {"value": v, "cell": id}}. Untrusted preferences are excluded —
    they may be read as data elsewhere, but they never orient."""
    out = {}
    for c in weave.of_type(PREFERENCE):
        if not c.content.get("recallable", True):
            continue
        if not c.content.get("instruction_eligible"):
            continue                                   # untrusted preference can't bind
        if scope is not None and c.content.get("scope") != scope:
            continue
        out[c.content["key"]] = {"value": c.content["value"], "cell": c.id}
    return out


# ── the Orientation object ───────────────────────────────────────────────────
@dataclass
class Orientation:
    situation: str
    values: dict                       # binding preferences: key -> {value, cell}
    governance: dict                   # memory.governance_check result
    horizon: object = None             # the agent's horizon (scope of action), if any
    evidence: list = field(default_factory=list)   # binding rule evidence for a block

    @property
    def allow(self) -> bool:
        return self.governance.get("allow", True)

    @property
    def blocked(self) -> bool:
        return not self.allow

    @property
    def fast_path(self) -> bool:
        """Oriented (a preference applies or a rule fires) ⇒ the lens resolves it
        fast; novel (neither) ⇒ deliberate. The non-linear OODA signal."""
        return bool(self.values) or self.governance.get("verdict", "allow") != "allow"

    def refusal(self) -> str:
        """A spoken refusal citing the rule(s) that blocked the situation."""
        return self.governance.get("reason") or f"oriented refusal: {self.situation!r}"

    def preferred_capability(self) -> str | None:
        """A preference may name a capability to steer toward (`prefer-cap`)."""
        pref = self.values.get("prefer-cap")
        return pref["value"] if pref else None

    def value(self, key: str, default=None):
        pref = self.values.get(key)
        return pref["value"] if pref else default


def orient(weave, agent_cell, situation: str, *, scope: str | None = None) -> Orientation:
    """Assemble the Orientation for `situation`: governance verdict + binding
    preferences + the agent's horizon. Pure read; performs no effect."""
    situation = nfc(situation or "")
    gov = memory.governance_check(weave, situation, scope)
    vals = preferences(weave, scope)
    horizon = agent_cell.content.get("horizon") if agent_cell is not None else None
    return Orientation(situation=situation, values=vals, governance=gov,
                       horizon=horizon, evidence=gov.get("evidence", []))


def orient_k(k, agent_cell, situation: str, *, scope: str | None = None) -> Orientation:
    """Kernel convenience: orient over the current Weave."""
    return orient(k.weave(), agent_cell, situation, scope=scope)
