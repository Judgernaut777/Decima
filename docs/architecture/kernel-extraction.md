# Kernel extraction (Phase 2 / Epic 2)

_Status 2026-07-11: Stage 1 landed — the canonical/log/fold/identity/capability core is
extracted, imports independently, and is proven byte-for-byte equal to the reference._

## Goal

Create an independently-testable `decima/kernel/` package **without changing the
semantics** of the reference implementation (handoff Phase 2). The reference stays intact
and runnable at `heartbeat/decima/`; the kernel is proven equivalent via golden fixtures,
so it can become the conformance oracle for the eventual Rust port.

## Method — faithful extraction, not rewrite

Stage 1 **copies** the kernel modules verbatim from the reference and rewrites only their
intra-package import paths (`from decima.X` → `from decima.kernel.X`). No logic changes.
Equivalence is then proven, not assumed: `tools/kernel/gen_fixtures.py` runs the
**reference** and records canonical bytes, content IDs, event IDs, and a fold `state_root`
into `protocol/fixtures/`; `tests/kernel/test_conformance.py` replays the identical
operations through the **extracted** `decima.kernel` and asserts identity. Formatting and
annotation of the copied code (to the strict lint/type bar) is **Stage 2**, tracked below.

## What was extracted (Stage 1 — 12 modules + receipts)

`hashing` · `model` · `crypto` · `keystore` · `verifier` · `rotation` · `weft` · `weave`
· `capability` · `context_fold` · `snapshot` · `inbox`, plus a new `receipts.py` holding
the effect-outcome status constants. `capability.py` carries the full authorization
surface (`authorize`, `verify_proof`, `build_proof`, `attenuate`, `morta_floor`,
`lease_status`, …), so the conformance-critical authorization primitives are already in.

The dependency closure of this set is self-contained: every intra-`decima` import lands
within the set (verified — `grep` finds no `decima.*` import outside `decima.kernel`).

## The five external seams and how each was handled

| Seam (reference) | Handling in the extraction |
|---|---|
| `weft → rotation` | `rotation` is genuinely kernel/identity-tier (succession-chain key verification, `crypto`+`hashing`+`nacl`) — **copied in**. The imports are lazy and only fire for authors enrolled on a rotation chain; the common path never touches it. |
| `weave → executor.UNKNOWN` | Only a **status constant** is needed, not the effect-execution machinery. Defined in the new `decima/kernel/receipts.py`; `weave` repointed there. Isolation-backed execution stays OUT of the kernel (it belongs in workers, Phase 5). |
| `identity → secrets` | `identity.py` in the reference is an **OIDC/SSO broker** (service-tier), not the crypto core. The real identity TCB is `crypto`/`keystore`/`verifier`, which have no secrets dependency. The broker is **not** part of the kernel core; the seam drops out. |
| `autonomy → wager` | `autonomy`/`roe` are policy modules (and `wager` is calibration) — **deferred** from the Stage-1 core. They remain guarded-as-TCB in `heartbeat` and move in a later pass if kept. |
| `kernel.py → agent/discovery/disposition/memory/planning` | `kernel.py` is the **boot orchestrator**; its runtime coupling confirms the split. Its trusted core (authorize / Morta gate / revoke / `integrate_tool` / mint) is extracted in **Stage 1b** (see below) into `authorization.py` + `lifecycle.py`; the boot wiring becomes `decima/runtime/supervisor.py` (Phase 4). |

## Conformance proof (Stage 1)

`tests/kernel/test_conformance.py` — all green against the extracted package:
- canonical bytes / content IDs / blob IDs match the reference across unicode, nested
  maps, empty collections, big integers, and key-order permutations (DEC-010 / DEC-030);
- a fixed event script (TYPE_DEF → CONTENT ×3 → EDGE → RETRACT) produces identical event
  IDs and an identical fold `state_root` (DEC-012 / DEC-013 / DEC-014);
- tamper (one edited payload byte) is detected on read (`WeftError`).

The security-critical **import-boundary guard** (`tests/architecture/`) now scans
`decima/kernel/` and passes — no forbidden or undeclared third-party import.

## Stage 1b — authorization + lifecycle facades (DONE, DEC-016 / DEC-018)

The realization that de-risked this: the authorization/lifecycle **primitives are already
extracted** (`capability.authorize`/`verify_proof`/`attenuate`, `inbox.ApprovalInbox`, the
revoke cascade the fold derives in `weave.py`). `kernel.py`'s `invoke`/`say`/`execute_plan`
are runtime orchestration (agent brain, effect dispatch) that belongs in Phase 4 — NOT a
TCB dissection. So Stage 1b is clean facades over the frozen primitives, not surgery:

- `decima/kernel/authorization.py` (DEC-016): `AuthorizationDecision(allowed, reason_code,
  reason, matched_grant_id, required_approval)` with a stable `ReasonCode` enum.
  `authorize_decision(...)` delegates to `capability.authorize` (verdict unchanged) and
  classifies its string reason into a machine-readable code — so a scheduler / the Shell /
  an audit view can branch on the outcome. Deterministic; no clock beyond the caller's
  logical `now`.
- `decima/kernel/lifecycle.py` (DEC-018): thin `revoke`/`redact`/`supersede`/`terminate`
  helpers — the reference kernel's exact RETRACT bodies, taking `(weft, author, …)` instead
  of a bound `self`, so the fold derives the same DERIVED_AUTHORITY / LEASE_TREE cascade.

Proven by `tests/kernel/test_authz_lifecycle.py`: OK / NO_SUCH_CAPABILITY / NO_ENVELOPE /
APPROVAL_REQUIRED (and its clearance) / SIGNER_MISMATCH classify correctly, and
`lifecycle.revoke` → re-fold → a descendant invocation fails closed (`REVOKED`).

## Epic 3 — conformance + adversarial suite (DONE, DEC-020 / DEC-030..035)

Built as a 5-lane workflow over the stable kernel, each lane self-verified green and
adversarially reviewed (`tools/workflows/epic3-conformance.js`). All landed:
`tests/kernel/test_event_fixtures.py` (golden vectors + the ingest acceptance gate),
`tests/property/test_fold_properties.py` (fold determinism / order-independence /
idempotence / rebuild == incremental), `tests/property/test_capability_properties.py`
(attenuation monotonicity, proof binding, revocation invalidates descendants),
`tests/adversarial/test_hostile_input.py` (every §2 defect fails closed, kernel never
crashes), and `decima/kernel/checkpoints.py` + `tests/kernel/test_checkpoints.py` (a signed
local checkpoint binding frontier + event count + state_root + protocol version). **70
tests green under pytest.**

## Remaining Phase-2 work

- **Protocol facades** (DEC-010/011/013): typed `CanonicalCodec`, `Signer`/`Verifier`,
  `WeftStore` protocols over the copied implementations (the `AuthorizationDecision` facade
  is done).
- **Stage 2 — clean up**: annotate + format the copied modules (and the new facades) to the
  strict lint/type bar and remove the `decima/kernel` exclusions in `pyproject.toml`.
