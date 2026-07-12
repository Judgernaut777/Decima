"""Capability-aware routing (post-0.3 evolution, Lane A).

Proves the load-bearing properties of routing by CAPABILITY, not name:

  * with NO capability requirements the ranking is IDENTICAL to capability-unaware
    routing (the whole 0.3 behaviour is preserved);
  * a REQUIRED capability hard-filters the eligible set (fail closed);
  * a PREFERRED capability biases the deterministic ranking toward a better match,
    with stable tie-breaks and no clock;
  * capability metadata is int-clean end to end (recorded content, validation bounds);

and the ADVERSARIAL invariants (capabilities drive SELECTION only, never AUTHORITY):

  * a model over-claiming every capability gains no capability/grant/authority — the
    selection is still inert DATA with no effect method;
  * a sensitive/private task NEVER selects an external model even when that external
    model claims the strongest capabilities and the local model claims none.
"""

from __future__ import annotations

import pytest

from decima.models import routing
from decima.models.providers import EXTERNAL_PAID, LOCAL_ONLY
from decima.models.registry import (
    CAP_MAX,
    ModelEntry,
    ModelRegistry,
)
from decima.models.routing import ReasonCode, RoutingDecision, RoutingPolicy, TaskSpec


def _entry(model, *, local, cost, **caps):
    return ModelEntry(
        provider=("local" if local else "cloud"),
        model=model,
        local=local,
        context_limit=(8192 if local else 200_000),
        modalities=("text", "code"),
        structured_output=True,
        tool_use=not local,
        est_cost_per_1k_microcents=cost,
        privacy_class=(LOCAL_ONLY if local else EXTERNAL_PAID),
        **caps,
    )


def _two_local_registry():
    """Two local models, identical cost — a weak generalist and a strong coder — so
    ONLY a capability preference can distinguish them."""
    reg = ModelRegistry()
    reg.register(_entry("weak-generalist", local=True, cost=0, coding=1, reasoning_strength=1))
    reg.register(_entry("strong-coder", local=True, cost=0, coding=5, reasoning_strength=3))
    return reg


# ── 1. no requirements ⇒ ranking identical to capability-unaware routing ───────
def test_no_capability_requirements_is_identical_ranking():
    reg = ModelRegistry()
    reg.register(_entry("local-free", local=True, cost=0, coding=1))
    reg.register(_entry("cloud-paid", local=False, cost=3000, coding=5, reasoning_strength=5))
    policy = RoutingPolicy()

    plain = policy.select(TaskSpec(task_class="chat", modalities=("text",)), reg)
    # capability-unaware: local is free ⇒ selected; cloud is the fallback (unchanged).
    assert plain.selected_model == "local-free"
    assert plain.fallback_models == ("cloud-paid",)
    # the capability-match reason is ONLY emitted when requirements exist.
    assert ReasonCode.CAPABILITY_MATCH not in plain.reason_codes


def test_empty_capabilities_rank_key_is_constant_term():
    """The capability shortfall term is 0 for every entry when no preferences are
    given, so it cannot reorder the eligible set."""
    reg = _two_local_registry()
    spec = TaskSpec(task_class="chat")
    key = RoutingPolicy()._rank_key(spec, 512)
    shortfalls = {e.model: key(e)[1] for e in reg.enabled_entries()}
    assert set(shortfalls.values()) == {0}


# ── 2. required capability hard-filters (fail closed) ──────────────────────────
def test_required_capability_hard_filters_eligible_set():
    reg = _two_local_registry()
    spec = TaskSpec(
        task_class="code",
        required_capabilities=(("coding", 4),),
    )
    decision = RoutingPolicy().select(spec, reg)
    assert decision.selected_model == "strong-coder"
    # the weak model is hard-rejected for the unmet capability.
    rej = {r["model"]: r for r in decision.rejected}
    assert "weak-generalist" in rej
    assert rej["weak-generalist"]["reason"] == ReasonCode.CAPABILITY_UNMET
    assert rej["weak-generalist"]["capabilities"] == ["coding"]
    assert ReasonCode.CAPABILITY_MATCH in decision.reason_codes


