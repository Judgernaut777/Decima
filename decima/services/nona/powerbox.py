"""The capability broker — the module `decima/kernel/capability.py:272` already names (N6).

`capability.py` has always said that "a broker (powerbox.py) merges these in before it
issues a grant", and `powerbox.py` did not exist in `decima/`. This is it, and its whole
reason to exist is a UX one with a security shape: ocap is only usable if something makes
LEAST AUTHORITY the default path rather than a discipline someone has to remember.

IT ASKS FOR SCOPE, NOT FOR CONSENT. The flow (`specs/MORTA_CAPABILITIES.md` §7) is: an
agent asserts a request carrying a purpose, a target, a duration and the MINIMUM scope it
needs; the broker searches the grants it already holds; it proposes the NARROWEST
attenuation that covers the request; policy either auto-issues a low-risk grant or routes
exactly ONE human decision. The human is not asked "may this agent have the shell?" — the
narrowing has already happened by the time they see anything, and what they approve is a
scoped grant, recorded, revocable, and provably ⊆ what the broker held.

FOUR REFUSALS DO THE WORK, IN THIS ORDER:

  1. **The floor is merged BEFORE the policy decision, and the decision cannot see the
     request.** `with_morta_floor` (plus the Nona TIER floor below) is applied to the
     caller's proposed caveats first, and `policy_decision` is a function of (effect, tier)
     ONLY — the requested scope and the stated purpose are recorded but never inputs. So a
     request proposing a loose scope can neither reach around the floor nor talk the policy
     out of routing an approval. Reversing those two steps, or letting the requested scope
     into the decision, is the classic broker bug: the decision gets made about a scope the
     grant will not actually have.
  2. **Narrowing is proved STRUCTURALLY before issuing.** `capability.attenuate` does NOT
     validate — it writes every non-numeric `stricter` key VERBATIM into the child, so
     `{"requires_approval": False}` lands as False and nothing at the Weft door refuses
     it. Only `attenuation_valid` / `_caveats_downhill` catches that, and only if someone
     calls it. `issue_grant` calls it and refuses on False, so the broker cannot widen
     authority even when a caller hands it a hand-built child. An attestation is never a
     substitute for a proof of narrowing.
  3. **The grant plumbing is checked, not assumed.** `verify_delegation` requires
     `child.granter == parent.grantee` and a live parent, and it runs at AUTHORIZE time —
     so a broker that issues from a source it is not the grantee of produces grants that
     look perfect and are denied `DELEGATION_INVALID` on every use. `issue_grant` refuses
     that shape at ISSUE time instead, where the error is legible.
  4. **A quarantined or dead source brokers nothing.** And a child of a promoted organ
     whose promotion is later ROLLED BACK fails closed by itself: rollback re-adds
     `sandbox_only` to the parent (`weave.py` cascade step 3), and a child that lacks it
     is no longer downhill.

THE NONA TIER FLOOR, AND WHY `with_morta_floor` ALONE IS A TRAP. `MORTA_FLOORS` is keyed
by EFFECT NAME (`shell`, `financial`). A Nona organ's effect is `generated_code` and its
blast radius lives in `declared_effect_class`, so `with_morta_floor("generated_code", …)`
returns the caveats UNCHANGED. Calling it and believing the organ is floored is the
easiest way to ship a broker that silently auto-approves a `financial`-tier organ. So this
module applies a second floor derived from `promotion.SIGNER_POLICY`: a tier whose
promotion needs a human needs an approval caveat too, an unclassified tier is floored by
DEFAULT (fail safe), and `network` — which has no executor at all — is DENIED rather than
approved, because a prompt for something that can never run only teaches people to click
yes.

PROMPT VOLUME IS A SECURITY PROPERTY (design §5.8 points 3-4). The last section of this
module is about that: the default for a promoted organ is ONE capability-scoped approval
at promotion time plus invocation-scoped approvals only for FLOORED effects — one prompt
per organ, not one per call — and the inbox surface is TIERED so a `pure` promotion is a
revocable notification while a `financial` one is an explicit approval with its evidence
inline. A prompt the user always clicks yes on has negative security value.

WHAT THIS MODULE IS NOT. It is free functions over an explicit `Weft`/`Weave` (design
§5.9 point 2), not a `Kernel` god-object like the heartbeat reference. It holds no INVOKE
authority: everything it hands out is still gated by `capability.authorize` at use time.
It writes no approval Cell and mints no principal. And it is deliberately NOT reachable
over HTTP in this wave — brokering is a service call with an explicit broker principal,
not an endpoint an unauthenticated shape could reach.

DETERMINISM. Every id here is content-addressed over Cell data plus `weft.head`; nothing
uses `os.urandom` (`kernel/inbox.py`'s nonce does — do not copy it) and nothing reads a
clock. `duration` becomes an `expires_at` bound on the LOGICAL frontier
(`weave.frontier_lamport`), an int, exactly like every other lease bound.
"""

