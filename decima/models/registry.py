"""The model registry — a catalogue of available models and their bound providers.

Tracks, per model: provider name, local-or-remote, context limit, modalities,
structured-output + tool-use support, estimated cost, privacy class, and whether
the model is enabled. This is CONFIG the router reads; it holds NO authority — a
registry entry is a description, and binding a provider to it grants nothing
(invariant 3). Every recorded numeric is an INT (invariant 6).

The registry is the single place routing consults for "what models exist and what
can they do", so swapping the fleet never touches routing policy.
"""

from __future__ import annotations

from dataclasses import dataclass

from decima.models.providers import (
    TEXT,
    ModelCapabilities,
    ModelProvider,
)

# ── capability vocabulary (bounded ints + small fixed enums, NEVER floats) ─────
# Capability metadata drives model SELECTION only. It confers NO authority: a
# ModelEntry that claims the strongest capabilities still holds no capability,
# grant, principal, or key, and selecting it grants nothing (invariant 3). Over-
# claiming a tag can only change which model is *proposed*, never what it is allowed
# to do — every effect still flows through the kernel's authorization chain.

# Graded capability tiers: bounded ints 0..CAP_MAX, higher = stronger. 0 is the
# SAFE DEFAULT (unspecified / baseline) — an entry that declares nothing ranks and
# filters exactly as it did before capability metadata existed.
CAP_MIN = 0
CAP_MAX = 5

# The graded (int-scored) capabilities a task may REQUIRE or PREFER. Enum-valued
# descriptors (latency_class, cost_class) are reported but are not threshold-matched.
GRADED_CAPABILITIES = (
    "reasoning_strength",
    "coding",
    "planning",
    "structured_reliability",
)

# latency_class — a small fixed enum describing responsiveness class.
LATENCY_REALTIME = "realtime"
LATENCY_INTERACTIVE = "interactive"
LATENCY_BATCH = "batch"
LATENCY_CLASSES = frozenset({LATENCY_REALTIME, LATENCY_INTERACTIVE, LATENCY_BATCH})

# Deterministic total ordering of the latency classes (lower rank = faster), so a
# router can do "at least this fast" soft preference matching with pure INTEGER
# distance. Description only — an ordering confers no authority, and the honest int
# cost remains the load-bearing economic number; this only steers SELECTION.
LATENCY_RANK = {
    LATENCY_REALTIME: 0,
    LATENCY_INTERACTIVE: 1,
    LATENCY_BATCH: 2,
}

# cost_class — a small fixed enum describing coarse price tier (honest int cost in
# est_cost_per_1k_microcents remains the load-bearing ranking number; this is a tag).
COST_FREE = "free"
COST_LOW = "low"
COST_MEDIUM = "medium"
COST_HIGH = "high"
COST_CLASSES = frozenset({COST_FREE, COST_LOW, COST_MEDIUM, COST_HIGH})


