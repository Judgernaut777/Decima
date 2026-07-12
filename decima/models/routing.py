"""Model routing — a pure policy from a task spec to a RECORDED decision.

Given a `TaskSpec` (task-class, sensitivity, modalities, context-size, latency,
cost-budget) and the `ModelRegistry` (which models exist, local or remote), the
policy computes a `RoutingDecision`: the selected model, an ordered fallback chain,
the reason codes that explain the choice, the estimated cost (int micro-cents), and
a context policy. The decision is DATA — it is RETURNED, and a caller may fold it
onto the Weft via `record`. It carries ZERO authority (invariant 3/4): naming — or
recording — a model grants NO permission to invoke it; the kernel's authorization +
approval + receipt chain, which lives outside this package, still gates every effect.

Hard rules first (eligibility), then a deterministic integer ranking over the
eligible set — mirroring `heartbeat/decima/provider_router.py` in spirit but at the
model-catalogue level. A local-only policy for sensitive tasks is ENFORCEABLE: a
sensitive/private task filters to local models before ranking, so it can never be
routed to an external provider. Provider failure at call time triggers a BOUNDED
fallback down the recorded chain (`route_and_complete`).

Every recorded numeric is an INT (invariant 6); nothing here reads a clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from decima.models.providers import ModelRequest, ModelResponse
from decima.models.registry import GRADED_CAPABILITIES, ModelEntry, ModelRegistry

POLICY_VERSION = 1


class ReasonCode:
    """The auditable vocabulary of WHY a routing landed where it did."""

    SENSITIVE_LOCAL_ONLY = "sensitive_local_only"   # sensitivity forced the local lane
    LOCAL_AVAILABLE = "local_available"
    NO_LOCAL_FOR_SENSITIVE = "no_local_for_sensitive"  # fail closed: sensitive + no local
    MODALITY_MATCH = "modality_match"
    MODALITY_UNSUPPORTED = "modality_unsupported"
    CONTEXT_FITS = "context_fits"
    CONTEXT_EXCEEDS = "context_exceeds"              # no model's window fits
    CONTEXT_TRUNCATE = "context_truncate"
    COST_WITHIN_BUDGET = "cost_within_budget"
    COST_EXCEEDS_BUDGET = "cost_exceeds_budget"
    STRUCTURED_REQUIRED = "structured_required"
    TOOLS_REQUIRED = "tools_required"
    CAPABILITY_UNMET = "capability_unmet"          # a REQUIRED capability was not met
    CAPABILITY_MATCH = "capability_match"          # required/preferred caps steered choice
    LOWEST_COST = "lowest_cost"
    LATENCY_PREFERS_LOCAL = "latency_prefers_local"
    NO_ELIGIBLE = "no_eligible"
    SELECTED = "selected"
    FALLBACK_CHAIN = "fallback_chain"


# ── context policy tags ───────────────────────────────────────────────────────
CTX_FULL = "full"           # the whole context fits the selected model's window
CTX_TRUNCATE = "truncate"   # context exceeds the window → truncate/summarize upstream


SENSITIVE_CLASSES = frozenset({"sensitive", "private", "repo_sensitive", "secret_sensitive"})


@dataclass(frozen=True)
class TaskSpec:
    """What the caller knows about the turn, vendor-neutrally. Ints are ints.

    `required_capabilities` and `preferred_capabilities` let a caller route by
    CAPABILITY rather than name: each is a tuple of ``(capability_name, min_level)``
    pairs over the graded capabilities (``reasoning_strength``, ``coding``,
    ``planning``, ``structured_reliability``). REQUIRED pairs HARD-FILTER the
    catalogue (an entry scoring below the level is ineligible); PREFERRED pairs only
    bias the deterministic ranking toward better-matching models. With BOTH empty the
    routing is byte-identical to capability-unaware routing. Capabilities steer
    SELECTION only — they mint no authority (invariant 3); a model that over-claims a
    tag can be *proposed* more often but is never *permitted* more."""

    task_class: str = "chat"          # classify|extract|generate|plan|judge|code|chat…
    sensitivity: str = "public"       # public | sensitive | private | repo_sensitive
    modalities: tuple[str, ...] = ("text",)
    context_size: int = 0             # estimated context tokens
    latency: str = "interactive"      # realtime | interactive | batch
    cost_budget_microcents: int | None = None  # None ⇒ unbounded
    structured_output: bool = False
    tool_use: bool = False
    required_capabilities: tuple[tuple[str, int], ...] = ()
    preferred_capabilities: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.context_size, bool) or not isinstance(self.context_size, int):
            raise TypeError("context_size must be int")
        if self.cost_budget_microcents is not None and (
            isinstance(self.cost_budget_microcents, bool)
            or not isinstance(self.cost_budget_microcents, int)
        ):
            raise TypeError("cost_budget_microcents must be int or None")
        for field_name in ("required_capabilities", "preferred_capabilities"):
            for pair in getattr(self, field_name):
                if (not isinstance(pair, tuple)) or len(pair) != 2:
                    raise TypeError(f"{field_name} entries must be (name, level) pairs")
                name, level = pair
                if name not in GRADED_CAPABILITIES:
                    raise ValueError(
                        f"unknown graded capability {name!r}; "
                        f"expected one of {GRADED_CAPABILITIES}"
                    )
                if isinstance(level, bool) or not isinstance(level, int) or level < 0:
                    raise TypeError(f"{field_name} level for {name!r} must be a non-negative int")

    @property
    def is_sensitive(self) -> bool:
        return self.sensitivity in SENSITIVE_CLASSES

    @property
    def has_capability_requirements(self) -> bool:
        return bool(self.required_capabilities) or bool(self.preferred_capabilities)


@dataclass(frozen=True)
class RoutingDecision:
    """The routing result — DATA, ZERO authority. Note the absence of any
    capability, grant, principal, or key. `estimated_cost` is int micro-cents."""

    selected_model: str
    fallback_models: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    estimated_cost: int = 0
    context_policy: str = CTX_FULL
    policy_version: int = POLICY_VERSION
    rejected: tuple[dict, ...] = ()
    cell: str = ""

    @property
    def routed(self) -> bool:
        return bool(self.selected_model)

    def to_content(self) -> dict:
        return {
            "selected_model": self.selected_model,
            "fallback_models": list(self.fallback_models),
            "reason_codes": list(self.reason_codes),
            "estimated_cost": int(self.estimated_cost),
            "context_policy": self.context_policy,
            "policy_version": int(self.policy_version),
            "rejected": [dict(r) for r in self.rejected],
        }


def estimate_cost(entry: ModelEntry, context_size: int, max_output_tokens: int) -> int:
    """Deterministic integer cost estimate in micro-cents: cost-per-1k × ceil(total
    tokens / 1000). Local models (cost 0) come out 0. Pure integer arithmetic."""
    total = int(context_size) + int(max_output_tokens)
    kilotokens = math.ceil(total / 1000) if total > 0 else 0
    return int(entry.est_cost_per_1k_microcents) * kilotokens


class RoutingPolicy:
    """Pure model selector over a registry. Construct once and share; selection has
    no mutable state and no authority."""

    def __init__(self, policy_version: int = POLICY_VERSION) -> None:
        self.policy_version = policy_version

    def _eligible(
        self, spec: TaskSpec, registry: ModelRegistry, max_output_tokens: int
    ) -> tuple[list[ModelEntry], list[dict], list[str]]:
        """Hard-filter the enabled catalogue. Returns (eligible, rejected, reasons).
        Sensitive ⇒ local-only (enforceable privacy). Modality/context/structured/
        tool are hard requirements; over-budget cost is a hard rejection too."""
        reasons: list[str] = []
        rejected: list[dict] = []
        want_modalities = set(spec.modalities)
        need_context = int(spec.context_size)

        if spec.is_sensitive:
            reasons.append(ReasonCode.SENSITIVE_LOCAL_ONLY)

        eligible: list[ModelEntry] = []
        for e in registry.enabled_entries():
            if spec.is_sensitive and not e.local:
                rejected.append({"model": e.model, "reason": ReasonCode.SENSITIVE_LOCAL_ONLY})
                continue
            if want_modalities and not want_modalities.issubset(set(e.modalities)):
                rejected.append({"model": e.model, "reason": ReasonCode.MODALITY_UNSUPPORTED})
                continue
            if spec.structured_output and not e.structured_output:
                rejected.append({"model": e.model, "reason": ReasonCode.STRUCTURED_REQUIRED})
                continue
            if spec.tool_use and not e.tool_use:
                rejected.append({"model": e.model, "reason": ReasonCode.TOOLS_REQUIRED})
                continue
            unmet = [
                name for name, level in spec.required_capabilities
                if e.capability_score(name) < level
            ]
            if unmet:
                rejected.append({
                    "model": e.model,
                    "reason": ReasonCode.CAPABILITY_UNMET,
                    "capabilities": sorted(unmet),
                })
                continue
            if e.context_limit < need_context:
                rejected.append({"model": e.model, "reason": ReasonCode.CONTEXT_EXCEEDS})
                continue
            cost = estimate_cost(e, spec.context_size, max_output_tokens)
            if spec.cost_budget_microcents is not None and cost > spec.cost_budget_microcents:
                rejected.append({"model": e.model, "reason": ReasonCode.COST_EXCEEDS_BUDGET})
                continue
            eligible.append(e)
        return eligible, rejected, reasons

    def _capability_shortfall(self, spec: TaskSpec, e: ModelEntry) -> int:
        """Deterministic, bounded integer measure of how POORLY an entry matches the
        task's preferred capabilities: the summed shortfall below each preferred
        level (0 = fully meets or exceeds every preference). Lower is better. With no
        preferences this is 0 for EVERY entry, so it drops out of the ranking and the
        order is identical to capability-unaware routing."""
        return sum(
            max(0, level - e.capability_score(name))
            for name, level in spec.preferred_capabilities
        )

    def _rank_key(self, spec: TaskSpec, max_output_tokens: int):
        """Deterministic ranking key over the eligible set. Sensitive/realtime
        prefer local; then the capability-match term (0 when no capabilities are
        requested, so the ranking is IDENTICAL to today); then cheapest; ties broken
        by model id (stable). The capability term sits BEFORE cost/model but AFTER the
        local-preference rank, so a sensitive/realtime task still keeps its local lane
        and the deterministic placeholder still ranks below a real provider."""

        def key(e: ModelEntry):
            prefer_local = spec.is_sensitive or spec.latency == "realtime"
            local_rank = 0 if (prefer_local and e.local) else 1
            cap_shortfall = self._capability_shortfall(spec, e)
            cost = estimate_cost(e, spec.context_size, max_output_tokens)
            return (local_rank, cap_shortfall, cost, e.model)

        return key

    def select(
        self,
        spec: TaskSpec,
        registry: ModelRegistry,
        *,
        max_output_tokens: int = 512,
    ) -> RoutingDecision:
        """Route a task to a model + ordered fallback chain, with reason codes and an
        int cost estimate. FAIL CLOSED: nothing eligible ⇒ selected_model='' and every
        candidate appears in `rejected`. Pure; no authority; no clock."""
        eligible, rejected, reasons = self._eligible(spec, registry, max_output_tokens)

        if not eligible:
            if spec.is_sensitive and not registry.has_local():
                reasons.append(ReasonCode.NO_LOCAL_FOR_SENSITIVE)
            reasons.append(ReasonCode.NO_ELIGIBLE)
            return RoutingDecision(
                selected_model="",
                reason_codes=tuple(reasons),
                estimated_cost=0,
                policy_version=self.policy_version,
                rejected=tuple(rejected),
            )

        ranked = sorted(eligible, key=self._rank_key(spec, max_output_tokens))
        chosen = ranked[0]
        fallbacks = tuple(e.model for e in ranked[1:])
        cost = estimate_cost(chosen, spec.context_size, max_output_tokens)

        reasons.append(ReasonCode.SELECTED)
        if chosen.local:
            reasons.append(ReasonCode.LOCAL_AVAILABLE)
            if spec.latency == "realtime":
                reasons.append(ReasonCode.LATENCY_PREFERS_LOCAL)
        if spec.modalities:
            reasons.append(ReasonCode.MODALITY_MATCH)
        ctx_policy = CTX_FULL
        if int(spec.context_size) <= chosen.context_limit:
            reasons.append(ReasonCode.CONTEXT_FITS)
        else:  # pragma: no cover - eligibility already excludes this, defensive
            reasons.append(ReasonCode.CONTEXT_TRUNCATE)
            ctx_policy = CTX_TRUNCATE
        if spec.cost_budget_microcents is not None:
            reasons.append(ReasonCode.COST_WITHIN_BUDGET)
        if spec.has_capability_requirements:
            reasons.append(ReasonCode.CAPABILITY_MATCH)
        reasons.append(ReasonCode.LOWEST_COST)
        if fallbacks:
            reasons.append(ReasonCode.FALLBACK_CHAIN)

        return RoutingDecision(
            selected_model=chosen.model,
            fallback_models=fallbacks,
            reason_codes=tuple(reasons),
            estimated_cost=cost,
            context_policy=ctx_policy,
            policy_version=self.policy_version,
            rejected=tuple(rejected),
        )


# ── bounded fallback execution over the recorded chain ────────────────────────
@dataclass(frozen=True)
class RouteResult:
    """The outcome of driving a routing decision through providers, DATA only. Names
    the model that answered and the chain of attempts (each a model id + why it was
    abandoned). Confers no authority — the response is a PROPOSAL."""

    response: ModelResponse | None
    model: str
    decision: RoutingDecision
    attempts: tuple[dict, ...] = ()

    @property
    def ok(self) -> bool:
        return self.response is not None and not self.response.failed


def route_and_complete(
    decision: RoutingDecision,
    registry: ModelRegistry,
    request: ModelRequest,
    *,
    max_hops: int = 3,
) -> RouteResult:
    """Try the selected model, and on provider FAILURE or REFUSAL fall through the
    recorded fallback chain — BOUNDED by `max_hops`. Returns a `RouteResult` naming
    the model that answered and every attempt. A provider that raises (e.g. a live
    adapter with no transport) is treated as a failed attempt, not a crash. This
    switches models; it grants no authority — the answer is still a proposal."""
    chain = [decision.selected_model, *decision.fallback_models]
    chain = [m for m in chain if m][:max_hops]
    attempts: list[dict] = []
    last: ModelResponse | None = None

    for model in chain:
        provider = registry.provider_for(model)
        if provider is None:
            attempts.append({"model": model, "outcome": "no_provider"})
            continue
        try:
            resp = provider.complete(request)
        except Exception as exc:  # a live adapter with no transport, etc. — bounded
            attempts.append({"model": model, "outcome": f"exception:{type(exc).__name__}"})
            continue
        last = resp
        if resp.failed:
            attempts.append({"model": model, "outcome": "failed"})
            continue
        if resp.refused:
            attempts.append({"model": model, "outcome": "refused"})
            continue
        attempts.append({"model": model, "outcome": "ok"})
        return RouteResult(resp, model, decision, tuple(attempts))

    return RouteResult(last, "", decision, tuple(attempts))


# ── provenance: record a decision as a Cell on the Weft ───────────────────────
MODEL_ROUTING = "model_routing"


def record(k, decision: RoutingDecision, *, author=None, provenance=None) -> str:
    """Record a `RoutingDecision` as a `model_routing` Cell on the Weft (provenance,
    invariant 1). Every recorded numeric is an int. Recording confers ZERO authority
    — it writes a provenance Cell; it mints no capability and no grant. `provenance`,
    if given, links the decision to the request Cell that raised it (a `routes` edge).

    `k` is a kernel handle exposing `.weft` and `.decima_agent_id` (same shape the
    reference `provider_router.record` uses)."""
    from decima.kernel.hashing import content_id
    from decima.kernel.model import assert_content, assert_edge

    author = author or k.decima_agent_id
    cid = content_id(
        {
            "model_routing": decision.selected_model,
            "reasons": list(decision.reason_codes),
            "policy_version": int(decision.policy_version),
            "lamport": k.weft.lamport,
        }
    )
    assert_content(k.weft, author, cid, MODEL_ROUTING, decision.to_content())
    if provenance is not None:
        assert_edge(k.weft, author, cid, "routes", provenance)
    return cid
