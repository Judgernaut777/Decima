export const meta = {
  name: 'evo-wave3',
  description: 'Post-0.3.0 Decima evolution Wave 3: 3 file-disjoint lanes, each impl + independent + adversarial review',
  phases: [
    { title: 'Implement' },
    { title: 'Review' },
  ],
}

const REPO = '/home/mini/decima-claude'
const TESTENV = '/tmp/claude-1000/-home-mini/56d98ee8-eef4-4e7a-845d-004995e016ad/scratchpad/testenv'
const BASE = 'a0af362' // current main HEAD (post-Wave-2)

const HOUSE = [
  'ARCHITECTURAL INVARIANTS — never violate (this is a POST-0.3.0 EVOLUTION, not a rewrite):',
  '  1. No redesign, no protocol rewrite, no kernel rewrite.',
  '  2. No weakening of the capability model. No reduction in determinism. Each new stage/feature',
  '     introduces NO authority regression.',
  '  3. The Weft is the sole canonical store; four verbs only (ASSERT/RETRACT/INVOKE/ATTEST);',
  '     no ambient authority; models PROPOSE, deterministic code AUTHORIZES; projections are',
  '     disposable + rebuildable; untrusted content (incl. any model output) stays',
  '     instruction_eligible=False (DATA), never an instruction.',
  '  4. Do NOT edit heartbeat/, decima/kernel/, or protocol/ (byte-frozen).',
  '  5. Recorded content is deterministic: no wall-clock, no unseeded randomness, ints not floats,',
  '     stable tie-breaks. Any projection reproduces a byte-identical fingerprint on rebuild.',
  'SCOPE DISCIPLINE (hard): edit ONLY the files in your OWNED list. If you believe you must touch',
  '  a file outside it, STOP — do not edit it; report it in out_of_scope_edits and work around it.',
  'CROSS-LANE COURTESY: other lanes are simultaneously editing files you IMPORT (Lane 1 touches',
  '  contracts.py + models_setup.py; Lane 2 touches decima/models/*; Lane 3 touches qa_service.py).',
  '  Therefore: make additive, backward-compatible changes to any shared type/interface; do NOT',
  '  rename or remove an existing public name, signature, or contract field. Preserve public',
  '  interfaces so a sibling branch that imports you still builds after integration.',
  'Every new capability/stage MUST ship adversarial tests (hostile input, boundary, fail-closed).',
].join('\n')

function implPrompt(lane) {
  return [
    'You are implementing one lane of a parallel Decima evolution wave, in your OWN git worktree',
    '(an isolated checkout). The main repo is at ' + REPO + '.',
    '',
    HOUSE,
    '',
    'YOUR LANE: ' + lane.key,
    'OWNED FILES (create/edit only these, plus new test files under the listed test dirs):',
    lane.owns.map((f) => '  - ' + f).join('\n'),
    '',
    'TASK:',
    lane.task,
    '',
    'WORKFLOW (all inside your worktree):',
    '  1. Confirm your worktree base is ' + BASE + ' (git log -1 --format=%H). Create your branch:',
    '       git checkout -b ' + lane.branch,
    '  2. Read the existing owned file(s) FIRST and match style/naming/comment density. Implement the',
    '     task editing ONLY owned files. Keep public interfaces additive/backward-compatible.',
    '  3. Add adversarial + regression tests for the new behavior.',
    '  4. Format + lint ONLY your owned files with the pinned ruff (0.15.20, on PATH):',
    '       ruff format <owned .py files> ; ruff check <owned .py + test files>',
    '  5. Run your lane tests (NOT the whole suite):',
    '       PYTHONPATH="' + TESTENV + ':$PWD" python3 -m pytest <your test files> -q',
    '     They must pass.',
    '  6. Commit on your branch with these trailers exactly:',
    '       Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>',
    '       Claude-Session: https://claude.ai/code/session_01GNKVaKXKb8vwuiwhygggc2',
    '',
    'Do NOT run the heartbeat smoke (slow; heartbeat is frozen). Do NOT merge/push. The orchestrator',
    'runs the authoritative full gate and integrates.',
    '',
    'Return IMPL_SCHEMA: branch, head_sha, files changed, new test files, fast-gate results, a note on',
    'HOW you preserved each relevant invariant, out_of_scope_edits (MUST be empty), and a summary.',
  ].join('\n')
}

