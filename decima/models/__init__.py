"""decima.models — model routing: models as PROPOSAL engines with ZERO authority.

This package treats every model as a PROPOSAL engine (invariant 4: models propose,
deterministic code authorizes). Nothing here mints, holds, or checks authority: a
provider returns DATA, a routing decision is DATA a caller folds onto the Weft, a
validated proposal is inert DATA. There is deliberately NO path from a `ModelResponse`
to an effect inside this package — turning a proposal into an effect requires the
kernel's authorization + approval + receipt chain, which this package does not import.

Modules:
  * `providers`  — the `ModelProvider` Protocol, the reproducible offline
    `DeterministicProvider` (default fallback / test engine), and thin
    `LocalProvider` / `CloudProvider` adapters that make NO live network call by
    themselves (a live call is gated behind an injected backend; secrets via broker).
  * `registry`   — the catalogue of models (local/remote, context, modality,
    structured-output, tool-use, cost, privacy class, enabled).
  * `routing`    — the `RoutingPolicy` (task-class/sensitivity/modality/context/
    latency/cost → `RoutingDecision`), local-only enforcement for sensitive tasks,
    and bounded provider-failure fallback.
  * `budgets` / `accounting` — token + cost accounting and a pre/post budget gate
    that STOPS calls when exhausted.
  * `validation` — structured proposals validated against explicit schemas; invalid
    proposals rejected (never eval-repaired) with a bounded re-prompt path.

Determinism (invariant 6): every recorded numeric is an INT; nothing reads a
wall-clock or draws unseeded randomness in recorded content.
"""

from decima.models import (  # noqa: F401
    accounting,
    budgets,
    providers,
    registry,
    routing,
    validation,
)

__all__ = [
    "providers",
    "registry",
    "routing",
    "budgets",
    "accounting",
    "validation",
]