from __future__ import annotations

from typing import Any

from decima.kernel import capability as cap_mod
from decima.kernel import model
from decima.kernel.hashing import content_id, nfc
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.services.nona import promotion

CAP_REQUEST = "cap_request"
CAPABILITY = "capability"
AGENT = "agent"

# Request lifecycle, recorded on the `cap_request` Cell (the broker's audit trail).
RECEIVED = "received"
GRANTED = "granted"
NEEDS_APPROVAL = "needs_approval"
DENIED = "denied"

# Policy outcomes.
AUTO = "auto"  # issue now, ungated
APPROVAL = "approval"  # issue, but born with the Morta approval caveat
DENY = "deny"  # issue nothing

# Effects a scoped grant of which is not worth a human's attention. Deliberately tiny and
# deliberately NOT containing `generated_code`: a Nona organ's risk is its TIER, so the
# generated-code path is judged by its TIER in `policy_decision` below, not by effect name.
LOW_RISK_EFFECTS: frozenset[str] = frozenset({"echo", "transform"})

# The tiers whose promotion the Reckoner may sign without a human (design Decision 1).
# DERIVED from the promotion policy rather than re-listed, so the two can never disagree:
# if a tier is ever moved off AUTOMATED, the broker stops auto-issuing it in the same
# commit, with no second table to remember.
AUTO_TIERS: frozenset[str] = frozenset(
    t for t, p in promotion.SIGNER_POLICY.items() if p == promotion.AUTOMATED
)

# The scope dimensions a request may TIGHTEN. `_SHRINK_ONLY` bounds are ints that may only
# shrink; the boolean constraints may only be ADDED. There is deliberately no vocabulary
# here for removing a constraint — a request cannot even express "drop sandbox_only".
_BOUNDS: tuple[str, ...] = ("budget", "max_uses")
_CONSTRAINTS: tuple[str, ...] = (
    "requires_approval",
    "sandbox_only",
    "read_only",
    "reversible_only",
    "no_outward_effects",
)


class BrokerRefused(RuntimeError):
    """The broker refused to issue. Always a statement about AUTHORITY or EVIDENCE — the
    source is not held, is not live, or the proposed child is not provably narrower — never
    about a transient condition, so a refusal is reproducible from the log."""


