"""Decima runtime — durable plans, agents, jobs, and the scheduler (Phase 4).

Everything durable is a Cell on the Weft (Law 1 / invariant 2.1): plans, plan steps,
agents, jobs, and leases are asserted as content-addressed Cells and read back by folding
the Weave — never held only in an in-memory queue. The runtime folds current state,
decides what is ready, dispatches bounded work under a lease, and records every transition
as an event, so a fresh process over the same log rebuilds the whole world (durability is
structural, not a feature).

This package is built ON `decima.kernel` (the trusted core) and holds NO trusted-core
logic itself; it composes the kernel's public API.
"""
