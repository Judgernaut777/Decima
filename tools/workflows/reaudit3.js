export const meta = {
  name: 'decima-roadmap-reaudit3',
  description: 'THIRD read-only completeness audit of the whole roadmap (P1-P5) against the actual code after Batch S — model-routed (Sonnet 5 stable phases, Fable 5 recent), synthesized (Fable 5) into the definitive P6-readiness verdict + any residual punch-list. Includes a fresh exhaustive callerless-module sweep to catch recurrence of the "proven library, no caller" pattern.',
  phases: [
    { title: 'Audit', detail: 'one read-only reader per phase + a dedicated callerless-sweep reader; judge real-vs-thin against the code', model: 'mixed' },
    { title: 'Synthesize', detail: 'Fable 5: the authoritative P6-readiness verdict + any residual batches', model: 'fable' },
  ],
}

const REPO = '/home/mini/decima-claude'
const HB = `${REPO}/heartbeat`

const AUDIT_RULES = `
You are AUDITING, not building. READ ONLY — do NOT edit, create, commit, or run anything that mutates ${REPO}. You may read files and run read-only commands (grep, ls, python3 -c for a quick import check; a full read-only smoke.py run is fine to observe green, but do NOT write files).
Ground truth is the CODE, not the board's prose. For each capability the board claims for this phase, open the actual module + its check and judge: is the enforcement REAL and load-bearing, or THIN (a stub / a decorative check / a documented-deferred placeholder)? Cite file:line evidence. A board paragraph is a claim to verify, not a fact.
The roadmap (top of ${REPO}/docs/BACKLOG.md) defines each phase as a list of named sub-items. For THIS phase, judge which named sub-items are (a) real & enforced, (b) present-but-thin, (c) NOT done. Name each gap precisely with the file that would hold it.
CONTEXT — this is the THIRD re-audit. History of the recurring failure mode ("proven library, no caller" — a check-proven module that nothing in the RUNNING system calls):
  • FIRST re-audit (Cycle 62): found 3 such gaps — bind_strategy zero callers, privacy-map divergence, zero P5 modules reachable from the shell. Batch R (Cycles 63-64) claimed to close them.
  • SECOND re-audit (Cycle 64): P1-P4 green, but P5 STILL RED on SIX MORE such seams: (1) do_view never routed to workspace.render for user-defined views; (2) research.research had zero production callers; (3) mailpoll.schedule_poll was never armed; (4) mcp_server.handle() had no serving transport; (5) discovery.discover at kernel.py:544 + agent.py:317 both omitted forge= (real forge pipeline unreached); (6) do_forge still routed the pre-Cycle-49 toy.
  • Batch S (Cycle 65, JUST LANDED, 216 checks green) claims to close ALL SIX: shellwire (shell.py 488 — do_view→workspace.render fallback + do_research→research.research + do_mail arm→mailpoll.schedule_poll + do_forge→forge.forge + do_mcpserve→mcp_server.handle), forgerealdefault (discovery.py 490 — discover() DEFAULTS forge to forge.forge so both production sites inherit it), mcpserve (mcp_server.py 492 — serve_stdio serving transport driving handle() through the gate).
YOUR JOB: verify (against the CODE, on the RUNNING path) each Batch-S wiring is GENUINELY a real caller — not another unwired helper + an isolated check. Be maximally skeptical; this audit decides whether the P6 Rust port opens. If P1-P5 is now green modulo the operator's API key (a legitimate gate, not a code gap), say so plainly; if ANY real code gap remains, name it precisely with file:line.
`

