export const meta = {
  name: 'decima-batchS-final-wiring',
  description: 'Batch S — the final wiring pass to make the roadmap green: shell-wire (do_view/do_research/do_mail-arm/do_forge/do_mcpserve reach their proven modules), forge-real-default (discovery.discover defaults to the real forge pipeline at both production sites), mcp-serve (a real serving transport). All Fable 5; each adversarially mutation-reviewed (Fable 5).',
  phases: [
    { title: 'Implement', detail: 'one Fable 5 agent per lane, isolated heartbeat copy; disjoint files (shell.py / discovery.py / mcp_server.py)', model: 'fable' },
    { title: 'Review', detail: 'adversarial mutation-test + REAL-CALLER audit per lane (Fable 5)', model: 'fable' },
  ],
}

const REPO = '/home/mini/decima-claude'

const HOUSE = `
DECIMA HOUSE RULES (obey exactly):
- Repo: ${REPO}. The heartbeat is at ${REPO}/heartbeat; the package ${REPO}/heartbeat/decima; checks auto-run from ${REPO}/heartbeat/checks/NN_*.py by smoke.py.
- WORK IN ISOLATION: WORK=$(mktemp -d); cp -r ${REPO}/heartbeat "$WORK/heartbeat"; cd "$WORK/heartbeat". NEVER edit the canonical ${REPO} tree.
- Pure Python STDLIB only (PyNaCl already present). NO new pip deps.
- Five Laws by SHAPE: everything on the Weft (append-only, model.assert_content); ZERO ambient authority (a shell verb / default / server mints NOTHING; outward+gated actions still route through kernel.invoke = authorize + Morta); UNTRUSTED CONTENT IS DATA (instruction_eligible=False); INTS-NOT-FLOATS in recorded content; FAIL CLOSED + DETERMINISTIC (no wall-clock/unseeded-random in recorded content).
- Do NOT edit smoke.py or the CORE-SERIALIZED files (weave.py, weft.py, kernel.py, executor.py). Your lane edits ONLY its assigned file and composes everything else over PUBLIC APIs. The 3 lanes have DISJOINT files (shell.py / discovery.py / mcp_server.py).
- STUDY the seams with Read before writing. For a shell verb match the EXACT do_ idiom in shell.py (def do_x(self, arg); self.k the Kernel; self.inbox; print "   " prefix). Model checks on checks/424_resume.py, checks/480_shellsurface.py.
- Your check heartbeat/checks/<NN>_<name>.py: def run(k, line): ... FAILS LOUD via assert; prints line("  ..."); ends "  → ...". Register your OWN hermetic effect via executor.register if needed — never 'echo'.
- SELF-TEST: cd "$WORK/heartbeat" && python3 smoke.py → must end "heartbeat: alive. ✓" exit 0, ALL existing checks green (210+ checks; minutes — be patient).
- Report the ONE load-bearing line whose reversion makes YOUR check go red, verbatim.
- DELIVERABLE = returned source (module_source + check_source; extra_file_path/extra_file_source ONLY for a disclosed second file). Do NOT commit/modify ${REPO}.
`

