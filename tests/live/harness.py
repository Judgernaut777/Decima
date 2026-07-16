"""Shared driver logic for the bounded model-provider release qualification (WS3).

The SAME functions here are exercised two ways:

  * OFFLINE (normal CI, no credential): `test_provider_qualification_offline.py`
    drives them against the reproducible `DeterministicProvider` plus a SYNTHETIC
    `CloudProvider` whose `backend` is a local stub — no network, no key.
  * LIVE (operator-gated): `test_provider_qualification_live.py`, marked
    `live_provider`, drives the identical functions against a REAL configured
    provider (an OpenAI-compatible chat endpoint) with the credential applied by a
    broker at call time. Skipped unless the operator supplies the credential.

Because both paths call the same harness, proving the offline path proves the shape
of the logic the live path runs; only the transport differs.

Nothing here holds authority. A provider PROPOSES; the routing decision, the usage
record, and any structured proposal are inert DATA. Turning a proposal into an effect
still requires the kernel's authorization + approval + receipt chain, which lives
OUTSIDE `decima/models` and is never imported here (invariant 4).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from decima.models import accounting, budgets, routing, validation
from decima.models.providers import (
    EXTERNAL_PAID,
    LOCAL_ONLY,
    CloudProvider,
    DeterministicProvider,
    LocalProvider,
    ModelRequest,
    ModelResponse,
)
from decima.models.registry import ModelEntry, ModelRegistry
from decima.models.routing import ReasonCode, RoutingPolicy, TaskSpec

# ── operator-supplied configuration (NAMES ONLY — never values) ────────────────
# These environment-variable names are the ONLY way a credential or endpoint enters
# the qualification. No value is ever hard-coded, committed, logged, or defaulted.
# Documented for the operator in docs/operations/model-configuration.md.
ENV_PROVIDER = "DECIMA_LIVE_PROVIDER"  # "cloud" | "local"
ENV_MODEL = "DECIMA_LIVE_MODEL"  # model id at the endpoint
ENV_BASE_URL = "DECIMA_LIVE_BASE_URL"  # OpenAI-compatible base URL
ENV_API_KEY = "DECIMA_LIVE_API_KEY"  # secret NAME the broker reads (cloud only)
ENV_TIMEOUT = "DECIMA_LIVE_TIMEOUT_S"  # optional int seconds; default 30

# A single synthetic payload. A cloud-eligible task may transmit ONLY this string;
# the privacy checks assert nothing else (no real user data) ever reaches a backend.
SYNTHETIC_PROMPT = "SYNTHETIC-QUAL: summarize the phrase 'release qualification' in one word."
SYNTHETIC_MARKER = "SYNTHETIC-QUAL"
# A sentinel that stands in for real user data; privacy checks assert it NEVER leaves.
REAL_USER_DATA_SENTINEL = "REAL-USER-PRIVATE-DO-NOT-TRANSMIT"


# ── secrets broker: applies the key INSIDE itself, never returns/stores/logs it ─
class EnvSecretBroker:
    """A minimal `broker` for `CloudProvider`: it reads the secret from the named
    environment variable (or an in-memory store for the offline stub), applies it
    to the thunk, and NEVER returns, stores on an attribute, or logs it. Mirrors the
    contract `CloudProvider` expects (`use_secret(name, fn)`)."""

    def __init__(self, store: dict[str, str] | None = None) -> None:
        # `store` lets the offline test inject a fake secret without touching env.
        self._store = store  # names→values; None ⇒ read process env at call time
        self.calls: list[str] = []  # records the NAMES requested, never the values

    def use_secret(self, name: str, fn):
        self.calls.append(name)
        if self._store is not None:
            secret = self._store.get(name)
        else:
            secret = os.environ.get(name)
        if not secret:
            # Fail closed: a missing credential surfaces as an error, not a crash and
            # not a silent unauthenticated call.
            raise KeyError(f"secret {name!r} not available in the configured store")
        try:
            return fn(secret)
        finally:
            del secret  # do not let the value linger in this frame


# ── log capture with a redaction assertion using the PRODUCT redactor ──────────
class CaptureLog:
    """Collects every line the harness would log around a provider call. The
    qualification asserts, via the SHIPPING product redactor
    (`decima.services.diagnostics.service._redact_line`), that no captured line
    carries the credential off the box."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, line: str) -> None:
        self.lines.append(str(line))

    def redacted(self) -> list[str]:
        from decima.services.diagnostics.service import _redact_line

        return [_redact_line(ln) for ln in self.lines]

    def contains_secret(self, secret: str) -> bool:
        """True if the RAW capture leaked the secret (a bug). The redacted view must
        never contain it regardless."""
        return any(secret in ln for ln in self.lines)