const PHASES = [
  {
    key: 'P1', model: 'sonnet', effort: 'medium',
    title: 'Phase 1 — Enforcement',
    roadmap: 'untrusted-content quarantine boundary · real worker isolation (seccomp/landlock/microVM/WASM) · network egress boundary · sync channel confidentiality + peer auth',
    read: 'quarantine.py, isolation.py / cli_worker.py (worker isolation — REAL OS-level seccomp/landlock/WASM, or a documented seam/stub?), live_wire.py + wire (egress boundary), sync.py + crypto.py (sync channel confidentiality + peer auth), and their checks. This phase was green in both prior re-audits — confirm no regression from Batch S (which did not touch these files).',
  },
  {
    key: 'P2', model: 'sonnet', effort: 'medium',
    title: 'Phase 2 — Go live',
    roadmap: 'model brain as the default driver · a few engines live against real accounts · a real surface with an approval inbox',
    read: 'golive.py activate_engine populates k.live_engines (check 460) + shell.py do_flip exposes it (check 480). agent.py ModelBrain (redaction + _route_and_meter + reconciled privacy map). Confirm the only remaining P2 item is the legitimately operator-key-gated live flip (real API key + human-approved grant), NOT a code gap. Green in both prior re-audits — confirm no regression.',
  },
  {
    key: 'P3', model: 'fable', effort: 'high',
    title: 'Phase 3 — Self-extension',
    roadmap: 'the forge-real loop: intent → codegen → sandboxed test → scan → attested promotion → versioning',
    read: 'CRITICAL for this re-audit: Batch S forgerealdefault (discovery.py 490) claims discover() now DEFAULTS its forge seam to the REAL forge.forge pipeline, closing the second-re-audit gap where kernel.py:544 (kernel.say chat-fallback) and agent.py:317 (suggest_capabilities) passed no forge= and reached the stub/toy. VERIFY on the CODE: (a) does discovery.discover default forge to forge.forge? grep discovery.py for the default; (b) do BOTH production call sites now inherit the real pipeline (they still pass no explicit forge=, so they get the default)? read kernel.py:~544 and agent.py:~317; (c) is do_forge in shell.py now routed through forge.forge (not the pre-Cycle-49 toy reckoner path)? grep shell.py do_forge; (d) is the explicit-forge= test-injection seam preserved and does a failing candidate get REFUSED (not stubbed)? Also confirm the underlying pipeline (forge.py, reckoner.py, promotion.py, quarantine.py) is real. Judge whether P3 self-extension is now REACHED by production, not just check-proven.',
  },
  {
    key: 'P4', model: 'fable', effort: 'high',
    title: 'Phase 4 — Always-on substrate',
    roadmap: 'durable scheduling/background across restart · crash-resumable execution · concurrency · observability + live spend metering · live-world test harness · key rotation/recovery · schema migration · backup/restore',
    read: 'Green in the second re-audit. Confirm no regression + spot-check the wiring: golive.py bind_strategy_plane called at boot (check 476, strategy non-None on live boot), _route_and_meter on the live path (456), do_beat drives daemon.resume + concurrency.run_concurrent (458, 480), weft consults rotation chain (462), mailpoll drives mail_engine.receive on the beat (482, and NOW armable via do_mail arm per Batch S 488). Flag any residual.',
  },
  {
    key: 'P5', model: 'fable', effort: 'xhigh',
    title: 'Phase 5 — Full surface, citizens, mediated I/O, knowledge',
    roadmap: 'accreting voice-first Shell · terminals-as-citizens + real MCP mount/expose · sandboxed email digest + mediated browser · personal-corpus ingestion · multi-human · install/self-update',
    read: 'THE DECIDING PHASE. The second re-audit ruled P5 RED on six "proven library, no caller" seams; Batch S (Cycle 65) claims to close all six. VERIFY EACH on the CODE, on the RUNNING path (grep the verb/default body — a REAL call, not a help-string stub): (1) shell.py do_view now falls through the four built-ins to workspace.render for a user-defined view + view define grows one via workspace.define_view (check 488); (2) shell.py do_research composes research.research (check 488); (3) shell.py do_mail "arm" composes mailpoll.schedule_poll so the beat receives mail on its own (check 488); (4) mcp_server.py serve_stdio drives handle() through the gate — a real serving transport (check 492); (5) discovery.py discover() defaults forge to forge.forge (check 490 — cross-check with P3); (6) shell.py do_forge routes forge.forge + do_mcpserve drives mcp_server.handle (check 488). ALSO re-confirm the earlier-landed P5 surface still holds: do_flip/do_mcp/do_mail/do_corpus/do_browse/do_citizen/do_human/do_update/do_voice/do_migrate (check 480), accreting views (484), research synthesis (486), MCP depth (468/470), mail (472), corpus (474), multihuman/citizens/voice_shell/mediated_browser/selfupdate (438-450). Judge: is P5 NOW genuinely green — every named sub-item real or legitimately deferred-by-design (e.g. real whisper.cpp/Piper voice engines behind the voice contract; real OS-MCP-server launch entrypoint) — or does ANY real code gap remain? Name residual thinness precisely.',
  },
  {
    key: 'SWEEP', model: 'fable', effort: 'xhigh',
    title: 'Callerless-module sweep — catch recurrence of the pattern',
    roadmap: 'NO check-proven decima/*.py module should be unreachable from the running system (a shell verb, a boot/reactor hook, a production call site, or a legitimately operator/test-only harness).',
    read: `Do a FRESH exhaustive sweep for the recurring "proven library, no caller" pattern across ALL of ${HB}/decima/*.py. For EACH module, determine whether it is REACHED by the running system (imported+called by kernel.py/reactor.py/agent.py/shell.py/run.py/golive.py on a production path, OR armed as a recurring job, OR a legitimately operator-invoked shell verb, OR an explicitly test-only/harness module like liveworld.py). Method: for each decima/<mod>.py, grep for importers within decima/ and for shell.py verb references and for reactor/boot hooks; list any module whose ONLY caller is its own check file checks/NN_*.py. Report, per suspicious module: module name, its public entry functions, who (if anyone) calls them on the running path, and your verdict {reached | callerless | harness-ok}. This is the anti-recurrence backstop — if Batch S missed a seam or introduced a new one, find it here. Be exhaustive; enumerate every decima module and classify it.`,
  },
]

