"""Provider-level routing — pick a concrete PROVIDER INSTANCE inside a tier.

VISION "Advanced model strategy — compose, not replace" says model providers are
*replaceable engines behind Decima contracts*, and selection is driven by task,
cost, latency, privacy, context, and verification — never by a vendor brand.

`router.py` answers the first question: which of the four strategy TIERS
(local-small | retrieval-assisted | frontier | judge) fits a `TaskDescriptor`.
It stops there — a tier is a *lane*, not a concrete provider you can invoke. This
module answers the second question: given the live fleet, WHICH provider instance
in (or near) that lane should serve this turn? It reimplements — natively, behind
Decima-owned contracts — the two-stage split a cost/privacy-aware control plane
uses:

  1. ELIGIBILITY — HARD constraints. A provider is either usable for this task or
     it is not, and every rejection carries an explainable, auditable reason:
       • privacy — a private / repo-sensitive task must NOT reach an external
         provider; a secret-sensitive task has ZERO eligible providers (fail
         CLOSED). This composes with router.py `_r_private` (the tier-level hard
         rule); here it is the instance-level hard filter.
       • health — an unhealthy provider is rejected.
       • quota — a provider with no quota left (quota_remaining <= 0) is rejected.
       • capacity — a provider with no capacity (capacity <= 0) is rejected.
       • budget — a paid / rented provider is rejected when the spend budget is
         not configured, or when budget pressure is too high (spend never runs
         autonomously; a paid lane needs headroom + eventually the Morta/approval
         gate downstream).

  2. SCORE — a soft, ADDITIVE, INTEGER ranking over the *eligible* set only. The
     formula is data (auditable): capability_fit + expected_quality(scorecard)
     + latency_fit + privacy_fit + availability + residency_bonus
     − quota_scarcity − queue_delay − model_switch − cost − opportunity_cost.
     A hard-failed provider is NEVER resurrected by a high score — score ranks the
     eligible, it cannot override eligibility.

ZERO authority, exactly like router.py. A `RoutingDecision` names a provider and a
model; it holds no capability, no grant, no principal. Selecting a provider — even
recording that selection on the Weft — confers NO permission to invoke it.
`capability.authorize` + Morta still gate every INVOKE. Selection is advice.

Everything recorded is an INT (ints-not-floats in signed content): costs in
micro-cents, scorecards in [-100, 100], pressure in [0, 100], quotas, and every
score component. The live fleet arrives as the shared int-keyed "live status"
dict; this lane never reads a wall-clock and never touches the network.
"""
from dataclasses import dataclass, field, replace

from decima.model import assert_content, assert_edge

# ── provider privacy tiers (data residency / trust class of the instance) ─────
LOCAL_ONLY = "local_only"          # on-device / in-VPC; data never leaves
PRIVATE_RENTED = "private_rented"  # dedicated rented capacity; rented ⇒ costs money
EXTERNAL = "external"              # a public API endpoint (free tier / no charge)
EXTERNAL_PAID = "external_paid"    # a public API endpoint that bills per token
PRIVACY_TIERS = (LOCAL_ONLY, PRIVATE_RENTED, EXTERNAL, EXTERNAL_PAID)

# Tiers that COST MONEY to use — a charge against them must have budget headroom
# (and, downstream, route through the ApprovalInbox + Morta gate; spend is never
# autonomous). These are the tiers eligibility budget-gates.
PAID_TIERS = frozenset({PRIVATE_RENTED, EXTERNAL_PAID})

# ── task privacy class → the set of provider privacy tiers it MAY reach ───────
# The router-level `TaskDescriptor.privacy` is public|sensitive|private; this lane
# also honours richer upstream classes (repo_sensitive, secret_sensitive) that the
# redaction lane may stamp — without editing router.py (privacy is a plain str).
# UNKNOWN classes fail CLOSED (empty allowed set) — untrusted-is-data.
_ALL = frozenset(PRIVACY_TIERS)
PRIVACY_ALLOWED = {
    "public":          _ALL,
    "sensitive":       frozenset({LOCAL_ONLY, PRIVATE_RENTED}),
    "repo_sensitive":  frozenset({LOCAL_ONLY, PRIVATE_RENTED}),
    "private":         frozenset({LOCAL_ONLY}),
    "secret_sensitive": frozenset(),   # ZERO eligible — fail closed
}


