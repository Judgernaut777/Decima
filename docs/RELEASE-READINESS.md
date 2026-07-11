# Decima 0.3 — release readiness

_Updated 2026-07-11. Honest accounting of what is verified vs. what a human must confirm
before tagging `0.3`._

## Verified (automated, green on `main`)

| Definition-of-Success item | Evidence |
|---|---|
| The Weft remains the sole canonical store | every mutation path asserts Cells via the kernel; API/Shell write nothing directly (tests/api, tests/shell) |
| Projections rebuild from the Weft | `tests/projections/test_rebuild_equals_incremental.py`, `tests/api/test_projection_rebuild_preserves_state.py` |
| Plans/jobs survive restart | `tests/e2e/test_crash_recovery.py`, `tests/runtime/test_supervisor.py` |
| Sensitive effects require approval, bound to the invocation | `tests/e2e/test_approval_gating.py` (deny→approve-once→consumed→reuse-fails) |
| Revoked authority stops future use | `tests/e2e/test_revocation.py` (cascade, receipts preserved) |
| Backup + restore work | `tests/e2e/test_backup_restore.py` (state_root round-trip), `tests/ops/` |
| Worker escape tests pass | `tests/adversarial/test_worker_isolation.py` (on aarch64) |
| Models never authorize | `tests/models`, `tests/api/test_no_arbitrary_python.py` |
| Kernel boundary preserved | `tests/architecture/` (import guard) |
| Conformance / golden fixtures pass | `tests/kernel/test_conformance.py` |
| The Shell launches + gates | live: `GET /` → 200 + strict CSP; `GET /api/...` unauth → 401 |

**307 tests green.** `heartbeat/` unmodified across the whole milestone.

## Requires human-in-the-loop confirmation before tagging 0.3

These cannot be verified in this headless environment and are the remaining acceptance gate:

1. **Browser walkthrough of scenarios A–C** — knowledge Q&A, project planning, and a coding
   task driven *through the Shell UI* in a real browser (the API-level equivalents pass; the
   rendered-UI flows and the trusted/untrusted visual separation need human eyes).
2. **Clean-install rehearsal** — run `deploy/install/install.sh` + the systemd unit on a
   fresh Linux user account; confirm reboot starts the service and the Shell is reachable.
3. **First-run flow** end to end (identity, data dir, default budgets, first workspace).
4. **A real model provider** wired via config (the deterministic provider is the tested
   default; a live local/cloud provider needs the operator's key + a smoke call).

## Known deferred / follow-on (not release-blocking, documented)

- **Stage-2 kernel cleanup**: the 13 extracted kernel modules are verbatim reference copies
  (frozen, proven equivalent) and are excluded from ruff/mypy; annotating them to the strict
  bar and dropping the exclusion is tracked in `docs/architecture/kernel-extraction.md`.
- Real OS namespace isolation depth depends on host capability; the worker documents the
  enforced subset honestly (no OS-sandbox claim beyond what it enforces).
- Everything on the handoff §3.2 deferral list (financial automation, live brokerage, full
  browser automation, mobile, replication, the Rust port) is intentionally out of 0.3.