const PHASE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['phase', 'items', 'gaps', 'phase_green', 'summary'],
  properties: {
    phase: { type: 'string' },
    items: {
      type: 'array',
      description: 'one entry per named roadmap sub-item (for SWEEP: one entry per suspicious/borderline module)',
      items: {
        type: 'object', additionalProperties: false,
        required: ['name', 'status', 'evidence'],
        properties: {
          name: { type: 'string' },
          status: { type: 'string', enum: ['real', 'thin', 'missing', 'operator-gated', 'reached', 'callerless', 'harness-ok'], description: 'real/reached=enforced & on the running path; thin=present but stubbed/decorative; missing/callerless=proven but no production caller; operator-gated=code-complete blocked only on the human; harness-ok=legitimately test/operator-only' },
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
          gap: { type: 'string' },
          where: { type: 'string', description: 'the file/module that would hold it' },
          hardness: { type: 'string', enum: ['mechanical', 'correctness-heavy'] },
        },
      },
    },
    phase_green: { type: 'boolean', description: 'is this phase GENUINELY complete (every sub-item real or legitimately operator-gated/deferred-by-design)? For SWEEP: true iff NO callerless module remains.' },
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
      description: 'remaining work grouped into executable fleet batches, ordered by what to do next (should be small/empty beyond Batch D if green)',
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
                module: { type: 'string' },
                why: { type: 'string' },
                model: { type: 'string', enum: ['fable', 'sonnet'] },
              },
            },
          },
          rationale: { type: 'string' },
        },
      },
    },
    assessment: { type: 'string', description: 'the bottom line: what is left, how many batches, and whether/when P6 can begin' },
  },
}

