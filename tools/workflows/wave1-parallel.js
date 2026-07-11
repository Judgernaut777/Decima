export const meta = {
  name: 'decima-0.3-wave1',
  description: 'Decima 0.3 Wave 1 — five worktree-isolated, disjoint-ownership workstreams built in parallel on decima.kernel + decima.runtime: runtime-completion (budgets/cancellation/reconciliation DEC-046/047/048), workers (Phase 5 isolated execution + adversarial escape tests), models (Phase 6 provider/routing/deterministic-provider), projections (Phase 7 engine + task/project/knowledge/approval/activity + rebuild==incremental), ops (Phases 11-13 backup/restore/doctor/diagnostics/systemd/first-run). Each self-verifies GREEN under pytest in its own worktree, then is adversarially reviewed. The orchestrator integrates approved lanes in merge order.',
  phases: [
    { title: 'Implement', detail: 'one agent per workstream, isolated git worktree, disjoint file ownership' },
    { title: 'Review', detail: 'adversarial reviewer verifies tests pass, invariants preserved, no forbidden edits' },
  ],
}

const REPO = '/home/mini/decima-claude'
const TESTENV = '/tmp/claude-1000/-home-mini/56d98ee8-eef4-4e7a-845d-004995e016ad/scratchpad/testenv'
const PYT = `PYTHONPATH=${TESTENV}:$PWD python3 -m pytest`

const HOUSE = `
DECIMA 0.3 — WAVE 1 HOUSE RULES (obey exactly; violating an ARCHITECTURAL INVARIANT means STOP and report, never redesign):
- You work in an ISOLATED GIT WORKTREE of the repo (a full copy). Build + test HERE. Your deliverable is the returned files[] (each {path, source} with FULL file contents) — the orchestrator integrates them into main.
- BUILD ON the existing packages, do NOT reimplement them: decima.kernel (the trusted core — hashing, model, crypto/keystore, weft.Weft, weave.Weave [.fold(weft)/.of_type(t)/.get(id)/.state_root()], capability, authorization.AuthorizationDecision+ReasonCode, inbox, checkpoints, receipts) and decima.runtime (cells.py: create_plan/create_step/create_agent/create_lease/record_receipt + Agent/Plan/Step/Lease/Receipt Cells + StepStatus/AgentStatus/PlanStatus; scheduler.py: ready_steps/blocked_steps/reconcile_readiness/plan_is_complete; supervisor.py: dispatch_step/run_once/run_to_completion). STUDY them with Read first.
- ARCHITECTURAL INVARIANTS (never violate): (1) the Weft is the SOLE canonical store — everything durable is a Cell asserted via decima.kernel.model.assert_content; NO second canonical DB/state; (2) projections are DISPOSABLE — rebuildable from the fold, never canonical; (3) NO ambient authority — an effect needs principal+capability+invocation+authorization+approval+receipt; a new module/server/default mints NOTHING; (4) models PROPOSE, deterministic code AUTHORIZES — model output is never itself an authorization; (5) untrusted content is DATA (instruction_eligible=False); (6) ints-not-floats + NO wall-clock/unseeded-random in recorded (Weft) content — determinism; (7) the kernel executes nothing untrusted. Golden fixtures + kernel import-boundary must keep passing.
- FORBIDDEN GLOBALLY: do NOT modify heartbeat/ (the frozen reference oracle — you MAY read it for API/patterns), decima/kernel/ (the TCB — read-only), protocol/fixtures/, smoke.py, or another lane's directory. Own ONLY your stated directory(ies).
- Pure Python stdlib + PyNaCl (already available) + the installed pytest/hypothesis. NO new pip deps. New code must be TYPE-ANNOTATED and pass ruff (line-length 100, the repo config) — it is NOT excluded like the frozen kernel copies.
- SELF-VERIFY before returning (from your worktree root): run \`${PYT} tests/<your-test-dir> -q\` AND \`${PYT} tests/architecture -q\` (import boundary must stay green) AND \`python3 -c "import decima.kernel, decima.runtime"\`. Confirm all green; iterate until they are. Report the exact pass count.
- Tests must actually exercise your code and FAIL LOUD via assert. Report the ONE load-bearing property whose reversion makes a test go red.
- DELIVERABLE (schema): files[] = every source AND test file you wrote/changed, each {path, source} with COMPLETE contents (relative paths from repo root); test_paths[]; passed (int total); invariant; a short doc_markdown for docs/. Do NOT edit files outside your ownership.
`

