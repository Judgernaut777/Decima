# Decima — Independent Assessment & Ultraplan

**Date:** 2026-07-24
**Scope:** whole repository at commit `999a6cf` (Cycle 67 BATCH U), branch `main`
**Method:** four parallel deep reviews — kernel/security, runtime/services, tests/CI,
docs-vs-reality — each verifying documented claims against the code, plus a live run of
the full test gate in a clean environment.

This document is descriptive (what Decima *is* today) and prescriptive (what to do next,
in priority order). It does not modify any product code.

---

## 1. Executive verdict

Decima is a **genuinely well-engineered prototype** whose implementation is, in most
respects, ahead of its own reputation. The parts that can be mechanically verified verify.
The primitives are real, the tests are real and reproduce live, and the documentation is
unusually candid about what is deferred.

The gap between claim and reality is **not** in the code's correctness — it is in three
narrower places: (a) a small set of **trust-model assumptions** that hold only under a
deployment posture the default does not use; (b) **performance/scaling** shortcuts that are
fine for a demo and will bite a long-lived daemon; and (c) **documentation integrity drift**
(stale counts, a "released" tag that was never cut, orphaned evidence hashes) produced by an
automated multi-cycle build outrunning its own frozen docs.

**One-line summary:** the engineering is real and the honesty is above-average; the risks
are concentrated in the identity/custody model, in fold-performance at scale, and in
reconciling the paper trail — not in the machinery itself.

### What was verified live (not just read)

- `pip install -e ".[dev]"` clean, `import decima` OK.
- `pytest -m "not adversarial" --cov=decima --cov-fail-under=78` → **577 passed, 25 skipped,
  coverage 79.88%**, floor reached.
- `pytest -m adversarial` → **49 passed** (real Linux namespaces engaged).
- Combined **626 passed / 25 skipped** — matches README byte-for-byte.
- `mypy` → **no issues in 172 files**; `ruff format --check` + `ruff check` → clean.
- `scripts/check_release_metadata.py -v` → OK: 651 tests, 13 spec-cases / 9 files, version
  `0.3.1.dev0` consistent.

---

## 2. What is actually built (grounded)

- **Weft** — append-only SQLite log, no `UPDATE`/`DELETE` in the module, total order via
  `AUTOINCREMENT`, content id = BLAKE2b-128 over canonical NFC-JSON with domain separation.
  All SQL parameterized (no injection surface).
- **Four verbs** — `ASSERT/RETRACT/INVOKE/ATTEST` are the whole instruction set; anything
  else is rejected at `append`.
- **Weave / fold** — a real CRDT suite (LWW/MV/OR-set/counter/RGA/map/append-log/adjudicated)
  ordered on `(lamport, event_id)` so folds are arrival-order independent.
- **Ed25519** — genuine libsodium/PyNaCl signing, not a stub; deterministic key derivation
  through a custodian seam; private key never leaves the custodian.
- **Object-capability layer** — grants are Cells; a full authorize spine (possession → exists
  → not-retracted → not-quarantined → in-envelope → grantee-match → downhill delegation →
  caveats/budget/approval/sandbox/lease) with a stable `DenialCode` vocabulary.
- **Attenuation** — only tightens (numeric bounds min-clamped); delegation walks the chain
  checking downhill + granter-held-parent + upstream-not-revoked.
- **Morta gates** — approvals are Weft events; `requires(human_approval)` floors on
  shell/financial effects; durable approval inbox.
- **Key rotation** — Keybase-style sigchain with point-in-time verification so history keeps
  verifying across rotations.
- **Durable runtime** — event-sourced; steps marked RUNNING durably *before* the effect,
  receipts keyed by idempotency key, crash-window reconciliation that classifies stranded
  work rather than blindly retrying.
- **Local API** — stdlib WSGI, loopback-only bind refused otherwise, pairing-secret auth with
  constant-time compare, unauth → 401, per-route auth levels, strict CSP with **zero**
  `innerHTML`/`eval`-class sinks in the shell JS (createElement + textContent only).
