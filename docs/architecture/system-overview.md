# Decima 0.3 — system overview

Decima is a locally-hosted agent operating layer for one technical user. Linux stays the
host OS; Decima runs on top of it as a capability-secured, event-sourced personal agent
system. This document maps the packages built for the 0.3 milestone and how they compose.

## The one idea

Everything durable is an event on the **Weft** — an append-only, signed, content-addressed
log. There is no UPDATE and no DELETE, only the four verbs **ASSERT / RETRACT / INVOKE /
ATTEST**. All state you ever see (tasks, plans, agents, approvals, capabilities, receipts,
knowledge) is a **fold** of the Weft into the **Weave** — a pure, deterministic projection.
Because state is a fold, time-travel, undo, crash recovery, and reproducibility all fall
out for free, and every read-model is disposable and rebuildable.

## Packages (trust flows outward from the kernel)

```
decima/
├── kernel/        TRUSTED COMPUTING BASE — verifies, authorizes, folds, appends. Nothing else.
│                  canonical encoding · identity/Ed25519 · Weft store · deterministic fold ·
│                  capability + authorization (machine-readable decisions) · Morta approvals ·
│                  lifecycle/revocation · receipts · signed checkpoints · seam Protocols.
│                  Imports NO network/subprocess/provider/MCP/web — enforced by a build test.
├── runtime/       Durable plans/steps/agents/leases/receipts as Cells; a fold-based scheduler;
│                  a supervisor that dispatches under leases with crash recovery + idempotency;
│                  budgets, cancellation propagation, effect reconciliation.
├── workers/       Isolated effect execution — the ONLY place untrusted code/tools run. Versioned
│                  IPC, digest-bound implementations, scrubbed env, rlimits, lease validation.
├── models/        Model providers behind one Protocol; a deterministic provider (tests need no
│                  paid API); routing policy (recorded decisions); token/cost budgets; structured-
│                  proposal validation. Models PROPOSE; they never authorize.
├── projections/   Disposable read-models over the fold: tasks, projects, agents, approvals,
│                  activity, knowledge, search. Deleting + rebuilding never changes meaning.
├── services/      api/ (the local Shell backend — auth, streaming, command service; every
│                  mutation becomes Weft events) · backup/restore · diagnostics/doctor · first-run.
├── capabilities/  Daily-driver workflows: document ingestion (source-linked), source-grounded
│                  Q&A with citations, isolated coding workspace.
├── shell/         The trusted web frontend served over the API (Phase 9).
└── cli/           decima / -server / -worker / -doctor / -rebuild / -backup / -restore.
```

`heartbeat/` remains the frozen Python reference oracle; the kernel was extracted from it
and proven byte-for-byte equivalent via golden fixtures (`protocol/fixtures/`).

## How a privileged action happens (no ambient authority)

1. A model or user PROPOSES an action (structured, validated — never itself an authorization).
2. Deterministic kernel code AUTHORIZES: the acting principal must hold a capability in its
   envelope, the grant must be downhill of its parent, caveats/leases/budgets must hold, and
   any Morta approval must be present. The decision is machine-readable (`AuthorizationDecision`
   with a stable `ReasonCode`).
3. If approval is required, a pending inbox item is asserted; the effect does NOT run until a
   human approval is recorded on the Weft (bound to the exact invocation — reuse fails).
4. The authorized INVOKE is dispatched to an isolated **worker** under a bounded **lease**.
5. The worker's outcome is appended as a **receipt**. Replay is a no-op (idempotency by
   receipt); a crash before the receipt is reconciled, never silently retried.

## The invariants (enforced, not aspirational)

- The Weft is the sole canonical store; projections are disposable.
- Durable operations are only the four verbs.
- No ambient authority; models propose, deterministic code authorizes.
- Untrusted content is DATA (never rendered/executed as trusted).
- The kernel executes nothing untrusted; its import boundary is a passing build test.
- Recorded content is deterministic (ints, no wall-clock/unseeded-random).

## Verification

233+ tests: kernel conformance (golden fixtures), capability/fold property tests, hostile-
input adversarial tests, worker-escape tests on-box, projection rebuild==incremental, and
end-to-end scenarios — crash recovery, revocation cascade, backup/restore round-trip,
approval gating — plus fault-injection (kill between dispatch and receipt never retries an
unsafe effect). See `docs/verification/scenarios.md`.
