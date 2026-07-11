export const meta = {
  name: 'decima-0.3-wave2a',
  description: 'Decima 0.3 Wave 2a — three worktree-isolated disjoint-ownership workstreams on the integrated kernel+runtime+workers+models+projections+ops: backend (Phase 8 local HTTP API + auth + streaming + command service, every mutation → Weft events), daily-driver (Phase 10 document ingestion + isolated repo workspace/coding + source-grounded Q&A), verification (independent property/fault-injection/end-to-end scenarios). Each self-verifies GREEN, then adversarial review.',
  phases: [
    { title: 'Implement', detail: 'one agent per workstream, isolated git worktree, disjoint file ownership' },
    { title: 'Review', detail: 'adversarial reviewer verifies tests pass, invariants preserved, no forbidden edits' },
  ],
}

const REPO = '/home/mini/decima-claude'
const TESTENV = '/tmp/claude-1000/-home-mini/56d98ee8-eef4-4e7a-845d-004995e016ad/scratchpad/testenv'
const PYT = `PYTHONPATH=${TESTENV}:$PWD python3 -m pytest`

const HOUSE = `
DECIMA 0.3 — WAVE 2 HOUSE RULES (obey exactly; violating an ARCHITECTURAL INVARIANT means STOP and report, never redesign):
- You work in an ISOLATED GIT WORKTREE (a full repo copy). Build + test HERE. Deliverable is the returned files[] (each {path, source} FULL contents) — the orchestrator integrates them.
- BUILD ON the existing packages (STUDY with Read; do NOT reimplement): decima.kernel (hashing, model.assert_content, crypto.Keyring, weft.Weft, weave.Weave[.fold/.of_type/.get/.state_root], capability, authorization.authorize_decision+AuthorizationDecision+ReasonCode, inbox, lifecycle.revoke/redact/supersede/terminate, checkpoints), decima.runtime (cells: create_plan/create_step/create_agent/create_lease/record_receipt + Plan/Step/Agent/Lease/Receipt + *Status; scheduler; supervisor.dispatch_step/run_once/run_to_completion; budgets.check_budget/guarded_dispatch; cancellation; reconciliation), decima.workers (protocol, execution [isolated child, scrubbed env, rlimits, digest-bound], lease validation, profiles), decima.models (providers.DeterministicProvider+ModelProvider, registry, routing, validation), decima.projections (engine.Projection + driver, tasks/projects/agents/approvals/activity/knowledge/search — DISPOSABLE read-models over the fold), decima.services (data_layout, backup, diagnostics).
- ARCHITECTURAL INVARIANTS (never violate): (1) the Weft is the SOLE canonical store — every durable mutation is a Cell asserted via decima.kernel/runtime; NO second canonical store; a web framework NEVER mutates storage directly. (2) projections DISPOSABLE. (3) NO ambient authority — an effect needs principal+capability+invocation+authorization(+approval)+receipt; a high-risk command CANNOT bypass the approval/authorization path. (4) models PROPOSE, deterministic code AUTHORIZES. (5) untrusted content is DATA (instruction_eligible=False) — never render/execute imported HTML/JS/markdown as trusted. (6) ints-not-floats + NO wall-clock/unseeded-random in recorded (Weft) content. (7) the API/kernel process executes nothing untrusted — code/browser/tools run in decima.workers. Golden fixtures + kernel import-boundary must keep passing.
- Pure Python STDLIB ONLY + PyNaCl + installed pytest/hypothesis. NO new pip deps — for HTTP use stdlib http.server/wsgiref, NOT fastapi/flask/django. New code TYPE-ANNOTATED + ruff-clean (line-length 100).
- FORBIDDEN GLOBALLY: never modify heartbeat/ (read-only reference), decima/kernel/, protocol/fixtures/, smoke.py, or another lane's directory. Own ONLY your stated directory(ies).
- SELF-VERIFY before returning (from worktree root): \`${PYT} tests/<your-dir> -q\` AND \`${PYT} tests/architecture -q\` AND \`python3 -c "import decima.kernel, decima.runtime, decima.projections"\` — all green. Iterate until green.
- Tests must exercise your code + FAIL LOUD. Report the ONE load-bearing property whose reversion makes a test red.
- DELIVERABLE (schema): files[] = every source AND test file, each {path, source} COMPLETE (repo-root-relative); test_paths[]; passed (int); invariant; forbidden_untouched (bool); doc_markdown; notes.
`