const LANES = [
  {
    name: 'runtime', order: 1, effort: 'xhigh',
    own: 'decima/runtime/ (ADD budgets.py, cancellation.py, reconciliation.py only) + tests/runtime/',
    forbid: 'decima/kernel, decima/models, decima/workers, decima/projections, decima/services, decima/shell',
    title: 'RUNTIME COMPLETION — budgets, cancellation, reconciliation (DEC-046/047/048)',
    spec: `Complete Phase 4 on top of decima/runtime/{cells,scheduler,supervisor}.py. ADD three modules (do NOT edit the existing three — compose their public API):
  - budgets.py (DEC-046): enforce token_budget / monetary_budget / deadline / max_attempts / max_child_agents / max_concurrent as DURABLE state transitions, not log lines. A budget check runs BEFORE dispatch; exhaustion transitions the agent/plan to a durable blocked/failed status (a new assertion). Provide check_budget(weave, agent_id, cost, now) -> (ok, reason) and a spend ledger folded from receipts/invocations. Budgets are ints (logical). Exhaustion must BLOCK further execution (prove a dispatch is refused after exhaustion).
  - cancellation.py (DEC-047): cancellation PROPAGATES — cancel_plan cascades to pending steps → active leases; cancel_agent cascades to child agents → leases → pending invocations; a capability revoke (compose decima.kernel.lifecycle.revoke) cascades to descendant grants. Use RETRACT/terminate semantics via decima.kernel.lifecycle where authority is involved, and status transitions for plan/step/agent cells. Cancelling must not dispatch NEW work; already-committed external effects are NOT reversed (record that honestly).
  - reconciliation.py (finish DEC-048): an effect state machine (PROPOSED/AUTHORIZED/DISPATCHED/SUCCEEDED/FAILED/UNKNOWN/RECONCILING/SUPERSEDED/COMPENSATED); a reconciler for a step whose lease has a RUNNING status but NO terminal receipt (crash window) → classify from the receipt/lease as safe-to-retry / UNKNOWN / already-succeeded; declare a per-effect idempotency strategy enum (naturally-idempotent / idempotency-key / read-before-write / write-once / not-safely-retryable) and make not-safely-retryable enter UNKNOWN on ambiguous interruption rather than silently retrying.
REQUIRED tests (tests/runtime/): budget exhaustion blocks dispatch; cancellation propagates plan→steps→leases and agent→children; an UNKNOWN effect (RUNNING lease, no receipt) is reconciled to a defined status; a duplicate receipt does not create duplicate current state (idempotence). Build plans/agents with the existing cells.py helpers.`,
  },
  {
    name: 'workers', order: 2, effort: 'xhigh',
    own: 'decima/workers/ (NEW package) + tests/workers/ + tests/adversarial/test_worker_isolation.py',
    forbid: 'decima/kernel, decima/runtime (read-only), decima/models, decima/projections, decima/services, decima/shell',
    title: 'WORKER ISOLATION — Phase 5: isolated execution + IPC + adversarial escape tests',
    spec: `Build decima/workers/ so effect execution NEVER inherits the parent process authority (invariant 7 / handoff §5). STUDY heartbeat/decima/isolation.py + executor.py (read-only) as the proven containment template on THIS aarch64 Linux box. Implement:
  - protocol.py: a VERSIONED local worker IPC (JSON request {protocol_version, invocation_id, job_id, effect, implementation_digest, arguments, lease, capability_proof} → response {invocation_id, status: SUCCEEDED|FAILED|UNKNOWN, output_refs, receipt_data, diagnostics}). NEVER transfer raw private signing keys.
  - execution.py: run a bounded effect in an isolated child process — dedicated tmp cwd, SCRUBBED environment (no inherited secrets/HOME/SSH_AUTH_SOCK), resource limits (RLIMIT_CPU/AS/NOFILE/NPROC via resource + preexec), no inherited fds, the STRONGEST available subset of Linux isolation (unshare/namespaces where the box permits, else document the fallback per §5.3 — mandatory vs optional). The implementation is bound by DIGEST (implementation_digest) — a mismatch fails closed.
  - lease.py: validate a runtime lease (decima.runtime.cells lease shape) before executing — an EXPIRED or REPLAYED lease fails closed.
  - worker profiles: a PURE worker (no network, tmp dir, no home, no secrets) at minimum; note WORKSPACE + PROVIDER profiles as structure.
ADVERSARIAL tests (tests/adversarial/test_worker_isolation.py + tests/workers/) MUST actually run on this box and prove the worker CANNOT: read ~/.ssh, read a parent env secret, execute an ungranted/undigested effect, reuse a replayed lease, use an expired lease; and that resource limits bound it. If real namespace isolation is unavailable here, TEST the guarantees the available subset DOES enforce (scrubbed env, tmp cwd, rlimits, digest binding, lease validation) and DOCUMENT the gap honestly — do NOT claim OS sandboxing you cannot enforce (handoff §16).`,
  },
  {
    name: 'models', order: 3, effort: 'high',
    own: 'decima/models/ (NEW package) + tests/models/',
    forbid: 'decima/kernel, decima/runtime, decima/workers, decima/projections, decima/services, decima/shell',
    title: 'MODEL ROUTING — Phase 6: provider abstraction, routing, deterministic provider',
    spec: `Build decima/models/ so models are PROPOSAL engines with ZERO authority (invariant 4). STUDY heartbeat/decima/{router,provider_router,model,inference}.py (read-only) for patterns. Implement:
  - providers.py: a ModelProvider Protocol (capabilities() -> ModelCapabilities; complete(request) -> ModelResponse; stream(request) -> iterator). A DeterministicProvider (rule-based, no network — the one used in tests and as the default fallback so the whole product is testable without paid APIs). Thin LocalProvider + CloudProvider ADAPTERS that conform to the Protocol structurally but make NO live network call in tests (a live call is a runtime concern gated behind config; secrets are applied by a broker, NEVER placed in code/context/logs).
  - registry.py: track provider/model/local-or-remote/context-limit/modality/structured-output/tool-use/est-cost/privacy-class/enabled.
  - routing.py: a routing policy taking task-class/sensitivity/modalities/context-size/latency/cost-budget/local-availability → RoutingDecision(selected_model, fallback_models, reason_codes, estimated_cost, context_policy). The decision is RECORDED (returned as data; a caller folds it onto the Weft). A local-only policy for sensitive tasks is enforceable. Provider failure → bounded fallback.
  - budgets.py / accounting.py: token + cost accounting (provider/model/in-out tokens/latency/est-cost/purpose) with a pre-and-post budget check that STOPS further calls when exhausted.
  - validation.py: structured model-proposed actions validate against explicit schemas; invalid proposals are REJECTED and recorded as model errors, never repaired by arbitrary eval, with a bounded re-prompt path.
REQUIRED tests (tests/models/): deterministic provider is reproducible; routing selects local for a sensitive task and records the decision + reason codes; provider failure triggers the declared fallback; a token budget stops further calls; an invalid structured proposal is rejected (not executed). No test hits a live API. Models cannot execute actions (assert there is no path from a ModelResponse to an effect without going through authorization).`,
  },
  {
    name: 'projections', order: 4, effort: 'high',
    own: 'decima/projections/ (NEW package) + tests/projections/',
    forbid: 'decima/kernel, decima/runtime (read-only), decima/models, decima/workers, decima/services, decima/shell',
    title: 'PROJECTIONS — Phase 7: engine + task/project/knowledge/approval/activity + rebuild',
    spec: `Build decima/projections/ — DISPOSABLE read-models over the Weave fold (invariant 2/5). STUDY heartbeat/decima/{memory,retrieval,search,knowledge,timeline,dashboard}.py (read-only) for patterns, and decima.runtime.cells for the plan/step/agent cell shapes. Implement:
  - engine.py: a Projection Protocol (name; version:int; reset(); apply(event); checkpoint() -> ProjectionCheckpoint) + a driver that supports incremental update, FULL REBUILD from an empty projection, version + migration-by-rebuild, lag reporting, DETERMINISTIC output. A projection store may be an in-memory/sqlite index — it is REBUILDABLE, never canonical.
  - tasks.py, projects.py, agents.py, approvals.py, activity.py, knowledge.py: read-models over the fold (task list/status/deps/due; project objective/status/members; agent hierarchy/status/budget; pending/approved/denied/expired/consumed approvals from inbox; a human-readable activity timeline from asserts/retracts/invokes/attests/receipts/transitions; knowledge notes/documents/links/provenance).
  - search.py: exact text search as a DERIVED, disposable index; semantic/embeddings optional and derived.
CRITICAL acceptance test (tests/projections/): build a Weft with plans/tasks/notes/approvals, project incrementally; then DELETE every projection and REBUILD from the Weft — the rebuilt state MUST EQUAL the incremental state (byte/field equal). Also: a retracted note stops appearing; deleting the search index does not delete knowledge; projection version bump triggers a clean rebuild.`,
  },
  {
    name: 'ops', order: 8, effort: 'high',
    own: 'decima/services/ (ADD backup/, diagnostics/) + decima/cli/main.py (wire doctor/backup/restore/rebuild) + deploy/ + tests/ops/',
    forbid: 'decima/kernel, decima/runtime (read-only), decima/models, decima/workers, decima/projections, decima/shell, decima/services/api',
    title: 'OPERATIONS — Phases 11-13: backup/restore/doctor/diagnostics/systemd/first-run',
    spec: `Build the operability layer so a local install is recoverable (handoff §12-13). STUDY heartbeat/decima/{backup,snapshot,migrate,observ}.py (read-only). Implement:
  - decima/services/backup/: define the data layout (weft/ artifacts/ projections/ checkpoints/ config/ logs/ under a base dir); backup_create(base, dest) backs up the Weft + artifacts + checkpoints + public config (NOT projections — rebuildable; NOT secrets in plaintext); backup_verify(path) checks integrity (content digests / checkpoint); restore_apply(dest, base) verifies → preserves a rollback copy → restores Weft+artifacts → verifies event integrity by folding → rebuilds nothing canonical → confirms the state_root matches. A corrupted backup is REJECTED.
  - decima/services/diagnostics/: doctor() checks package/py version, Weft integrity (fold verifies), checkpoint consistency, artifact digests, disk space, unresolved effects; returns a structured report (and --json). diagnostic_export() produces a SCRUBBED support bundle (versions/error-codes/states/redacted logs) with NO secrets/keys/private docs.
  - wire decima/cli/main.py: make decima-doctor / decima-backup / decima-restore / decima-rebuild call the real implementations (replace the stubs). Keep argument parsing minimal + documented.
  - deploy/: a systemd USER unit template (NoNewPrivileges, PrivateTmp, restricted paths, resource limits, restart) + an install.sh sketch + a first-run flow function (create identity, data dir, default budgets — NO network).
REQUIRED tests (tests/ops/): backup → restore into a fresh base → folded state_root EQUALS the original (round-trip); a corrupted backup is rejected; doctor detects a tampered/corrupted artifact and a stale/missing checkpoint; diagnostic export contains NO test secret. Use a temp base dir + a small Weft built via decima.kernel/runtime.`,
  },
]

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['files', 'test_paths', 'passed', 'invariant', 'doc_markdown', 'self_verified'],
  properties: {
    files: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['path', 'source'], properties: { path: { type: 'string' }, source: { type: 'string' } } } },
    test_paths: { type: 'array', items: { type: 'string' } },
    passed: { type: 'integer' },
    self_verified: { type: 'boolean', description: 'true iff you ran pytest on your tests AND tests/architecture AND the import check, all green' },
    invariant: { type: 'string' },
    forbidden_untouched: { type: 'boolean', description: 'true iff you modified NOTHING outside your ownership' },
    doc_markdown: { type: 'string' },
    notes: { type: 'string' },
  },
}