# ── the routing qualification record: provider + model + reasons + cost + class ─
@dataclass(frozen=True)
class RoutingQualification:
    """Everything the charter requires a routing decision to make auditable:
    provider, model, reason codes, estimated cost, and the task-sensitivity class.
    `provider` is derived from the registry entry for the selected model;
    `sensitivity_class` from the `TaskSpec`. All numerics are ints."""

    provider: str
    model: str
    reason_codes: tuple[str, ...]
    estimated_cost_microcents: int
    sensitivity_class: str
    routed: bool

    def to_content(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "reason_codes": list(self.reason_codes),
            "estimated_cost_microcents": int(self.estimated_cost_microcents),
            "sensitivity_class": self.sensitivity_class,
            "routed": self.routed,
        }


def routing_qualification(
    spec: TaskSpec, registry: ModelRegistry, decision: routing.RoutingDecision
) -> RoutingQualification:
    entry = registry.get(decision.selected_model) if decision.selected_model else None
    return RoutingQualification(
        provider=entry.provider if entry else "",
        model=decision.selected_model,
        reason_codes=decision.reason_codes,
        estimated_cost_microcents=int(decision.estimated_cost),
        sensitivity_class=spec.sensitivity,
        routed=decision.routed,
    )


# ── a synthetic cloud backend for the OFFLINE path (records what it saw) ────────
@dataclass
class RecordingBackend:
    """A stand-in for a network transport used by the offline qualification. It never
    touches the network; it RECORDS every payload it was handed (so privacy checks can
    assert only synthetic content reached it) and can be told to simulate the failure
    modes a live endpoint exhibits. It also records whether a secret was applied."""

    model: str
    # mode ∈ ok | invalid_credential | timeout | rate_limit | unavailable |
    #        malformed (invalid structured proposal) | malformed_transport (unparseable body)
    mode: str = "ok"
    seen_payloads: list[str] = field(default_factory=list)
    seen_secret_len: int | None = None
    log: CaptureLog | None = None

    def __call__(self, request: ModelRequest, caps, secret=None) -> ModelResponse:
        # Record what crossed the seam — but NEVER the secret value itself.
        self.seen_payloads.append(request.prompt + "\x00" + request.context)
        self.seen_secret_len = len(secret) if secret else None
        if self.log is not None:
            # A well-behaved adapter logs the request WITHOUT the key.
            self.log.write(f"call model={caps.model} prompt_len={len(request.prompt)}")
        if self.mode == "invalid_credential":
            return ModelResponse(
                model=caps.model,
                text="",
                input_tokens=1,
                output_tokens=0,
                stop_reason="error",
                error="401 invalid credential",
            )
        if self.mode == "timeout":
            raise TimeoutError("simulated upstream timeout")
        if self.mode == "rate_limit":
            return ModelResponse(
                model=caps.model,
                text="",
                input_tokens=1,
                output_tokens=0,
                stop_reason="error",
                error="429 rate limited",
            )
        if self.mode == "unavailable":
            raise urllib.error.URLError("model unavailable")
        if self.mode == "malformed_transport":
            # a non-JSON / unparseable transport reply — surfaces as a failed attempt.
            raise ValueError("malformed response body (not valid JSON)")
        if self.mode == "malformed":
            # A malformed structured proposal: present but schema-invalid.
            return ModelResponse(
                model=caps.model,
                text="{",
                input_tokens=1,
                output_tokens=1,
                structured={"action": "send_email"},
            )  # missing required field
        # ok: echo a bounded, well-formed answer + a valid structured proposal.
        structured = None
        if request.structured_schema is not None:
            structured = {"action": "summarize", "topic": SYNTHETIC_MARKER, "length": 1}
        return ModelResponse(
            model=caps.model, text="one", input_tokens=3, output_tokens=1, structured=structured
        )