# ── floors: what no request can talk the broker out of ───────────────────────
def tier_floor(tier: str | None) -> dict[str, Any]:
    """The permanent minimum caveats for a Nona effect-class TIER.

    Two sources, merged, because neither alone is sufficient:

      * `capability.morta_floor(tier)` — the realm's floor for a name that happens to be
        both an effect and a tier (`financial` ⇒ `requires_approval` + `reversible_only`);
      * `promotion.SIGNER_POLICY` — a tier whose PROMOTION requires a human is not a tier
        whose invocations should be ungated, so it carries `requires_approval` too.

    A tier that is DECLARED but unrecognised is floored as if it needed a human: an organ
    nobody classified is not an organ nobody needs to see. `tier is None` means something
    different and is deliberately unfloored HERE — it says "this is not a Nona organ at
    all", so the effect's own floor governs it and `policy_decision` still defaults an
    unclassified generated organ to a human (it is not in `AUTO_TIERS`).
    """
    if tier is None:
        return {}
    floor: dict[str, Any] = dict(cap_mod.morta_floor(tier))
    if promotion.SIGNER_POLICY.get(tier) != promotion.AUTOMATED:
        floor["requires_approval"] = True
    return floor


def with_floors(effect: str, tier: str | None, caveats: dict[str, Any]) -> dict[str, Any]:
    """Merge the realm's effect floor AND the Nona tier floor over `caveats`.

    Floors only ever add or strengthen constraints, and they are applied BEFORE the policy
    decision (see the module docstring) so the decision is made about the scope the grant
    will really carry.
    """
    merged = cap_mod.with_morta_floor(effect, dict(caveats))
    merged.update(tier_floor(tier))
    return merged


def policy_decision(effect: str, tier: str | None, purpose: str) -> str:
    """`AUTO` / `APPROVAL` / `DENY` for a request — the realm policy, as one pure function.

    Reading order matters and is the security content:

      1. a tier with NO EXECUTOR is DENIED, not approved (there is nothing to consent to);
      2. anything with a floor NEVER auto-issues, whatever else is true;
      3. a low-risk effect, or a generated organ on an AUTOMATED tier, auto-issues;
      4. everything else defaults to a human — unclassified fails SAFE, not open.

    `purpose` is recorded on the request and does not (yet) change the decision: a purpose
    string is a claim by the requester, and a claim must never be able to lower a floor.
    """
    if promotion.SIGNER_POLICY.get(tier or "") == promotion.NOT_EXECUTABLE:
        return DENY
    if cap_mod.morta_floor(effect) or tier_floor(tier):
        return APPROVAL
    if effect in LOW_RISK_EFFECTS:
        return AUTO
    if tier in AUTO_TIERS:
        return AUTO
    _ = nfc(purpose)
    return APPROVAL


# ── the source grants a broker may broker from ───────────────────────────────
def source_cell_id(broker: str, name: str, effect: str) -> str:
    """The deterministic id of a broker source grant. Deterministic so installing the
    broker's authority is idempotent rather than accreting duplicate wide grants."""
    return "broker_source:" + content_id(
        {"broker": broker, "name": nfc(name), "effect": nfc(effect)}, kind="cell"
    )


def install_broker_source(
    weft: Weft,
    root: str,
    *,
    broker: str,
    name: str,
    effect: str,
    target: str = "*",
    caveats: dict[str, Any] | None = None,
) -> str:
    """Assert the ROOT-authored source grant the broker brokers from — `grantee = broker`.

    That field is load-bearing: `verify_delegation` walks child → parent and requires
    `child.granter == parent.grantee`, so a source whose grantee is anyone else produces
    children that issue cleanly and are denied `DELEGATION_INVALID` at every use. Install
    it with the root, and only with the root: a broker that could mint its own source
    authority would be ambient authority with extra steps.
    """
    # The id covers (broker, name, effect) but NOT the caveats, so re-installing under the
    # same name rewrites the source grant in place. That is deliberate for an operator's own
    # root grant — one name, one source, no accreting duplicates for `brokerable_sources` to
    # choose between — and it is bounded by R1 exactly like every other ASSERT until N7
    # authorizes writes to `capability` cells.
    cid = source_cell_id(broker, name, effect)
    content = cap_mod.capability_content(
        name=nfc(name),
        effect=nfc(effect),
        target=target,
        caveats=dict(caveats or {}),
        grantee=broker,
        granter=root,
    )
    model.assert_content(weft, root, cid, CAPABILITY, content)
    return cid