const REVIEW_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'tests_pass', 'invariants_preserved', 'ownership_respected', 'reasons'],
  properties: {
    verdict: { type: 'string', enum: ['APPROVE', 'REJECT'] },
    tests_pass: { type: 'boolean' },
    invariants_preserved: { type: 'boolean' },
    ownership_respected: { type: 'boolean' },
    reasons: { type: 'string' },
  },
}

const results = await pipeline(
  LANES,
  (lane) => agent(
    `${HOUSE}\n\n=== YOUR WORKSTREAM: ${lane.name} (merge order ${lane.order}) — ${lane.title} ===\nOWN (edit ONLY these): ${lane.own}\nFORBIDDEN (never touch): ${lane.forbid} + heartbeat/ + decima/kernel/ + protocol/fixtures/.\n\n${lane.spec}\n\nStudy the existing kernel/runtime APIs, implement in your worktree, self-verify GREEN (your tests + tests/architecture + import check), and return the structured result with FULL source of every file.`,
    { label: `impl:${lane.name}`, phase: 'Implement', schema: IMPL_SCHEMA, effort: lane.effort, isolation: 'worktree' }
  ).then((impl) => ({ lane, impl })),
  ({ lane, impl }) => {
    if (!impl) return { lane: lane.name, order: lane.order, impl: null, review: null }
    return agent(
      `${HOUSE}\n\n=== ADVERSARIAL REVIEW — workstream ${lane.name} ===\nThe implementer claims ${impl.passed} tests pass, self_verified=${impl.self_verified}, forbidden_untouched=${impl.forbidden_untouched}, invariant: "${impl.invariant}". Files: ${impl.files.map((f) => f.path).join(', ')}.\n\nIn your OWN fresh worktree, apply the implementer's files[] (write each to its path) and INDEPENDENTLY verify: (1) run \`${PYT} ${(impl.test_paths || []).join(' ')} -q\` and \`${PYT} tests/architecture -q\` — do they pass? (2) Did they touch anything OUTSIDE their ownership (${lane.own}) — especially heartbeat/, decima/kernel/, protocol/fixtures/, or another lane's dir? (3) Do the tests genuinely exercise the code and assert the claimed invariant, or are they vacuous? (4) Are any architectural invariants violated (second canonical store? ambient authority? model authorizing? non-determinism / wall-clock in Weft content? projection treated as canonical?). Return APPROVE only if tests_pass AND invariants_preserved AND ownership_respected.`,
      { label: `review:${lane.name}`, phase: 'Review', schema: REVIEW_SCHEMA, effort: 'high', isolation: 'worktree' }
    ).then((review) => ({ lane: lane.name, order: lane.order, impl, review }))
  }
)

const approved = results.filter((r) => r && r.impl && r.review && r.review.verdict === 'APPROVE')
const rejected = results.filter((r) => !(r && r.review && r.review.verdict === 'APPROVE'))
log(`Wave 1: ${approved.length}/${LANES.length} workstreams APPROVED`)
return {
  approved: approved.sort((a, b) => a.order - b.order).map((r) => ({
    lane: r.lane, order: r.order, passed: r.impl.passed, invariant: r.impl.invariant,
    files: r.impl.files, test_paths: r.impl.test_paths, doc_markdown: r.impl.doc_markdown,
  })),
  rejected: rejected.map((r) => r && ({ lane: r.lane, reasons: r.review && r.review.reasons, notes: r.impl && r.impl.notes })),
}
