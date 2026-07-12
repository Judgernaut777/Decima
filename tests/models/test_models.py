"""decima.models — model routing tests (Phase 6).

Proves the load-bearing properties: the deterministic provider is reproducible;
routing selects local for a sensitive task and records the decision + reason codes;
provider failure triggers the declared fallback; a token budget stops further calls;
an invalid structured proposal is rejected (not executed); and there is no path from
a `ModelResponse` to an effect without going through kernel authorization (which this
package does not import). No test touches a live API.
"""

from __future__ import annotations

import ast
import os
import pathlib
import tempfile

import pytest

from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.models import accounting, budgets, providers, routing, validation
from decima.models.providers import (
    EXTERNAL_PAID,
    LOCAL_ONLY,
    CloudProvider,
    DeterministicProvider,
    LiveTransportRequired,
    LocalProvider,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProposedAction,
)
from decima.models.registry import ModelEntry, ModelRegistry
from decima.models.routing import ReasonCode, RoutingPolicy, TaskSpec


# ── kernel handle shim for provenance recording ───────────────────────────────
class _K:
    def __init__(self, weft: Weft, agent_id: str) -> None:
        self.weft = weft
        self.decima_agent_id = agent_id


def _kernel():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return _K(Weft(db, kr), author)


def _registry_with(local: bool = True, cloud: bool = True) -> ModelRegistry:
    reg = ModelRegistry()
    if local:
        reg.register(
            ModelEntry(
                "local",
                "on-host-7b",
                local=True,
                context_limit=8192,
                modalities=("text", "code"),
                structured_output=True,
                est_cost_per_1k_microcents=0,
                privacy_class=LOCAL_ONLY,
            ),
            DeterministicProvider(model="on-host-7b"),
        )
    if cloud:
        reg.register(
            ModelEntry(
                "cloud",
                "frontier-x",
                local=False,
                context_limit=200_000,
                modalities=("text", "code"),
                structured_output=True,
                tool_use=True,
                est_cost_per_1k_microcents=3000,
                privacy_class=EXTERNAL_PAID,
            ),
            DeterministicProvider(model="frontier-x", local=False, privacy_class=EXTERNAL_PAID),
        )
    return reg


# ── 1. deterministic provider is reproducible ────────────────────────────────
def test_deterministic_provider_is_reproducible():
    p = DeterministicProvider()
    req = ModelRequest(prompt="hello world", purpose="chat")
    r1 = p.complete(req)
    r2 = p.complete(req)
    assert r1 == r2, "same request must yield byte-identical response"
    assert r1.text == r2.text
    assert r1.input_tokens == r2.input_tokens and r1.output_tokens == r2.output_tokens
    # different prompt ⇒ different output (it actually reads the input)
    r3 = p.complete(ModelRequest(prompt="something else", purpose="chat"))
    assert r3.text != r1.text
    # structural conformance to the Protocol
    assert isinstance(p, ModelProvider)
    # token counts are ints (determinism / invariant 6)
    assert isinstance(r1.input_tokens, int) and isinstance(r1.output_tokens, int)


def test_deterministic_stream_matches_completion():
    p = DeterministicProvider()
    req = ModelRequest(prompt="alpha beta gamma")
    streamed = " ".join(p.stream(req))
    assert streamed == p.complete(req).text


# ── 2. routing selects local for a sensitive task + records reason codes ──────
def test_sensitive_task_routes_local_and_is_recorded():
    reg = _registry_with(local=True, cloud=True)
    policy = RoutingPolicy()
    spec = TaskSpec(task_class="summarize", sensitivity="sensitive", modalities=("text",))
    decision = policy.select(spec, reg)

    assert decision.selected_model == "on-host-7b", "sensitive → local model"
    entry = reg.get(decision.selected_model)
    assert entry is not None and entry.local is True
    assert ReasonCode.SENSITIVE_LOCAL_ONLY in decision.reason_codes
    assert ReasonCode.SELECTED in decision.reason_codes
    # the external model was hard-rejected for privacy
    assert any(r["model"] == "frontier-x" for r in decision.rejected)

    # the decision is DATA the caller folds onto the Weft; recording mints nothing
    k = _kernel()
    cid = routing.record(k, decision, author=k.decima_agent_id)
    cell = Weave.fold(k.weft).get(cid)
    assert cell is not None
    assert cell.content["selected_model"] == "on-host-7b"
    assert ReasonCode.SENSITIVE_LOCAL_ONLY in cell.content["reason_codes"]
    # everything recorded that is numeric is an int (invariant 6)
    assert isinstance(cell.content["estimated_cost"], int)
    assert isinstance(cell.content["policy_version"], int)


