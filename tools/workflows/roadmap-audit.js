export const meta = {
  name: 'decima-roadmap-audit',
  description: 'Read-only completeness audit of the whole roadmap (P1-P5) against the actual code, model-routed (Sonnet 5 for stable phases, Fable 5 for recent), synthesized (Fable 5) into the definitive remaining-work punch-list + P6-readiness verdict',
  phases: [
    { title: 'Audit', detail: 'one read-only reader per phase; judge real-vs-thin against the code; list undone roadmap sub-items', model: 'mixed' },
    { title: 'Synthesize', detail: 'Fable 5: the authoritative remaining-work list as executable fleet batches + P6-readiness verdict', model: 'fable' },
  ],
}

const REPO = '/home/mini/decima-claude'
const HB = `${REPO}/heartbeat`

const AUDIT_RULES = `
You are AUDITING, not building. READ ONLY — do NOT edit, create, commit, or run anything that mutates ${REPO}. You may read files and run read-only commands (grep, ls, python3 -c for a quick import check is fine, but do NOT run smoke.py-mutating steps or write files).
Ground truth is the CODE, not the board's prose. For each capability the board claims for this phase, open the actual module + its check and judge: is the enforcement REAL and load-bearing, or THIN (a stub / a decorative check / a documented-deferred placeholder)? Cite file:line evidence. Be skeptical and concrete — a board paragraph is a claim to verify, not a fact.
The roadmap (top of ${REPO}/docs/BACKLOG.md) defines each phase as a list of named sub-items. Your job: for THIS phase, which named sub-items are (a) real & enforced, (b) present-but-thin, (c) NOT done at all. Name the gap precisely with the file that would hold it.
CONTEXT — a background fleet (Phase-5 batch 2) is landing these four lanes right now; treat them as DONE and do not list them as gaps: selfupdate.py (install/self-update), voice_shell.py (voice-first shell), mediated_browser.py (mediated browser), and a citizens.py hardening (omitted-target + MCP-bridge scope gate). Everything ELSE is fair game.
`

const PHASES = [
  {
    key: 'P1', model: 'sonnet', effort: 'medium',
    title: 'Phase 1 — Enforcement',
    roadmap: 'untrusted-content quarantine boundary · real worker isolation (seccomp/landlock/microVM/WASM) · network egress boundary · sync channel confidentiality + peer auth',
    read: 'quarantine.py, sandbox / cli_worker.py (worker isolation — is it REAL OS-level seccomp/landlock/WASM, or a documented seam/stub?), live_wire.py + wire (egress boundary), sync.py + crypto.py (sync channel confidentiality + peer auth), and their checks. Cycles ~47 and the SANDBOX/egress/channel checks.',
  },
  {
    key: 'P2', model: 'sonnet', effort: 'medium',
    title: 'Phase 2 — Go live',
    roadmap: 'model brain as the default driver · a few engines live against real accounts · a real surface with an approval inbox',
    read: 'agent.py (ModelBrain driver + the egress redaction gate), golive.py + run.py (operator go-live rail), inbox.py (ApprovalInbox), the engine modules (are they wired for LIVE via the gated transport, or only offline stubs?). Cycles 48/51/52. Note what is genuinely blocked only on the operator API key vs what is still code-incomplete.',
  },
  {
    key: 'P3', model: 'sonnet', effort: 'medium',
    title: 'Phase 3 — Self-extension',
    roadmap: 'the forge-real loop: intent → codegen → sandboxed test → scan → attested promotion → versioning',
    read: 'forge.py (synthesize/forge), reckoner.py (sandboxed test/eval), detection.py or the scan step, promotion.py (attested promotion + register_version), quarantine.py (born-quarantined). Cycle 49. Is every arrow of intent→codegen→test→scan→promote→version real and enforced, or are stages stubbed?',
  },
  {
    key: 'P4', model: 'fable', effort: 'high',
    title: 'Phase 4 — Always-on substrate',
    roadmap: 'durable scheduling/background across restart · crash-resumable execution · concurrency · observability + live spend metering · live-world test harness · key rotation/recovery · schema migration · backup/restore',
    read: 'resume.py (crash-resumable), daemon.py (durable scheduling across restart), concurrency.py, observ.py (observability + spend metering — is LIVE spend metering wired or just folded metrics?), rotation.py (key rotation/recovery), migrate.py (schema migration), backup.py (backup/restore), and the "live-world test harness" item (is there one? tracing.py?). Cycles 53/54/55. Flag any named sub-item that is thin or missing — pay special attention to "live-world test harness" and "live spend metering".',
  },
  {
    key: 'P5', model: 'fable', effort: 'high',
    title: 'Phase 5 — Full surface, citizens, mediated I/O, knowledge',
    roadmap: 'accreting voice-first Shell · terminals-as-citizens + real MCP mount/expose · sandboxed email digest + mediated browser · personal-corpus ingestion · multi-human · install/self-update',
    read: 'multihuman.py (multi-human, Cycle 56), citizens.py + mcp.py + mcp_server.py (terminals-as-citizens + REAL MCP mount/expose — is mount/expose deep & real, with consent/resource support, or thin?), corpus.py (personal-corpus ingestion), maildigest.py (email digest), knowledge.py + research.py (the "knowledge" strand). REMEMBER voice_shell/mediated_browser/selfupdate/citizens-hardening are landing via batch 2 — treat as done. Focus on what P5 STILL lacks: especially the DEPTH of "real MCP mount/expose", and anything in the "knowledge" strand not yet real.',
  },
]