def test_unsatisfiable_required_capability_fails_closed():
    reg = _two_local_registry()
    spec = TaskSpec(required_capabilities=(("coding", CAP_MAX + 0), ("reasoning_strength", 5)))
    # no entry meets reasoning_strength=5 ⇒ nothing eligible.
    decision = RoutingPolicy().select(spec, reg)
    assert decision.selected_model == ""
    assert decision.routed is False
    assert ReasonCode.NO_ELIGIBLE in decision.reason_codes
    assert all(r["reason"] == ReasonCode.CAPABILITY_UNMET for r in decision.rejected)


# ── 3. preferred capability biases ranking (soft), deterministically ───────────
def test_preferred_capability_biases_ranking_without_filtering():
    reg = _two_local_registry()
    # both eligible (no hard filter), same cost — the preference picks the coder.
    spec = TaskSpec(task_class="code", preferred_capabilities=(("coding", 5),))
    decision = RoutingPolicy().select(spec, reg)
    assert decision.selected_model == "strong-coder"
    assert "weak-generalist" in decision.fallback_models  # still a fallback, not filtered
    # both remain eligible: neither was rejected.
    assert decision.rejected == ()


def test_preferred_capability_ranking_is_deterministic_and_stable():
    reg = _two_local_registry()
    spec = TaskSpec(preferred_capabilities=(("coding", 5),))
    d1 = RoutingPolicy().select(spec, reg)
    d2 = RoutingPolicy().select(spec, reg)
    assert d1.selected_model == d2.selected_model == "strong-coder"
    assert d1.fallback_models == d2.fallback_models


def test_capability_preference_never_overrides_cost_when_shortfall_ties():
    """When two models fully satisfy the preference (shortfall 0 for both), the term
    drops out and cheaper still wins — capability is a term BEFORE cost, not instead
    of it."""
    reg = ModelRegistry()
    reg.register(_entry("cheap", local=True, cost=0, coding=5))
    reg.register(_entry("pricey", local=True, cost=10, coding=5))
    spec = TaskSpec(preferred_capabilities=(("coding", 5),))
    decision = RoutingPolicy().select(spec, reg)
    assert decision.selected_model == "cheap"


# ── 4. int-clean end to end + validation of the vocabulary ─────────────────────
def test_capability_metadata_is_int_clean_in_recorded_content():
    e = _entry(
        "m",
        local=True,
        cost=0,
        coding=3,
        reasoning_strength=2,
        planning=1,
        structured_reliability=4,
    )
    content = e.to_content()
    for name in ("reasoning_strength", "coding", "planning", "structured_reliability"):
        assert isinstance(content[name], int) and not isinstance(content[name], bool)
    assert content["latency_class"] == "interactive"
    assert content["cost_class"] == "free"


def test_float_capability_score_is_rejected():
    with pytest.raises(TypeError):
        _entry("m", local=True, cost=0, coding=3.5)  # type: ignore[arg-type]


def test_out_of_range_capability_score_is_rejected():
    with pytest.raises(ValueError):
        _entry("m", local=True, cost=0, coding=CAP_MAX + 1)


def test_unknown_required_capability_name_is_rejected():
    with pytest.raises(ValueError):
        TaskSpec(required_capabilities=(("cleverness", 3),))


def test_float_requirement_level_is_rejected():
    with pytest.raises(TypeError):
        TaskSpec(preferred_capabilities=(("coding", 2.0),))  # type: ignore[arg-type]


# ── 5. ADVERSARIAL: capabilities drive SELECTION only, never AUTHORITY ─────────
def test_overclaimed_capability_confers_no_authority():
    """A model claiming the maximum on every capability is still selected as inert
    DATA — the decision exposes no capability/grant/principal/key and no effect
    method. Capabilities change WHICH model is proposed, never what it may do."""
    reg = ModelRegistry()
    reg.register(
        _entry(
            "overclaimer",
            local=True,
            cost=0,
            coding=CAP_MAX,
            reasoning_strength=CAP_MAX,
            planning=CAP_MAX,
            structured_reliability=CAP_MAX,
        )
    )
    spec = TaskSpec(
        required_capabilities=(("coding", CAP_MAX),),
        preferred_capabilities=(("reasoning_strength", CAP_MAX),),
    )
    decision = RoutingPolicy().select(spec, reg)
    assert decision.selected_model == "overclaimer"
    assert isinstance(decision, RoutingDecision)
    # the decision is DATA: no authority-bearing attribute, no effect method.
    for attr in (
        "capability",
        "grant",
        "principal",
        "key",
        "token",
        "execute",
        "invoke",
        "authorize",
        "perform",
    ):
        assert not hasattr(decision, attr), f"a RoutingDecision must not expose {attr!r}"


