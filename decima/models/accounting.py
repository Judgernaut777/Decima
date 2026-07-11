"""Token + cost accounting for model calls — integer-clean, no clock.

Every model call produces a `UsageRecord`: provider, model, in/out tokens, latency,
estimated cost (micro-cents), and the purpose it served. All numerics are INTS
(invariant 6). Latency is a CALLER-SUPPLIED int — this module never reads a
wall-clock, so recorded content stays deterministic (the runtime measures latency
outside the recorded boundary and passes it in, or passes 0).

A `UsageLedger` accumulates records and totals them by model / provider / purpose.
It holds no authority; it is a projection over what happened, and `record_usage`
can fold a `UsageRecord` onto the Weft as an audit Cell.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class UsageRecord:
    """One model call's accounting. Every numeric is an INT. `latency_ms` is
    supplied by the caller (measured outside the deterministic boundary), never read
    from a clock here — so this record is reproducible from the same inputs."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    est_cost_microcents: int = 0
    latency_ms: int = 0
    purpose: str = "chat"

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "est_cost_microcents", "latency_ms"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} must be int, got {type(v).__name__}")
            if v < 0:
                raise ValueError(f"{name} must be non-negative")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_content(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "total_tokens": int(self.total_tokens),
            "est_cost_microcents": int(self.est_cost_microcents),
            "latency_ms": int(self.latency_ms),
            "purpose": self.purpose,
        }


@dataclass
class _Totals:
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_microcents: int = 0
    calls: int = 0


class UsageLedger:
    """Accumulates `UsageRecord`s and totals them. Pure integer arithmetic; no
    authority. Iteration/grouping is deterministic (insertion order preserved by the
    underlying dicts)."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._by_model: dict[str, _Totals] = defaultdict(_Totals)
        self._by_provider: dict[str, _Totals] = defaultdict(_Totals)
        self._by_purpose: dict[str, _Totals] = defaultdict(_Totals)
        self._grand = _Totals()

    def add(self, record: UsageRecord) -> UsageRecord:
        self._records.append(record)
        for bucket in (
            self._by_model[record.model],
            self._by_provider[record.provider],
            self._by_purpose[record.purpose],
            self._grand,
        ):
            bucket.tokens += record.total_tokens
            bucket.input_tokens += record.input_tokens
            bucket.output_tokens += record.output_tokens
            bucket.cost_microcents += record.est_cost_microcents
            bucket.calls += 1
        return record

    @property
    def records(self) -> list[UsageRecord]:
        return list(self._records)

    @property
    def total_tokens(self) -> int:
        return self._grand.tokens

    @property
    def total_cost_microcents(self) -> int:
        return self._grand.cost_microcents

    @property
    def calls(self) -> int:
        return self._grand.calls

    def _project(self, table: dict[str, _Totals]) -> dict[str, dict]:
        return {
            key: {
                "tokens": t.tokens,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
                "cost_microcents": t.cost_microcents,
                "calls": t.calls,
            }
            for key, t in table.items()
        }

    def by_model(self) -> dict[str, dict]:
        return self._project(self._by_model)

    def by_provider(self) -> dict[str, dict]:
        return self._project(self._by_provider)

    def by_purpose(self) -> dict[str, dict]:
        return self._project(self._by_purpose)

    def summary(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "total_cost_microcents": self.total_cost_microcents,
            "calls": self.calls,
            "by_model": self.by_model(),
            "by_provider": self.by_provider(),
            "by_purpose": self.by_purpose(),
        }


USAGE = "model_usage"


def usage_from_response(response, *, provider: str, latency_ms: int = 0, purpose: str = "chat"):
    """Build a `UsageRecord` from a `ModelResponse`. `latency_ms` is caller-measured
    (no clock read here). Cost is not on the response (it depends on the catalogued
    price), so it defaults to 0 unless the caller supplies it via `record`."""
    return UsageRecord(
        provider=provider,
        model=response.model,
        input_tokens=int(response.input_tokens),
        output_tokens=int(response.output_tokens),
        est_cost_microcents=0,
        latency_ms=int(latency_ms),
        purpose=purpose,
    )


def record_usage(k, record: UsageRecord, *, author=None, provenance=None) -> str:
    """Fold a `UsageRecord` onto the Weft as a `model_usage` audit Cell (invariant 1).
    Ints only; no authority minted. `provenance`, if given, links to the routing/
    request Cell (a `measures` edge)."""
    from decima.kernel.hashing import content_id
    from decima.kernel.model import assert_content, assert_edge

    author = author or k.decima_agent_id
    cid = content_id(
        {
            "model_usage": record.model,
            "tokens": int(record.total_tokens),
            "cost": int(record.est_cost_microcents),
            "lamport": k.weft.lamport,
        }
    )
    assert_content(k.weft, author, cid, USAGE, record.to_content())
    if provenance is not None:
        assert_edge(k.weft, author, cid, "measures", provenance)
    return cid