# ── a real OpenAI-compatible transport for the LIVE path ───────────────────────
def http_openai_backend(log: CaptureLog | None = None, timeout_s: int = 30):
    """Build a `backend(request, caps, secret)` that calls an OpenAI-compatible
    `/v1/chat/completions` endpoint (llama.cpp server, vLLM, or a hosted API). The
    key, when present, is applied ONLY to the Authorization header inside this call
    and never logged. Base URL comes from the environment (names only)."""

    base = os.environ.get(ENV_BASE_URL, "").rstrip("/")

    def backend(request: ModelRequest, caps, secret=None) -> ModelResponse:
        url = f"{base}/v1/chat/completions"
        body = json.dumps(
            {
                "model": caps.model,
                "messages": [{"role": "user", "content": request.prompt}],
                "max_tokens": int(request.max_output_tokens),
                "temperature": 0,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        if log is not None:
            log.write(f"POST {url} model={caps.model} auth={'yes' if secret else 'no'}")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return ModelResponse(
                model=caps.model,
                text="",
                input_tokens=0,
                output_tokens=0,
                stop_reason="error",
                error=f"http {exc.code}",
            )
        choice = (data.get("choices") or [{}])[0]
        text = str(choice.get("message", {}).get("content", ""))
        usage = data.get("usage", {})
        return ModelResponse(
            model=caps.model,
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        )

    return backend


# ── registries the qualification drives ────────────────────────────────────────
def local_provider_stub() -> DeterministicProvider:
    """A LOCAL provider stand-in that is fully offline and reproducible — the default
    fallback and the sensitive-task lane."""
    return DeterministicProvider(
        model="on-host-7b", local=True, privacy_class=LOCAL_ONLY, structured_output=True
    )


def build_offline_registry(
    cloud_backend: RecordingBackend, broker: EnvSecretBroker
) -> ModelRegistry:
    """A registry with a local (offline) model and a SYNTHETIC cloud model whose
    transport is the recording stub. Mirrors the real fleet shape (one local, one
    external_paid) so routing behaves identically to production."""
    reg = ModelRegistry()
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
        local_provider_stub(),
    )
    reg.register(
        ModelEntry(
            "cloud",
            cloud_backend.model,
            local=False,
            context_limit=200_000,
            modalities=("text", "code"),
            structured_output=True,
            tool_use=True,
            est_cost_per_1k_microcents=3000,
            privacy_class=EXTERNAL_PAID,
        ),
        CloudProvider(
            model=cloud_backend.model, secret_name=ENV_API_KEY, broker=broker, backend=cloud_backend
        ),
    )
    return reg


def build_live_registry(
    kind: str, model: str, base_url: str, log: CaptureLog, timeout_s: int
) -> tuple[ModelRegistry, EnvSecretBroker]:
    """The live registry: one already-supported provider wired via config. `cloud`
    uses `CloudProvider` with a broker (key from env); `local` uses `LocalProvider`
    (no key, on-host endpoint)."""
    reg = ModelRegistry()
    broker = EnvSecretBroker()  # reads process env at call time
    backend = http_openai_backend(log=log, timeout_s=timeout_s)
    if kind == "cloud":
        reg.register(
            ModelEntry(
                "cloud",
                model,
                local=False,
                context_limit=200_000,
                modalities=("text",),
                structured_output=True,
                tool_use=True,
                est_cost_per_1k_microcents=3000,
                privacy_class=EXTERNAL_PAID,
            ),
            CloudProvider(model=model, secret_name=ENV_API_KEY, broker=broker, backend=backend),
        )
    else:  # local on-host endpoint — no credential leaves the box
        reg.register(
            ModelEntry(
                "local",
                model,
                local=True,
                context_limit=8192,
                modalities=("text",),
                structured_output=True,
                est_cost_per_1k_microcents=0,
                privacy_class=LOCAL_ONLY,
            ),
            LocalProvider(
                model=model,
                structured_output=True,
                backend=lambda req, caps: backend(req, caps, None),
            ),
        )
    return reg, broker


# ── the six qualification checks, provider-agnostic ────────────────────────────
_SCHEMA = {
    "action": "summarize",
    "fields": {
        "topic": {"type": "string", "required": True},
        "length": {"type": "int", "min": 1, "max": 10},
    },
}


def check_connectivity_and_routing(
    reg: ModelRegistry, *, model: str, sensitivity: str = "public"
) -> dict:
    """Provider is available in diagnostics; a task routes to it; the decision records
    provider/model/reason codes/cost/sensitivity; the response returns through the
    normal `ModelResponse` abstraction."""
    available = {e.model for e in reg.enabled_entries()}
    assert model in available, "configured provider must be diagnostically available"
    spec = TaskSpec(task_class="summarize", sensitivity=sensitivity, modalities=("text",))
    decision = RoutingPolicy().select(spec, reg)
    qual = routing_qualification(spec, reg, decision)
    assert qual.routed and qual.model, "a task must route to a model"
    assert qual.reason_codes, "routing must record reason codes"
    assert ReasonCode.SELECTED in qual.reason_codes
    result = routing.route_and_complete(
        decision, reg, ModelRequest(prompt=SYNTHETIC_PROMPT, purpose="summarize")
    )
    assert result.ok, "a routed call must return through the model abstraction"
    assert isinstance(result.response, ModelResponse)
    return {
        "qualification": qual.to_content(),
        "answered_by": result.model,
        "attempts": [dict(a) for a in result.attempts],
    }


def check_structured_proposal(reg: ModelRegistry, *, model: str, expect_valid: bool) -> dict:
    """A structured request is validated against a schema. A valid proposal is
    WELL-FORMED but inert (never auto-invoked); a malformed one is rejected / takes
    the bounded-correction path and NEVER becomes an invocation."""
    provider = reg.provider_for(model)
    assert provider is not None
    rr = validation.validate_with_reprompt(
        provider,
        ModelRequest(prompt=SYNTHETIC_PROMPT, purpose="summarize"),
        _SCHEMA,
        max_attempts=3,
    )
    if rr.ok:
        action = rr.result.proposal
        assert action is not None
        # well-formed does NOT mean authorized
        assert action.instruction_eligible is False
        for attr in ("execute", "invoke", "authorize", "perform"):
            assert not hasattr(action, attr), "a proposal must be inert"
    else:
        assert rr.result.proposal is None, "an exhausted re-prompt executes nothing"
        assert rr.attempts >= 1 and len(rr.rejected) == rr.attempts
    return {
        "ok": rr.ok,
        "attempts": rr.attempts,
        "rejected_errors": [list(r.errors) for r in rr.rejected],
    }


def check_budget_enforcement(reg: ModelRegistry, *, model: str) -> dict:
    """A deliberately small budget allows exactly one call then deterministically
    blocks further calls. The budget state is inspectable (an inspector view dict)."""
    provider = reg.provider_for(model)
    assert provider is not None
    # size the budget to admit exactly one call, then deny.
    req = ModelRequest(prompt=SYNTHETIC_PROMPT, purpose="summarize")
    probe = provider.complete(req)
    one = probe.total_tokens or 1
    guard = budgets.BudgetGuard(budgets.Budget(token_limit=one))
    calls = 0

    def thunk():
        nonlocal calls
        resp = provider.complete(req)
        calls += 1
        rec = accounting.UsageRecord(
            provider="qual",
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        return rec, resp

    guard.spend(thunk, est_tokens=one)  # first call admitted
    blocked = False
    try:
        guard.spend(thunk, est_tokens=one)  # second must be blocked
    except budgets.BudgetExceeded:
        blocked = True
    inspector = {
        "spent_tokens": guard.spent_tokens,
        "remaining_tokens": guard.remaining_tokens(),
        "exhausted": guard.exhausted,
        "calls_made": calls,
    }
    assert blocked, "a small budget must deterministically STOP further calls"
    assert calls == 1, "the blocked call must not invoke the provider"
    assert guard.exhausted and guard.check(est_tokens=one).allowed is False
    return inspector


def check_privacy_local_only(reg: ModelRegistry, cloud_backend: RecordingBackend) -> dict:
    """A local-only (sensitive) task never selects the cloud provider and no request
    reaches it. A separate synthetic cloud-eligible task transmits ONLY the synthetic
    content (no real user data)."""
    before = len(cloud_backend.seen_payloads)
    spec = TaskSpec(task_class="summarize", sensitivity="sensitive", modalities=("text",))
    decision = RoutingPolicy().select(spec, reg)
    local_entry = reg.get(decision.selected_model)
    assert local_entry is not None and local_entry.local, "sensitive → local model"
    assert any(r["model"] == cloud_backend.model for r in decision.rejected), (
        "the cloud model must be hard-rejected for a sensitive task"
    )
    routing.route_and_complete(
        decision, reg, ModelRequest(prompt=REAL_USER_DATA_SENTINEL, purpose="summarize")
    )
    assert len(cloud_backend.seen_payloads) == before, (
        "no request may reach the cloud provider for a local-only task"
    )

    # a public, cloud-eligible task: only synthetic content may transmit.
    pub = TaskSpec(
        task_class="summarize",
        sensitivity="public",
        modalities=("text",),
        cost_budget_microcents=10_000_000,
    )
    # force the cloud lane by disabling local for this synthetic probe
    reg.set_enabled("on-host-7b", False)
    pdec = RoutingPolicy().select(pub, reg)
    pdec_entry = reg.get(pdec.selected_model)
    assert pdec_entry is not None
    assert pdec_entry.local is False, "synthetic probe uses the cloud lane"
    routing.route_and_complete(
        pdec, reg, ModelRequest(prompt=SYNTHETIC_PROMPT, purpose="summarize")
    )
    reg.set_enabled("on-host-7b", True)
    transmitted = cloud_backend.seen_payloads[before:]
    assert transmitted, "the synthetic probe should have reached the cloud stub"
    for payload in transmitted:
        assert SYNTHETIC_MARKER in payload, "only synthetic content may transmit"
        assert REAL_USER_DATA_SENTINEL not in payload, "no real user data may transmit"
    return {"local_only_reached_cloud": False, "synthetic_payloads": len(transmitted)}


def check_failure_fallback(mode: str) -> dict:
    """A cloud failure of `mode` is surfaced, falls back BOUNDED to the local model,
    records every attempt, and never widens authority or leaks a secret."""
    log = CaptureLog()
    broker = EnvSecretBroker(store={ENV_API_KEY: "TEST-SECRET-VALUE"})
    backend = RecordingBackend(model="frontier-x", mode=mode, log=log)
    reg = build_offline_registry(backend, broker)
    # force the cloud lane to be selected first: cloud primary, local fallback.
    reg.set_enabled("on-host-7b", True)
    # public task with the cloud model made cheapest so it is tried first.
    reg2 = ModelRegistry()
    reg2.register(
        ModelEntry(
            "cloud",
            "frontier-x",
            local=False,
            context_limit=200_000,
            modalities=("text",),
            structured_output=True,
            tool_use=True,
            est_cost_per_1k_microcents=1,
            privacy_class=EXTERNAL_PAID,
        ),
        reg.provider_for("frontier-x"),
    )
    reg2.register(
        ModelEntry(
            "local",
            "on-host-7b",
            local=True,
            context_limit=8192,
            modalities=("text",),
            structured_output=True,
            est_cost_per_1k_microcents=2,
            privacy_class=LOCAL_ONLY,
        ),
        reg.provider_for("on-host-7b"),
    )
    spec = TaskSpec(task_class="summarize", sensitivity="public", modalities=("text",))
    decision = RoutingPolicy().select(spec, reg2)
    assert decision.selected_model == "frontier-x", "cloud tried first"
    result = routing.route_and_complete(
        decision, reg2, ModelRequest(prompt=SYNTHETIC_PROMPT, purpose="summarize"), max_hops=3
    )
    outcomes = [a["outcome"] for a in result.attempts]
    # the cloud attempt is surfaced as a non-ok outcome; the local model answers.
    assert outcomes[0] != "ok", f"the {mode} failure must be surfaced, got {outcomes}"
    assert result.ok and result.model == "on-host-7b", "bounded fallback to local"
    assert len(result.attempts) <= 3, "fallback must be bounded (no retry storm)"
    # no secret leaked into any log line, raw or redacted.
    assert not log.contains_secret("TEST-SECRET-VALUE"), "secret must never be logged"
    assert not any("TEST-SECRET-VALUE" in ln for ln in log.redacted())
    return {
        "mode": mode,
        "attempts": [dict(a) for a in result.attempts],
        "answered_by": result.model,
    }


def secret_redaction_evidence() -> dict:
    """Feed adversarial log lines (some carrying a credential) through the SHIPPING
    product redactor and assert the secret never survives. Also assert a credential
    placed on a `CloudProvider` never appears in its repr / attributes / model
    context."""
    secret = "sk-live-DEADBEEF0123456789ABCDEF0123456789"
    log = CaptureLog()
    for ln in (
        f"Authorization: Bearer {secret}",
        f"DECIMA_LIVE_API_KEY={secret}",
        f"connecting with api_key {secret} to endpoint",
        "call model=frontier-x prompt_len=42",  # a clean line
        f"debug token={secret}",
    ):
        log.write(ln)
    redacted = log.redacted()
    leaked = [ln for ln in redacted if secret in ln]
    assert not leaked, f"redactor let a secret through: {leaked}"

    # the credential is applied by the broker and never stored on the provider.
    broker = EnvSecretBroker(store={ENV_API_KEY: secret})
    backend = RecordingBackend(model="frontier-x", mode="ok")
    prov = CloudProvider(
        model="frontier-x", secret_name=ENV_API_KEY, broker=broker, backend=backend
    )
    prov.complete(ModelRequest(prompt=SYNTHETIC_PROMPT, purpose="summarize"))
    assert secret not in repr(prov), "secret must not appear in provider repr"
    assert not hasattr(prov, "secret") and not hasattr(prov, ENV_API_KEY)
    # the backend saw the secret's LENGTH applied but the value never entered a payload.
    assert all(secret not in p for p in backend.seen_payloads)
    return {
        "log_lines": len(log.lines),
        "redacted_leaks": len(leaked),
        "broker_secret_names": list(broker.calls),  # names only, never values
        "secret_in_repr": secret in repr(prov),
    }


# a re-export used by both suites for the env-var documentation cross-check.
ENV_VARS = {
    "provider_kind": ENV_PROVIDER,
    "model": ENV_MODEL,
    "base_url": ENV_BASE_URL,
    "api_key_name": ENV_API_KEY,
    "timeout_s": ENV_TIMEOUT,
}