const phaseResults = await parallel(PHASES.map((p) => () =>
  agent(
    `Audit ONE phase of Decima's roadmap against the actual code (an agent-native OS reference in pure-stdlib Python at ${HB}). Main is at Cycle 65 (Batch S just landed, 216 checks green).

PHASE: ${p.title}
ROADMAP DEFINITION (the named sub-items you must account for): ${p.roadmap}
READ (in ${HB}): ${p.read}
Also read the phase's board entries in ${REPO}/docs/BACKLOG.md for the CLAIMS to verify.
${AUDIT_RULES}
Return {name, status, evidence(file:line)} per sub-item (or per module for SWEEP), then concrete gaps (file + mechanical/correctness-heavy), then phase_green. Be precise and skeptical; cite code.`,
    { label: `audit:${p.key}`, phase: 'Audit', agentType: 'general-purpose', model: p.model, effort: p.effort, schema: PHASE_SCHEMA },
  ).then((r) => ({ key: p.key, report: r }))
))

const reports = phaseResults.filter(Boolean)
log(`audited ${reports.length}/${PHASES.length} phases; synthesizing the verdict`)

const dossier = reports.map((r) => `### ${r.key}\nphase_green=${r.report?.phase_green}\nitems=${JSON.stringify(r.report?.items)}\ngaps=${JSON.stringify(r.report?.gaps)}\nsummary=${r.report?.summary}`).join('\n\n')

const synthesis = await agent(
  `You are the SYNTHESIS lead. Read-only auditors reported on Decima's roadmap phases P1-P5 plus a dedicated callerless-module SWEEP (a pure-stdlib agent-native OS reference; the FINAL phase P6 is a single Rust port, explicitly GATED on "the reference being stable with this roadmap green"). This is the THIRD RE-AUDIT.

History: the first re-audit found 3 "proven library, no caller" gaps (Batch R closed them); the second re-audit found 6 MORE in P5 (Batch S, Cycle 65, just landed at 216 green, claims to close all six: do_view→workspace.render, do_research→research.research, do_mail arm→mailpoll.schedule_poll, mcp_server.serve_stdio→handle, discover() defaults forge→forge.forge at both sites, do_forge→forge.forge + do_mcpserve→handle). The auditors verified whether those are now genuinely closed on the running path, AND ran a fresh exhaustive sweep for any REMAINING or NEW callerless module.

THE PHASE DOSSIER:
${dossier}

Produce the AUTHORITATIVE verdict:
  - roadmap_green: is P1-P5 genuinely complete (modulo legitimately operator-gated items like the live API key, and deferred-by-design items like real whisper.cpp/Piper voice engines behind the voice contract)?
  - p6_ready + p6_rationale: per the P6 gate ("gated on the reference being stable with this roadmap green"), is the reference NOW ready to START the Rust port? Be honest — if the SWEEP or any phase found a real callerless/thin gap on the running path, say NOT YET and name it precisely with file:line. If the only remaining items are legitimately operator-gated or deferred-by-design, the roadmap is green and P6 is ready.
  - remaining_batches: any residual-thinness lanes the auditors flagged (each a candidate fleet lane, model-routed fable/sonnet). Then Batch D — the planned P6 on-ramp (conformance golden-vectors: a new conformance.py + fixtures freezing the reference's observable behavior as the Rust port's oracle, Fable; spawn-audit-hardening: isolation.py assert_no_raw_spawn beyond AST name-matching, Fable; oracle-freeze: a smoke.py manifest pinning the check set, Sonnet). Batch D is the ON-RAMP, appropriate once roadmap_green — include it as the next batch if green.
  - assessment: the bottom line — is the roadmap green, what (if anything) is left, and whether/when P6 can begin.
Be concrete and ruthless about thin spots; the goal is a definitive go/no-go on the Rust port.`,
  { label: 'synthesize:verdict', phase: 'Synthesize', agentType: 'general-purpose', model: 'fable', effort: 'high', schema: SYNTH_SCHEMA },
)

return { reports: reports.map((r) => ({ phase: r.key, phase_green: r.report?.phase_green, gaps: r.report?.gaps, items: r.report?.items })), synthesis }
