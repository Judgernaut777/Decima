"""Rules of Engagement (ROE) — a declarative policy that says WHEN the human decides.

From the Method offensive-cyber platform's design ("clear rules of engagement dictate
when user input is needed"): an engagement runs autonomously at scale, and a single
declarative policy governs each action — proceed automatically, require human approval,
or refuse outright. Decima already enforces these decisions three ways (the Morta gate,
the autonomy ladder, and B4 governance); ROE UNIFIES them into ONE engagement-scoped,
data-defined policy object (a Cell) that the session/engagement layer consults.

An ROE is a list of rules evaluated in order; the FIRST rule whose `match` is satisfied
by the action wins. A `match` is a dict of constraints (any subset of `effect_class`,
`capability`, `archetype`, `target`, `source`); every key present must equal the
action's value. Each rule yields a verdict:
  - "proceed" — act autonomously (no human needed);
  - "approve" — pause for human approval (the Morta gate);
  - "refuse"  — deny outright.
No rule matches ⇒ the policy's `default` (conservative default: "approve").

ROE is POLICY, not authority: it decides when to involve the human. It COMPOSES with —
never replaces — `capability.authorize`/Morta/leases, which independently gate every
INVOKE. A permissive ROE can never grant authority the ocap layer withholds; a strict
ROE only adds friction. Ints not floats; deterministic (first match wins).

Public `model`/`hashing` API only — no core edit.
"""
from decima.model import assert_content
from decima.hashing import content_id, nfc

ROE = "roe"
VERDICTS = ("proceed", "approve", "refuse")
_MATCH_KEYS = ("effect_class", "capability", "archetype", "target", "source")


def roe_policy(name: str, rules, *, default: str = "approve") -> dict:
    """Build + validate an ROE policy (a dict → a Cell). Each rule is
    {match: {..subset of the match keys..}, verdict: proceed|approve|refuse, reason}.
    Fail loud on a bad verdict or an unknown match key."""
    if default not in VERDICTS:
        raise ValueError(f"default must be one of {VERDICTS}, got {default!r}")
    norm = []
    for i, r in enumerate(rules or []):
        verdict = r.get("verdict")
        if verdict not in VERDICTS:
            raise ValueError(f"rule {i}: verdict must be one of {VERDICTS}, got {verdict!r}")
        match = dict(r.get("match", {}))
        for key in match:
            if key not in _MATCH_KEYS:
                raise ValueError(f"rule {i}: unknown match key {key!r} (allowed: {_MATCH_KEYS})")
        norm.append({
            "match": {k: nfc(str(v)) for k, v in match.items()},
            "verdict": verdict,
            "reason": nfc(str(r.get("reason", ""))),
        })
    return {"name": nfc(name), "rules": norm, "default": default}


def register(k, policy: dict, *, author: str | None = None) -> str:
    """Record an ROE policy as a Cell on the Weft (validates by rebuilding). Returns
    the policy cell id."""
    p = roe_policy(policy["name"], policy.get("rules", []),
                   default=policy.get("default", "approve"))
    author = author or k.decima_agent_id
    cid = content_id({"roe": p["name"]}, kind="cell")
    assert_content(k.weft, author, cid, ROE, p)
    return cid


def _matches(match: dict, action: dict) -> bool:
    """Every constraint present in `match` must equal the action's value."""
    return all(str(action.get(key)) == val for key, val in match.items())


def evaluate(k, policy_or_id, action: dict) -> dict:
    """Evaluate an ROE against an `action` = {effect_class?, capability?, archetype?,
    target?, source?}. Returns {verdict, reason, rule} — the FIRST matching rule's
    verdict, or the policy `default` if none match. Deterministic. This is what the
    engagement layer calls to decide proceed / require-approval / refuse; the kernel's
    authorize/Morta still gate the actual effect independently."""
    p = policy_or_id
    if isinstance(policy_or_id, str):
        cell = k.weave().get(policy_or_id)
        if cell is None or cell.type != ROE:
            raise ValueError(f"not an ROE policy: {policy_or_id}")
        p = cell.content
    for i, r in enumerate(p.get("rules", [])):
        if _matches(r["match"], action):
            return {"verdict": r["verdict"], "reason": r["reason"], "rule": i}
    return {"verdict": p.get("default", "approve"),
            "reason": "no rule matched — policy default", "rule": None}
