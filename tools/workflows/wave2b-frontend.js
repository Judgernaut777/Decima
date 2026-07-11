export const meta = {
  name: 'decima-0.3-wave2b-frontend',
  description: 'Decima 0.3 Wave 2b — the trusted Shell frontend (Phase 9): a conventional, dependency-free web UI served over the backend API, with strict separation between trusted UI chrome, model-generated content, and human approval controls. Worktree-isolated implement → adversarial review.',
  phases: [
    { title: 'Implement', detail: 'frontend assets + a static/serve composition over the backend API' },
    { title: 'Review', detail: 'adversarial reviewer verifies security separation + tests pass' },
  ],
}

const REPO = '/home/mini/decima-claude'
const TESTENV = '/tmp/claude-1000/-home-mini/56d98ee8-eef4-4e7a-845d-004995e016ad/scratchpad/testenv'
const PYT = `PYTHONPATH=${TESTENV}:$PWD python3 -m pytest`

const HOUSE = `
DECIMA 0.3 — WAVE 2b HOUSE RULES (obey exactly; a threatened ARCHITECTURAL INVARIANT means STOP and report):
- Work in an ISOLATED GIT WORKTREE. Deliverable = returned files[] (each {path, source} FULL contents).
- The backend API already exists: decima.services.api (app.py exposes a WSGI-style app / handler; commands.py, routes.py, auth.py session+CSRF, events.py streaming; /api/v1/... endpoints; every mutation → Weft events). STUDY it with Read. Your frontend consumes THAT API — it never writes storage or bypasses commands.
- FRONTEND TECH: a CONVENTIONAL, hand-written web UI — plain HTML + CSS + vanilla JS, ZERO external/CDN/runtime deps (a strict local CSP must be satisfiable: no remote scripts/styles/fonts, inline all assets or serve them locally). NO build step. NO npm. The initial Shell is trusted application code — NOT agent-generated at runtime.
- SECURITY RULES (invariant 5/7, handoff §9): NEVER render arbitrary imported HTML; sanitize/ESCAPE all model-generated and imported content (render as text, never as markup/JS); do NOT execute JS from artifacts/messages; an agent/model message must NOT be able to imitate trusted approval chrome; VISUALLY + STRUCTURALLY separate (a) untrusted/imported content, (b) model-generated content, (c) trusted system decisions, (d) human approvals. Approval action buttons exist ONLY in trusted UI components (never inside a rendered message). NO "always allow everything from this agent" control. External links clearly marked. No inline eval / new Function / innerHTML of untrusted data.
- OWN: decima/shell/ (NEW package: frontend assets under decima/shell/frontend/, plus decima/shell/serve.py — a stdlib server that serves the static frontend for non-/api paths and DELEGATES /api/* to the imported backend app, so the whole Shell runs from one local endpoint) + tests/shell/.
- FORBIDDEN: heartbeat/, decima/kernel/, decima/services/api/ (read-only — do not edit the backend), protocol/fixtures/, any other package. Pure stdlib for serve.py.
- REQUIRED SCREENS (as HTML views + JS view-switching): Conversation, Today, Projects, Knowledge, Plans, Approval inbox, Capability inspector, Activity timeline, Settings. Each renders from API data; the approval inbox shows per-card requesting-agent/effect/exact-target/args/data-leaving-machine/provider/max-cost/expiry/reversibility/causal-step/reason, with deny / approve-once / approve-with-stricter-limits actions (trusted component).
- SELF-VERIFY (from worktree root): \`${PYT} tests/shell -q\` AND \`${PYT} tests/architecture -q\` AND \`python3 -c "import decima.shell.serve"\` — all green. Iterate until green.
- Tests (tests/shell/, Python): serve.py serves index.html at / and delegates /api/v1/* to the backend app (drive the handler/loopback in-process); the sanitizer escapes HTML/script in model/imported content (unit-test the escape fn with hostile inputs like '<script>' / '<img onerror>' / an approval-chrome-imitation string); required screen files exist and reference real API endpoints; NO forbidden pattern (grep your own JS for eval(/new Function/.innerHTML= of untrusted — assert absent in tests).
- DELIVERABLE (schema): files[]=every file (HTML/CSS/JS/py/tests) {path, source} COMPLETE; test_paths[]; passed(int); invariant; forbidden_untouched(bool); doc_markdown; notes.
`

const IMPL = `${HOUSE}

=== WORKSTREAM: frontend (Phase 9, merge order 6) — the trusted Shell ===
Build decima/shell/ per the rules above. Deliver a coherent daily-use interface: a top-level app shell with nav to the 9 screens, a fetch-based API client (sending the CSRF token + credentials), streaming rendering for the conversation, and the trusted approval inbox. Keep it small, dependency-free, and secure-by-construction. Self-verify GREEN and return the structured result with FULL source of every file.`

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
    notes: { type: 'string' },
  },
}

const REVIEW_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'tests_pass', 'security_separation_ok', 'ownership_respected', 'reasons'],
  properties: {
    verdict: { type: 'string', enum: ['APPROVE', 'REJECT'] },
    tests_pass: { type: 'boolean' },
    security_separation_ok: { type: 'boolean' },
    ownership_respected: { type: 'boolean' },
    reasons: { type: 'string' },
  },
}

phase('Implement')
const impl = await agent(IMPL, { label: 'impl:frontend', phase: 'Implement', schema: IMPL_SCHEMA, effort: 'xhigh', isolation: 'worktree' })

phase('Review')
let review = null
if (impl) {
  review = await agent(
    `${HOUSE}\n\n=== ADVERSARIAL REVIEW — frontend ===\nImplementer claims ${impl.passed} tests pass, forbidden_untouched=${impl.forbidden_untouched}, invariant: "${impl.invariant}". Files: ${impl.files.map((f) => f.path).join(', ')}.\n\nIn your OWN worktree apply files[] and verify: (1) \`${PYT} ${(impl.test_paths || []).join(' ')} -q\` and \`${PYT} tests/architecture -q\` pass? (2) touched anything outside decima/shell/ + tests/shell/ (esp. decima/services/api, kernel, heartbeat)? (3) SECURITY: does model/imported content get ESCAPED (no raw HTML/JS render, no innerHTML of untrusted, no eval/new Function)? Are approval actions confined to trusted components (a rendered message cannot forge approval chrome or an always-allow)? Any remote/CDN dependency (must be none)? (4) does serve.py delegate /api to the backend without bypassing it? Return APPROVE only if tests_pass AND security_separation_ok AND ownership_respected.`,
    { label: 'review:frontend', phase: 'Review', schema: REVIEW_SCHEMA, effort: 'high', isolation: 'worktree' }
  )
}

return {
  approved: review && review.verdict === 'APPROVE',
  passed: impl && impl.passed,
  invariant: impl && impl.invariant,
  files: impl && impl.files ? impl.files.map((f) => f.path) : [],
  review_reasons: review && review.reasons,
}
