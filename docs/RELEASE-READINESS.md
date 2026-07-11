# Decima 0.3 — release readiness

_Updated 2026-07-12. Evidence-based release-decision matrix for the `0.3.0` candidate._

Candidate commit: HEAD of `main` (`78adb84` or descendant). Independent audit verdict:
**APPROVE WITH DOCUMENTED LIMITATIONS** — no Blocker / no High finding survives verification
(`docs/release-evidence/audit/0.3-audit.md`, authoritative). This file is the honest,
per-item accounting behind that verdict: every row names the exact test/command, the
environment it ran in, the evidence path, and any limitation. It does not merely tick boxes.

## Status legend

| Status | Meaning |
|---|---|
| **verified** | Re-run on this host in the normal gate; passes with no credential or container. |
| **verified-in-container** | Proven in a systemd-enabled Docker container because the host systemd is degraded; reproducible per the recorded steps. |
| **BLOCKED-pending-operator** | Authored + evidenced, but the final pass needs an operator action (a real credential) that cannot exist on this host. |
| **documented-limitation** | Acceptable-for-0.3 gap, disclosed at the top of `README.md` and here; not a defect to fix before tag. |

## Environment (measured on this host)

AArch64 (arm64) Linux 6.6.10 · Python 3.11.2 · pytest 9.1.1 (from `$TESTENV`) · PyNaCl 1.6.2 ·
ruff 0.15.20 · Node v22.23.0, `@playwright/test` ^1.49, bundled `chromium_headless_shell-1228`
(arm64, `--no-sandbox`) · Docker 20.10.24 · setuptools 66.1.1 build backend
(`python3 -m build` not installed → wheel via `pip wheel --no-build-isolation`, sdist via the
same `setuptools.build_meta` backend). Gate invocation:
`PYTHONPATH="$TESTENV:$PWD" python3 -m pytest`.

## Release-decision matrix

