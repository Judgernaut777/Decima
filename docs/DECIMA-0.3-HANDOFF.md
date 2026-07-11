# Decima 0.3 — Local Daily Driver · Coding-Agent Implementation Handoff

_The governing charter for the 0.3 milestone. Phases are sequential; the architectural
invariants (§2) are non-negotiable. This document is the source of truth referenced by
`CONTRIBUTING.md` and the Epic/issue tracker._

## 1. Mission

Transform the existing Decima reference implementation into a reliable, locally hosted
application that one technical user can use every day to: converse with Decima; manage
knowledge, tasks, and projects; delegate bounded work to agents; inspect plans and agent
activity; approve or deny sensitive actions; create and review artifacts; use local or
cloud models; survive process crashes and machine restarts; and inspect the provenance
and authority behind every durable action.

The target is not a Linux replacement. Linux remains the host OS; Decima becomes the
user-facing agent operating layer on top of it.

**Decima 0.3 — Local Daily Driver** is complete when a user can install Decima on one
Linux machine, open a browser-based Shell, complete the daily workflows, reboot, resume
work, and inspect or revoke agent authority without touching Python modules or SQLite.

## 2. Architectural invariants (do not violate)

1. **The Weft remains canonical.** All durable state originates from accepted events on
   the append-only Weft. No second canonical DB for tasks, projects, agents, messages,
   approvals, plans, artifacts, settings, or capabilities. Indexes, caches, relational
   projections, UI stores, materialized views are rebuildable projections.
2. **Preserve the four-verb model.** Durable ops are represented through ASSERT, RETRACT,
   INVOKE, ATTEST. No hidden mutation commands that bypass the event model.
3. **No ambient authority.** A model/agent/worker/MCP-tool/UI component gets no authority
   merely by running inside Decima. Each effect needs: an identified principal; an
   applicable capability; a concrete invocation; authorization against current state; any
   required Morta approval; an execution receipt.
4. **Models propose; deterministic code authorizes.** No model output is itself an
   authorization decision. Models classify/summarize/plan/propose/select/generate.
   Deterministic code validates schemas, selects grants, verifies attenuation, applies
   budgets, enforces approvals, initiates effects, appends receipts.
5. **Projections are disposable.** Deleting and rebuilding any projection must not change
   canonical meaning.
6. **Untrusted code never executes in the kernel process.** The kernel may verify,
   authorize, fold, and append. It must not execute generated code, arbitrary shell,
   MCP servers, unknown provider adapters, browser automation, or user scripts.
7. **Do not port to Rust during this milestone.** Python through 0.3. The Rust port
   begins only after protocol behavior is stable, fixtures exist, the conformance suite
   is implementation-independent, and product workflows are proven.

## 3. Scope

**Required (3.1):** kernel package boundary; protocol fixtures + conformance; packaging +
CI; durable runtime supervisor; isolated local workers; capability-bound execution; Morta
approval service; persistent plans/jobs; crash recovery + idempotent execution; knowledge
+ document management; tasks + projects; artifact management; development workspace; local
+ cloud model routing; browser Shell; agent inspector; capability inspector; approval
inbox; activity + provenance timeline; backup + restore; local install + service
management; operational diagnostics.

**Explicitly deferred (3.2):** public capability marketplace; automatic Nona promotion
into production; broad financial automation; live brokerage; autonomous payment movement;
healthcare; insurance automation; tax filing; production KYC; full browser automation;
mobile-native apps; cross-device replication; cloud relay; custom Linux distribution;
Rust rewrite; arbitrary generated UIs; general social automation; dozens of new domain
modules. Existing experiments in these areas may remain but must not block or expand 0.3.

## 4. Required repository restructuring (before major product functionality)

Target structure: a top-level `decima/` package (`kernel/`, `runtime/`, `models/`,
`capabilities/`, `projections/`, `services/`, `shell/`, `cli/`), plus `protocol/`
(schemas/fixtures/conformance), `tests/` (unit/property/conformance/integration/
adversarial/end_to_end), `deploy/` (systemd/containers/install), `docs/`, and
`legacy/heartbeat/`. See `docs/architecture/trust-boundaries.md` for the current →
target module map.

**Compatibility strategy (4.2):** create new paths; move one cohesive subsystem at a
time; leave compatibility imports; run the full existing smoke suite; add tests to the
new package; update callers; remove compatibility imports only after all callers move;
preserve the old interactive Heartbeat until the new Shell reaches parity.

