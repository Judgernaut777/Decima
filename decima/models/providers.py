"""Model providers — PROPOSAL engines with ZERO authority (invariant 4).

A provider turns a `ModelRequest` into a `ModelResponse`. That is *all* it does.
Note what a `ModelResponse` deliberately does NOT carry and cannot do:

  * no capability, no grant, no principal, no key — selecting or running a model
    confers no permission (invariant 3: no ambient authority);
  * no `.execute()`, `.invoke()`, `.authorize()` — a model output is DATA, never
    itself an authorization (invariant 4: models PROPOSE, deterministic code
    AUTHORIZES). To turn a proposed action into an effect a caller must go through
    the kernel's authorization + approval + receipt chain, which lives OUTSIDE this
    package (this package imports none of it).

Three concrete providers conform to one structural `ModelProvider` Protocol:

  * `DeterministicProvider` — rule-based, no network, fully reproducible. Same
    request in ⇒ byte-identical response out (derived via the kernel's content
    hash, never wall-clock or unseeded random). This is the default fallback and
    the engine every test uses, so the whole product is testable with no paid API.
  * `LocalProvider` / `CloudProvider` — thin ADAPTERS that conform to the Protocol
    structurally but make NO live network call by themselves. A live call is a
    runtime concern gated behind an injected `backend` seam; with no backend
    configured they FAIL CLOSED (raise `LiveTransportRequired`) rather than reach
    the network. Secrets are applied by a broker at call time, NEVER placed in
    code, context, or logs — so no provider here stores or logs a key.

Determinism (invariant 6): every token/cost number a provider reports is an INT;
nothing here reads a clock or draws unseeded randomness.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from decima.kernel.hashing import content_id, nfc

# ── modality tags (vendor-neutral) ────────────────────────────────────────────
TEXT = "text"
CODE = "code"
IMAGE = "image"
AUDIO = "audio"
MULTIMODAL = "multimodal"

# ── provider privacy classes (data residency / trust class of the instance) ───
LOCAL_ONLY = "local_only"  # on-device / in-VPC; data never leaves
PRIVATE_RENTED = "private_rented"  # dedicated rented capacity
EXTERNAL = "external"  # public API endpoint (free tier)
EXTERNAL_PAID = "external_paid"  # public API endpoint that bills per token

# ── stop reasons a response may carry ─────────────────────────────────────────
STOP = "stop"  # normal completion
REFUSAL = "refusal"  # the model declined the task (triggers bounded fallback)
LENGTH = "length"  # hit the output token cap
ERROR = "error"  # the provider failed (triggers bounded fallback)


class LiveTransportRequired(RuntimeError):
    """A live-network adapter was invoked with no `backend` seam configured. The
    adapter FAILS CLOSED here rather than reach the network by default — a live
    call is gated behind config, and secrets are applied by a broker at call
    time, never embedded in this package."""


# ── capability descriptors a provider may surface (bounded ints + enums) ───────
# These mirror decima.models.registry's capability vocabulary. They are DESCRIPTION
# only — a capability tag confers no authority (a model over-claiming a strength can
# change what is proposed, never what is permitted). Graded scores are bounded ints
# 0..5 (never floats); latency/cost are small fixed-enum strings. SAFE DEFAULTS (0 /
# interactive / free) keep every existing provider's declared capabilities inert.
LATENCY_INTERACTIVE = "interactive"
COST_FREE = "free"


@dataclass(frozen=True)
class ModelCapabilities:
    """What a model can do, in vendor-neutral terms. Every numeric is an INT.
    Carries no authority — it is a static description a registry indexes.

    Capability metadata (`reasoning_strength`, `coding`, `planning`,
    `structured_reliability`, `latency_class`, `cost_class`) lets routing select by
    capability rather than name. It is a description a registry copies onto its
    entry; it mints no authority."""

    model: str
    context_limit: int
    modalities: tuple[str, ...] = (TEXT,)
    structured_output: bool = False
    tool_use: bool = False
    local: bool = False
    privacy_class: str = EXTERNAL
    # ── capability metadata (NEW; SAFE DEFAULTS keep existing capabilities inert) ─
    reasoning_strength: int = 0
    coding: int = 0
    planning: int = 0
    structured_reliability: int = 0
    latency_class: str = LATENCY_INTERACTIVE
    cost_class: str = COST_FREE

    def __post_init__(self) -> None:
        if isinstance(self.context_limit, bool) or not isinstance(self.context_limit, int):
            raise TypeError(f"context_limit must be int, got {type(self.context_limit).__name__}")
        if self.context_limit < 0:
            raise ValueError("context_limit must be non-negative")
        for name in ("reasoning_strength", "coding", "planning", "structured_reliability"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} must be int, got {type(v).__name__}")


@dataclass(frozen=True)
class ModelRequest:
    """A request TO a model. `prompt` is the trusted instruction the caller frames;
    `context` is DATA (possibly untrusted) and is marked `instruction_eligible`
    False by default (invariant 5: untrusted content is data, never instruction).
    `structured_schema`, if given, asks the model to PROPOSE a structured action —
    which `validation.py` then checks against that schema before anything happens.

    `max_output_tokens` and `context_tokens` are INTS (determinism)."""

    prompt: str
    purpose: str = "chat"
    context: str = ""
    context_tokens: int = 0
    max_output_tokens: int = 512
    structured_schema: dict | None = None
    instruction_eligible: bool = False

    def __post_init__(self) -> None:
        for name in ("context_tokens", "max_output_tokens"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} must be int, got {type(v).__name__}")


@dataclass(frozen=True)
class ModelResponse:
    """A model's PROPOSAL, as inert DATA. Note the absence of any capability,
    grant, principal, key, or effect method — a response cannot authorize or
    perform anything. Token counts are INTS. `structured` is the raw proposed
    action (unvalidated); a caller MUST run it through `validation.py` and then
    the kernel's authorization chain before any effect."""

    model: str
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str = STOP
    structured: dict | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} must be int, got {type(v).__name__}")

    @property
    def refused(self) -> bool:
        return self.stop_reason == REFUSAL

    @property
    def failed(self) -> bool:
        return self.stop_reason == ERROR or self.error is not None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@runtime_checkable
