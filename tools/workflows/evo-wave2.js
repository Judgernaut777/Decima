export const meta = {
  name: 'evo-wave2',
  description: 'Post-0.3.0 Decima evolution Wave 2: 3 file-disjoint lanes, each impl + independent + adversarial review',
  phases: [
    { title: 'Implement' },
    { title: 'Review' },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Ground truth passed to every agent.
const REPO = '/home/mini/decima-claude'
const TESTENV = '/tmp/claude-1000/-home-mini/56d98ee8-eef4-4e7a-845d-004995e016ad/scratchpad/testenv'
const BASE = 'fcc11ed' // current main HEAD (post-Wave-1)

const HOUSE = [
  'ARCHITECTURAL INVARIANTS — never violate (this is a POST-0.3.0 EVOLUTION, not a rewrite):',
  '  1. No redesign, no protocol rewrite, no kernel rewrite.',
  '  2. No weakening of the capability model. No reduction in determinism.',
  '  3. The Weft is the sole canonical store; four verbs only (ASSERT/RETRACT/INVOKE/ATTEST);',
  '     no ambient authority; models PROPOSE, deterministic code AUTHORIZES; projections are',
  '     disposable + rebuildable; untrusted content stays instruction_eligible=False (DATA).',
  '  4. Do NOT edit heartbeat/, decima/kernel/, or protocol/ (byte-frozen).',
  '  5. Recorded content is deterministic: no wall-clock, no unseeded randomness, ints not floats,',
  '     stable tie-breaks. Any projection must reproduce a byte-identical fingerprint on rebuild.',
  'SCOPE DISCIPLINE (hard): edit ONLY the files in your OWNED list. If you believe you must touch',
  '  a file outside it (especially a shared seam like contracts.py), STOP — do not edit it; report',
  '  it in out_of_scope_edits and work around it. Cross-lane edits break parallel integration.',
  'Every new capability MUST ship adversarial tests (hostile input, boundary, fail-closed).',
].join('\n')

function implPrompt(lane) {
  return [
    'You are implementing one lane of a parallel Decima evolution wave. You are in your OWN git',
    'worktree (an isolated checkout). The main repo is at ' + REPO + '.',
    '',
    HOUSE,
    '',
    'YOUR LANE: ' + lane.key,
    'OWNED FILES (you may create/edit only these, plus new test files under the listed test dirs):',
    lane.owns.map((f) => '  - ' + f).join('\n'),
    '',
    'TASK:',
    lane.task,
    '',
    'WORKFLOW (do all of this inside your worktree):',
    '  1. Confirm your worktree is based on ' + BASE + ' (git log -1 --format=%H). Create your branch:',
    '       git checkout -b ' + lane.branch,
    '  2. Implement the task, editing ONLY owned files. Read the existing file(s) first and MATCH the',
    '     surrounding style, naming, and comment density. Keep public interfaces stable unless your',
    '     OWNED list is the sole consumer.',
    '  3. Add adversarial + regression tests for the new behavior.',
    '  4. Format + lint ONLY your owned files with the pinned ruff (0.15.20, already on PATH):',
    '       ruff format <your owned .py files>',
    '       ruff check <your owned .py files and test files>',
    '  5. Run your lane tests (NOT the whole suite — keep it fast):',
    '       PYTHONPATH="' + TESTENV + ':$PWD" python3 -m pytest <your test files> -q',
    '     They must pass. If a change is deterministic-sensitive (a projection fingerprint), add a',
    '     test that a full rebuild and your incremental path produce the SAME fingerprint.',
    '  6. Commit on your branch with these trailers (exactly):',
    '       git add -A && git commit -m "<subject>',
    '',
    '       <body>',
    '',
    '       Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>',
    '       Claude-Session: https://claude.ai/code/session_01GNKVaKXKb8vwuiwhygggc2"',
    '',
    'Do NOT run the heartbeat smoke (slow; heartbeat is frozen and untouched). Do NOT merge to main.',
    'Do NOT push. The orchestrator runs the authoritative full gate and integrates.',
    '',
    'Return the IMPL_SCHEMA object: your branch, head_sha (git rev-parse HEAD), the exact files you',
    'changed, new test files, your fast-gate results (ruff/format/pytest), a one-paragraph note on',
    'HOW you preserved each relevant invariant, out_of_scope_edits (MUST be empty), and a summary.',
  ].join('\n')
}

function reviewPrompt(lane, role, roleBrief) {
  return [
    'You are the ' + role + ' reviewer for Decima evolution lane "' + lane.key + '".',
    'The implementation is committed on branch ' + lane.branch + ' in the shared repo at ' + REPO + '.',
    'Review it by reading the diff and the new/changed files:',
    '    git -C ' + REPO + ' diff ' + BASE + '..' + lane.branch + ' --stat',
    '    git -C ' + REPO + ' diff ' + BASE + '..' + lane.branch,
    '    git -C ' + REPO + ' show ' + lane.branch + ':<path>   # to read a full changed file',
    'You MAY check out nothing and run nothing that mutates the repo. To run the lane tests, use a',
    'throwaway: you may `git -C ' + REPO + ' worktree add` is NOT allowed; instead reason from the',
    'diff. If you must execute, only read-only commands.',
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
    '    (' + lane.interface + ')?',
    '  - Invariant preservation: determinism (fingerprint reproducibility, no wall-clock/random),',
    '    no capability weakening, models-propose/code-authorizes, untrusted stays DATA, no ambient',
    '    authority, four-verbs-only, frozen dirs untouched.',
    '  - Scope: did it edit ONLY owned files (' + lane.owns.join(', ') + ')? Any out-of-scope edit is',
    '    at least High.',
    '  - Tests: are there real adversarial tests, or only happy-path? Missing adversarial coverage on',
    '    a new capability is a High.',
    '',
    'Return REVIEW_SCHEMA. verdict=REJECT if you find ANY Blocker or High; else APPROVE. Every finding',
    'must be concrete (file + line + why it is wrong + a failing scenario), not stylistic hand-waving.',
    'If you find nothing real, APPROVE with an empty findings array — do NOT invent issues.',
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

// ─────────────────────────────────────────────────────────────────────────────
const LANES = [
  {
    key: 'qa-retrieval',
    branch: 'evo2/qa-retrieval',
    interface: 'SearchIndex.query(query, *, limit)->list[Hit], Hit dataclass, semantic_rank(hits, query), content_tokens/tokens, fingerprint()/rebuild()',
    owns: [
      'decima/projections/search.py',
      'tests/projections/ (new/edited test files)',
      'tests/capabilities/test_qa_grounding.py',
    ],
    task: [
      'Improve grounded-Q&A retrieval quality in decima/projections/search.py, ENTIRELY behind the',
      'existing interface (SearchIndex.query / Hit / semantic_rank / fingerprint / rebuild) so',
      'decima/services/api/qa_service.py and decima/capabilities/qa.py stay untouched.',
      '',
      'Deliver, all DETERMINISTIC and dependency-free (NO numpy/vector/embedding library may enter',
      'this package — that would break the local-first, deterministic invariant):',
      '  (a) HYBRID scoring: keep the exact content-token overlap gate (a segment sharing only',
      '      stopwords with the query is NOT citable — this is a load-bearing security property from',
      '      0.3, keep test_qa_grounding green), but rank the survivors with a richer deterministic',
      '      signal: IDF-style weighting (rarer corpus tokens weigh more than common ones), a',
      '      char-n-gram fuzzy bonus (so "running" can match "run"/"runs" as a SECONDARY signal, never',
      '      enough on its own to make a stopword-only overlap citable), and a proximity/phrase bonus.',
      '      Scores stay integers (or a fixed-scale integer), tie-breaks stay total + stable, and the',
      '      Hit.score remains a meaningful integer. Give semantic_rank() a REAL deterministic',
      '      implementation (documented as a dependency-free proxy, not embeddings) OR fold the hybrid',
      '      signal into query(); keep the seam comment describing how a true vector backend would',
      '      wrap in behind the same Hit list later.',
      '  (b) INCREMENTAL indexing: add methods to add/remove a single knowledge item to the inverted',
      '      index without a full rebuild, such that after any sequence of incremental updates the',
      "      index's fingerprint() is BYTE-IDENTICAL to a full rebuild() from the same knowledge fold.",
      '      Prove it with a property/example test (incremental == full-rebuild fingerprint).',
      '  (c) Preserve provenance, trust, and instruction_eligible on every Hit; deleting the index',
      '      still loses nothing canonical.',
      'The stopword-only-overlap-is-not-citable behavior and existing determinism tests MUST stay green.',
    ].join('\n'),
  },
  {
    key: 'planner-composition',
    branch: 'evo2/planner-composition',
    interface: 'RequestPlanProposal / AcceptPlanProposal command handlers and their existing routes in contracts.py (which you must NOT edit); PLAN_PROPOSAL_SCHEMA; the ModelStack.propose seam',
    owns: [
      'decima/services/api/plan_service.py',
      'decima/services/api/models_setup.py',
      'tests/api/test_plan_service.py',
    ],
    task: [
      'Expand planner COMPOSITION in decima/services/api/plan_service.py so a model-proposed plan can',
      'compose the real product capabilities — grounded Q&A (derive-from-knowledge), the isolated',
      'workspace, document ingestion, and an approval/gated step — as typed plan steps with richer',
      'dependency graphs, ALL under the existing proposal -> validation -> authorization -> execution',
      'flow. Today KNOWN_STEP_CAPABILITIES = {"local:derive","local:note"} and MAX_PLAN_STEPS=32.',
      '',
      'Requirements (hard):',
      '  - AcceptPlanProposal stays the SOLE durable Plan/Step/Agent minting point. Nothing else mints',
      '    durable planning Cells. The model only PROPOSES (its output is a plan_proposal Cell with',
      '    instruction_eligible=False, DATA); deterministic code in this module VALIDATES every step',
      '    (id/description/capability/depends_on shape, capability in the allow-set, acyclic deps,',
      '    step cap) and AUTHORIZES before minting. No capability weakening: a step that names a',
      '    capability the requesting principal does not hold must be REFUSED at validation, not minted.',
      '  - Extend KNOWN_STEP_CAPABILITIES to the new step kinds with EXPLICIT per-kind validation',
      '    (required selector fields per kind), and validate the dependency graph (referenced depends_on',
      '    ids exist, no cycles, deterministic topological readiness). Keep MAX_PLAN_STEPS as a hard cap.',
      '  - Enrich _PLAN_PROMPT + PLAN_PROPOSAL_SCHEMA so a real model can emit the richer step shapes,',
      '    and update PlanAwareDeterministicProvider in models_setup.py so the DETERMINISTIC provider',
      '    emits a valid composed plan for representative objectives (this keeps the offline default',
      '    working and gives deterministic tests). models_setup.py edits must be limited to the',
      '    deterministic plan-proposal behavior — do NOT change routing authority, cost ranks, or the',
      '    RECOMMENDED_LOCAL_MODEL literal.',
      '  - Do NOT edit contracts.py, routes.py, or add routes: the RequestPlanProposal/AcceptPlanProposal',
      '    handlers already exist and delegate here. Work entirely within the service + provider.',
      '  - Determinism: validation, ordering, and readiness are deterministic; no wall-clock/random in',
      '    minted Cells; stable step ordering.',
      'Adversarial tests required: cyclic deps refused; unknown/over-privileged capability refused; a',
      'hostile model proposal (injection text, missing fields, > MAX_PLAN_STEPS, dangling depends_on)',
      'is rejected with NO durable effect; accept mints exactly the validated forest and nothing more.',
    ].join('\n'),
  },
  {
    key: 'worker-isolation',
    branch: 'evo2/worker-isolation',
    interface: 'the worker execution entry (run/_BOOTSTRAP/apply_namespaces) and containment_report(); decima/workers/__init__.py exports; the WorkerProfile contract',
    owns: [
      'decima/workers/execution.py',
      'decima/workers/__init__.py',
      'docs/architecture/worker-containment.md',
      'tests/adversarial/ (new/edited test files)',
    ],
    task: [
      'Strengthen worker isolation in decima/workers/execution.py BEYOND the current floor, behind the',
      'existing worker interface (decima/services/api/workspace_service.py must stay untouched).',
      '',
      'Current enforced floor (do NOT weaken or remove any of it): RLIMIT_CPU/AS/NOFILE/NPROC/FSIZE',
      'set+read-back, prctl(PR_SET_NO_NEW_PRIVS), prctl(PR_SET_DUMPABLE,0), user+mount+net namespaces,',
      'chroot into the scratch jail. Documented-absent (honest): CLONE_NEWPID, seccomp, cgroup.',
      '',
      'Add the STRONGEST additional hardening that is genuinely enforceable ON THIS HOST (rootful',
      'arm64 Linux 6.6), following the fail-closed / capability-detected discipline:',
      '  - Evaluate and, where safely enforceable, ADD: a PID namespace (CLONE_NEWPID with a correct',
      '    PID-1/reaper so the worker cannot see or signal host PIDs), and/or a seccomp-bpf syscall',
      '    allow-list (via prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER) + a raw BPF program built with',
      '    ctypes — no libseccomp dependency), and/or closing inherited fds beyond the pipe set.',
      '  - CAPABILITY-DETECT each new mechanism at launch. If a mechanism you TREAT AS A HARD FLOOR is',
      '    unenforceable, REFUSE to launch (fail-closed) rather than run degraded. If a mechanism is',
      '    best-effort, degrade gracefully and record its absence honestly in containment_report().',
      '  - CRITICAL: you must NOT break the existing worker path. The heartbeat smoke and the workspace',
      '    tests launch real workers; if your hardening makes workers fail to start on this host, that',
      '    is a regression, not hardening. Anything you cannot make robust must degrade gracefully and',
      '    be documented as absent — exactly as Wave 1 honestly documented seccomp as not-added. Do not',
      '    force a mechanism that destabilizes the floor.',
      '  - Update containment_report() (keep it a PURE function) and docs/architecture/worker-containment.md',
      '    to reflect exactly what is now enforced vs best-effort vs absent — no overclaiming.',
      'Adversarial tests required, and they must ACTUALLY LAUNCH a worker (an escape/repro lens, per the',
      'Wave-1 lesson that a security slice needs a test that really runs the isolation): prove the new',
      'mechanism holds (e.g. worker cannot see host PIDs / blocked syscall is denied), prove the floor',
      'still holds, and prove fail-closed refuses when a hard-floor mechanism is unavailable.',
    ].join('\n'),
  },
]

// ─────────────────────────────────────────────────────────────────────────────
log('Wave 2: 3 file-disjoint lanes (qa-retrieval | planner-composition | worker-isolation)')
log('Each lane: worktree-isolated impl -> independent-correctness review + adversarial review')

const results = await pipeline(
  LANES,
  // Stage 1 — implementation, isolated per worktree.
  (lane) =>
    agent(implPrompt(lane), {
      label: 'impl:' + lane.key,
      phase: 'Implement',
      schema: IMPL_SCHEMA,
      isolation: 'worktree',
    }),
  // Stage 2 — two independent reviewers per lane, in parallel, reading the committed branch.
  (impl, lane) => {
    if (!impl) return { lane: lane.key, impl: null, reviews: [] }
    return parallel([
      () =>
        agent(reviewPrompt(lane, 'independent-correctness', 'Assume the implementer is competent but fallible. Verify the task is actually met and no interface/invariant is broken. Focus on real correctness and determinism defects.'), {
          label: 'review:' + lane.key + ':correctness',
          phase: 'Review',
          schema: REVIEW_SCHEMA,
        }),
      () =>
        agent(reviewPrompt(lane, 'adversarial', 'Assume the implementer made a subtle security/determinism mistake and try to PROVE it: hunt for capability weakening, an ambient-authority leak, a non-deterministic recorded value, a hostile-input path with a durable side effect, an overclaimed containment guarantee, or missing adversarial tests. Default to REJECT if a real hole exists.'), {
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