def allowed_privacy_tiers(privacy_class: str) -> frozenset:
    """The provider privacy tiers a task of this privacy class may reach. An
    unrecognised class fails CLOSED (nothing eligible)."""
    return PRIVACY_ALLOWED.get(privacy_class, frozenset())


# Budget pressure at/above this (on a 0..100 scale) locks out paid/rented lanes.
BUDGET_PRESSURE_LOCKOUT = 80

# Score tuning — every constant is an INT; the whole formula is auditable data.
POLICY_VERSION = 1
_SCARCE_QUOTA = 2000      # below this, quota scarcity starts to bite
_LOW_CAPACITY = 8         # below this, queue delay starts to bite
_COST_DIVISOR = 100       # micro-cents → penalty points (integer floor divide)
_MODEL_SWITCH_PENALTY = 4 # discourage thrashing the in-flight model
_FIT_MATCH = 20           # provider tier matches the task's desired tier
_FIT_NEAR = 5             # provider tier is a plausible neighbour


@dataclass(frozen=True)
class Provider:
    """One concrete, invokable model instance in the fleet. Static identity
    (id/tier/privacy_tier/model/residency) plus its live int metrics from the
    shared status dict (cost/health/quota/capacity/scorecard). Note what is
    ABSENT: no capability, no grant, no key — a Provider confers no authority."""
    id: str
    tier: str                         # a router.py lane name
    privacy_tier: str                 # one of PRIVACY_TIERS
    model: str = ""                   # concrete engine id (vendor-neutral config)
    cost_per_1k_microcents: int = 0   # per-1k-token cost, MICRO-CENTS, int
    healthy: bool = True
    quota_remaining: int = 0          # tokens/requests left this window, int
    capacity: int = 0                 # concurrent slots free, int
    residency: str = ""               # data-residency region tag
    scorecard: int = 0                # learned quality in [-100, 100], int

    def __post_init__(self):
        # ints-not-floats: reject float metrics at the boundary, loudly.
        for f in ("cost_per_1k_microcents", "quota_remaining", "capacity", "scorecard"):
            v = getattr(self, f)
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"Provider.{f} must be int, got {type(v).__name__}: {v!r}")


def providers_from_status(status: dict) -> list:
    """Build the candidate Provider list from a shared "live status" dict. Each
    entry supplies the provider's live int metrics (the spend lane produces
    quota_remaining/scorecard; the fleet supplies the rest)."""
    out = []
    for p in status.get("providers", []):
        out.append(Provider(
            id=p["id"], tier=p["tier"], privacy_tier=p["privacy_tier"],
            model=p.get("model", p["id"]),
            cost_per_1k_microcents=int(p.get("cost_per_1k_microcents", 0)),
            healthy=bool(p.get("healthy", True)),
            quota_remaining=int(p.get("quota_remaining", 0)),
            capacity=int(p.get("capacity", 0)),
            residency=p.get("residency", ""),
            scorecard=int(p.get("scorecard", 0)),
        ))
    return out


def _budget(status: dict) -> dict:
    b = status.get("budget") or {}
    return {
        "remaining_microcents": int(b.get("remaining_microcents", 0)),
        "pressure": int(b.get("pressure", 0)),
        "configured": bool(b.get("configured", False)),
    }


# ── stage 1: eligibility (HARD constraints, explainable rejections) ───────────
def eligibility(descriptor, providers, status):
    """Split `providers` into (eligible, rejected). Each rejection is
    {"provider_id": str, "reason": str} — an explainable hard failure. A
    secret-sensitive task (or any provider that violates the privacy class,
    is unhealthy, is out of quota/capacity, or is a paid lane with no budget
    headroom) is rejected here and cannot be scored back in."""
    allowed = allowed_privacy_tiers(descriptor.privacy)
    budget = _budget(status)
    eligible, rejected = [], []
    for p in providers:
        reason = _reject_reason(descriptor, p, allowed, budget)
        if reason is None:
            eligible.append(p)
        else:
            rejected.append({"provider_id": p.id, "reason": reason})
    return eligible, rejected