def brokerable_sources(weave: Weave, broker: str) -> dict[str, str]:
    """name → source cap id for every LIVE, delegable, unquarantined grant `broker` holds.

    The broker's reach is exactly this fold — data on the log, not configuration — so
    "what can the broker hand out right now?" is answerable without trusting a config
    file, and RETRACTing a source removes it from the broker's vocabulary on the next
    fold with no code change. Deterministic when two sources share a name: the
    lexicographically smallest cell id wins.
    """
    out: dict[str, str] = {}
    for cid, cell in sorted(weave.cells.items()):
        if cell.type != CAPABILITY or cell.retracted:
            continue
        if cell.content.get("grantee") != broker:
            continue
        if cell.content.get("quarantined") or not cell.content.get("delegable", True):
            continue
        name = cell.content.get("name")
        if isinstance(name, str) and name and name not in out:
            out[name] = cid
    return out


# ── the narrowest attenuation that still covers the request ─────────────────
def narrowest(
    scope: dict[str, Any] | None, duration: int | None, *, frontier: int
) -> dict[str, Any]:
    """The TIGHTEST scope that still covers the request — only what was asked for.

    Unmentioned dimensions inherit the source's (and the floor's) constraints unchanged,
    and there is no way to express a widening: a bound must be an int (a float would be
    unhashable content and unreplayable arithmetic) and a boolean constraint is copied
    only when TRUTHY, so `{"sandbox_only": False}` in a request is not "please drop the
    sandbox" — it is nothing at all.

    `duration` is a number of LOGICAL ticks, converted to an absolute `expires_at` bound
    on the frontier lamport. Never a wall clock: a lease that expires "in 60 seconds"
    cannot be replayed, and `lease_status` fails closed without a frontier anyway.
    """
    scope = dict(scope or {})
    stricter: dict[str, Any] = {}
    for key in _BOUNDS:
        if key in scope:
            value = scope[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise BrokerRefused(
                    f"scope {key!r} must be a plain int (no floats in signed content): {value!r}"
                )
            stricter[key] = int(value)
    if duration is not None:
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
            raise BrokerRefused(f"duration must be a positive int of logical ticks: {duration!r}")
        stricter["expires_at"] = int(frontier) + int(duration)
    for key in _CONSTRAINTS:
        if scope.get(key):
            stricter[key] = True
    return stricter


# ── issuing: the structural proof, then the grant ───────────────────────────
def grant_cell_id(broker: str, requester_principal: str, source: str, request: str) -> str:
    """Content-addressed over (broker, holder, source, request): re-issuing the SAME
    request resolves to the same grant rather than accreting a second one."""
    return "broker_grant:" + content_id(
        {"broker": broker, "to": requester_principal, "from": source, "req": request},
        kind="cell",
    )


def issue_grant(
    weft: Weft,
    weave: Weave,
    *,
    broker: str,
    source: str,
    child: dict[str, Any],
    requester_cell: str,
    request: str,
) -> str:
    """Prove `child` is narrower than `source`, then assert it and hand it to the holder.

    THE PROOF IS THE POINT. `attenuate` will happily write a WIDER child (it copies every
    non-numeric `stricter` key verbatim), so this is the only place a widening is caught —
    and it is caught before any Cell is written. Refuses (`BrokerRefused`) on:

      * a source that is missing, retracted, not a capability, or QUARANTINED;
      * a source the broker does not hold (`grantee != broker`) — the delegation check
        would otherwise deny every later use with a confusing code;
      * a child whose `granter` is not the broker or whose `parent` is not the source;
      * a child that is not provably ⊆ the source (`capability.attenuation_valid`).

    The envelope write is SCOPED (design R1 / N7): the requester's own agent Cell is
    re-asserted with the just-issued grant id APPENDED and every other field preserved.
    `ASSERT` is not authorized in this kernel yet, so the broker principal could in
    principle rewrite any agent's envelope; that is bounded here by construction — one
    cell, one field, append-only — and the bound becomes an enforced rule in N7.
    """
    base = weave.get(source)
    if base is None or base.type != CAPABILITY:
        raise BrokerRefused(f"no such source capability {source!r}")
    if base.retracted:
        raise BrokerRefused(f"source {source!r} is not live (revoked or lapsed)")
    if base.content.get("quarantined"):
        raise BrokerRefused(
            f"source {source!r} is quarantined: an unpromoted grant brokers nothing "
            "(its children would inherit the quarantine and be denied anyway)"
        )
    if base.content.get("grantee") != broker:
        raise BrokerRefused(
            f"broker {broker} is not the grantee of {source!r}: a child of a grant the "
            "broker does not hold fails `verify_delegation` at every use "
            "(granter must equal the parent's grantee)"
        )
    if child.get("granter") != broker or child.get("parent") != source:
        raise BrokerRefused(
            "child grant does not name the broker as granter and the source as parent: "
            "the delegation chain would not be walkable"
        )
    valid, why = cap_mod.attenuation_valid(child, base.content)
    if not valid:
        raise BrokerRefused(f"attenuation invalid: {why}")

    agent = weave.get(requester_cell)
    if agent is None or agent.type != AGENT:
        raise BrokerRefused(f"no such requester agent cell {requester_cell!r}")
    holder = agent.content.get("principal")
    if child.get("grantee") != holder:
        raise BrokerRefused("child grant is not issued to the requesting agent's principal")

    grant_id = grant_cell_id(broker, str(holder), source, request)
    model.assert_content(weft, broker, grant_id, CAPABILITY, dict(child))
    envelope = list(agent.content.get("envelope") or [])
    if grant_id not in envelope:
        envelope.append(grant_id)
        model.assert_content(
            weft,
            broker,
            requester_cell,
            AGENT,
            {**agent.content, "envelope": envelope},
        )
    model.assert_edge(weft, broker, grant_id, "brokered_for", request)
    return grant_id


def request_id(requester_cell: str, name: str, purpose: str, *, at: str | None) -> str:
    """Deterministic request id: the asking agent, what it asked for, why, and the log
    position it asked at. `weft.head` (not a clock, not `os.urandom`) is what makes two
    identical asks at different points distinct while staying replayable."""
    return "cap_request:" + content_id(
        {"by": requester_cell, "name": nfc(name), "purpose": nfc(purpose), "at": at},
        kind="cell",
    )


def request_capability(
    weft: Weft,
    weave: Weave,
    *,
    broker: str,
    requester_cell: str,
    name: str,
    purpose: str,
    scope: dict[str, Any] | None = None,
    duration: int | None = None,
    tier: str | None = None,
) -> dict[str, Any]:
    """The whole broker flow, as six recorded steps (`MORTA_CAPABILITIES.md` §7).

    1. the request lands as a `cap_request` Cell (`received`) — an ask is auditable even
       when it is refused, which is what makes "who asked for what" answerable later;
    2. the broker searches the sources it HOLDS (`brokerable_sources`);
    3. it proposes the narrowest attenuation covering the request (`narrowest`);
    4. floors merge FIRST, then policy decides `AUTO` / `APPROVAL` / `DENY`;
    5. `issue_grant` proves the narrowing structurally and binds holder + caveats;
    6. the request Cell records the decision.

    Returns `{"request": id, "granted": cap_id, "needs_approval": bool, "caveats": {...}}`
    or `{"request": id, "denied": reason}`. Nothing here invokes anything: the grant it
    returns is still subject to the full ocap check on every use, and an `APPROVAL`
    decision means the grant is BORN with `requires_approval` — the one human decision it
    routes is a capability-scoped approval, not a prompt per call (see `prompt_plan`).
    """
    rid = request_id(requester_cell, name, purpose, at=weft.head)
    received: dict[str, Any] = {
        "requester": requester_cell,
        "broker": broker,
        "name": nfc(name),
        "purpose": nfc(purpose),
        "scope": dict(scope or {}),
        "duration": None if duration is None else int(duration),
        "tier": tier,
        "status": RECEIVED,
    }
    model.assert_content(weft, broker, rid, CAP_REQUEST, received)

    sources = brokerable_sources(weave, broker)
    source = sources.get(nfc(name))
    if source is None:
        return _close(weft, broker, rid, received, denied=f"no brokerable source for {name!r}")
    base = weave.get(source)
    if base is None:  # pragma: no cover - brokerable_sources only yields live cells
        return _close(weft, broker, rid, received, denied=f"source for {name!r} not live")

    effect = str(base.content.get("effect", ""))
    try:
        stricter = narrowest(scope, duration, frontier=weave.frontier_lamport)
    except BrokerRefused as exc:
        return _close(weft, broker, rid, received, denied=str(exc))
    # ORDER IS LOAD-BEARING: floors first, decision second. Reversed, a caller-proposed
    # loose scope would be judged instead of the scope the grant actually gets.
    stricter = with_floors(effect, tier, stricter)
    decision = policy_decision(effect, tier, purpose)
    if decision == DENY:
        return _close(
            weft,
            broker,
            rid,
            received,
            denied=(
                f"policy forbids brokering {name!r} for tier {tier!r}: "
                f"{promotion.signer_policy(tier or '')}"
            ),
        )
    if decision == APPROVAL:
        stricter["requires_approval"] = True

    agent = weave.get(requester_cell)
    if agent is None or agent.type != AGENT:
        return _close(
            weft, broker, rid, received, denied=f"no such requester agent {requester_cell!r}"
        )
    child = cap_mod.attenuate(
        base.content,
        stricter,
        source,
        grantee=str(agent.content.get("principal")),
        granter=broker,
    )
    try:
        grant = issue_grant(
            weft,
            weave,
            broker=broker,
            source=source,
            child=child,
            requester_cell=requester_cell,
            request=rid,
        )
    except BrokerRefused as exc:
        return _close(weft, broker, rid, received, denied=str(exc))
    return _close(
        weft,
        broker,
        rid,
        received,
        granted=grant,
        needs_approval=decision == APPROVAL,
        caveats=dict(child.get("caveats") or {}),
    )


def _close(
    weft: Weft,
    broker: str,
    rid: str,
    received: dict[str, Any],
    *,
    denied: str | None = None,
    granted: str | None = None,
    needs_approval: bool = False,
    caveats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record the decision on the request Cell and return the public summary. Every exit
    from `request_capability` goes through here, so an abandoned request is not a state
    the log can be in."""
    status = DENIED if denied is not None else (NEEDS_APPROVAL if needs_approval else GRANTED)
    decision: dict[str, Any] = {"outcome": status}
    if denied is not None:
        decision["reason"] = denied
    if granted is not None:
        decision["grant"] = granted
        decision["caveats"] = dict(caveats or {})
    model.assert_content(
        weft, broker, rid, CAP_REQUEST, {**received, "status": status, "decision": decision}
    )
    out: dict[str, Any] = {"request": rid}
    if denied is not None:
        out["denied"] = denied
        return out
    out["granted"] = granted
    out["needs_approval"] = needs_approval
    out["caveats"] = dict(caveats or {})
    return out


def requests(weave: Weave) -> list[dict[str, Any]]:
    """Every brokered request, folded — the broker's audit log, in id order (deterministic,
    no wall-clock to sort by)."""
    return [
        {"request": cid, **cell.content}
        for cid, cell in sorted(weave.cells.items())
        if cell.type == CAP_REQUEST and not cell.retracted
    ]


# ── prompt-volume discipline (design §5.8 points 3-4) ────────────────────────
# The failure mode being designed against is named in `VISION.md`: a prompt the user always
# clicks yes on has NEGATIVE security value, because it trains the reflex that a real prompt
# then rides in on. So the surface is tiered by blast radius, and the DEFAULT for a promoted
# organ is one capability-scoped approval at promotion time — not one per call.
NOTIFICATION = "notification"  # recorded, listed, revocable; no decision solicited
CANARY = "canary"  # a notification with a visible rollback affordance
EXPLICIT = "approval"  # a real decision, with the evidence summary inline

_SURFACE: dict[str, str] = {
    "pure": NOTIFICATION,
    "read_only": NOTIFICATION,
    "workspace_write": CANARY,
    "network": EXPLICIT,
    "financial": EXPLICIT,
}


def inbox_surface(tier: str | None) -> str:
    """How a promotion of `tier` should be SHOWN. Unknown tiers get the explicit surface:
    if we cannot say how much power something has, the human sees it."""
    return _SURFACE.get(tier or "", EXPLICIT)


# The three approval budgets an organ can have. Exactly one applies.
SCOPE_NONE = "none"  # nothing is gated: zero prompts, ever
SCOPE_CAPABILITY = "capability"  # ONE durable approval at promotion time
SCOPE_INVOCATION = "invocation"  # one approval per call, pinned to that call


def prompt_plan(tier: str | None, caveats: dict[str, Any] | None = None) -> dict[str, Any]:
    """The approval budget for one promoted organ — how many prompts, of which scope, why.

    Three cases, and the middle one is the design's stated default (§5.8 point 3):

    * `SCOPE_NONE` — an AUTOMATED-tier organ carrying no approval caveat is not gated at
      all, so nothing should be written and nobody should be asked. Manufacturing a prompt
      here would be the purest form of the failure mode: ceremony with no decision in it.
    * `SCOPE_CAPABILITY` — an AUTOMATED-tier organ that IS gated gets ONE durable "yes,
      this organ may act" at promotion time (`capability.approval_id(cap, None)`), so it
      costs a single decision for its whole life instead of one per invocation.
    * `SCOPE_INVOCATION` — a FLOORED tier (or one we do not recognise) must NEVER acquire
      that blanket: a durable yes on a `financial` organ is exactly the prompt a user learns
      to click through. Each call carries its own `op_bind`-pinned approval instead —
      frontier-independent, so "pay 5" can never authorize "pay 500", and single-use.

    `rollback_affordance` / `evidence_inline` are what the surface owes the reader: a canary
    tier is worthless without a visible way to undo it, and an explicit approval is
    worthless without the evidence that justifies clicking.

    Note what this plan does NOT include: the promotion decision itself. That is one gated
    command and one recorded human decision, counted separately — this is the budget for the
    organ's LIFE after it is live.

    Every value is a bool, a small int or a fixed token; no floats, so a plan could be
    recorded verbatim if a later wave wants it on the log.
    """
    floor = with_floors("", tier, dict(caveats or {}))
    automated = promotion.SIGNER_POLICY.get(tier or "") == promotion.AUTOMATED
    gated = bool(floor.get("requires_approval"))
    scope = SCOPE_INVOCATION if not automated else (SCOPE_CAPABILITY if gated else SCOPE_NONE)
    surface = inbox_surface(tier)
    return {
        "tier": tier,
        "surface": surface,
        "signer_policy": promotion.signer_policy(tier or ""),
        "approval_scope": scope,
        "capability_scoped_approval": scope == SCOPE_CAPABILITY,
        "invocation_approvals": scope == SCOPE_INVOCATION,
        "prompts_per_organ": 1 if scope == SCOPE_CAPABILITY else 0,
        "prompts_per_call": 1 if scope == SCOPE_INVOCATION else 0,
        "rollback_affordance": surface in (CANARY, NOTIFICATION),
        "evidence_inline": surface == EXPLICIT,
        "floor": floor,
    }