def test_sensitive_task_with_no_local_fails_closed():
    reg = _registry_with(local=False, cloud=True)
    decision = RoutingPolicy().select(TaskSpec(sensitivity="private", modalities=("text",)), reg)
    assert decision.selected_model == "", "no local model ⇒ fail closed, route nothing"
    assert decision.routed is False
    assert ReasonCode.NO_ELIGIBLE in decision.reason_codes
    assert ReasonCode.NO_LOCAL_FOR_SENSITIVE in decision.reason_codes


def test_public_task_prefers_cheapest_and_lists_fallbacks():
    reg = _registry_with(local=True, cloud=True)
    decision = RoutingPolicy().select(
        TaskSpec(task_class="chat", sensitivity="public", modalities=("text",)), reg
    )
    # local is free (cost 0) ⇒ cheapest wins; the cloud model is the fallback
    assert decision.selected_model == "on-host-7b"
    assert "frontier-x" in decision.fallback_models


# ── 3. provider failure triggers the declared fallback ───────────────────────
def test_provider_failure_triggers_declared_fallback():
    reg = ModelRegistry()
    # primary refuses on this purpose; fallback answers
    reg.register(
        ModelEntry("p", "primary", local=True, context_limit=8192, structured_output=True),
        DeterministicProvider(model="primary", refuse_purposes=frozenset({"plan"})),
    )
    reg.register(
        ModelEntry(
            "s",
            "secondary",
            local=True,
            context_limit=8192,
            est_cost_per_1k_microcents=10,
            structured_output=True,
        ),
        DeterministicProvider(model="secondary"),
    )
    policy = RoutingPolicy()
    spec = TaskSpec(task_class="plan", sensitivity="public")
    decision = policy.select(spec, reg)
    assert decision.selected_model == "primary"  # cheapest (0) is primary
    assert "secondary" in decision.fallback_models

    result = routing.route_and_complete(
        decision, reg, ModelRequest(prompt="draft a plan", purpose="plan")
    )
    assert result.ok
    assert result.model == "secondary", "primary refused → declared fallback answered"
    outcomes = [a["outcome"] for a in result.attempts]
    assert outcomes[0] == "refused" and outcomes[-1] == "ok"


def test_fallback_is_bounded():
    reg = ModelRegistry()
    for name in ("a", "b", "c"):
        reg.register(
            ModelEntry(name, name, local=True, context_limit=8192),
            DeterministicProvider(model=name, fail_purposes=frozenset({"chat"})),
        )
    decision = RoutingPolicy().select(TaskSpec(task_class="chat"), reg)
    result = routing.route_and_complete(
        decision, reg, ModelRequest(prompt="hi", purpose="chat"), max_hops=2
    )
    assert not result.ok
    assert len(result.attempts) <= 2, "fallback must be bounded by max_hops"


def test_live_adapter_failure_is_contained_in_fallback():
    """A live adapter with no transport FAILS CLOSED (raises); the router treats it
    as a failed attempt and falls through — it never reaches the network."""
    reg = ModelRegistry()
    reg.register(
        ModelEntry("cloud", "needs-net", local=True, context_limit=8192),
        CloudProvider(model="needs-net"),  # no backend ⇒ LiveTransportRequired
    )
    reg.register(
        ModelEntry("det", "offline", local=True, context_limit=8192, est_cost_per_1k_microcents=1),
        DeterministicProvider(model="offline"),
    )
    decision = RoutingPolicy().select(TaskSpec(task_class="chat"), reg)
    result = routing.route_and_complete(decision, reg, ModelRequest(prompt="hi"))
    assert result.ok and result.model == "offline"
    assert result.attempts[0]["outcome"].startswith("exception:")