def _reject_reason(descriptor, p, allowed, budget):
    """The FIRST hard constraint a provider violates, as an explainable string,
    or None if it is eligible. Privacy is checked first (fail-closed spine)."""
    if p.privacy_tier not in allowed:
        return (f"privacy: a {descriptor.privacy} task cannot route to a "
                f"{p.privacy_tier} provider")
    if not p.healthy:
        return "health: provider is unhealthy"
    if p.quota_remaining <= 0:
        return "quota: no quota_remaining"
    if p.capacity <= 0:
        return "capacity: no free capacity"
    if p.privacy_tier in PAID_TIERS:
        if not budget["configured"]:
            return "budget: paid/rented lane but no budget configured"
        if budget["pressure"] >= BUDGET_PRESSURE_LOCKOUT:
            return (f"budget: pressure {budget['pressure']} >= lockout "
                    f"{BUDGET_PRESSURE_LOCKOUT} — paid/rented lane held back")
    return None


# ── stage 2: additive integer score over the ELIGIBLE set only ────────────────
@dataclass(frozen=True)
class ScoreBreakdown:
    """Every component is an INT and the total is their exact integer sum. The
    breakdown is auditable data — a reviewer can recompute `total` by hand."""
    provider_id: str
    capability_fit: int
    expected_quality: int
    latency_fit: int
    privacy_fit: int
    availability: int
    residency_bonus: int
    quota_scarcity_penalty: int
    queue_delay_penalty: int
    model_switch_penalty: int
    cost_penalty: int
    opportunity_cost: int

    @property
    def total(self) -> int:
        return (self.capability_fit + self.expected_quality + self.latency_fit
                + self.privacy_fit + self.availability + self.residency_bonus
                - self.quota_scarcity_penalty - self.queue_delay_penalty
                - self.model_switch_penalty - self.cost_penalty
                - self.opportunity_cost)

    def to_content(self) -> dict:
        d = {
            "provider_id": self.provider_id,
            "capability_fit": self.capability_fit,
            "expected_quality": self.expected_quality,
            "latency_fit": self.latency_fit,
            "privacy_fit": self.privacy_fit,
            "availability": self.availability,
            "residency_bonus": self.residency_bonus,
            "quota_scarcity_penalty": self.quota_scarcity_penalty,
            "queue_delay_penalty": self.queue_delay_penalty,
            "model_switch_penalty": self.model_switch_penalty,
            "cost_penalty": self.cost_penalty,
            "opportunity_cost": self.opportunity_cost,
            "total": self.total,
        }
        return d


# Which tier a descriptor "wants", instance-independent — mirrors router.py's
# spirit (high stakes → frontier; evaluation → judge; else the cheap lane).
def _desired_tier(descriptor):
    from decima import router as R
    if descriptor.kind in R._EVAL_KINDS:
        return R.JUDGE
    if descriptor.stakes == "high" or descriptor.kind in R._HARD_KINDS:
        return R.FRONTIER
    if descriptor.needs_context or descriptor.context_tokens > R.CONTEXT_TOKEN_THRESHOLD:
        return R.RETRIEVAL_ASSISTED
    return R.LOCAL_SMALL


_PRIVACY_FIT = {LOCAL_ONLY: 8, PRIVATE_RENTED: 4, EXTERNAL: 1, EXTERNAL_PAID: 0}