**Trusted-computing-base rule (4.3):** canonical serialization, event validation,
signatures/identity, Weft storage interfaces, fold semantics, capability-chain
validation, authorization decisions, approval verification, lifecycle/revocation, receipt
validation, and checkpoint verification stay in `decima/kernel`. That package must not
import model providers, HTTP clients, web framework, domain integrations, shell
execution, MCP clients, browser code, document parsers, or vector databases. An automated
architecture test fails if forbidden imports cross the boundary
(`tests/architecture/test_import_boundaries.py`).

## 5. Workstream execution order — the phases

Phases are sequential; parallel work only where explicitly identified.

- **Phase 1 — Baseline, packaging, safety rails.** Capture current behavior
  (`docs/baseline/`), module inventory, `pyproject.toml`, ruff/mypy/pytest, CI, repo
  policies (SECURITY.md, migration/protocol policy). Acceptance: clean clone installs
  with one command; smoke preserved; CI passes; new kernel code lint/type-clean;
  inventory covers every module; no domain feature work added.
- **Phase 2 — Extract the kernel.** Canonical codec (versioned, compatible with stored
  events, golden fixtures); identity/signatures (fail closed, no key bytes in logs/model
  context); split event validation; Weft interfaces (transactional append, integrity,
  schema version, migrations, read-only mode); pure deterministic fold (no provider/clock/
  fs/global mutable state); capabilities + authorization with machine-readable reason
  codes; Morta approvals bound to invocation digest + exact effect/target/args/cost/
  expiry/use-count/policy; lifecycle + receipts; kernel architecture tests. Stop
  condition: no nondeterminism in fold/authorization between clean runs.
- **Phase 3 — Protocol fixtures + adversarial tests.** Golden event/fold/capability
  fixtures; property tests (fold determinism, idempotent duplicate delivery, arrival-
  order independence, retraction stability, attenuation monotonicity, revocation
  invalidates descendants, invalid signatures fail, changed args invalidate proof, replay
  executes nothing, projection rebuild preserves state, crash-before-receipt fabricates
  nothing); hostile-input tests; signed local checkpoints.
- **Phase 4 — Durable runtime supervisor.** Agent/Plan/PlanStep Cells; scheduler; durable
  leases; crash recovery; cancellation + termination propagation; budgets as durable
  state transitions. Acceptance: 3-step plan survives restart; completed steps not
  repeated; inconclusive receipts → reconciliation; terminated agent gets no lease;
  budget exhaustion blocks; pause/resume works; scheduler testable without a live model.
- **Phase 5 — Isolate workers and effects.** Versioned worker IPC (no raw keys); worker
  profiles (pure / workspace / provider); Linux containment (unprivileged user, rlimits,
  namespaces, no docker/ssh sockets, no inherited creds); immutable digest-bound handler
  registration; adversarial escape tests. Stop condition: no live email/push/deploy/
  financial effects until containment + lease tests pass.
- **Phase 6 — Model routing.** Provider interface; model registry; routing policy
  (recorded decisions); model context ≠ authority (no secrets/keys/db handles/signing);
  structured proposal validation; deterministic fallback provider (tests don't need paid
  APIs); token + cost accounting with pre/post budget checks.
- **Phase 7 — Core projections + application services.** Projection engine
  (incremental + full rebuild, versioned, deterministic); knowledge/tasks/projects/agent/
  approval/activity projections; artifact service (bytes outside event body, digest
  verify); document ingestion preserving claim→source provenance.
- **Phase 8 — Trusted Shell backend API.** Versioned local HTTP API w/ streaming;
  loopback-bound local auth (HTTP-only cookies, CSRF, reauth for high-risk); command
  service translating to explicit ops → Weft events; streaming; per-endpoint
  authorization. Every durable mutation produces accepted Weft events; no endpoint
  executes arbitrary Python.
- **Phase 9 — Shell frontend.** Conventional (not agent-generated) web frontend; trusted
  approval/navigation chrome. Screens: Conversation, Today, Projects, Knowledge, Agents,
  Plans, Approval inbox, Capability inspector, Activity timeline, Settings. Never render
  arbitrary imported HTML; sanitize Markdown; no JS from artifacts; visually separate
  untrusted / model-generated / trusted-system / human-approval content; approval actions
  only in trusted components; no "always allow everything from this agent".