function reviewPrompt(lane, role, roleBrief) {
  return [
    'You are the ' + role + ' reviewer for Decima evolution lane "' + lane.key + '".',
    'The implementation is committed on branch ' + lane.branch + ' in the shared repo at ' + REPO + '.',
    'Review by reading the diff and changed files (read-only commands only):',
    '    git -C ' + REPO + ' diff ' + BASE + '..' + lane.branch + ' --stat',
    '    git -C ' + REPO + ' diff ' + BASE + '..' + lane.branch,
    '    git -C ' + REPO + ' show ' + lane.branch + ':<path>',
    '',
    HOUSE,
    '',
    'LANE TASK (what it was supposed to do):',
    lane.task,
    '',
    'YOUR REVIEW LENS: ' + roleBrief,
    '',
    'Judge specifically:',
    '  - Correctness: does it do what the task says, without breaking the existing public interface',
    '    (' + lane.interface + ')? Are shared-type changes additive/backward-compatible?',
    '  - Invariant preservation: NO authority regression (a model/untrusted input never gains authority,',
    '    never signs approvals, never becomes an instruction); determinism; four-verbs-only; no ambient',
    '    authority; untrusted stays DATA; frozen dirs untouched.',
    '  - Scope: did it edit ONLY owned files (' + lane.owns.join(', ') + ')? Out-of-scope edit ≥ High.',
    '  - Tests: real adversarial tests, or only happy-path? Missing adversarial coverage on a new',
    '    capability is a High.',
    '',
    'Return REVIEW_SCHEMA. verdict=REJECT if you find ANY Blocker or High; else APPROVE. Every finding',
    'must be concrete (file + line + why + a failing scenario). If nothing real, APPROVE with empty',
    'findings — do NOT invent issues.',
  ].join('\n')
}

const IMPL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['lane', 'branch', 'head_sha', 'files_changed', 'fast_gate', 'out_of_scope_edits', 'invariants_note', 'summary'],
  properties: {
    lane: { type: 'string' },
    branch: { type: 'string' },
    head_sha: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    new_tests: { type: 'array', items: { type: 'string' } },
    fast_gate: {
      type: 'object',
      additionalProperties: false,
      required: ['ruff_ok', 'format_ok', 'pytest_ok', 'pytest_summary'],
      properties: {
        ruff_ok: { type: 'boolean' },
        format_ok: { type: 'boolean' },
        pytest_ok: { type: 'boolean' },
        pytest_summary: { type: 'string' },
      },
    },
    invariants_note: { type: 'string' },
    out_of_scope_edits: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
}

const REVIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['lane', 'role', 'verdict', 'findings'],
  properties: {
    lane: { type: 'string' },
    role: { type: 'string' },
    verdict: { type: 'string', enum: ['APPROVE', 'REJECT'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['severity', 'file', 'issue'],
        properties: {
          severity: { type: 'string', enum: ['Blocker', 'High', 'Medium', 'Low'] },
          file: { type: 'string' },
          line: { type: 'integer' },
          issue: { type: 'string' },
          why: { type: 'string' },
        },
      },
    },
    notes: { type: 'string' },
  },
}