class ModelProvider(Protocol):
    """The structural contract every provider conforms to. A provider PROPOSES;
    it holds no authority. `stream` yields text chunks for the same request."""

    def capabilities(self) -> ModelCapabilities: ...

    def complete(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> Iterator[str]: ...


def estimate_tokens(text: str) -> int:
    """Deterministic ~¾-word/token estimate for offline accounting. A real
    tokenizer slots in here without changing any policy. Pure; no clock."""
    words = len((text or "").split())
    return (words * 4 + 2) // 3


# ── the deterministic provider — reproducible, offline, the default fallback ──
@dataclass(frozen=True)
class DeterministicProvider:
    """Rule-based, network-free, fully reproducible provider. Same request in ⇒
    byte-identical response out, because the output is derived from the kernel's
    content hash of the request — never from a clock or unseeded random.

    `refuse_purposes` makes it decline (stop_reason=REFUSAL) on given purposes, so
    a test can exercise the declared fallback path. `structured_builder`, if set,
    turns a request into a proposed structured action (still DATA; validated
    downstream). It carries no authority."""

    model: str = "deterministic-1"
    context_limit: int = 8192
    modalities: tuple[str, ...] = (TEXT, CODE)
    structured_output: bool = True
    tool_use: bool = False
    local: bool = True
    privacy_class: str = LOCAL_ONLY
    refuse_purposes: frozenset[str] = frozenset()
    fail_purposes: frozenset[str] = frozenset()
    # capability metadata (description only; excluded from the reproducible digest)
    reasoning_strength: int = 0
    coding: int = 0
    planning: int = 0
    structured_reliability: int = 0
    latency_class: str = LATENCY_INTERACTIVE
    cost_class: str = COST_FREE

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            model=self.model,
            context_limit=self.context_limit,
            modalities=self.modalities,
            structured_output=self.structured_output,
            tool_use=self.tool_use,
            local=self.local,
            privacy_class=self.privacy_class,
            reasoning_strength=self.reasoning_strength,
            coding=self.coding,
            planning=self.planning,
            structured_reliability=self.structured_reliability,
            latency_class=self.latency_class,
            cost_class=self.cost_class,
        )

    def _digest(self, request: ModelRequest) -> str:
        """A stable, content-addressed fingerprint of the request — the seed of the
        deterministic output. No clock, no random; identical requests hash alike."""
        return content_id(
            {
                "model": self.model,
                "prompt": nfc(request.prompt),
                "context": nfc(request.context),
                "purpose": nfc(request.purpose),
                "max_out": int(request.max_output_tokens),
            }
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        in_tokens = estimate_tokens(request.prompt) + int(request.context_tokens)
        if request.purpose in self.fail_purposes:
            return ModelResponse(
                model=self.model,
                text="",
                input_tokens=in_tokens,
                output_tokens=0,
                stop_reason=ERROR,
                error=f"deterministic failure on purpose={request.purpose}",
            )
        if request.purpose in self.refuse_purposes:
            return ModelResponse(
                model=self.model,
                text="",
                input_tokens=in_tokens,
                output_tokens=0,
                stop_reason=REFUSAL,
            )
        digest = self._digest(request)
        text = f"[{self.model}·{request.purpose}] {request.prompt} <{digest[:12]}>"
        out_tokens = min(estimate_tokens(text), int(request.max_output_tokens))
        structured = None
        if request.structured_schema is not None:
            structured = self._propose(request, digest)
        return ModelResponse(
            model=self.model,
            text=text,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            stop_reason=STOP,
            structured=structured,
        )

    def _propose(self, request: ModelRequest, digest: str) -> dict:
        """Deterministically fill the requested schema with placeholder values, so a
        test can drive the validation path with a reproducible proposal. This is
        DATA — a proposed action, not an authorized one."""
        schema = request.structured_schema or {}
        fields = schema.get("fields", {})
        out: dict = {}
        for name, spec in fields.items():
            t = spec.get("type", "string") if isinstance(spec, dict) else "string"
            if isinstance(spec, dict) and spec.get("enum"):
                out[name] = spec["enum"][0]
            elif t == "int":
                out[name] = int(spec.get("min", 0)) if isinstance(spec, dict) else 0
            else:
                out[name] = f"{name}:{digest[:8]}"
        return out

    def stream(self, request: ModelRequest) -> Iterator[str]:
        """Deterministic chunking of the completion (word by word). Reproducible."""
        resp = self.complete(request)
        yield from resp.text.split(" ")


@runtime_checkable
class SecretsBroker(Protocol):
    """The seam a `CloudProvider` calls through to apply a secret without ever
    holding it itself: the broker resolves `name` and invokes `fn` with the secret
    INSIDE itself, returning whatever `fn` returns. Never stores/logs the value."""

    def use_secret(self, name: str, fn: Callable[[object], ModelResponse]) -> ModelResponse: ...


# ── thin live adapters — structural conformance, NO network by default ────────
@dataclass(frozen=True)
class LocalProvider:
    """Adapter for on-host inference (llama.cpp / vLLM slot in behind `backend`).
    Conforms to `ModelProvider` structurally but makes NO network call itself; the
    live seam is `backend(request, capabilities) -> ModelResponse`, injected by the
    runtime. With no backend it FAILS CLOSED (raises `LiveTransportRequired`).
    Local ⇒ `privacy_class=local_only`; the data never leaves the host."""

    model: str
    context_limit: int = 8192
    modalities: tuple[str, ...] = (TEXT,)
    structured_output: bool = False
    tool_use: bool = False
    backend: Callable[..., ModelResponse] | None = (
        None  # callable seam injected at runtime; None ⇒ fail closed
    )
    reasoning_strength: int = 0
    coding: int = 0
    planning: int = 0
    structured_reliability: int = 0
    latency_class: str = LATENCY_INTERACTIVE
    cost_class: str = COST_FREE

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            model=self.model,
            context_limit=self.context_limit,
            modalities=self.modalities,
            structured_output=self.structured_output,
            tool_use=self.tool_use,
            local=True,
            privacy_class=LOCAL_ONLY,
            reasoning_strength=self.reasoning_strength,
            coding=self.coding,
            planning=self.planning,
            structured_reliability=self.structured_reliability,
            latency_class=self.latency_class,
            cost_class=self.cost_class,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self.backend is None:
            raise LiveTransportRequired(
                "LocalProvider has no backend seam configured; a live inference call "
                "is gated behind runtime config (inject backend=...)"
            )
        return self.backend(request, self.capabilities())

    def stream(self, request: ModelRequest) -> Iterator[str]:
        yield self.complete(request).text


@dataclass(frozen=True)
class CloudProvider:
    """Adapter for a hosted API. Conforms structurally but makes NO network call
    itself: the live seam is `backend(request, capabilities, secret) -> ModelResponse`.
    The API key is applied by a `broker` at call time (`broker.use_secret(name, fn)`),
    NEVER stored on this object, embedded in code, placed in context, or logged. With
    neither backend nor broker configured it FAILS CLOSED."""

    model: str
    context_limit: int = 200_000
    modalities: tuple[str, ...] = (TEXT,)
    structured_output: bool = True
    tool_use: bool = True
    privacy_class: str = EXTERNAL_PAID
    secret_name: str = ""
    backend: Callable[..., ModelResponse] | None = None  # callable seam injected at runtime
    broker: SecretsBroker | None = None  # secrets broker: applies the key INSIDE the broker
    reasoning_strength: int = 0
    coding: int = 0
    planning: int = 0
    structured_reliability: int = 0
    latency_class: str = LATENCY_INTERACTIVE
    cost_class: str = COST_FREE

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            model=self.model,
            context_limit=self.context_limit,
            modalities=self.modalities,
            structured_output=self.structured_output,
            tool_use=self.tool_use,
            local=False,
            privacy_class=self.privacy_class,
            reasoning_strength=self.reasoning_strength,
            coding=self.coding,
            planning=self.planning,
            structured_reliability=self.structured_reliability,
            latency_class=self.latency_class,
            cost_class=self.cost_class,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self.backend is None:
            raise LiveTransportRequired(
                "CloudProvider has no backend seam configured; a live API call is "
                "gated behind runtime config, and the key is applied by a broker "
                "(never embedded here)"
            )
        caps = self.capabilities()
        backend = self.backend
        if self.broker is not None and self.secret_name:
            # The broker applies the secret INSIDE itself and never returns it.
            return self.broker.use_secret(
                self.secret_name, lambda secret: backend(request, caps, secret)
            )
        return self.backend(request, caps, None)

    def stream(self, request: ModelRequest) -> Iterator[str]:
        yield self.complete(request).text


# ── a proposed action — inert DATA extracted from a response ──────────────────
@dataclass(frozen=True)
class ProposedAction:
    """A structured action a model PROPOSED. It is DATA and it is INERT: it has no
    execute/invoke/authorize method, no capability, no grant. `instruction_eligible`
    is False (invariant 5). To perform it a caller must run it through validation
    and then the kernel's authorization + approval + receipt chain — none of which
    lives in this package."""

    action: str
    params: dict = field(default_factory=dict)
    source_model: str = ""
    instruction_eligible: bool = False

    @classmethod
    def of(cls, response: ModelResponse) -> ProposedAction | None:
        s = response.structured
        if not s:
            return None
        return cls(
            action=str(s.get("action", response.model)),
            params={k: v for k, v in s.items() if k != "action"},
            source_model=response.model,
            instruction_eligible=False,
        )
