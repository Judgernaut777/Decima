"""Decima projections — DISPOSABLE read-models over the Weave fold (invariant 2/5).

A projection is a derived view, never canonical: the Weft is the sole source of
truth (invariant 1) and every read-model here is folded from it and rebuildable
byte-identically at any time. This package appends NOTHING to the Weft, mints no
authority (invariant 3), executes nothing, and treats every item's content as DATA
(invariant 5). Determinism is structural — ints, sorted output, no wall-clock.

  * ``engine``    — the ``Projection`` protocol, ``ProjectionCheckpoint``, the shared
                    ``FoldState`` reducer, and the ``ProjectionDriver`` (incremental
                    update, full rebuild, migration-by-rebuild, lag reporting).
  * ``tasks``     — plan-step list / status / deps / due.
  * ``projects``  — plan objective / status / members / progress.
  * ``agents``    — agent hierarchy / status / budget.
  * ``approvals`` — Morta inbox: pending / approved / denied / expired / consumed.
  * ``activity``  — a human-readable timeline of asserts/retracts/invokes/attests.
  * ``knowledge`` — notes / documents / links / provenance.
  * ``search``    — a derived, disposable exact-text index (deleting it loses
                    nothing canonical).
"""

from decima.projections.engine import (
    BaseProjection,
    FoldState,
    Projection,
    ProjectionCheckpoint,
    ProjectionDriver,
)

__all__ = [
    "BaseProjection",
    "FoldState",
    "Projection",
    "ProjectionCheckpoint",
    "ProjectionDriver",
]