const LANES = [
  {
    name: 'shellwire', module: 'shell.py', nn: '488', model: 'fable', effort: 'xhigh', edits: true, extra: '',
    title: 'SHELL-WIRE — the last callerless proven libraries get their operator verbs (do_view/do_research/do_mail-arm/do_forge/do_mcpserve)',
    seams: 'shell.py (the cmd.Cmd surface + existing verbs incl do_view at ~765 — TODAY do_view only dispatches to the FOUR hardcoded lenses notes/board/graph/timeline and NEVER calls workspace.define_view/render; do_mail at ~269 supports only recv/digest and never arms the poll; do_forge at ~700 routes through a TOY reckoner path, not the real forge pipeline; there is no do_research and no do_mcpserve), workspace.py (define_view(k,name,spec) + render(k,name) + views(k) — the accreting-view API, check 484), research.py (research(k, agent, question, urls) — the real cited synthesis, check 486), mailpoll.py (schedule_poll(k, agent_cell, cap_id, *, interval, ...) — the recurring poll, check 482), forge.py (forge / forge_with — the REAL candidate→reckoner→promotion pipeline, check 464), mcp_server.py (handle(k, agent_cell, request) — the gated JSON-RPC handler + list_tools + resources, check 470). ALL these functions EXIST at the current HEAD — you only add the shell VERBS that call them.',
    spec: `
PURPOSE: the second re-audit found the SAME "proven library, no caller" pattern once more — five proven modules are unreachable from the operator Shell. Close ALL of them in shell.py (do NOT edit the modules — only ADD/FIX verbs that CALL their existing public APIs):
  - do_view: KEEP the four built-in lenses, but ADD a fallback so a NON-built-in name routes to workspace.render(self.k, name) (a user-defined/accreting view), and add a way to DEFINE one (e.g. "view define <name> <spec>") → workspace.define_view. An unknown/undefined view fails closed.
  - do_research <question> [urls...]: NEW verb → research.research(self.k, agent, question, urls); print the cited synthesis (DATA — instruction_eligible=False, never obeyed).
  - do_mail: KEEP recv/digest; ADD "mail arm [interval]" → mailpoll.schedule_poll(...) to ARM the always-on recurring poll (so the beat receives mail on its own). Print that the poll is armed.
  - do_forge: REROUTE it through the REAL pipeline — call forge.forge (candidate→reckoner→promotion, born quarantined, attested) instead of the toy reckoner path, so the operator's forge yields a real promoted (or refused) capability, not a stub/toy.
  - do_mcpserve: NEW verb → drive mcp_server.handle(self.k, agent_cell, request) to serve Decima's own tools/resources over MCP for a request (the gate is NOT bypassed — every tools/call still routes through authorize + Morta; foreign args validated by the inputSchema gate). Show it serving list_tools / a resources read.
  Every verb routes through the ordinary gates and mints nothing; untrusted results (research/mail/mcp) are DATA. Keep EVERY existing command + check green.

CHECK checks/488_shellwire.py proves, offline + deterministically (instantiate the Shell over a fresh Kernel; drive its do_ methods; capture output or assert weave-level effects):
  (a) EACH PROVEN MODULE IS NOW REACHABLE (load-bearing): driving do_view <a-user-defined-view> actually calls workspace.render (a defined view renders; before this it was unreachable); do_research calls research.research (a cited synthesis is produced as DATA); "mail arm" calls mailpoll.schedule_poll (the recurring poll is now scheduled — a subsequent beat receives mail); do_forge calls forge.forge (a REAL promoted capability, not the toy path); do_mcpserve calls mcp_server.handle (tools/resources served through the gate). Assert the underlying MODULE was actually invoked (weave-level effect / return), not a help-string stub.
  (b) GATE + DATA PRESERVED: gated verbs fail closed without approval; untrusted results are instruction_eligible=False.
  (c) NO REGRESSION: built-in views + existing verbs still work; all existing checks green.
  Mutation: revert ONE wiring (e.g. do_view's render fallback, or the do_mail schedule_poll arm) to a no-op/help-string → (a) goes RED (that module is unreachable again). State the load_bearing_line (the verb→module call your check pins).
This lane EDITS shell.py: module_path = heartbeat/decima/shell.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'sw2_probe'), never 'echo'. BREADTH lane — be thorough; keep smoke green.`
  },
  {
    name: 'forgerealdefault', module: 'discovery.py', nn: '490', model: 'fable', effort: 'high', edits: true, extra: '',
    title: 'FORGE-REAL DEFAULT — production discovery reaches the REAL forge pipeline (both discover() sites, no core edit)',
    seams: 'discovery.py (discover(k, goal, *, threshold, research=..., forge=...) — the plug-in-or-forge policy; its `forge` seam is the LAST resort; TODAY the two PRODUCTION callers pass NO forge=, so discovery falls back to a stub/toy: kernel.py:544 `discovery.discover(self, text, threshold=...)` and agent.py:317 `discovery.discover(k, goal, threshold=..., research=research)` — neither passes forge=), forge.py (forge / forge_with — the REAL candidate→reckoner→promotion adapter from Cycle 60, check 464; forge_with(codegen) builds the callable shaped for discover(..., forge=...)), candidate.py/reckoner.py/promotion.py (the real pipeline forge composes). Do NOT edit kernel.py or agent.py (core / owned elsewhere) — fix it at the discovery SEAM so BOTH sites inherit the real pipeline.',
    spec: `
PURPOSE: Cycle-60 forgereal made forge.forge route discovery-forge through the REAL candidate→reckoner→promotion pipeline — but the two PRODUCTION discover() call sites (kernel.py:544, agent.py:317) pass no forge=, so production self-extension still reaches the stub/toy last-resort. Close it AT THE SEAM (one file, non-core, so BOTH sites inherit it without editing kernel.py/agent.py): make discovery.discover DEFAULT its forge seam to the real forge.forge pipeline.

HARDEN decima/discovery.py (edit ONLY discovery.py): change the discover() forge parameter so that when a caller passes no forge (the production default), discovery uses the REAL forge.forge (compose forge / forge_with) as its last-resort — a forged capability is born quarantined, evaluated, and attested-promoted (or refused, fail closed), NOT a decorative stub. Preserve the injectable seam: a test passing an explicit forge= still overrides (tests inject deterministic codegen). Watch for import cycles (discovery importing forge — verify forge does not import discovery at module load; use a lazy import if needed). Keep every existing discovery/forge check green (the stub path may still be reachable ONLY when codegen is genuinely unavailable — the honest fallback — but the DEFAULT production path is now real).

CHECK checks/490_forgerealdefault.py proves, offline + deterministically (inject deterministic codegen where the pipeline needs it):
  (a) PRODUCTION DISCOVER REACHES THE REAL PIPELINE (load-bearing): call discover() the way production does (NO explicit forge=) for a goal that misses plug-in, and assert the forged result is a REAL promoted (born-quarantined→evaluated→attested) capability that runs its behavior — NOT stub=True/toy. (If codegen is unavailable it may honestly fall back — arrange codegen so the real path runs and assert it did.)
  (b) TEST OVERRIDE PRESERVED: passing an explicit forge= still overrides the default.
  (c) FAIL CLOSED: a candidate that fails evaluation is refused (PromotionBlocked), not silently stubbed.
  (d) NO REGRESSION: existing discovery/forge checks green; no import cycle.
  Mutation: revert the default so discover() with no forge= reaches the stub/toy again → (a) goes RED (production forge is a stub, not the real pipeline). State the load_bearing_line.
This lane EDITS discovery.py: module_path = heartbeat/decima/discovery.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'frd_probe'), never 'echo'.`
  },
  {
    name: 'mcpserve', module: 'mcp_server.py', nn: '492', model: 'fable', effort: 'high', edits: true, extra: '',
    title: 'MCP SERVE — a real serving transport so Decima can actually be driven as an MCP server (handle() gets a loop)',
    seams: 'mcp_server.py (handle(k, agent_cell, request) — a transport-AGNOSTIC JSON-RPC 2.0 request handler with the inputSchema gate + per-consumer identity, check 470; TODAY there is NO serving transport — nothing drives handle() over a stream, so the expose side cannot actually be consumed by an external agent), mcp.py (stdio_transport / the newline-delimited JSON-RPC framing the CLIENT uses — mirror that framing on the SERVER side), the wrapped-engine offline-stub idiom (how a transport is exercised offline with injected streams — no real subprocess/socket in the check).',
    spec: `
PURPOSE: the MCP SERVER exposes handle() (gated, schema-validated, per-consumer) but has NO serving transport — nothing drives it over a stream, so another agent/harness cannot actually consume Decima as an MCP server. Add a real serving transport, offline-testable, that preserves every gate.

HARDEN decima/mcp_server.py (edit ONLY mcp_server.py): add serve_stdio(k, agent_cell, *, stdin=..., stdout=..., stop=...) (or an equivalent injectable-stream server): read newline-delimited JSON-RPC 2.0 requests from an input stream, route EACH through the existing handle() (so authorize + Morta + the inputSchema gate all still run — serving does NOT bypass the gate), and write JSON-RPC responses to an output stream, until the stream ends / a stop condition. Streams are INJECTABLE (StringIO/pipes) so the check drives it offline with NO real stdio/subprocess. Per-consumer identity is honored (the served consumer acts as its own attenuated principal). Deterministic; ints; no wall-clock in recorded content.

CHECK checks/492_mcpserve.py proves, offline + deterministically (injected in/out streams):
  (a) SERVE DRIVES HANDLE THROUGH THE GATE (load-bearing): feed a sequence of JSON-RPC requests (initialize, tools/list, a tools/call, a resources/read) on an injected input stream; assert serve_stdio writes correct JSON-RPC responses AND that a gated tools/call still routes through authorize/Morta (an ungranted/gated call is refused/queued, NOT auto-run) and a schema-violating call is refused by the inputSchema gate — i.e. serving did not weaken the gate.
  (b) FOREIGN CONTENT IS DATA: a resources/read body served out is the data view; the server never executes foreign content.
  (c) CLEAN LIFECYCLE: the loop terminates on stream end; malformed input yields a JSON-RPC error (definite no-effect), never a crash or a fabricated success.
  Mutation: make serve_stdio bypass handle() (answer tools/call directly without authorize) → (a) goes RED (a gated call runs without approval). State the load_bearing_line.
This lane EDITS mcp_server.py: module_path = heartbeat/decima/mcp_server.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'serve_probe'), never 'echo'.`
  },
]