# ── 4. a token budget stops further calls ────────────────────────────────────
def test_token_budget_stops_further_calls():
    guard = budgets.BudgetGuard(budgets.Budget(token_limit=100))
    provider = DeterministicProvider()
    made = 0

    def one_call():
        nonlocal made
        resp = provider.complete(ModelRequest(prompt="a b c d e f g h i j"))
        made += 1
        rec = accounting.UsageRecord(
            provider="det",
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        return rec, resp

    # spend until the budget denies; the thunk must NOT run once denied
    stopped = False
    for _ in range(1000):
        try:
            guard.spend(one_call, est_tokens=40)
        except budgets.BudgetExceeded:
            stopped = True
            break
    assert stopped, "budget must eventually STOP further calls"
    assert guard.check(est_tokens=40).allowed is False
    calls_at_stop = made
    # a further attempt does not increase the call count (thunk never runs)
    with pytest.raises(budgets.BudgetExceeded):
        guard.spend(one_call, est_tokens=40)
    assert made == calls_at_stop, "denied call must not invoke the provider"


def test_budget_precheck_denies_before_breach():
    guard = budgets.BudgetGuard(budgets.Budget(cost_limit_microcents=1000))
    assert guard.check(est_cost_microcents=500).allowed is True
    assert guard.check(est_cost_microcents=2000).allowed is False
    assert guard.check(est_cost_microcents=2000).reason == "cost_budget_exceeded"


def test_usage_ledger_totals_are_int_clean():
    ledger = accounting.UsageLedger()
    ledger.add(accounting.UsageRecord("p", "m", 10, 5, est_cost_microcents=30))
    ledger.add(accounting.UsageRecord("p", "m", 20, 5, est_cost_microcents=70))
    assert ledger.total_tokens == 40
    assert ledger.total_cost_microcents == 100
    assert ledger.by_model()["m"]["calls"] == 2
    assert all(isinstance(v, int) for v in ledger.by_model()["m"].values())


# ── 5. an invalid structured proposal is rejected (not executed) ─────────────
_SCHEMA = {
    "action": "send_email",
    "fields": {
        "to": {"type": "string", "required": True},
        "priority": {"type": "int", "min": 0, "max": 5},
        "mode": {"type": "string", "enum": ["draft", "send"]},
    },
}


def test_invalid_proposal_is_rejected_and_recorded_not_executed():
    bad = {"action": "send_email", "priority": 99}  # missing `to`, priority out of range
    result = validation.validate_proposal(bad, _SCHEMA)
    assert result.valid is False
    assert result.proposal is None, "an invalid proposal yields NO executable action"
    assert any("to" in e for e in result.errors)
    assert any("priority" in e for e in result.errors)

    # it is recorded as a MODEL ERROR (data) — never repaired or executed
    k = _kernel()
    cid = validation.record_rejection(k, result, model="m", author=k.decima_agent_id)
    cell = Weave.fold(k.weft).get(cid)
    assert cell is not None and cell.content["instruction_eligible"] is False
    assert cell.type == validation.MODEL_ERROR


def test_valid_proposal_is_wellformed_but_inert():
    good = {"action": "send_email", "to": "a@b.c", "priority": 2, "mode": "draft"}
    result = validation.validate_proposal(good, _SCHEMA)
    assert result.valid is True
    action = result.proposal
    assert isinstance(action, ProposedAction)
    # well-formed does NOT mean authorized: the action is inert data
    assert action.instruction_eligible is False
    assert not hasattr(action, "execute")
    assert action.params["to"] == "a@b.c"


def test_bounded_reprompt_gives_up_without_executing():
    # a provider whose structured output never satisfies the schema
    class NeverValid(DeterministicProvider):
        def complete(self, request):
            return ModelResponse(
                model="nv",
                text="",
                input_tokens=1,
                output_tokens=1,
                structured={"action": "send_email"},
            )  # missing `to`

    rr = validation.validate_with_reprompt(
        NeverValid(), ModelRequest(prompt="mail bob"), _SCHEMA, max_attempts=3
    )
    assert rr.ok is False
    assert rr.attempts == 3
    assert len(rr.rejected) == 3
    assert rr.result.proposal is None, "exhausted re-prompt executes nothing"


def test_reprompt_succeeds_when_provider_conforms():
    # DeterministicProvider fills the schema deterministically → valid on attempt 1
    rr = validation.validate_with_reprompt(
        DeterministicProvider(), ModelRequest(prompt="mail"), _SCHEMA, max_attempts=3
    )
    assert rr.ok is True and rr.attempts == 1
    assert rr.result.proposal is not None
    assert rr.result.proposal.instruction_eligible is False


# ── 6. models cannot execute actions — no ModelResponse → effect path ─────────
def test_model_response_has_no_authority_or_effect_method():
    resp = DeterministicProvider().complete(
        ModelRequest(prompt="do a thing", structured_schema=_SCHEMA)
    )
    # a response is inert DATA: no capability/grant/principal/key, no effect method
    for attr in (
        "execute",
        "invoke",
        "authorize",
        "perform",
        "capability",
        "grant",
        "principal",
        "key",
    ):
        assert not hasattr(resp, attr), f"ModelResponse must not expose {attr!r}"
    proposed = ProposedAction.of(resp)
    if proposed is not None:
        assert proposed.instruction_eligible is False
        for attr in ("execute", "invoke", "authorize"):
            assert not hasattr(proposed, attr)


def test_models_package_imports_no_authorization_or_executor():
    """Structural proof of invariant 4: nothing in decima/models reaches the
    authorization / capability / executor / effect machinery — a model output can
    only become an effect via the kernel chain OUTSIDE this package."""
    pkg = pathlib.Path(providers.__file__).resolve().parent
    banned_modules = {
        "decima.kernel.authorization",
        "decima.kernel.capability",
        "decima.kernel.inbox",
        "decima.kernel.receipts",
        "decima.runtime",
    }
    banned_calls = {"authorize", "invoke", "execute", "dispatch_step"}
    offenders: dict[str, list[str]] = {}
    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        hits: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in banned_modules or any(
                    node.module.startswith(m + ".") for m in banned_modules
                ):
                    hits.append(f"import {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in banned_modules:
                        hits.append(f"import {alias.name}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in banned_calls:
                    hits.append(f"call .{node.func.attr}()")
        if hits:
            offenders[path.name] = hits
    assert not offenders, (
        f"decima.models must not touch the authorization/effect chain: {offenders}"
    )


# ── adapters conform structurally but make no live call ───────────────────────
def test_live_adapters_fail_closed_without_transport():
    with pytest.raises(LiveTransportRequired):
        LocalProvider(model="x").complete(ModelRequest(prompt="hi"))
    with pytest.raises(LiveTransportRequired):
        CloudProvider(model="y").complete(ModelRequest(prompt="hi"))
    # they still satisfy the Protocol structurally
    assert isinstance(LocalProvider(model="x"), ModelProvider)
    assert isinstance(CloudProvider(model="y"), ModelProvider)


def test_cloud_adapter_applies_secret_via_broker_never_stores_it():
    seen = {}

    class Broker:
        def use_secret(self, name, fn):
            # the broker applies the secret INSIDE itself and never returns it
            seen["name"] = name
            return fn("SECRET-VALUE")

    def backend(request, caps, secret):
        seen["secret_reached_backend"] = secret
        return ModelResponse(model=caps.model, text="ok", input_tokens=1, output_tokens=1)

    prov = CloudProvider(model="z", secret_name="api_key", broker=Broker(), backend=backend)
    resp = prov.complete(ModelRequest(prompt="hi"))
    assert resp.text == "ok"
    assert seen["name"] == "api_key"
    # the key never lives on the provider object or its repr
    assert "SECRET-VALUE" not in repr(prov)
    assert not hasattr(prov, "secret") and not hasattr(prov, "api_key")


def test_estimate_cost_is_integer_and_zero_for_local():
    reg = _registry_with()
    local = reg.get("on-host-7b")
    cloud = reg.get("frontier-x")
    assert routing.estimate_cost(local, 5000, 512) == 0
    c = routing.estimate_cost(cloud, 5000, 512)
    assert isinstance(c, int) and c > 0