const LANES = [
  {
    key: 'workspace-stage2',
    branch: 'evo3/workspace-stage2',
    interface: 'CreateWorkspaceRun/StartWorkspaceRun command handlers + their existing routes; WorkspaceRequest.from_args; svc.models.propose(task_spec, ModelRequest) seam; _validate_edits/_prevalidate_mount_paths',
    owns: [
      'decima/services/api/workspace_service.py',
      'decima/services/api/models_setup.py',
      'decima/services/api/contracts.py',
      'decima/shell/frontend/js/screens/workspace.js',
      'tests/api/test_workspace_service.py',
      'tests/api/test_contracts.py',
    ],
    task: [
      'Coding-workspace STAGE 2: let the bounded change be MODEL-PROPOSED instead of only',
      'operator-declared, under proposal -> validation -> authorization -> execution, with NO',
      'authority regression (the change still runs ONLY in the jailed, networkless decima.workers',
      'child; no push, no credential, no network).',
      '',
      'Today CreateWorkspaceRun takes a literal operator-declared `edits` list ([{path, content}]) and',
      'StartWorkspaceRun applies them in the jail and runs a DECLARED check. Stage 2 adds an OBJECTIVE',
      'path, reusing the EXISTING CreateWorkspaceRun route (add NO new route):',
      '  - Extend WorkspaceRequest (contracts.py) with an OPTIONAL `objective` string. Additive and',
      '    backward-compatible: a request with literal `edits` behaves EXACTLY as before; `objective`',
      '    and `edits` are mutually exclusive (both -> BAD_REQUEST).',
      '  - In create_workspace_run: when `objective` is given, route a model proposal via the existing',
      '    seam — build a ModelRequest with a strict WORKSPACE_EDITS structured_schema and call',
      '    `result, decision = svc.models.propose(req.task_spec(), model_request)`; record the routing',
      '    decision (routing.record) like plan_service does; if not decision.routed ->',
      '    NO_ELIGIBLE_MODEL(503); if no usable reply -> MODEL_FAILED(502). Then VALIDATE the proposed',
      '    edits with the SAME deterministic guards as operator edits (_validate_edits, MAX_EDITS,',
      '    _prevalidate_mount_paths) BEFORE any durable mount/write. Record the model proposal as a Cell',
      '    with instruction_eligible=False (DATA) — the model text NEVER becomes an instruction and',
      '    NEVER selects the check (the check stays from the DECLARED CHECKS catalogue only).',
      '  - Update PlanAwareDeterministicProvider / the deterministic provider in models_setup.py so the',
      '    OFFLINE default emits a valid, schema-conforming workspace-edit proposal for a representative',
      '    objective (keeps the deterministic default working + gives deterministic tests). Limit',
      '    models_setup edits to this deterministic proposal behavior — do NOT change routing authority,',
      '    cost ranks, or the RECOMMENDED_LOCAL_MODEL literal.',
      '  - workspace.js: add an objective input as an ALTERNATIVE to literal edits, and render',
      '    proposed-vs-authorized-vs-executed as visually distinct states; proposed edits + diffs + test',
      '    output remain untrusted display text (they already are).',
      'Adversarial tests required: objective+edits both -> 400; a hostile model proposal (path traversal',
      'like ../ or absolute/backslash paths, > MAX_EDITS, non-{path,content} shape, injection text in',
      'content) is REJECTED with NO durable mount/write and NO leaked cells; a proposal that names a',
      'check is ignored (check still only from the catalogue); the literal-edits path is byte-for-byte',
      'unchanged in behavior. Keep the existing workspace containment tests green.',
    ].join('\n'),
  },
  {
    key: 'provider-routing',
    branch: 'evo3/provider-routing',
    interface: 'ModelStack.propose / routing.route / routing.record / ModelRequest / validation entry points / provider public classes — all PUBLIC signatures must stay stable (models_setup.py and the services import them)',
    owns: [
      'decima/models/routing.py',
      'decima/models/registry.py',
      'decima/models/providers.py',
      'tests/models/test_capability_routing.py',
      'tests/models/test_models.py',
    ],
    task: [
      'Deepen CAPABILITY-BASED routing selection in decima/models, behind the existing PUBLIC',
      'interfaces (do NOT change the signatures that models_setup.py and the api services import;',
      'additive only). Wave 1 added bounded-int/enum capability metadata',
      '(reasoning/coding/planning/latency/locality/structured-output/context/cost); this lane makes the',
      'ROUTER actually exploit it in a DETERMINISTIC, capability-integrity-preserving way:',
      '  - Selection uses capabilities only to CHOOSE among ELIGIBLE providers, NEVER to grant',
      '    authority. Sensitive/private task classes still route LOCAL-ONLY (an external provider must',
      '    remain non-vacuously refused for sensitive work) — keep that invariant and its test.',
      '  - Add richer, deterministic tie-breaks/scoring over the metadata: e.g. required-capability',
      '    thresholds (a task needing structured-output or a min context refuses a provider that lacks',
      '    it), locality preference, latency/cost ordering — all with INTEGER scores and a total,',
      '    stable final tie-break (never wall-clock/random). A request with NO requirements must rank',
      '    BYTE-IDENTICALLY to today (regression-lock this).',
      '  - Every routing decision stays RECORDED with reason codes (routing.record) and the estimated',
      '    cost/sensitivity class; the deterministic placeholder still carries nominal cost so a real',
      '    configured provider outranks it (do not regress the Wave-2/Path-A selection fix).',
      'Adversarial tests required: a task with a hard capability requirement refuses an under-provisioned',
      'provider even if it is cheaper/closer; sensitive task never routes to an external provider;',
      'no-requirements ranking is unchanged vs base; scoring is deterministic across repeated runs and',
      'independent of provider registration order.',
    ].join('\n'),
  },
  {
    key: 'qa-citations',
    branch: 'evo3/qa-citations',
    interface: 'AskGroundedQuestion / the Q&A reader routes (unchanged); qa_service public command/reader handlers; capabilities/qa.retrieve + answer shape consumed by qa.js',
    owns: [
      'decima/services/api/qa_service.py',
      'decima/capabilities/qa.py',
      'decima/shell/frontend/js/screens/qa.js',
      'tests/api/test_qa_service.py',
      'tests/capabilities/test_qa_grounding.py',
    ],
    task: [
      'Improve grounded-Q&A CITATION quality + UX on top of the Wave-2 deterministic hybrid retrieval',
      '(decima/projections/search.py is already merged and STABLE — do NOT edit it; consume it).',
      'Deliver, all deterministic and behind the existing Q&A routes (add NO new route):',
      '  - Better citation surfacing: rank/deduplicate the cited passages deterministically, expose the',
      '    matched-token / relevance signal per citation, and ensure each citation still OPENS THE REAL',
      '    SOURCE PASSAGE it cites (the load-bearing 0.3 property). A citation must correspond to an',
      '    actual retrieved Hit with real provenance — never a fabricated or model-authored reference.',
      '  - Keep the security properties EXACTLY: generated answer text stays visually/semantically',
      '    distinct from imported source DATA; hostile imports stay inert DATA (instruction_eligible=',
      '    False); a question that is only stopwords / only fuzzy-overlaps earns NO citation (the',
      '    not-citable gate from Wave 2). The model only composes an answer from retrieved DATA; it',
      '    never gains authority and its output is recorded instruction_eligible=False.',
      '  - qa.js: render citations with their relevance signal and a clear source-open affordance;',
      '    keep generated-vs-imported visually distinct.',
      'Determinism: citation ordering/dedup is a total, stable function of the retrieval result; no',
      'wall-clock/random in any recorded content.',
      'Adversarial tests required: a stopword-only / fuzzy-only question yields an answer with NO',
      'citations (never a spurious grounded cite); a hostile imported passage cannot inject an',
      'instruction via a citation; every rendered citation resolves to a real source passage; repeated',
      'identical questions produce identical citation ordering.',
    ].join('\n'),
  },
]