def score(descriptor, provider, status):
    """Additive integer ScoreBreakdown for one provider. Deterministic; pure;
    reads only the descriptor, the provider's int metrics, and the injected
    status budget. Higher is better; the components are auditable."""
    budget = _budget(status)
    desired = _desired_tier(descriptor)

    capability_fit = _FIT_MATCH if provider.tier == desired else _FIT_NEAR
    expected_quality = provider.scorecard          # already an int in [-100, 100]

    latency_fit = 10 if (descriptor.latency == "realtime"
                         and provider.tier == "local-small") else 0
    privacy_fit = _PRIVACY_FIT.get(provider.privacy_tier, 0)
    availability = min(provider.quota_remaining, provider.capacity * 100) // 100
    residency_bonus = 5 if provider.residency in ("local", "on_prem", "in_vpc") else 0

    quota_scarcity_penalty = (max(0, _SCARCE_QUOTA - provider.quota_remaining)
                              // 100)
    queue_delay_penalty = max(0, _LOW_CAPACITY - provider.capacity)
    # model-switch penalty is applied by the caller (it knows the in-flight model);
    # in the pure per-provider score it is 0 and folded in during ranking.
    model_switch_penalty = 0
    cost_penalty = provider.cost_per_1k_microcents // _COST_DIVISOR
    # opportunity cost: hold back expensive frontier lanes as budget tightens.
    opportunity_cost = (budget["pressure"] // 10) if provider.tier == "frontier" else 0

    return ScoreBreakdown(
        provider_id=provider.id,
        capability_fit=capability_fit,
        expected_quality=expected_quality,
        latency_fit=latency_fit,
        privacy_fit=privacy_fit,
        availability=availability,
        residency_bonus=residency_bonus,
        quota_scarcity_penalty=quota_scarcity_penalty,
        queue_delay_penalty=queue_delay_penalty,
        model_switch_penalty=model_switch_penalty,
        cost_penalty=cost_penalty,
        opportunity_cost=opportunity_cost,
    )


# ── the decision object (ZERO authority — no cap/grant/principal) ─────────────
@dataclass(frozen=True)
class RoutingDecision:
    """The selection result. Like router.py's `Routing`, note what is ABSENT: no
    capability, no grant, no principal, no key. Selecting — or recording — a
    provider grants nothing; authorize() still gates every INVOKE."""
    selected_provider: str            # provider id, or "" if none eligible
    selected_model: str
    rejected: tuple = ()              # ({provider_id, reason}, …)
    scores: dict = field(default_factory=dict)   # {provider_id: int total}
    breakdowns: tuple = ()            # (ScoreBreakdown, …) for the eligible set
    policy_version: int = POLICY_VERSION
    cell: str = ""                    # Weft Cell id, once recorded

    @property
    def routed(self) -> bool:
        return bool(self.selected_provider)


def select(descriptor, providers, status, *, last_model=None):
    """Pure two-stage selection. Eligibility hard-filters, then an additive
    integer score ranks the eligible set; the highest total wins (ties broken
    deterministically by provider id). Returns a RoutingDecision. FAIL CLOSED:
    if nothing is eligible (e.g. a secret-sensitive task), selected_provider is
    "" and every candidate appears in `rejected` with a reason.

    `last_model` (optional) folds a model-switch penalty into ranking only — it
    never changes eligibility."""
    eligible, rejected = eligibility(descriptor, providers, status)

    breakdowns = []
    scores = {}
    for p in eligible:
        bd = score(descriptor, p, status)
        if last_model is not None and p.model != last_model:
            bd = replace(bd, model_switch_penalty=_MODEL_SWITCH_PENALTY)
        breakdowns.append(bd)
        scores[p.id] = bd.total

    if breakdowns:
        # Deterministic: highest total wins; ties broken by provider id ascending.
        best = min(breakdowns, key=lambda b: (-b.total, b.provider_id))
        chosen = next(p for p in eligible if p.id == best.provider_id)
        selected_provider, selected_model = chosen.id, chosen.model
    else:
        selected_provider, selected_model = "", ""

    return RoutingDecision(
        selected_provider=selected_provider,
        selected_model=selected_model,
        rejected=tuple(rejected),
        scores=scores,
        breakdowns=tuple(breakdowns),
        policy_version=POLICY_VERSION,
    )


# ── provenance: record a selection as a Cell on the Weft ──────────────────────
PROVIDER_ROUTING = "provider_routing"


def record(k, decision, *, author=None, provenance=None, descriptor=None):
    """Record a RoutingDecision as a `provider_routing` Cell on the Weft (Law 4,
    provenance). Every recorded numeric is an int (scores, penalties, cost,
    policy_version). Returns the Cell id.

    Recording confers ZERO authority — this writes a provenance Cell, it mints no
    capability and no grant. `provenance` (optional) links the decision back to
    the request Cell that raised it (a `routes` edge)."""
    from decima.hashing import content_id
    author = author or k.decima_agent_id
    cid = content_id({
        "provider_routing": decision.selected_provider,
        "scores": decision.scores,
        "policy_version": int(decision.policy_version),
        "lamport": k.weft.lamport,
    })
    content = {
        "selected_provider": decision.selected_provider,
        "selected_model": decision.selected_model,
        "rejected": [dict(r) for r in decision.rejected],
        "scores": {pid: int(s) for pid, s in decision.scores.items()},
        "breakdowns": [b.to_content() for b in decision.breakdowns],
        "policy_version": int(decision.policy_version),
    }
    if descriptor is not None:
        content["privacy_class"] = descriptor.privacy
        content["desired_tier"] = _desired_tier(descriptor)
    assert_content(k.weft, author, cid, PROVIDER_ROUTING, content)
    if provenance is not None:
        assert_edge(k.weft, author, cid, "routes", provenance)
    return cid
