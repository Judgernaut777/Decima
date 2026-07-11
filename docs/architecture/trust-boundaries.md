# Trust boundaries & the trusted computing base (TCB)

_DEC-003. Frozen 2026-07-11. Anchored on real import analysis of `heartbeat/decima/`._

Decima 0.3 draws one hard line: the **kernel process verifies, authorizes, folds, and
appends — and executes nothing untrusted**. Everything with an outward effect runs
elsewhere, under a capability it was explicitly granted (handoff §2.3, §2.6).

## What the TCB is

The trusted computing base is the set of modules that will live in `decima/kernel/` and
whose correctness the whole system's security depends on. They are pure verify/authorize/
fold/append logic with **no** network, subprocess, provider, web, or domain code.

### Current module → target kernel member

| Target `decima/kernel/` member | Current module(s) | Role |
|---|---|---|
| `canonical.py` | `weave.py` (encoding), `hashing.py` | canonical serialization + content IDs |
| `event.py` | `weft.py` (event shape), `weave.py` | the four-verb event + validation split |
| `identity.py` | `identity.py`, `crypto.py`, `keystore.py`, `verifier.py` | Ed25519 signing / verification, principals |
| `weft.py` | `weft.py` | append-only signed log (`WeftStore`, sqlite default) |
| `fold.py` | `weave.py` (`fold`), `context_fold.py` (Law-5 window) | deterministic fold → `WeaveState` |
| `cells.py` | `weave.py` (Cell projection) | Cell / edge materialization |
| `capabilities.py` | `capability.py`, `manifest.py` | grants, attenuation, invocation proofs |
| `authorization.py` | `kernel.py` (authorize half) | deterministic authorization decisions |
| `approvals.py` | `inbox.py` (ApprovalInbox / Morta) | approval bound to a concrete invocation |
| `lifecycle.py` | `kernel.py` (revoke/terminate) | revocation, descendant invalidation |
| `receipts.py` | `weave.py` / `kernel.py` (attest) | effect receipts, reconciliation results |
| `checkpoints.py` | `snapshot.py` | signed local checkpoints (frontier + state root) |

### Modules that STRADDLE the line (must be split in Phase 2 / Phase 5)

- **`kernel.py`** (1026 lines) — its *authorize + Morta gate + revoke* logic is TCB; its
  *boot orchestration* (mint root, wire primitives, start loops) is **runtime**, not
  kernel. Extract the deterministic authorization/lifecycle core into
  `decima/kernel/`; leave boot wiring to `decima/runtime/supervisor.py`.
- **`executor.py`** — the **authorize→dispatch boundary**. Turning an authorized INVOKE
  into a real effect is exactly what must NOT happen in the kernel process (§2.6). The
  *registry + authorization binding* stays trusted; the *effect execution* moves to
  isolated workers over the worker IPC (Phase 5, DEC-050+). Do not let generated code,
  shell commands, MCP servers, or provider adapters execute in-process.

## The forbidden-import rule (enforced by DEC-006)

`decima/kernel/**` (and, today, the current TCB modules above) MUST NOT import any of:

```
requests            urllib.request / urllib.*      http.client / http.server
socket              ssl                            subprocess
anthropic           openai                         mcp  (+ decima.mcp / decima.mcp_server)
fastapi             flask                          django
```

…nor any **provider adapter, web framework, frontend, domain integration, shell-command
execution, browser, document parser, or vector database** module (handoff §2.9, §4.3).

**Permitted through declared interfaces:** cryptographic libraries (`nacl`) and the
storage backend (`sqlite3`) — but only behind the `Signer`/`Verifier`/`WeftStore`
protocol seams, never a raw provider or web client.

`tests/architecture/test_import_boundaries.py` fails the build if any TCB module's
import graph crosses this line. It runs against the current core modules today and
transfers verbatim to `decima/kernel/**` when Phase 2 extracts the package.

## The effect surface (the untrusted side)

Import analysis found the outward-effect surface confined to ~26 modules (network /
subprocess / MCP): `ads, agent, brokerage_engine, citizens, cloud_compute,
cloud_storage, comms, dns, egress, exchange, isolation, live_wire, maps_engine, mcp,
oidc, payouts, payroll, process_effect, ride, shell, shipping, sms, storage,
stripe_rail, sync, weather_engine, wire`. These are `service` / `capability` / `runtime`
tier — never TCB — and in 0.3 their execution must occur in isolated workers, not the
kernel process.

## The five invariants this boundary enforces

1. The Weft is the only canonical store (no second DB for tasks/agents/approvals/…).
2. Durable ops are only ASSERT / RETRACT / INVOKE / ATTEST.
3. No ambient authority — every effect needs principal + capability + invocation +
   authorization + any Morta approval + receipt.
4. Models propose; deterministic code (the TCB) authorizes.
5. Projections are disposable; canonical meaning survives a rebuild.