log('Wave 3: 3 file-disjoint lanes (workspace-stage2 | provider-routing | qa-citations)')
log('Each lane: worktree-isolated impl -> independent-correctness review + adversarial review')

const results = await pipeline(
  LANES,
  (lane) =>
    agent(implPrompt(lane), {
      label: 'impl:' + lane.key,
      phase: 'Implement',
      schema: IMPL_SCHEMA,
      isolation: 'worktree',
    }),
  (impl, lane) => {
    if (!impl) return { lane: lane.key, impl: null, reviews: [] }
    return parallel([
      () =>
        agent(reviewPrompt(lane, 'independent-correctness', 'Assume the implementer is competent but fallible. Verify the task is actually met and no interface/invariant is broken. Focus on real correctness, backward-compatibility of shared types, and determinism defects.'), {
          label: 'review:' + lane.key + ':correctness',
          phase: 'Review',
          schema: REVIEW_SCHEMA,
        }),
      () =>
        agent(reviewPrompt(lane, 'adversarial', 'Assume the implementer made a subtle security/authority/determinism mistake and try to PROVE it: hunt for an authority regression (model/untrusted input gaining authority, becoming an instruction, selecting a check, signing an approval, or being cited as if trusted), a capability-weakening, a non-deterministic recorded value, a hostile-input path with a durable side effect, or a non-additive change to a shared public interface. Default to REJECT if a real hole exists.'), {
          label: 'review:' + lane.key + ':adversarial',
          phase: 'Review',
          schema: REVIEW_SCHEMA,
        }),
    ]).then((rv) => ({ lane: lane.key, impl, reviews: rv.filter(Boolean) }))
  },
)

return {
  base: BASE,
  lanes: results.filter(Boolean).map((r) => ({
    lane: r.lane,
    branch: r.impl ? r.impl.branch : null,
    head_sha: r.impl ? r.impl.head_sha : null,
    files_changed: r.impl ? r.impl.files_changed : [],
    out_of_scope_edits: r.impl ? r.impl.out_of_scope_edits : ['(impl agent returned null)'],
    fast_gate: r.impl ? r.impl.fast_gate : null,
    reviews: (r.reviews || []).map((v) => ({ role: v.role, verdict: v.verdict, findings: v.findings, notes: v.notes })),
  })),
}