- **Phase 10 — Daily-driver capabilities.** Knowledge (notes, import, source-grounded
  Q&A, horizon-scoped); tasks/projects; isolated dev workspace (edit/test/diff, no
  network/push/deploy by default; push/PR deferred until containment proven; local coding
  + diff review required); restricted local filesystem (canonical paths, no traversal/
  symlink escape, explicit roots, never whole home); model capability.
- **Phase 11 — Reconciliation + failure handling.** Effect state machine (PROPOSED →
  AUTHORIZED → DISPATCHED → SUCCEEDED/FAILED/UNKNOWN → RECONCILING/SUPERSEDED/
  COMPENSATED); declared idempotency strategy per effect; reconciler for UNKNOWN effects;
  surface failures in the Shell (not just logs).
- **Phase 12 — Backup, restore, diagnostics.** Data layout under
  `~/.local/share/decima/`; `decima-backup` (Weft/artifacts/checkpoints/config/public
  identities; projections omittable); `decima-restore` (verify → rollback copy → restore
  → verify integrity → rebuild → verify state root → restart); `decima-doctor`; scrubbed
  diagnostic export (no secrets/keys/private docs).
- **Phase 13 — Installation + local services.** `make install-dev`/`test`/`run`;
  `deploy/install/install.sh`; service separation (decima-kernel/runtime/api/worker);
  systemd hardening (NoNewPrivileges, PrivateTmp, restricted paths, rlimits, restart);
  first-run flow (identity, data dir, one model, budgets, workspace, backup, security
  defaults, root capability relationships).

## 14. End-to-end release scenarios (all must pass before tagging)

- **A — Knowledge question:** import 3 docs → index → ask → source-linked answer →
  recorded call → restart preserves → delete + rebuild projections → same records.
- **B — Project planning:** create project → propose plan → accept → durable step Cells →
  agents get bounded tasks → Shell shows hierarchy → pause (no new jobs) → resume.
- **C — Coding task:** grant one workspace → request change → agent+plan → workspace
  worker edits + tests → diff + test artifacts → review → no push without separate
  capability + approval → worker can't leave workspace.
- **D — Approval gating:** gated effect → APPROVAL_REQUIRED → shown with exact target +
  bounded params → deny → no effect + deterministic denial (durable). Then: approve once →
  exact invocation succeeds → approval consumed → reuse fails.
- **E — Crash recovery:** multi-step plan; finish step 1; begin step 2; kill runtime;
  restart → step 1 not repeated → step 2 classified from lease + receipts → resume safely
  → interruption visible in timeline.
- **F — Revocation:** agent with fs capability begins plan → revoke → pending invocations
  fail → new leases exclude it → descendant grants ineffective → completed receipts
  remain → shown in capability inspector.
- **G — Backup + restore:** create notes/project/tasks/artifacts → backup → clean install
  → restore → rebuild → compare state roots → verify artifacts → usable Shell.

## 15. Release-gate test matrix

Packaging (clean clone installs+builds) · Ruff format · Ruff lint · Types (kernel +
new runtime strict) · Unit · Property (security invariants) · Conformance (golden
fixtures) · Integration (no live creds) · Worker isolation (adversarial escape) ·
Recovery (crash-restart) · Projection rebuild (matches incremental) · Backup (full
restore) · UI (workflows without CLI) · Security (no known critical/high) · Docs
(install/recovery/architecture current).

## 16. Required documentation

`docs/architecture/` (system-overview, trust-boundaries, process-model, data-flow,
authority-flow) · `docs/protocol/` (event-format, capability-validation, approvals,
receipts, checkpoints) · `docs/operations/` (installation, configuration, backup-restore,
diagnostics, recovery, upgrading) · `docs/development/` (setup, testing,
adding-projections, adding-capabilities, release-process) · `docs/product/` (user-guide,
approvals, agents, privacy). Distinguish implemented / experimental / stubbed / planned /
production-disabled. Do not describe policy checks as OS sandboxing unless actual
containment enforces them.

## 17. Agent coordination rules

One **lead agent** owns architectural consistency, ordering, interface approval,
integration, release gates, docs. Worker-agent roles: kernel, runtime, containment,
projection, backend, frontend, operations. Before each task specify owned paths, allowed
shared interfaces, forbidden paths, expected tests, expected commit, dependencies.
Interface-first for cross-workstream features (lead defines + commits interface + tests,
implementers work against it). Commit discipline: one purpose, tests included, baseline
preserved, no unrelated churn, docs updated, no mixing protocol + UI changes.

