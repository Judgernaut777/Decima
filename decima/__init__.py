"""Decima — a locally-hosted, capability-secured personal agent operating layer.

This is the Decima 0.3 package, built incrementally out of the reference implementation
that currently lives at ``heartbeat/decima/`` (handoff §4: extract one cohesive subsystem
at a time, preserving behavior, until parity). During 0.3 this package grows the extracted
kernel (``decima.kernel``), runtime, projections, services, and Shell; the legacy tree
remains runnable until the new Shell reaches parity.

Architectural invariants (never violated during 0.3):
  1. The Weft is the only canonical store.
  2. Durable operations are only ASSERT / RETRACT / INVOKE / ATTEST.
  3. No ambient authority — every effect needs principal + capability + invocation +
     authorization + any Morta approval + receipt.
  4. Models propose; deterministic code authorizes.
  5. Projections are disposable; a rebuild does not change canonical meaning.
"""

__version__ = "0.3.0.dev0"