- **Worker isolation** — `python -I` child with scrubbed env, rlimits, no-new-privs,
  non-dumpable, USER+MNT+NET+PID namespaces (mandatory, fail-closed), chroot jail, digest-
  bound implementation, PID-1 reaper; seccomp-BPF denylist on aarch64.
- **Tests** — 651 collected / 626 run, golden byte-equality against the frozen `heartbeat/`
  oracle, an AST import-boundary guard on the kernel, adversarial isolation tests with
  explicit anti-vacuity patch-anchor guards, and four real multi-subsystem e2e scenarios
  (crash-recovery, revocation, backup-restore, approval-gating).

---

## 3. Risk register (ranked)

Severity reflects impact **in the intended single-user loopback deployment**; several would
rise sharply if Decima were ever exposed beyond loopback or run multi-tenant.

| # | Risk | Severity | Where |
|---|------|----------|-------|
| R1 | **Single master seed derives every principal's private key.** `DerivedKeyStore.has()` is always-True; every key is `blake2b(master+pid)`. Whoever reads `<db>.keys` (or runs in-process) can sign/approve/invoke as *any* principal — collapsing ocap, Morta approval, and per-agent identity to one secret. | High (structural) | `heartbeat/decima/kernel.py:38-46`, `keystore.py:68-93` |
| R2 | **Authorization is not enforced at the trusted boundary.** The ocap gate lives in `heartbeat/decima/kernel.invoke`; `decima/kernel/weft.append` writes a signed INVOKE for any author with no capability check. Anything holding a `Weft` handle is inside the TCB, and the import-boundary guard scans only `decima/kernel/`. | High | `weft.py:231`; gate at `heartbeat/decima/kernel.py:138` |
| R3 | **Approval authority is not caller-authenticated.** `approve()` records an approval as the human principal with no proof the caller is the human; combined with R1, any in-process component can self-clear a Morta gate. | High (with R1) | `kernel.py:336-348` |
| R4 | **"Local" live-provider base URL is never confined to loopback.** Any `DECIMA_LIVE_BASE_URL` is accepted for kind=`local` and POSTed to unchecked; the privacy guarantee for `sensitive`-classed tasks rests on a registry flag, not on the transport. SSRF-shaped / data-egress claim gap. | High | `models_setup.py:360-386`, `:105-134` |
| R5 | **Repeated full-log folds in hot paths.** `drive_plan_once` folds 4+ times per pass plus once per ready step; `spend_ledger` re-scans all receipts each call. `fold_incremental`/checkpoints exist but the runtime does not use them. ≈ O(events × steps) per command on a long-lived Weft. | Medium→High at scale | `weave.py:165`, `execution.py:114,130-136`, `budgets.py:81-110` |
| R6 | **Default anti-replay is a no-op across calls/restart.** `run_worker`/`execute_prepared_run` mint a fresh in-memory `LeaseGuard` each call; a lease can be replayed after restart within its expiry window unless a shared guard is threaded. Durable single-use exists only at the capability layer (`max_uses`), which the worker path does not consult. | Medium | `execution.py:933`, `workspace.py:415`, `lease.py:79` |
| R7 | **seccomp absent on x86_64, denylist-only on aarch64.** Off-aarch64 the syscall filter is gone entirely; even on aarch64 it is default-allow over ~30 entries (no `socket`/`open`/`execve` deny). Honestly disclosed, but "seccomp on aarch64" overstates the cross-arch posture. | Medium | `execution.py:128-133,615-662` |
| R8 | **Budget accounting is ephemeral.** `spent` is an in-memory dict, not folded from the Weft; a restart resets spend to 0, so a `budget` caveat is re-spendable each process lifetime. (Contrast `max_uses`, durably folded.) | Medium | `heartbeat/decima/kernel.py:124,185` |
| R9 | **64-bit principal identifiers.** The self-certifying `keyed_pid(pubkey)==pid` binding has only 64-bit second-preimage strength while event/cell ids are 128-bit — the security-critical key↔identity binding is grindable far below the crypto it protects. | Medium | `crypto.py:62,89,112-128` |
| R10 | **Sessions never expire; logins unthrottled.** `SessionStore` grows per login with no TTL/cap and no rate-limit/lockout. Low impact under loopback + 256-bit secret, but unbounded on a long-running daemon. | Low→Medium | `auth.py:71,76-89` |
| R11 | **Snapshot/incremental-fold trust is optional.** `verify_root` exists but is optional; a caller that omits it accepts a tampered checkpoint base. Safe path exists; default is unchecked. | Low→Medium | `snapshot.py:181`, `weave.py:236` |
| R12 | **Documentation integrity drift.** README says 626 (correct, live-verified); CHANGELOG/release notes say 616; RELEASE-READINESS's own audit-conditions say 510 and contradict its matrix. No `v0.3.0` git tag exists though CHANGELOG says "Released." The frozen-baseline commit `3aa70d7` cited across the release evidence is a dangling object after a history rewrite. | Medium (trust) | `README.md:31` vs `RELEASE-READINESS.md:28,100`, `CHANGELOG.md:65,145` |
| R13 | **Minor correctness/quality.** In-jail runner `path.replace("..","__")` mangles legitimate filenames; `_scan_executable`/`_sanitize_echo` are denylists (brittle if a field ever reaches a non-text sink); WSGI/loopback/pairing-secret helpers duplicated verbatim between `app.py`/`serve.py`/`server.py`. | Low | `workspace.py:64`, `plan_service.py:132-144`, `app.py:298-323`↔`serve.py:233-258` |