| Definition-of-Success item | Status | Test / command | Environment | Evidence | Limitation |
|---|---|---|---|---|---|
| Full gate green on the candidate | **verified** | `python3 -m pytest -q` → **323 passed, 5 skipped** (27.8s; the 5 skips are the `live_provider` suite) | host, `$TESTENV` | audit §3; re-run for this release | Must be re-run once more on the exact tag commit (audit condition 3). |
| Weft is the sole canonical store | **verified** | import-boundary guard `pytest tests/architecture` (19 passed); only non-kernel `sqlite3` is a read-only `SELECT` in `backup/service.py` | host | audit §7; `tests/architecture/` | — |
| Projections rebuild from the Weft | **verified** | `tests/projections/test_rebuild_equals_incremental.py`, `tests/api/test_projection_rebuild_preserves_state.py` | host | in the 323 | — |
| Plans / jobs survive restart | **verified** | `tests/e2e/test_crash_recovery.py`, `tests/runtime/test_supervisor.py` | host | in the 323 | — |
| Sensitive effects require bound approval | **verified** | `tests/e2e/test_approval_gating.py` (deny → approve-once → consumed → reuse-fails); approve route is `REAUTH` | host | in the 323; audit §7 | — |
| Revoked authority stops future use | **verified** | `tests/e2e/test_revocation.py` (cascade; receipts preserved) | host | in the 323 | — |
| Worker isolation holds | **verified** | `tests/adversarial/test_worker_isolation.py` 12/12; chroot jail, no net, no creds | host (aarch64) | audit §3, §7 | Enforced-subset only; real OS isolation depth depends on host capability (documented honestly by the worker). |
| Models never authorize | **verified** | `tests/models`, `tests/api/test_no_arbitrary_python.py`; `models/providers.py` exposes no `.execute()/.invoke()/.authorize()`; retrieved segments carried `instruction_eligible=False` | host | audit §7 (Invariant 4/5) | — |
| Kernel / protocol frozen | **verified** | `git diff --quiet` `heartbeat/` vs `3aa70d7`, `decima/kernel/` + `protocol/` vs `29bfe9a` → byte-identical | host | audit §1 | — |
| Conformance / golden fixtures | **verified** | `pytest tests/kernel/test_conformance.py` (canonical-bytes / event-id / fold-state-root / tamper-detect) | host | in the 323 | — |
| Adversarial + property suite | **verified** | `pytest tests/adversarial tests/property` → 35 passed | host | audit §3 | — |
| Lint clean | **verified** | `ruff check decima/ tests/` → all checks passed | host | audit §3 | Kernel is a verbatim reference copy, deliberately excluded from ruff/mypy (Stage-2 follow-up). |
| Wheel ships + serves the frontend from an isolated install | **verified** | `pip wheel . --no-deps --no-build-isolation`; installed into a `--target` OUTSIDE the repo; `FRONTEND_DIR` resolved under the target; `GET /` → 200, unauth `GET /api/v1/tasks` → 401, `GET /api/v1/health` → 200, CSP + nosniff present; 16 frontend files ship | host | audit §3; `docs/releases/0.3.0.md` (digests) | — |
| Clean install + first-run + fault matrix | **verified** | `pytest tests/install/`; `tests.install.rehearsal_core` → 64/64 ok (first-run, perms keys `0700`/seed `0600`, Shell 200 / 401 / CSP, representative data, fault matrix) | host, clean-room dir | `docs/release-evidence/install/rehearsal-summary.json` | First-run + fault mechanics proven in-process; systemd-manager mechanics see next row. |
| Service install / restart / reboot lifecycle | **verified-in-container** | `tests/install/rehearse_clean_install.sh`: `debian:bookworm-slim` + systemd PID 1 + unprivileged `operator`; `pip install .` + `deploy/install.sh`; enable → active → 200/401/CSP → restart → container reboot → service returns | systemd Docker container | `docs/release-evidence/install/docker-rehearsal-steps.json`, `container-rehearsal-summary.json` | Host systemd is degraded / no user instance → proven in a container, not on the host (documented concession). |
| Backup / restore reproduce usable state | **verified** | `pytest tests/e2e/test_backup_restore.py tests/ops` (16 passed); state-root round-trip `792c9f82…` create == restore; backup **excludes keys**; projections rebuilt; doctor ok | host | audit §5; `docs/release-evidence/install/` | — |
| Uninstall preserves data by default | **verified-in-container** | container rehearsal: uninstall preserves data unless `--purge` | systemd Docker container | `docs/release-evidence/install/docker-rehearsal-steps.json` | — |
| Doctor: no critical failure | **verified** | `decima-doctor --base <provisioned>` → exit 0, overall **warn** (only `checkpoint-missing`); keys `0700`, seed `0600` | host | `docs/release-evidence/install/doctor-report.json`, `doctor-export.json` | — |
| Rendered Shell A–C walkthrough | **verified** (as-shipped scope) | `npx playwright test` → 8/8 headless Chromium against the real backend + Shell on an ephemeral loopback port | host, Playwright chromium | audit §4; `docs/release-evidence/browser/README.md` | Qualifies what the Shell renders; the aspirational A/C are library-only (see documented-limitation rows). |
| Hostile approval-chrome inert | **verified** | `security_chrome.spec.js`: fake Approve buttons / banners / inline-JS are inert; only the real reauth-gated component approves | host, Playwright chromium | audit §4, §7 | — |
| Unauth API → 401 · strict CSP · no-script import | **verified** | `security_cross.spec.js` (×4): unauth `/api/*` → 401; strict CSP (`script-src 'self'`, no `unsafe-*`, `object-src 'none'`, `base-uri 'none'`); imported HTML/MD cannot execute; clean console/network | host, Playwright chromium | audit §4 | — |
| a11y | **verified** (smoke only) | `a11y.spec.js`: keyboard nav, named controls, text status, landmarks | host, Playwright chromium | audit §4 | Explicitly a smoke check, **not** a WCAG audit. |
| Model-provider bounded qualification (non-live) | **verified** | `pytest tests/live` — offline suite passes; connectivity/routing with reason codes + sensitivity class, budget block, privacy (local-only never reaches cloud), failure/fallback, **secret redaction (0 leaks, secret not in repr)** | host | `docs/release-evidence/models/offline-qualification.json` | — |
| Live model-provider smoke (one real provider) | **BLOCKED-pending-operator** | `DECIMA_LIVE_PROVIDER/…/DECIMA_LIVE_API_KEY … pytest -m live_provider tests/live` (names only; no values in repo) | operator host + real credential | emits `live-qualification.json` when run | No credential exists here — the 5 live tests skip cleanly; the operator MUST run this with a real key before the tag (audit condition 2 / I-2). |