def test_sensitive_task_never_selects_external_even_with_best_capabilities():
    """The privacy invariant dominates capability: a sensitive/private task filters to
    local BEFORE ranking, so an external model claiming every top capability — against
    a local model claiming NONE — can never be selected and is hard-rejected."""
    reg = ModelRegistry()
    reg.register(
        _entry(
            "weak-local",
            local=True,
            cost=0,
            coding=0,
            reasoning_strength=0,
            planning=0,
            structured_reliability=0,
        )
    )
    reg.register(
        _entry(
            "brilliant-cloud",
            local=False,
            cost=0,
            coding=CAP_MAX,
            reasoning_strength=CAP_MAX,
            planning=CAP_MAX,
            structured_reliability=CAP_MAX,
        )
    )
    # even PREFERRING the very capabilities only the cloud model has, sensitivity wins.
    spec = TaskSpec(
        task_class="code",
        sensitivity="sensitive",
        preferred_capabilities=(("coding", CAP_MAX), ("reasoning_strength", CAP_MAX)),
    )
    decision = RoutingPolicy().select(spec, reg)
    assert decision.selected_model == "weak-local", "sensitive → local only, always"
    assert "brilliant-cloud" not in decision.fallback_models
    rej = {r["model"]: r["reason"] for r in decision.rejected}
    assert rej.get("brilliant-cloud") == ReasonCode.SENSITIVE_LOCAL_ONLY
    assert ReasonCode.SENSITIVE_LOCAL_ONLY in decision.reason_codes


def test_required_capability_cannot_smuggle_an_external_model_into_a_private_task():
    """Even if ONLY an external model can satisfy a required capability, a private task
    fails closed rather than leak to the cloud — privacy is never traded for a match."""
    reg = ModelRegistry()
    reg.register(_entry("plain-local", local=True, cost=0, coding=1))
    reg.register(_entry("cloud-coder", local=False, cost=0, coding=CAP_MAX))
    spec = TaskSpec(
        sensitivity="private",
        required_capabilities=(("coding", CAP_MAX),),
    )
    decision = RoutingPolicy().select(spec, reg)
    # the only capability-satisfying model is external ⇒ filtered by privacy first ⇒
    # nothing eligible; we FAIL CLOSED instead of routing to the cloud.
    assert decision.selected_model == ""
    assert decision.routed is False
    assert ReasonCode.NO_ELIGIBLE in decision.reason_codes
    # the cloud model was rejected for privacy, never merely for capability.
    rej = {r["model"]: r["reason"] for r in decision.rejected}
    assert rej.get("cloud-coder") == ReasonCode.SENSITIVE_LOCAL_ONLY


# ── 6. the record path carries capability reasons, still mints nothing ─────────
def test_recorded_decision_preserves_capability_reason_without_authority():
    import os
    import tempfile

    from decima.kernel.crypto import Keyring
    from decima.kernel.weave import Weave
    from decima.kernel.weft import Weft

    class _K:
        def __init__(self, weft, agent_id):
            self.weft = weft
            self.decima_agent_id = agent_id

    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    k = _K(Weft(db, kr), kr.mint("decima", "root").id)

    reg = _two_local_registry()
    decision = RoutingPolicy().select(TaskSpec(required_capabilities=(("coding", 4),)), reg)
    cid = routing.record(k, decision)
    cell = Weave.fold(k.weft).get(cid)
    assert cell is not None
    assert cell.content["selected_model"] == "strong-coder"
    assert ReasonCode.CAPABILITY_MATCH in cell.content["reason_codes"]
    assert isinstance(cell.content["estimated_cost"], int)
    # the recorded decision carries the sensitivity class (int-clean audit) too.
    assert cell.content["sensitivity_class"] == "public"


