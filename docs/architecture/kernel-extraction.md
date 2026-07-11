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

## Remaining Phase-2 work

- **Stage 1b — the `kernel.py` authorization/lifecycle split** (DEC-016, DEC-018): extract
  the deterministic authorize + Morta + revoke core into `decima/kernel/authorization.py`
  and `lifecycle.py`, leaving boot orchestration to the runtime. Prove with authorization
  golden fixtures (grant → attenuate → authorize → approve → revoke → descendant fail).
- **Protocol facades** (DEC-010/011/013): typed `CanonicalCodec`, `Signer`/`Verifier`,
  `WeftStore`, and `AuthorizationDecision` (with machine-readable reason codes) over the
  copied implementations.
- **Stage 2 — clean up**: annotate + format the copied modules to the strict lint/type
  bar and remove the `decima/kernel` exclusions in `pyproject.toml`.
- **Epic 3 — property + adversarial tests** (DEC-033/034): fold determinism, attenuation
  monotonicity, revocation invalidates descendants, malformed-input fail-closed, etc.