const modulePath = (l) => `heartbeat/decima/${l.module}`
const checkPath = (l) => `heartbeat/checks/${l.nn}_${l.name}.py`

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['name', 'oracle_green', 'module_path', 'module_source', 'check_path', 'check_source', 'board_entry', 'load_bearing_line', 'summary'],
  properties: {
    name: { type: 'string' }, oracle_green: { type: 'boolean' }, self_test_tail: { type: 'string' },
    module_path: { type: 'string' }, module_source: { type: 'string' },
    check_path: { type: 'string' }, check_source: { type: 'string' },
    extra_file_path: { type: 'string' }, extra_file_source: { type: 'string' },
    board_entry: { type: 'string' }, load_bearing_line: { type: 'string' },
    core_files_touched: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' }, notes: { type: 'string' },
  },
}

const REVIEW_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['name', 'verdict', 'reproduced_green', 'mutation_caught'],
  properties: {
    name: { type: 'string' }, verdict: { type: 'string', enum: ['APPROVE', 'BLOCK'] },
    reproduced_green: { type: 'boolean' }, mutation_caught: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } }, must_fix: { type: 'string' },
    corrected_module_source: { type: 'string' }, corrected_check_source: { type: 'string' },
  },
}

function implPrompt(l) {
  return `You are building ONE lane of Decima Batch S — the FINAL wiring pass to make the roadmap GREEN. THREE re-audits have found the same recurring failure: proven LIBRARIES with no production CALLERS. Your lane wires the LAST callerless proven modules into the running system (a shell verb, a discovery default, or a serving transport) — using functions that ALREADY EXIST at HEAD. You are Fable 5.

LANE: ${l.title}
THIS LANE EDITS ${modulePath(l)} (return FULL updated source as module_source with that module_path). Edit ONLY that file. Add a NEW check at ${checkPath(l)}. Keep EVERY existing check green.
STUDY FIRST (Read in ${REPO}/heartbeat): ${l.seams}
Also read checks/424_resume.py and checks/480_shellsurface.py for the idiom.

SPEC:${l.spec}
${HOUSE}
KEY BAR: your wiring must be a REAL caller on the running path (the module is actually invoked when the verb/default/server runs), not another unwired helper — your check must prove the running system now REACHES the library. Self-test until smoke.py is green, then return full sources + board_entry + load_bearing_line + oracle_green. If not green, return best effort with oracle_green=false and explain in notes.`
}