---

## 4. The Ultraplan (prioritized, phased)

Ordering principle: **close the honesty gaps first (cheap, high trust-yield), then harden the
identity/custody model (highest structural risk), then fix scaling, then polish.** Each item
notes rough effort and the risk it retires.

### Phase 0 — Reconcile the paper trail (days; do first)
The code is more trustworthy than the docs currently imply; fix that before anything else.

- **P0.1** Make one script the single source of test/spec counts and have every doc read from
  it. Correct CHANGELOG (616→626), RELEASE-READINESS matrix + audit conditions (510/616→626),
  release notes. Wire `scripts/check_release_metadata.py` to fail on *any* doc that hard-codes
  a divergent number. *Retires R12. ~0.5 day.*
- **P0.2** Resolve the release-state fiction: either cut the `v0.3.0` tag the CHANGELOG claims,
  or change "Released" to "candidate, untagged" and move tagging into the open-conditions list
  consistently. *R12.*
- **P0.3** Re-anchor or remove the dangling `3aa70d7` freeze references; if byte-equality was
  proven against a now-rewritten commit, re-prove against a reachable one and cite that.
  *R12.*
- **P0.4** Resolve the `license = "Proprietary"` vs open-reference tension in `pyproject.toml`
  (operator decision I-1) — a one-line answer either way, but leaving it ambiguous undercuts
  the "reference" framing. *Trust.*

### Phase 1 — Harden identity & custody (the highest structural risk)
This is where "secure by shape" currently rests on a deployment assumption the default breaks.

- **P1.1** Make the **split-custody** (`DirectoryKeyStore`/`mint_keyed`) path the *documented
  and tested default posture*, and state plainly in SECURITY.md that the single-master-seed
  `DerivedKeyStore` is a **dev-only** convenience that collapses the trust model. Add a startup
  warning when the derived keystore is in use. *Retires the practical edge of R1/R3.*
- **P1.2** Authenticate the approver: require a possession proof (signature over the exact
  approval request at its causal frontier) for `approve()`/`approve_invocation()`, mirroring
  the invoke proof — so an approval cannot be minted by any in-process code even under a shared
  keystore. *R3.*