const LANES = [
  {
    name: 'backend', order: 5, effort: 'xhigh',
    own: 'decima/services/api/ (NEW subpackage) + tests/api/',
    forbid: 'decima/kernel, decima/runtime, decima/workers, decima/models, decima/projections, decima/services/{backup,diagnostics}, decima/shell, decima/capabilities',
    title: 'BACKEND API — Phase 8: local HTTP API, auth, streaming, command service',
    spec: `Build decima/services/api/ — a NARROW, authenticated, loopback-bound local API where EVERY durable mutation becomes accepted Weft events through the kernel/runtime (the web layer NEVER writes storage directly, invariant 1). Use stdlib http.server (threading) + wsgiref if helpful — NO web-framework deps.
  - app.py / server.py: a versioned HTTP API (/api/v1/...) bound to LOOPBACK by default (127.0.0.1), with a generated local app identity, authenticated browser sessions (secure HTTP-only cookie + CSRF token), and a streaming endpoint (chunked/SSE-style over stdlib) for assistant/plan/step/approval/error events. Binding non-loopback requires explicit config + a warning.
  - commands.py: a command service translating each user command to explicit operations that emit Weft events via kernel/runtime: CreateNote/UpdateNote/RetractNote (knowledge cells), CreateTask/CompleteTask/CreateProject (runtime cells), StartPlan/PausePlan/TerminateAgent (runtime + cancellation), ApproveInvocation/DenyInvocation (kernel.inbox), RevokeCapability (kernel.lifecycle.revoke), ImportArtifact/ExportArtifact (services). Reads go through decima.projections (disposable). NO endpoint evaluates arbitrary Python.
  - auth.py: session auth + CSRF + a reauth hook for high-risk approvals.
  - routes.py: map endpoints → commands + per-endpoint authorization (the Shell user may hold broad LOCAL authority, but each endpoint still maps to an explicit command + capability path).
REQUIRED tests (tests/api/, using the app in-process — no real socket needed, or a loopback socket on an ephemeral port): every durable API mutation produces one+ accepted Weft events; deleting + rebuilding the projection store preserves canonical state (read via projections after a mutation); a high-risk command (a gated effect) CANNOT bypass approval (returns APPROVAL_REQUIRED, no effect); an unauthenticated request is rejected; NO endpoint accepts arbitrary Python. Drive the WSGI/handler callable directly for determinism.`,
  },
  {
    name: 'daily-driver', order: 7, effort: 'xhigh',
    own: 'decima/capabilities/ (NEW package) + tests/capabilities/',
    forbid: 'decima/kernel, decima/runtime, decima/workers, decima/models, decima/projections, decima/services, decima/shell',
    title: 'DAILY-DRIVER — Phase 10: document ingestion, repo workspace/coding, source-grounded Q&A',
    spec: `Build decima/capabilities/ — the narrow but COMPLETE user workflows on top of kernel/runtime/workers/models/projections (deliver workflows, not isolated APIs). STUDY heartbeat/decima/{corpus,doc,retrieval,workspace}.py (read-only) for patterns.
  - documents.py: document ingestion — import an artifact (bytes + digest via services.data_layout/backup patterns OR a simple content-addressed store you own under capabilities), classify type (plain text / Markdown / PDF-text-extraction where safe / common source code), extract text, segment, create SOURCE-LINKED knowledge Cells (each claim keeps its source id + offset — NEVER discard the claim→source relationship), index via projections.search. Untrusted document content is DATA (instruction_eligible=False).
  - qa.py: source-grounded question answering — retrieve relevant source segments (projections.knowledge/search), answer via a decima.models DeterministicProvider (tests use it; no live API), and return the answer WITH citations that resolve to imported source segments. Knowledge access is HORIZON-SCOPED: an agent sees only explicitly selected projects/notes.
  - workspace.py: an isolated repository workspace via decima.workers — create workspace, mount a repo (copy into a bounded dir), inspect files, edit files, run declared commands + tests IN A WORKER (no network, no access outside the workspace, no ssh/git creds, no push/deploy — those are deferred), generate a diff, produce a diff artifact + test artifact. The change is REVIEWABLE before application.
REQUIRED tests (tests/capabilities/): a document's answer citations resolve to imported source segments; private-project knowledge is NOT exposed to an unrelated agent (horizon scoping); deleting the search index does not delete knowledge; retracted material stops appearing; a workspace worker CANNOT read files outside its workspace (compose the workers adversarial guarantees); a generated diff is reviewable before apply; restart does not lose the produced diff (it's a durable artifact).`,
  },
  {
    name: 'verification', order: 9, effort: 'xhigh',
    own: 'tests/verification/ + tests/e2e/ (TESTS ONLY — no source)',
    forbid: 'ALL of decima/ (source) — you write ONLY tests; heartbeat/; another lane dir',
    title: 'VERIFICATION — independent property, fault-injection, and end-to-end scenarios',
    spec: `Write INDEPENDENT tests (no source changes) over the INTEGRATED system (decima.kernel+runtime+workers+models+projections+services) — catch assumptions the implementers overlooked. STUDY the real modules to drive their true APIs.
  - tests/e2e/: the release scenarios that are achievable now, end to end on the durable stack (build a Weft + runtime + projections in-process):
    * CRASH RECOVERY (scenario E): a multi-step plan; complete step 1; begin step 2; DROP the process (fresh Weft over the same db); resume → step 1 NOT repeated, step 2 classified from lease+receipt, execution resumes; the interruption is visible in the activity projection.
    * REVOCATION (scenario F): an agent with a filesystem/effect capability begins a plan; revoke the capability (lifecycle.revoke); pending invocations fail closed; new leases exclude it; descendant grants ineffective; completed receipts remain; the revocation shows in the capability/approvals projection.
    * BACKUP+RESTORE (scenario G): create notes/project/tasks/artifacts; backup; restore into a fresh base; rebuild projections; compare state_root — equal; artifacts verify.
    * APPROVAL GATING (scenario D): a gated effect → APPROVAL_REQUIRED (authorize_decision) → deny → no effect + deterministic denial (durable); then approve once → exact invocation succeeds → approval consumed → reuse fails.
  - tests/verification/: PROPERTY + FAULT-INJECTION tests — e.g. random-order event delivery still yields one state_root; a simulated kill between dispatch and receipt does NOT retry a not-safely-retryable effect (compose runtime.reconciliation + supervisor); duplicate worker responses don't duplicate current state; budget exhaustion strictly blocks; a projection rebuilt after arbitrary interleavings equals the incremental projection.
REQUIRED: every test PASSES against current main and asserts a real invariant (report the load-bearing one). If you discover a genuine CORRECTNESS BUG in a lane's code, do NOT fix source — STOP and report it precisely in notes (which module/line, the failing scenario) so the orchestrator can route a fix.`,
  },
]

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['files', 'test_paths', 'passed', 'invariant', 'forbidden_untouched', 'doc_markdown'],
  properties: {
    files: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['path', 'source'], properties: { path: { type: 'string' }, source: { type: 'string' } } } },
    test_paths: { type: 'array', items: { type: 'string' } },
    passed: { type: 'integer' },
    invariant: { type: 'string' },
    forbidden_untouched: { type: 'boolean' },
    doc_markdown: { type: 'string' },
    bug_reports: { type: 'string', description: 'genuine correctness bugs found in OTHER lanes/modules (verification lane), else empty' },
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
    `${HOUSE}\n\n=== YOUR WORKSTREAM: ${lane.name} (merge order ${lane.order}) — ${lane.title} ===\nOWN (edit ONLY these): ${lane.own}\nFORBIDDEN: ${lane.forbid} + heartbeat/ + decima/kernel/ + protocol/fixtures/.\n\n${lane.spec}\n\nStudy the existing APIs, implement in your worktree, self-verify GREEN, return the structured result with FULL source of every file.`,
    { label: `impl:${lane.name}`, phase: 'Implement', schema: IMPL_SCHEMA, effort: lane.effort, isolation: 'worktree' }
  ).then((impl) => ({ lane, impl })),
  ({ lane, impl }) => {
    if (!impl) return { lane: lane.name, order: lane.order, impl: null, review: null }
    return agent(
      `${HOUSE}\n\n=== ADVERSARIAL REVIEW — workstream ${lane.name} ===\nImplementer claims ${impl.passed} tests pass, forbidden_untouched=${impl.forbidden_untouched}, invariant: "${impl.invariant}". Files: ${impl.files.map((f) => f.path).join(', ')}.\n\nIn your OWN fresh worktree, apply the files[] and INDEPENDENTLY verify: (1) run \`${PYT} ${(impl.test_paths || []).join(' ')} -q\` and \`${PYT} tests/architecture -q\` — pass? (2) touched anything OUTSIDE ${lane.own}? (heartbeat/decima.kernel/protocol/fixtures/another lane?) (3) tests genuinely exercise the code + assert the invariant (not vacuous)? (4) invariants intact (no second canonical store; web layer doesn't write storage directly; no ambient authority / approval bypass; models don't authorize; untrusted content not rendered as trusted; determinism)? Return APPROVE only if tests_pass AND invariants_preserved AND ownership_respected.`,
      { label: `review:${lane.name}`, phase: 'Review', schema: REVIEW_SCHEMA, effort: 'high', isolation: 'worktree' }
    ).then((review) => ({ lane: lane.name, order: lane.order, impl, review }))
  }
)

const approved = results.filter((r) => r && r.impl && r.review && r.review.verdict === 'APPROVE')
const rejected = results.filter((r) => !(r && r.review && r.review.verdict === 'APPROVE'))
log(`Wave 2a: ${approved.length}/${LANES.length} APPROVED`)
return {
  approved: approved.sort((a, b) => a.order - b.order).map((r) => ({ lane: r.lane, order: r.order, passed: r.impl.passed, invariant: r.impl.invariant, bug_reports: r.impl.bug_reports || '' })),
  rejected: rejected.map((r) => r && ({ lane: r.lane, reasons: r.review && r.review.reasons, notes: r.impl && r.impl.notes })),
}