# ── 7. DEEPENED ranking: soft locality/latency biases + min-context floor ──────
def _weak_local_strong_cloud():
    """A cheap, close (local) weak model vs an expensive, remote strong one — so a hard
    requirement or a locality bias is the ONLY thing that can flip the choice."""
    reg = ModelRegistry()
    reg.register(_entry("cheap-close-weak", local=True, cost=0, coding=1, reasoning_strength=1))
    reg.register(
        _entry("pricey-remote-strong", local=False, cost=5000, coding=5, reasoning_strength=5)
    )
    return reg


def test_no_requirements_ranking_is_byte_identical_regression_lock():
    """REGRESSION LOCK: a task with NO requirements and NO soft preferences must produce
    a byte-identical decision — same winner, same fallback order, same EXACT reason
    codes — as capability-unaware routing. Any future ranking change that perturbs the
    unrequested path fails here."""
    reg = ModelRegistry()
    reg.register(_entry("local-free", local=True, cost=0, coding=1))
    reg.register(_entry("cloud-paid", local=False, cost=3000, coding=5, reasoning_strength=5))
    decision = RoutingPolicy().select(TaskSpec(task_class="chat", modalities=("text",)), reg)
    assert decision.selected_model == "local-free"
    assert decision.fallback_models == ("cloud-paid",)
    assert decision.reason_codes == (
        ReasonCode.SELECTED,
        ReasonCode.LOCAL_AVAILABLE,
        ReasonCode.MODALITY_MATCH,
        ReasonCode.CONTEXT_FITS,
        ReasonCode.LOWEST_COST,
        ReasonCode.FALLBACK_CHAIN,
    )
    # none of the new soft-preference reason codes leak into an unrequested route.
    for code in (
        ReasonCode.LOCALITY_PREFERRED,
        ReasonCode.LATENCY_PREFERRED,
        ReasonCode.CAPABILITY_MATCH,
    ):
        assert code not in decision.reason_codes


def test_scoring_is_deterministic_and_registration_order_independent():
    """The ranking is a TOTAL order (model id is a unique final tie-break), so the
    decision is identical across repeated runs AND independent of the order in which
    the models were registered — no reliance on dict/insertion order or a clock."""
    spec = TaskSpec(preferred_capabilities=(("coding", 5),))

    forward = ModelRegistry()
    forward.register(_entry("weak-generalist", local=True, cost=0, coding=1, reasoning_strength=1))
    forward.register(_entry("strong-coder", local=True, cost=0, coding=5, reasoning_strength=3))

    reverse = ModelRegistry()
    reverse.register(_entry("strong-coder", local=True, cost=0, coding=5, reasoning_strength=3))
    reverse.register(_entry("weak-generalist", local=True, cost=0, coding=1, reasoning_strength=1))

    d_fwd = RoutingPolicy().select(spec, forward)
    d_rev = RoutingPolicy().select(spec, reverse)
    d_again = RoutingPolicy().select(spec, forward)
    assert d_fwd.selected_model == d_rev.selected_model == "strong-coder"
    assert d_fwd.fallback_models == d_rev.fallback_models == ("weak-generalist",)
    assert d_fwd.reason_codes == d_again.reason_codes  # repeated runs are byte-identical


def test_min_context_floor_refuses_under_provisioned_even_if_cheaper_and_closer():
    """ADVERSARIAL: a small-window model that is BOTH cheaper AND closer (local) is
    still hard-refused when the task declares a context floor it cannot meet — a hard
    requirement is never traded for cost or locality."""
    reg = ModelRegistry()
    reg.register(
        ModelEntry(
            "local",
            "small-close-cheap",
            local=True,
            context_limit=4096,
            modalities=("text", "code"),
            structured_output=True,
            est_cost_per_1k_microcents=0,
            privacy_class=LOCAL_ONLY,
        )
    )
    reg.register(
        ModelEntry(
            "cloud",
            "big-remote-costly",
            local=False,
            context_limit=200_000,
            modalities=("text", "code"),
            structured_output=True,
            est_cost_per_1k_microcents=9000,
            privacy_class=EXTERNAL_PAID,
        )
    )
    decision = RoutingPolicy().select(TaskSpec(min_context=100_000), reg)
    assert decision.selected_model == "big-remote-costly"
    rej = {r["model"]: r["reason"] for r in decision.rejected}
    assert rej["small-close-cheap"] == ReasonCode.MIN_CONTEXT_UNMET