function reviewPrompt(l, impl) {
  const focus = l.name === 'shellwire' ? 'does EACH verb (do_view render-fallback, do_research, do_mail arm, do_forge real-pipeline, do_mcpserve) REALLY call its module (grep the verb body — a real call, not a help string)? untrusted results DATA? gate preserved? all existing checks green?'
    : l.name === 'forgerealdefault' ? 'does discover() with NO explicit forge= now reach the REAL forge pipeline (born-quarantined→evaluated→promoted), so production self-extension is real? is the test-override seam preserved? any import cycle?'
    : 'does serve_stdio route EVERY request through handle() (authorize + Morta + inputSchema gate NOT bypassed)? offline via injected streams? clean lifecycle?'
  return `You are an ADVERSARIAL reviewer for Decima Batch-S lane "${l.name}". This batch exists because THREE audits kept finding proven libraries with no callers — so your PRIME question: is this a REAL caller on the running path, or another unwired helper + a check that exercises it in isolation? Default to BLOCK if the wiring is cosmetic or the load-bearing line is not load-bearing. Focus: ${focus}

Delivered:
  MODULE ${impl.module_path}:
\`\`\`python
${impl.module_source}
\`\`\`
  CHECK ${impl.check_path}:
\`\`\`python
${impl.check_source}
\`\`\`
${impl.extra_file_path ? `  EXTRA ${impl.extra_file_path}:\n\`\`\`python\n${impl.extra_file_source}\n\`\`\`\n` : ''}Load-bearing line: ${JSON.stringify(impl.load_bearing_line)}