const PHASE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['phase', 'items', 'gaps', 'phase_green', 'summary'],
  properties: {
    phase: { type: 'string' },
    items: {
      type: 'array',
      description: 'one entry per named roadmap sub-item for this phase',
      items: {
        type: 'object', additionalProperties: false,
        required: ['name', 'status', 'evidence'],
        properties: {
          name: { type: 'string', description: 'the roadmap sub-item' },
          status: { type: 'string', enum: ['real', 'thin', 'missing', 'operator-gated'], description: 'real=enforced & load-bearing; thin=present but stubbed/decorative/deferred; missing=not done; operator-gated=code-complete, blocked only on the human operator (e.g. an API key)' },
          evidence: { type: 'string', description: 'file:line or module/check citation backing the status' },
        },
      },
    },
    gaps: {
      type: 'array',
      description: 'concrete remaining work for this phase — each a candidate lane',
      items: {
        type: 'object', additionalProperties: false,
        required: ['gap', 'where', 'hardness'],
        properties: {
          gap: { type: 'string', description: 'what is missing/thin, precisely' },
          where: { type: 'string', description: 'the file/module that would hold it' },
          hardness: { type: 'string', enum: ['mechanical', 'correctness-heavy'], description: 'mechanical → Sonnet 5; correctness/security-heavy → Fable 5' },
        },
      },
    },
    phase_green: { type: 'boolean', description: 'is this phase GENUINELY complete (every sub-item real or legitimately operator-gated/deferred-by-design)?' },
    summary: { type: 'string' },
  },
}

const SYNTH_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['roadmap_green', 'p6_ready', 'p6_rationale', 'remaining_batches', 'assessment'],
  properties: {
    roadmap_green: { type: 'boolean', description: 'is the Python reference roadmap (P1-P5) genuinely green modulo operator-gated items?' },
    p6_ready: { type: 'boolean', description: 'is the reference stable & roadmap-green enough to START the P6 Rust port (per the P6 gate)?' },
    p6_rationale: { type: 'string' },
    remaining_batches: {
      type: 'array',
      description: 'the remaining work, grouped into executable fleet batches, ordered by what to do next',
      items: {
        type: 'object', additionalProperties: false,
        required: ['batch', 'lanes', 'rationale'],
        properties: {
          batch: { type: 'string' },
          lanes: {
            type: 'array',
            items: {
              type: 'object', additionalProperties: false,
              required: ['name', 'module', 'why', 'model'],
              properties: {
                name: { type: 'string' },
                module: { type: 'string', description: 'proposed new module (or existing module to harden)' },
                why: { type: 'string' },
                model: { type: 'string', enum: ['fable', 'sonnet'], description: 'fable=correctness/security-heavy; sonnet=mechanical' },
              },
            },
          },
          rationale: { type: 'string' },
        },
      },
    },
    assessment: { type: 'string', description: 'the bottom line: what is left, how many batches, and whether P6 can begin' },
  },
}

const phaseResults = await parallel(PHASES.map((p) => () =>
  agent(
    `Audit ONE phase of Decima's roadmap against the actual code (an agent-native OS reference in pure-stdlib Python at ${HB}).

PHASE: ${p.title}
ROADMAP DEFINITION (the named sub-items you must account for): ${p.roadmap}
READ (in ${HB}): ${p.read}
Also read the phase's board entries at the top+body of ${REPO}/docs/BACKLOG.md for the CLAIMS to verify.
${AUDIT_RULES}
For each named sub-item return {name, status (real/thin/missing/operator-gated), evidence (file:line)}. Then list concrete gaps (each with the file that would hold it and whether it is mechanical or correctness-heavy). Then judge phase_green. Be precise and skeptical; cite code.`,
    { label: `audit:${p.key}`, phase: 'Audit', agentType: 'general-purpose', model: p.model, effort: p.effort, schema: PHASE_SCHEMA },
  ).then((r) => ({ key: p.key, report: r }))
))

const reports = phaseResults.filter(Boolean)
log(`audited ${reports.length}/${PHASES.length} phases; synthesizing the punch-list`)

const dossier = reports.map((r) => `### ${r.key}\nphase_green=${r.report?.phase_green}\nitems=${JSON.stringify(r.report?.items)}\ngaps=${JSON.stringify(r.report?.gaps)}\nsummary=${r.report?.summary}`).join('\n\n')

const synthesis = await agent(
  `You are the SYNTHESIS lead. Five read-only auditors reported on Decima's roadmap phases P1-P5 (a pure-stdlib agent-native OS reference; the FINAL phase P6 is a single Rust port, explicitly GATED on "the reference being stable with this roadmap green"). A background fleet is landing Phase-5 batch 2 right now (selfupdate, voice_shell, mediated_browser, citizens-hardening) — treat those as DONE.

THE PHASE DOSSIER:
${dossier}

Produce the AUTHORITATIVE remaining-work plan:
  - roadmap_green: is P1-P5 genuinely complete (modulo legitimately operator-gated items like the live API key)?
  - p6_ready + p6_rationale: per the P6 gate, is the reference stable & roadmap-green enough to START the Rust port? Be honest — if real gaps remain, say not yet and name them.
  - remaining_batches: group ALL remaining work into executable sub-agent fleet batches (each lane = a new module or a hardening, with a model recommendation: fable for correctness/security-heavy, sonnet for mechanical), ORDERED by what to do next. Keep lanes disjoint (one file each) so they land conflict-free. Do NOT include batch-2's four lanes.
  - assessment: the bottom line — what is left, how many batches, and whether/when P6 can begin.
Be concrete and ruthless about thin spots; the goal is a plan I will execute fleet-by-fleet.`,
  { label: 'synthesize:punch-list', phase: 'Synthesize', agentType: 'general-purpose', model: 'fable', effort: 'high', schema: SYNTH_SCHEMA },
)

return { reports: reports.map((r) => ({ phase: r.key, phase_green: r.report?.phase_green, gaps: r.report?.gaps, items: r.report?.items })), synthesis }
