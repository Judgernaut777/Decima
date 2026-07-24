"""A kind=local live provider is confined to the loopback interface. Because the pure
routing policy treats a ``local_only`` entry as eligible for sensitive tasks
(``routing._eligible``: sensitive ⇒ local only), a 'local' endpoint that actually
reached off-box would let a sensitive task's DATA leave the host while still passing
that privacy filter. ``build_model_stack`` therefore refuses a non-loopback
``DECIMA_LIVE_BASE_URL`` for ``DECIMA_LIVE_PROVIDER=local`` with a clear error."""

from __future__ import annotations

import pytest

from decima.models.routing import TaskSpec
from decima.services.api.models_setup import DETERMINISTIC_MODEL, build_model_stack

LIVE_ENV = {
    "DECIMA_LIVE_PROVIDER": "local",
    "DECIMA_LIVE_MODEL": "qwen3-30b-a3b",
    "DECIMA_LIVE_BASE_URL": "http://127.0.0.1:8080",
}


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8080",
        "http://127.5.6.7:8080",  # anywhere in 127.0.0.0/8
        "http://localhost:8080",
        "http://[::1]:8080",
    ],
)
def test_loopback_base_urls_register_the_local_provider(base_url):
    stack = build_model_stack(env={**LIVE_ENV, "DECIMA_LIVE_BASE_URL": base_url})
    models = sorted(e.model for e in stack.registry.enabled_entries())
    assert models == sorted([DETERMINISTIC_MODEL, LIVE_ENV["DECIMA_LIVE_MODEL"]])


@pytest.mark.parametrize(
    "base_url",
    [
        "http://10.0.0.5:8080",  # private LAN, but off-box
        "https://api.openai.com",
        "http://169.254.169.254",  # cloud metadata endpoint
        "http://[2001:db8::1]:8080",  # a routable IPv6 literal
        "http://malicious.example.com",  # a name that does not resolve to loopback
    ],
)
def test_non_loopback_base_url_is_refused_with_a_clear_error(base_url):
    with pytest.raises(ValueError, match="loopback"):
        build_model_stack(env={**LIVE_ENV, "DECIMA_LIVE_BASE_URL": base_url})


def test_sensitive_task_can_only_route_to_a_genuinely_local_transport():
    """End to end: with a loopback local provider the sensitive task routes to an on-box
    model (deterministic or the loopback live model), and a remote endpoint mislabelled
    'local' can never be constructed — so no sensitive task can ever reach it."""
    stack = build_model_stack(env=dict(LIVE_ENV))
    spec = TaskSpec(
        task_class="chat",
        sensitivity="sensitive",
        modalities=("text",),
        context_size=100,
    )
    decision = stack.policy.select(spec, stack.registry)
    chosen = stack.registry.get(decision.selected_model)
    assert chosen is not None and chosen.local  # sensitive ⇒ genuinely local only
    # the off-box 'local' endpoint is refused at construction, so it never enters routing
    with pytest.raises(ValueError, match="loopback"):
        build_model_stack(env={**LIVE_ENV, "DECIMA_LIVE_BASE_URL": "http://evil.example.com"})
