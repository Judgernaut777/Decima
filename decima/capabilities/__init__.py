"""Daily-driver capabilities — the narrow, COMPLETE user workflows on the kernel.

This package is NOT a new store and mints NO authority. It composes the existing
seams — ``decima.kernel`` (the sole canonical Weft), ``decima.runtime`` (leases +
receipts), ``decima.workers`` (isolated effect execution), ``decima.models`` (a
DeterministicProvider that PROPOSES), and ``decima.projections`` (disposable
knowledge/search read-models) — into three end-to-end workflows a person actually
runs:

  * ``documents`` — import a file (bytes + digest), classify it, extract text SAFELY,
    segment it, and land each segment as a SOURCE-LINKED knowledge Cell on the Weft
    (every claim keeps its source id + offset — the claim→source relationship is
    NEVER discarded). Untrusted document content is DATA (``instruction_eligible``
    False), indexed via ``projections.search``.
  * ``qa`` — source-grounded question answering: retrieve relevant segments through
    the search read-model, answer via a ``models`` provider (which only proposes),
    and return the answer WITH citations that resolve back to imported segments.
    Knowledge access is HORIZON-SCOPED — an agent sees only the projects it was
    explicitly given.
  * ``workspace`` — an isolated repository workspace: mount a repo into a bounded
    dir, edit files, run declared commands/tests INSIDE a ``workers`` worker (no
    network, no filesystem outside its jail, no creds), generate a REVIEWABLE diff,
    and produce durable diff/test artifact Cells (a restart never loses them).

Everything durable is a Cell asserted through the kernel; the read-models and the
host working dirs are disposable. No web framework, no ambient authority, no
untrusted content ever executed in this (untrusted-content-adjacent) process — the
only place untrusted code runs is the isolated worker.
"""

from __future__ import annotations

from decima.capabilities import documents, qa, workspace

__all__ = ["documents", "qa", "workspace"]
