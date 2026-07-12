"""P5 integration-audit finding (EXPECTED TO FAIL until fixed): an operator-configured
live local provider must actually be selectable by PRODUCT routing.

``models_setup.build_model_stack`` registers the configured ``DECIMA_LIVE_*`` provider
alongside the always-present deterministic placeholder, and ``CommandService`` routes
every product lane (plan proposals, grounded Q&A) through ``ModelStack.propose``. But
``RoutingPolicy._rank_key`` ranks eligible entries by ``(local_rank, cost, model)``:
both entries are local, both cost 0, so the ALPHABETICAL model id decides — and
``"deterministic-offline"`` sorts before any id starting after "d" (e.g. the real
``qwen3-30b-a3b`` on this host). The configured real provider is then never selected
(it is only a dead fallback behind a provider that cannot fail), so ``DECIMA_LIVE_*``
is dead configuration for the product: no Shell-driven workflow ever reaches real
inference. Whether real inference is used silently depends on how the operator's
model id happens to sort against the placeholder's name — clearly not a contract.

Offline and deterministic: routing selection is pure; no endpoint is contacted.
"""

from __future__ import annotations

from decima.models.routing import TaskSpec
from decima.services.api.contracts import PlanProposalRequest
from decima.services.api.models_setup import DETERMINISTIC_MODEL, build_model_stack


def _stack(model: str):
    return build_model_stack(
        {
            "DECIMA_LIVE_PROVIDER": "local",
            "DECIMA_LIVE_MODEL": model,
            "DECIMA_LIVE_BASE_URL": "http://127.0.0.1:9",  # selection is pure; never called
        }
    )


def test_configured_live_model_is_selected_for_product_plan_routing():
    """The product plan lane's own TaskSpec must route to the operator's configured
    real model, not the offline placeholder (this is the audited defect)."""
    stack = _stack("qwen3-30b-a3b")
    spec = PlanProposalRequest(objective="summarize the readme").task_spec()
    decision = stack.policy.select(spec, stack.registry, max_output_tokens=256)
    assert decision.routed
    assert decision.selected_model == "qwen3-30b-a3b", (
        "operator-configured live provider is unreachable: product routing selected "
        f"{decision.selected_model!r} with the live model relegated to a fallback "
        "behind a provider that cannot fail"
    )


def test_configured_live_model_is_selected_for_product_qa_routing():
    """Same property for the grounded-Q&A lane's spec shape."""
    stack = _stack("qwen3-30b-a3b")
    spec = TaskSpec(
        task_class="qa", sensitivity="private", context_size=64, structured_output=False
    )
    decision = stack.policy.select(spec, stack.registry, max_output_tokens=256)
    assert decision.routed
    assert decision.selected_model == "qwen3-30b-a3b"


def test_selection_must_not_depend_on_model_id_alphabetics():
    """Two operators configuring the same endpoint under different model ids must get
    the same behavior. Today an id sorting BEFORE 'deterministic-offline' is selected
    and one sorting AFTER it never is — the placebo boundary is the letter 'd'."""
    spec = TaskSpec(task_class="plan", sensitivity="private", structured_output=True)
    selected = {
        model: _stack(model)
        .policy.select(spec, _stack(model).registry, max_output_tokens=256)
        .selected_model
        for model in ("aaa-local-model", "zzz-local-model")
    }
    live_chosen = {m: sel == m for m, sel in selected.items()}
    assert live_chosen["aaa-local-model"] == live_chosen["zzz-local-model"], (
        f"routing outcome flips on model-id alphabetics: {selected} "
        f"(placeholder id: {DETERMINISTIC_MODEL!r})"
    )