def _check_graded(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < CAP_MIN or value > CAP_MAX:
        raise ValueError(f"{name} must be in [{CAP_MIN}, {CAP_MAX}], got {value}")


@dataclass(frozen=True)
class ModelEntry:
    """One catalogued model. Static description + live-config flags. Carries no
    capability, grant, or key. `est_cost_per_1k_microcents` is per-1k-token cost in
    MICRO-CENTS (int); a local model is typically 0.

    Capability metadata (`reasoning_strength`, `coding`, `planning`,
    `structured_reliability`, `latency_class`, `cost_class`) describes what the model
    is GOOD AT so routing can select by capability, not by name. It is a description
    only — it mints no authority. All graded scores are bounded ints (never floats)."""

    provider: str
    model: str
    local: bool
    context_limit: int
    modalities: tuple[str, ...] = (TEXT,)
    structured_output: bool = False
    tool_use: bool = False
    est_cost_per_1k_microcents: int = 0
    privacy_class: str = "external"
    enabled: bool = True
    # ── capability metadata (NEW; SAFE DEFAULTS keep every existing entry inert) ──
    reasoning_strength: int = CAP_MIN
    coding: int = CAP_MIN
    planning: int = CAP_MIN
    structured_reliability: int = CAP_MIN
    latency_class: str = LATENCY_INTERACTIVE
    cost_class: str = COST_FREE

    def __post_init__(self) -> None:
        for name in ("context_limit", "est_cost_per_1k_microcents"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} must be int, got {type(v).__name__}")
            if v < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in GRADED_CAPABILITIES:
            _check_graded(name, getattr(self, name))
        if self.latency_class not in LATENCY_CLASSES:
            raise ValueError(f"latency_class must be one of {sorted(LATENCY_CLASSES)}")
        if self.cost_class not in COST_CLASSES:
            raise ValueError(f"cost_class must be one of {sorted(COST_CLASSES)}")

    def capability_score(self, name: str) -> int:
        """The bounded int score for a graded capability (0 if unknown). Pure."""
        if name not in GRADED_CAPABILITIES:
            raise KeyError(f"{name} is not a graded capability")
        return int(getattr(self, name))

    @classmethod
    def from_capabilities(
        cls,
        provider: str,
        caps: ModelCapabilities,
        *,
        est_cost_per_1k_microcents: int = 0,
        enabled: bool = True,
    ) -> ModelEntry:
        """Build an entry from a provider's declared capabilities."""
        return cls(
            provider=provider,
            model=caps.model,
            local=caps.local,
            context_limit=caps.context_limit,
            modalities=caps.modalities,
            structured_output=caps.structured_output,
            tool_use=caps.tool_use,
            est_cost_per_1k_microcents=int(est_cost_per_1k_microcents),
            privacy_class=caps.privacy_class,
            enabled=enabled,
            reasoning_strength=int(caps.reasoning_strength),
            coding=int(caps.coding),
            planning=int(caps.planning),
            structured_reliability=int(caps.structured_reliability),
            latency_class=caps.latency_class,
            cost_class=caps.cost_class,
        )

    def to_content(self) -> dict:
        """Auditable, int-clean projection for recording on the Weft. Capability
        metadata is included as bounded ints + enum strings (all int-clean)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "local": self.local,
            "context_limit": int(self.context_limit),
            "modalities": list(self.modalities),
            "structured_output": self.structured_output,
            "tool_use": self.tool_use,
            "est_cost_per_1k_microcents": int(self.est_cost_per_1k_microcents),
            "privacy_class": self.privacy_class,
            "enabled": self.enabled,
            "reasoning_strength": int(self.reasoning_strength),
            "coding": int(self.coding),
            "planning": int(self.planning),
            "structured_reliability": int(self.structured_reliability),
            "latency_class": self.latency_class,
            "cost_class": self.cost_class,
        }


class ModelRegistry:
    """A catalogue of `ModelEntry` plus optional bound `ModelProvider` instances.
    Pure config store — no authority. Deterministic iteration order (insertion)."""

    def __init__(self) -> None:
        self._entries: dict[str, ModelEntry] = {}
        self._providers: dict[str, ModelProvider] = {}

    # ── registration ─────────────────────────────────────────────────────────
    def register(self, entry: ModelEntry, provider: ModelProvider | None = None) -> ModelEntry:
        """Catalogue a model (optionally binding the provider that serves it)."""
        self._entries[entry.model] = entry
        if provider is not None:
            self._providers[entry.model] = provider
        return entry

    def register_provider(
        self,
        provider: ModelProvider,
        *,
        provider_name: str | None = None,
        est_cost_per_1k_microcents: int = 0,
        enabled: bool = True,
    ) -> ModelEntry:
        """Catalogue a provider's model directly from its declared capabilities."""
        caps = provider.capabilities()
        entry = ModelEntry.from_capabilities(
            provider_name or caps.model,
            caps,
            est_cost_per_1k_microcents=est_cost_per_1k_microcents,
            enabled=enabled,
        )
        return self.register(entry, provider)

    def set_enabled(self, model: str, enabled: bool) -> None:
        from dataclasses import replace

        e = self._entries.get(model)
        if e is None:
            raise KeyError(model)
        self._entries[model] = replace(e, enabled=enabled)

    # ── lookup ───────────────────────────────────────────────────────────────
    def get(self, model: str) -> ModelEntry | None:
        return self._entries.get(model)

    def provider_for(self, model: str) -> ModelProvider | None:
        return self._providers.get(model)

    def all_entries(self) -> list[ModelEntry]:
        return list(self._entries.values())

    def enabled_entries(self) -> list[ModelEntry]:
        return [e for e in self._entries.values() if e.enabled]

    def candidates(
        self,
        *,
        modalities: tuple[str, ...] = (),
        local_only: bool = False,
        structured_output: bool = False,
        tool_use: bool = False,
        min_context: int = 0,
    ) -> list[ModelEntry]:
        """Enabled entries matching hard capability filters, in insertion order.
        `local_only` keeps only local models (the sensitive-task filter)."""
        out = []
        want = set(modalities)
        for e in self.enabled_entries():
            if local_only and not e.local:
                continue
            if want and not want.issubset(set(e.modalities)):
                continue
            if structured_output and not e.structured_output:
                continue
            if tool_use and not e.tool_use:
                continue
            if e.context_limit < int(min_context):
                continue
            out.append(e)
        return out

    def has_local(self) -> bool:
        return any(e.local for e in self.enabled_entries())
