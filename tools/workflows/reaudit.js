export const meta = {
  name: 'decima-roadmap-reaudit',
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
CONTEXT — this is a RE-AUDIT. Since the first audit (Cycle 57), Cycles 58-62 landed all 13 remediation lanes across Batches A/B/C. VERIFY (against the code) that the specific gaps the first audit found are now GENUINELY CLOSED, not merely claimed on the board. Be skeptical: a landed check proves a library; confirm it is actually WIRED into the running system (e.g. is do_beat a real caller of daemon.advance? does ModelBrain._post really meter+route? does weft verification really consult the rotation chain?). Then judge whether any residual thinness remains. The prior verdict was "reference NOT green, P6 cannot start" — your job is to determine if that has changed.
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
    read: 'VERIFY the Cycle-59 remediation closed P2: golive.py activate_engine — does an approved grant NOW populate k.live_engines (check 460)? plus agent.py (ModelBrain + redaction + the new _route_and_meter), golive.py/run.py rail, inbox.py. Confirm the first-audit P2 gap (k.live_engines unpopulated seam) is CLOSED. Distinguish genuinely operator-key-gated items (legitimate, NOT a code gap) from any remaining code-incompleteness.',
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
    read: 'VERIFY the Cycle-58/59/61 remediations closed P4: liveworld.py (the live-world harness — now EXISTS? check 454), agent.py ModelBrain._post (_route_and_meter — is spend+provider NOW on the live path? check 456), shell.py do_beat + run.py (does the always-on loop have a PRODUCTION caller now? daemon.advance/resume actually driven? check 458), weft.py (does verification NOW consult rotation.valid_key_at — is key rotation load-bearing not orphaned? check 462), plus resume/daemon/concurrency/observ/migrate/backup. Confirm each first-audit P4 gap (live-world harness missing, live spend metering unwired, rotation kernel-orphaned, loop never driven) is CLOSED. Flag any residual.',
  },
  {
    key: 'P5', model: 'fable', effort: 'high',
    title: 'Phase 5 — Full surface, citizens, mediated I/O, knowledge',
    roadmap: 'accreting voice-first Shell · terminals-as-citizens + real MCP mount/expose · sandboxed email digest + mediated browser · personal-corpus ingestion · multi-human · install/self-update',
    read: 'VERIFY Batch C closed P5 depth: mcp.py (client — resources/prompts/elicitation/durable mounts NOW? check 468), mcp_server.py (server — resources/prompts/inputSchema-gate/per-consumer identity NOW? check 470), mail_engine.py (real inbound mail engine NOW? check 472), corpus.py (file walker/chunking/better recall NOW? check 474); plus multihuman/citizens/voice_shell/mediated_browser/selfupdate (all landed Cycles 56-57). Confirm the first-audit P5 gap (MCP tools-only, mail/corpus I/O unreal) is CLOSED. Flag any residual thinness (e.g. the knowledge strand knowledge.py/research.py if still thin).',
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
  `You are the SYNTHESIS lead. Five read-only auditors reported on Decima's roadmap phases P1-P5 (a pure-stdlib agent-native OS reference; the FINAL phase P6 is a single Rust port, explicitly GATED on "the reference being stable with this roadmap green"). This is a RE-AUDIT after Cycles 58-62 landed all 13 remediation lanes (Batches A/B/C) from the first audit. The auditors verified whether the first audit's gaps are now closed.

THE PHASE DOSSIER:
${dossier}

Produce the AUTHORITATIVE remaining-work plan:
  - roadmap_green: is P1-P5 genuinely complete (modulo legitimately operator-gated items like the live API key)?
  - p6_ready + p6_rationale: per the P6 gate, is the reference stable & roadmap-green enough to START the Rust port? Be honest — if real gaps remain, say not yet and name them.
  - remaining_batches: any REMAINING work (should be small or empty if the roadmap is now green). Batch D (conformance golden vectors + spawn-audit hardening) is the planned P6 on-ramp — include it. Add any residual-thinness lanes the auditors flagged. Empty (beyond Batch D) is a valid answer if P1-P5 is now green modulo the operator key.
  - assessment: the bottom line — what is left, how many batches, and whether/when P6 can begin.
Be concrete and ruthless about thin spots; the goal is a plan I will execute fleet-by-fleet.`,
  { label: 'synthesize:punch-list', phase: 'Synthesize', agentType: 'general-purpose', model: 'fable', effort: 'high', schema: SYNTH_SCHEMA },
)

return { reports: reports.map((r) => ({ phase: r.key, phase_green: r.report?.phase_green, gaps: r.report?.gaps, items: r.report?.items })), synthesis }