def test_hard_capability_requirement_refuses_under_provisioned_even_if_cheaper_and_closer():
    """ADVERSARIAL: a required capability the cheap/close model lacks refuses it even
    though it is free and local, and the only capable model is pricier and remote."""
    reg = _weak_local_strong_cloud()
    decision = RoutingPolicy().select(
        TaskSpec(task_class="code", required_capabilities=(("coding", 5),)), reg
    )
    assert decision.selected_model == "pricey-remote-strong"
    rej = {r["model"]: r for r in decision.rejected}
    assert rej["cheap-close-weak"]["reason"] == ReasonCode.CAPABILITY_UNMET
    assert rej["cheap-close-weak"]["capabilities"] == ["coding"]


def test_structured_output_requirement_refuses_provider_lacking_it():
    """A task needing structured output hard-refuses a provider that does not support
    it, regardless of cost/capability."""
    reg = ModelRegistry()
    reg.register(
        ModelEntry(
            "p", "no-structured", local=True, context_limit=8192, structured_output=False, coding=5
        )
    )
    reg.register(
        ModelEntry(
            "p",
            "has-structured",
            local=True,
            context_limit=8192,
            structured_output=True,
            est_cost_per_1k_microcents=50,
        )
    )
    decision = RoutingPolicy().select(TaskSpec(structured_output=True), reg)
    assert decision.selected_model == "has-structured"
    rej = {r["model"]: r["reason"] for r in decision.rejected}
    assert rej["no-structured"] == ReasonCode.STRUCTURED_REQUIRED


def test_prefer_local_soft_bias_selects_local_without_filtering():
    """A non-sensitive task may SOFTLY prefer local: the locality penalty is a term
    BEFORE cost, so a local model wins over a cheaper remote one — but the remote model
    is NOT filtered (it stays in the fallback chain). This is a bias, never authority."""
    reg = ModelRegistry()
    reg.register(_entry("local-costly", local=True, cost=100))
    reg.register(_entry("cloud-cheap", local=False, cost=1))
    decision = RoutingPolicy().select(TaskSpec(prefer_local=True), reg)
    assert decision.selected_model == "local-costly"
    assert "cloud-cheap" in decision.fallback_models  # softly biased, not hard-filtered
    assert decision.rejected == ()
    assert ReasonCode.LOCALITY_PREFERRED in decision.reason_codes


def test_prefer_latency_soft_bias_prefers_faster_class_deterministically():
    """Two equal-cost local models differing only in latency class: a realtime
    preference deterministically prefers the realtime-class model, emits the reason,
    and the slower model stays a fallback."""
    reg = ModelRegistry()
    reg.register(_entry("batchy", local=True, cost=0, latency_class="batch"))
    reg.register(_entry("snappy", local=True, cost=0, latency_class="realtime"))
    spec = TaskSpec(prefer_latency="realtime")
    d1 = RoutingPolicy().select(spec, reg)
    d2 = RoutingPolicy().select(spec, reg)
    assert d1.selected_model == d2.selected_model == "snappy"
    assert d1.fallback_models == ("batchy",)
    assert ReasonCode.LATENCY_PREFERRED in d1.reason_codes


def test_sensitive_task_ignores_soft_latency_bias_toward_external():
    """ADVERSARIAL: even if ONLY an external model matches the preferred latency class,
    a sensitive task filters to local FIRST — the soft latency bias can never leak it
    to the cloud. Privacy dominates every soft preference."""
    reg = ModelRegistry()
    reg.register(_entry("slow-local", local=True, cost=0, latency_class="batch"))
    reg.register(_entry("fast-cloud", local=False, cost=0, latency_class="realtime"))
    spec = TaskSpec(sensitivity="sensitive", prefer_latency="realtime", prefer_local=False)
    decision = RoutingPolicy().select(spec, reg)
    assert decision.selected_model == "slow-local"
    assert "fast-cloud" not in decision.fallback_models
    rej = {r["model"]: r["reason"] for r in decision.rejected}
    assert rej.get("fast-cloud") == ReasonCode.SENSITIVE_LOCAL_ONLY


def test_prefer_latency_rejects_invalid_class():
    with pytest.raises(ValueError):
        TaskSpec(prefer_latency="instant")


def test_min_context_rejects_float_and_negative():
    with pytest.raises(TypeError):
        TaskSpec(min_context=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TaskSpec(min_context=-1)