**§17.6 No silent security downgrade** — an agent must stop and report rather than:
bypass authorization to pass a test; disable signature checks; add broad filesystem
access; expose secrets to a model; execute handlers in the kernel; treat approval absence
as approval; replace durable state with in-memory state; weaken containment without
documentation.

## 18. Definition of done (per task)

Code implemented · unit tests · relevant property/adversarial tests · existing tests
pass · types pass for touched non-legacy code · docs updated · failure states handled ·
logging structured + scrubbed · no secrets in fixtures · user-visible behavior reachable
via Shell where applicable · lead verifies the invariant · focused commit. "Code exists"
is not completion.

## 19. Issue sequence (epics, executed in order)

- **Epic 1 — Foundation:** DEC-001 baseline · DEC-002 module inventory · DEC-003 TCB ·
  DEC-004 packaging · DEC-005 lint/typing/CI · DEC-006 architecture import tests.
- **Epic 2 — Kernel extraction:** DEC-010 canonical codec · 011 identity/crypto · 012
  event validation · 013 Weft storage · 014 deterministic fold · 015 capability model ·
  016 authorization service · 017 Morta approvals · 018 lifecycle · 019 receipts · 020
  checkpoints.
- **Epic 3 — Conformance:** DEC-030 event fixtures · 031 fold fixtures · 032 capability
  fixtures · 033 authorization property tests · 034 malformed-input tests · 035
  projection-rebuild tests.
- **Epic 4 — Runtime:** DEC-040 Agent Cells · 041 Plan Cells · 042 Job/lease schemas ·
  043 scheduler · 044 supervisor · 045 crash recovery · 046 budgets · 047 cancellation ·
  048 reconciliation.
- **Epic 5 — Containment:** DEC-050 worker IPC · 051 pure worker · 052 workspace worker ·
  053 provider worker · 054 digest-bound impls · 055 resource limits · 056 fs escape
  tests · 057 network escape tests · 058 lease replay tests.
- **Epic 6 — Model layer:** DEC-060 provider interface · 061 registry · 062 routing · 063
  deterministic test provider · 064 one cloud provider · 065 one local provider · 066
  token/cost accounting · 067 structured proposal validation.
- **Epic 7 — Projections:** DEC-070 engine · 071 knowledge · 072 tasks · 073 projects ·
  074 agents · 075 approvals · 076 activity · 077 exact search · 078 optional semantic
  index.
- **Epic 8 — Core services:** DEC-080 artifact store · 081 document ingestion · 082
  command service · 083 API auth · 084 API routes · 085 event streaming · 086 system
  status.
- **Epic 9 — Shell:** DEC-090 app shell · 091 conversation · 092 Today · 093 project ·
  094 knowledge · 095 agent inspector · 096 plan inspector · 097 approval inbox · 098
  capability inspector · 099 activity timeline · 100 settings.
- **Epic 10 — Daily-driver capabilities:** DEC-110 notes · 111 document import · 112
  source-grounded questions · 113 tasks · 114 projects · 115 isolated repo workspace ·
  116 code-edit workflow · 117 test + diff artifacts · 118 restricted file import/export.
- **Epic 11 — Operations:** DEC-120 data layout · 121 backup · 122 restore · 123 doctor ·
  124 diagnostic export · 125 service units · 126 installer · 127 first-run setup · 128
  upgrade procedure.
- **Epic 12 — Release:** DEC-130..136 execute scenarios A–G · 137 security review · 138
  documentation freeze · 139 tag Decima 0.3.

## 20. Final release definition

Decima 0.3 is complete only when: install through documented steps; Shell starts after
reboot; create + search knowledge; manage tasks + projects; delegate a coding task in an
isolated workspace; plans + jobs survive restart; models remain proposal engines;
sensitive effects enter an approval inbox; approval is bound to a concrete invocation;
revoked authority stops future use; agents inspectable + terminable; capabilities
inspectable + revocable; artifacts retain provenance; the Weft remains canonical;
projections rebuild; backup + restore work on a clean install; worker escape tests pass;
no known critical/high security defect; and no daily workflow requires editing source,
invoking internal Python, or manipulating SQLite.

Reject the release if it merely demos better but cannot safely recover from interruption,
explain authority, rebuild state, or complete the workflows. The milestone is reached not
by adding integrations but when the existing kernel architecture supports a narrow set of
useful workflows reliably enough to operate every day.