DO THIS:
1. WORK=$(mktemp -d); cp -r ${REPO}/heartbeat "$WORK/heartbeat"; write module → $WORK/${impl.module_path}, check → $WORK/${impl.check_path}.
2. GREEN: cd "$WORK/heartbeat" && python3 smoke.py — must end "heartbeat: alive. ✓" exit 0, ALL existing checks green. Minutes; be patient.
3. MUTATION + REAL-CALLER: revert the load-bearing line, re-run ONLY this lane's check, confirm FAIL. THEN grep the edited module to confirm the wiring is a REAL call on the running path (shellwire: each do_ verb calls its module; forgerealdefault: discover default → forge.forge; mcpserve: serve_stdio → handle). If cosmetic → mutation_caught=false → BLOCK.
4. LAW AUDIT: an unwired helper passed off as wired; a gate bypassed by the new serve/verb; untrusted content instruction_eligible=True; floats/wall-clock in recorded content; edits beyond the assigned file; import cycle; a check that does not fail loud; board_entry overclaim; any regression.
5. If you can cleanly fix a real defect without changing intent, do so and return FULL corrected source(s). Else BLOCK with must_fix.

Return the structured verdict reflecting what you OBSERVED running the code.`
}

const results = await pipeline(
  LANES,
  (l) => agent(implPrompt(l), { label: `impl:${l.name}`, phase: 'Implement', agentType: 'general-purpose', model: l.model, effort: l.effort, schema: IMPL_SCHEMA }),
  (impl, l) => {
    if (!impl) { log(`impl:${l.name} produced no result — skipping review`); return null }
    log(`impl:${l.name} → oracle_green=${impl.oracle_green}; reviewing`)
    return agent(reviewPrompt(l, impl), { label: `review:${l.name}`, phase: 'Review', agentType: 'general-purpose', model: 'fable', effort: 'high', schema: REVIEW_SCHEMA })
      .then((review) => ({ name: l.name, nn: l.nn, module_path: impl.module_path || modulePath(l), check_path: checkPath(l), impl, review }))
  }
)

const lanes = results.filter(Boolean)
const approved = lanes.filter((x) => x.review && x.review.verdict === 'APPROVE' && x.review.mutation_caught)
log(`Batch S done: ${approved.length}/${lanes.length} lanes APPROVED with a load-bearing check`)
return { lanes, approved: approved.map((x) => x.name) }