## Documented limitations (acceptable for 0.3; disclosed at the top of README)

| Limitation | Detail | Where enforced / evidenced |
|---|---|---|
| Source-grounded Q&A + clickable citations not in the Shell | `decima/capabilities/qa.py` is a real, unit-tested library module with **no route and no screen**; the Shell surfaces knowledge as trust-zoned notes + durable provenance instead | audit §8 (route table + import graph); README "0.3 Shell scope" |
| Isolated coding workspace not in the Shell | `decima/capabilities/workspace.py` is unit-tested but imported only by its test; **no route, no surface**; worker isolation itself is proven at the worker layer | audit §8; `tests/adversarial/test_worker_isolation.py` |
| No model-generated plan / agent forest from the UI (Scenario B) | The Shell's plan lifecycle is **manual start/pause**; the bounded agent used in tests is a canonical-kernel harness precondition, not a UI-spawned forest | audit §8; `docs/release-evidence/browser/README.md` |
| Single-threaded Shell server | Serves single-threaded so all Weft access stays on the one `sqlite3` thread; `/stream` returns a finite snapshot so requests serialize but never deadlock. Kernel fix (`weft.py` `check_same_thread=False` + lock) filed for **0.3.1** | audit §7 (Low/acceptable); `docs/release-evidence/browser/known-issues.md` |
| Service lifecycle proven in a container, not on the host | Host systemd degraded / no user instance | `docs/release-evidence/install/docker-rehearsal-steps.json` |
| Deterministic provider is the default | A live provider is opt-in and needs an operator credential | `docs/release-evidence/models/README.md` |

## Follow-ups (do not block the tag)

- **Stage-2 kernel cleanup**: the 13 extracted kernel modules are verbatim reference copies
  (frozen, proven equivalent) excluded from ruff/mypy; annotating them to the strict bar is
  tracked in `docs/architecture/kernel-extraction.md`.
- **Single-threaded-server root fix** (`weft.py` `check_same_thread=False` + lock) — 0.3.1
  (`docs/release-evidence/browser/known-issues.md`, audit L-2).
- **License metadata (I-1)**: `pyproject.toml` declares `license = "Proprietary"` /
  `Private :: Do Not Upload`. Not release-blocking for a non-published candidate; **an
  operator decision to reconcile before any publish** — do not relicense as part of tagging.
- The handoff §3.2 deferral list (financial automation, live brokerage, full browser
  automation, mobile, replication, the Rust port) is intentionally out of 0.3.

## Conditions the audit attaches to the tag

1. Land the three Medium doc fixes — test count (**done**: 323/5), Shell run command
   (**done in code**: `python3 -m decima.shell.serve` has a real `__main__`), and the
   prominent "0.3 Shell scope" limitation (**done**: README + this file).
2. Operator runs the live-provider smoke with a real credential and records
   `docs/release-evidence/models/live-qualification.json`.
3. Re-run the full gate on the exact tag commit; confirm still 323/5, `heartbeat/` frozen,
   tree clean; then apply the `v0.3.0` tag. Also complete the manual trust-boundary / UI review.