- **P1.3** Move (or mirror) the effect-gating spine so a raw `Weft.append` of an INVOKE cannot
  bypass authorization — e.g. an append-time guard that rejects INVOKE events lacking a valid
  authorization proof, and extend the import-boundary/TCB test to cover the *enforcing* code
  wherever it lives, not just `decima/kernel/`. *R2.*
- **P1.4** Widen the key↔identity binding to 128-bit (`digest_size=16`) to match event/cell id
  strength; migrate `keyed_pid`/`mint`. *R9.*

### Phase 2 — Durable enforcement of the resource invariants
Budgets and anti-replay currently trust process memory; fold them.

- **P2.1** Fold `budget` spend from the Weft (like `max_uses`) so a restart cannot reset spend.
  *R8.*
- **P2.2** Give the worker path a durable single-use check: consult folded `max_uses`, or
  persist consumed-lease markers, so a lease cannot be replayed across restart. Thread a shared
  `LeaseGuard` by default rather than minting a fresh one per call. *R6.*
- **P2.3** Make snapshot/incremental-fold `verify_root` the default, opt-*out* not opt-in.
  *R11.*

### Phase 3 — Confinement parity & egress mediation
- **P3.1** Confine the "local" provider: validate `DECIMA_LIVE_BASE_URL` resolves to loopback
  for kind=`local`, and refuse to route a `sensitive`-classed task to any transport not proven
  local. *R4.*
- **P3.2** Close the seccomp cross-arch gap honestly: either add an x86_64 filter (tighten to a
  small allowlist rather than a denylist) or make the *containment report and prose* uniformly
  state that syscall filtering is aarch64-only defense-in-depth, and have workspace/PURE refuse
  or loudly warn on unfiltered arches for network-permitted profiles. *R7.*
- **P3.3** Add an egress seam for the PROVIDER profile before it can be selected in anger, or
  gate the profile off entirely until one exists. *R4/R7.*

### Phase 4 — Scaling (before "daily driver" is literally true)
- **P4.1** Route the runtime through checkpoints + `fold_incremental` instead of folding from
  genesis on hot paths; cache the `spend_ledger` incrementally. *R5.*
- **P4.2** Replace the O(n) linear cell-class scans (`receipt_for_idempotency_key`,
  `_active_leases_for_step`, `_has_receipt`) with indexed lookups. *R5.*
- **P4.3** Add session TTL/cap + a login attempt limiter. *R10.*
- **P4.4** Auto-drain the workspace helper-thread result so a completed jailed run is reconciled
  without waiting for a follow-up request. *(runtime finding.)*

### Phase 5 — Polish
- **P5.1** Fix the in-jail `replace("..","__")` filename mangling (use the same `_safe_path`
  discipline). *R13.*
- **P5.2** De-duplicate the WSGI/loopback/pairing-secret helpers into a shared `wsgi_util`.
  *R13.*
- **P5.3** Decompose the two 1,075-line lane services and `execution.sync_agent_statuses`; add
  unit tests around `_rank_key`'s "unset soft term = 0" invariants so they rest on tests, not
  comments. *quality.*
- **P5.4** Remove the dev-host `TESTENV` default path leak in `tests/browser/serverManager.js`.

---

## 5. Bottom line

Decima earns most of its claims. The kernel primitives, the durable runtime, the loopback API,
the shell's XSS posture, and the test suite are real and reproduce under scrutiny — that is the
hard 80%. The remaining work is not "make it work"; it is **"make the trust model true by
default rather than by deployment assumption"** (Phase 1), **"fold the invariants you currently
keep in RAM"** (Phase 2), **"reach confinement parity across architectures and egress"**
(Phase 3), and **"stop folding from genesis before you call it a daily driver"** (Phase 4) —
after spending a day making the documentation as honest as the code already is (Phase 0).
