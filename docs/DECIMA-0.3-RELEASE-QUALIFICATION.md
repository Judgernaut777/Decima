# Decima 0.3 — Final Release-Qualification Handoff

_Governing charter for taking Decima from code-complete (`29bfe9a`) to a justified,
reproducible 0.3 release candidate — then **stopping before the tag** unless every release
gate is satisfied. This is release qualification, **not** feature development._

## Do NOT

Add new integrations, redesign the architecture, expand the capability surface, or begin the
Rust port. No new capability domains, UI screens unrelated to acceptance, new providers,
protocol redesign, kernel cleanup for aesthetics, distributed sync, or public remote deploy.

## Global invariants (every agent preserves these; STOP and report if one must change)

- `heartbeat/` byte-for-byte unchanged
- Weft is the only canonical durable state; all mutations route through kernel/application commands
- no direct projection mutation; no model-held authority; no approval bypass
- no weakening of worker isolation; no loosening of CSP or sanitizer behavior
- no secrets committed to the repo
- all existing 307 tests pass; conformance fixtures pass; kernel import-boundary guard passes; Ruff passes

## Environment ground truth (measured 2026-07-12 on this AArch64 host)

- node v22 + npx present; npm + pypi registries reachable → Playwright installable
- `/usr/bin/chromium` raw launch **hangs** — use Playwright's own bundled chromium; if even
  that can't launch, author the runnable suite and record BLOCKED-execution with the exact
  error + the operator's reproduce command (authoring a runnable harness is the deliverable)
- docker 20.10 daemon usable → containerized clean-install can execute
- host systemd is `degraded` with no user instance → prove service lifecycle in a
  systemd-enabled container, not on the host
- **no live provider credential is available** → the live provider *call* is operator-gated;
  author + run everything around it, never fabricate a key
- pytest runs via: `PYTHONPATH="$TESTENV:$PWD" python3 -m pytest` where
  `TESTENV=/tmp/claude-1000/-home-mini/56d98ee8-eef4-4e7a-845d-004995e016ad/scratchpad/testenv`
- candidate commit: `29bfe9a`; working tree clean; `heartbeat/` frozen at `3aa70d7`

## Workstreams, ownership, completion gates

### WS1 — Headless rendered-Shell qualification (branch `qual/browser`)
Own: `tests/browser/`, `tests/end_to_end/` (repo uses `tests/e2e/` — put new browser specs in
`tests/browser/`), Playwright config, `docs/release-evidence/browser/`. Narrow product fixes
only in `decima/shell/frontend/` and the Shell backend. Do NOT touch `decima/kernel/`,
`heartbeat/`, protocol fixtures, runtime/worker semantics. Drive scenarios A (knowledge Q&A
with source citations, durability across refresh/restart/rebuild), B (project → plan → execute
→ pause/resume → agent inspector → terminate), C (isolated coding workspace, diff + test
artifacts, no push/no external credential, no cross-workspace read), a hostile approval-chrome
fixture (fake Approve buttons/banners/inline-JS/handlers must be inert; only the real trusted
component can approve), and a11y smoke. Assertions: no uncaught console errors, no failed
same-origin requests, no citation to a nonexistent segment, imported HTML/MD can't execute,
CSP present, unauth API → 401. Use the deterministic provider. Never insert state directly
into SQLite/projections — drive the UI (supported setup commands only for out-of-scenario
preconditions). Complete when A–C pass through Chromium, failures produce traces, the suite
runs from a clean checkout, no canonical-store injection, and all normal gates pass.

### WS2 — Clean install + first-run + backup/restore (branch `qual/install`)
Own: `deploy/`, `tests/install/`, `tests/operations/`, `docs/release-evidence/install/`,
`docs/operations/` (except `model-configuration*`, owned by WS3). Prove install from a clean
supported Linux env (systemd-enabled container preferred here) without the dev checkout/venv/
config: install → deps only in intended locations → correct data/config perms → service units
installed → Shell 200, unauth API 401, CSP present → `decima-doctor` no critical failure →
restart recovers → reboot/service-restart recovers → uninstall preserves user data unless
explicit destructive flag. First-run: identity, data location, deterministic provider, default
budgets, first workspace, defer/enable backup, finish; restart does not repeat first-run;
secrets never exposed via UI/logs/doctor; never prepopulate first-run flags in storage.
Backup/restore: representative data → backup → verify → stop → move data aside → restore →
rebuild projections → doctor → compare state roots → Shell shows records. Fault cases:
insufficient perms, dir exists, unsupported Python, occupied port, missing model config,
corrupt backup, stale PID, second install invocation — all explicit + recoverable. Record a
full environment manifest. Complete when a clean system installs, first-run completes without
manual DB editing, services recover after restart, backup/restore reproduce usable state,
uninstall preserves data by default, and documented commands exactly match tested commands.

### WS3 — Live model-provider bounded qualification (branch `qual/models`)
Own: `decima/models/`, `tests/live/`, `docs/release-evidence/models/`,
`docs/operations/model-configuration*`. Do NOT touch `decima/kernel/`, authorization/approval
semantics, `heartbeat/`. Author a manually-invoked `pytest -m live_provider` harness proving,
against one already-supported provider: connectivity/routing (records provider, model, reason
codes, estimated cost, sensitivity class; response returns through the normal abstraction),
structured proposal (schema validation; malformed output rejected/bounded-correction, never
auto-invoked), budget enforcement (small budget → deterministic block; visible in inspector),
privacy (local-only task never reaches cloud; only intended synthetic content transmitted;
no real user data), and failure/fallback (invalid credential, timeout, rate limit, unavailable
model, malformed response → surfaced, bounded, no retry storm, no authority widening, no secret
leak, attempts recorded). Secret comes from env/secret-store only — never in git/fixtures/logs/
browser/model-context; add a redaction assertion over captured logs. Normal CI must not require
live credentials. Since no credential is available here: implement the full harness, run the
non-live equivalents against the deterministic provider + the redaction unit tests, and mark
the live call BLOCKED-pending-operator-credential with the exact env var names (values omitted)
and reproduce command. Complete (author side) when the harness exists, non-live parts pass, and
all non-live tests still pass without provider access.

## Merge order (lead integrates one branch at a time, full gate after each)
1 browser → 2 install → 3 models → 4 narrow fixes → 5 audit → 6 release-prep. After frontend/
backend changes rerun browser qual; after installer/service/first-run/backup changes rerun the
clean-install rehearsal; after model changes rerun non-live tests (and the live test only with
a credential). Never resolve a shared-interface conflict by taking one branch wholesale —
preserve the committed contract, add/adjust tests first, re-run dependents.

## Release decision — tag ONLY when ALL hold
A–C pass through the rendered Shell; clean install passes on a fresh supported Linux env;
first-run completes through the real flow; services survive restart/reboot; backup/restore
reproduce usable state; one real provider passes bounded live qual; deterministic provider is
the default test path; auditor returns APPROVE or justified APPROVE-WITH-DOCUMENTED-LIMITATIONS;
no blocker/high finding remains; full gate passes on the exact candidate commit; `heartbeat/`
unchanged; tree clean; docs match reality; artifacts built + verified; manual trust-boundary/UI
review passes. If any fails: do not tag; record in `docs/RELEASE-READINESS.md`; fix narrowly;
rerun the affected lane + full gate. Post-tag only: Stage-2 kernel cleanup (0.3.1 stream).
