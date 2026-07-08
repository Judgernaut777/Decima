export const meta = {
  name: 'decima-batchC-p5-depth',
  description: 'Batch C — P5 depth: MCP client resources/prompts/durable-mounts, MCP server resources/prompts/schema-gate/per-consumer-identity, real inbound mail engine, corpus file-walker+chunking+semantic recall. Fable 5 for the three correctness lanes, Sonnet 5 for corpusfeed; each adversarially mutation-reviewed (Fable 5).',
  phases: [
    { title: 'Implement', detail: 'one agent per lane, isolated heartbeat copy; Fable 5 for MCP/mail correctness, Sonnet 5 for the corpus walker', model: 'mixed' },
    { title: 'Review', detail: 'adversarial mutation-test + law audit per lane (Fable 5)', model: 'fable' },
  ],
}

const REPO = '/home/mini/decima-claude'

const HOUSE = `
DECIMA HOUSE RULES (obey exactly):
- Repo: ${REPO}. The heartbeat (pure-stdlib reference) is at ${REPO}/heartbeat. The decima package is ${REPO}/heartbeat/decima. Checks are auto-discovered from ${REPO}/heartbeat/checks/NN_*.py by smoke.py.
- WORK IN ISOLATION:
    WORK=$(mktemp -d)
    cp -r ${REPO}/heartbeat "$WORK/heartbeat"
    cd "$WORK/heartbeat"
- Pure Python STDLIB only (PyNaCl is the sole allowed dep and already present). NO new pip deps.
- The Five Laws are enforced by SHAPE:
    * Everything on the Weft (append-only). New state = Cells via decima.model.assert_content/assert_edge, authored through existing kernel principals. Never mutate history.
    * ZERO ambient authority: mint/attenuate ONLY through existing kernel APIs. Every MCP tools/call routes through kernel.invoke (authorize + Morta); a mounted server / mail / page confers NO authority.
    * UNTRUSTED CONTENT IS DATA, NEVER INSTRUCTION. A mounted tool RESULT, an MCP resource body, an inbound email, a fetched/ingested file — all instruction_eligible=False, recorded/recalled as DATA only, never obeyed.
    * INTS-NOT-FLOATS in recorded/signed content. FAIL CLOSED + DETERMINISTIC: no wall-clock, no unseeded randomness in RECORDED content; outward/irreversible actions are Morta-gated; an unobservable outcome is UNKNOWN, never a fabricated SUCCEEDED.
- Do NOT edit smoke.py. Do NOT edit the CORE-SERIALIZED files (weave.py, weft.py, kernel.py, executor.py). Your lane edits ONLY its assigned file below and composes everything else over PUBLIC APIs. The 4 lanes have DISJOINT files.
- STUDY the named seams with Read before writing. Match idiom + docstring style. Model checks on checks/424_resume.py and a check that proves "observed = data, never obeyed" (e.g. a BROWSER.OBSERVE / mediated-browser / maildigest check).
- Your check heartbeat/checks/<NN>_<name>.py defines def run(k, line): ..., FAILS LOUD via assert, prints line("  ..."), ends with "  → ...". Register your OWN uniquely-named effect via executor.register if needed — never 'echo'.
- SELF-TEST: from "$WORK/heartbeat" run  python3 smoke.py  → must end "heartbeat: alive. ✓" exit 0, ALL existing checks green (200+ checks; minutes — be patient). Capture the last ~15 lines.
- Report the ONE load-bearing line whose reversion makes YOUR check go red, verbatim.
- DELIVERABLE = returned source (module_source + check_source; extra_file_path/extra_file_source ONLY for a disclosed second file). Do NOT commit/push/modify ${REPO}.
`

const LANES = [
  {
    name: 'mcpclient', module: 'mcp.py', nn: '468', model: 'fable', effort: 'high', edits: true, extra: '',
    title: 'MCP CLIENT DEPTH — resources + prompts + elicitation-to-inbox + durable mounts (beyond tools-only)',
    seams: 'mcp.py (mount, list_tools/call_tool, _rpc, initialize, stdio_transport/http_transport, _content_text — TODAY it is tools-only; tool RESULTS are already recorded as untrusted DATA — extend that same law to resources/prompts), disposition.py + quarantine.py (untrusted admission), memory.py (remember instruction_eligible=False), inbox.py (ApprovalInbox — an elicitation/consent prompt becomes a Morta-gated item), model.py (assert_content for a durable mount Cell), the existing MCP mount check (grep MCP / mount).',
    spec: `
PURPOSE: the MCP CLIENT is tools-only. Deepen it to the parts of the protocol Decima actually needs, keeping the same law (foreign content is untrusted DATA):
  - resources/list + resources/read: mounting a server can enumerate + READ its resources; a resource body is admitted as QUARANTINED, instruction_eligible=False (a resource that says "run rm -rf" is data to cite, never a command).
  - prompts/list: enumerate a server's prompts as DATA (a prompt template is untrusted text, not an instruction to Decima).
  - elicitation → inbox: if a server ELICITS input/consent (an elicitation/sampling request), it does NOT auto-answer — it becomes a Morta-gated ApprovalInbox item; nothing is sent back until a human approves.
  - durable mounts: a mount is recorded as a Cell so it survives a restart (folds back), rather than dying with the process.

HARDEN decima/mcp.py (edit ONLY mcp.py): add resources_list/resources_read/prompts_list over the same transport+_rpc seam; admit every foreign body as untrusted DATA; route an elicitation to the inbox; record a durable mount Cell. Keep tools/list + tools/call and every existing MCP check green.

CHECK checks/468_mcpclient.py proves, offline + deterministically (a STUB transport returning canned resources/prompts/elicitation — no real server):
  (a) RESOURCE BODY IS DATA, NEVER OBEYED (load-bearing): resources_read a resource whose body is an injection — assert it is admitted instruction_eligible=False (quarantined), recalled as DATA, and NOTHING is invoked by reading it.
  (b) PROMPTS ARE DATA: prompts_list returns templates recorded/treated as untrusted data.
  (c) ELICITATION IS GATED: a server elicitation becomes a Morta-gated inbox item; nothing is answered/sent until approved.
  (d) DURABLE MOUNT: a mount folds back on a reconstructed Kernel (survives restart).
  Mutation: flip the resource-admission to instruction_eligible=True (or let an elicitation auto-answer) → (a)/(c) goes RED. State the load_bearing_line.
This lane EDITS mcp.py: module_path = heartbeat/decima/mcp.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'mcpc_probe'), never 'echo'.`
  },
  {
    name: 'mcpserver', module: 'mcp_server.py', nn: '470', model: 'fable', effort: 'high', edits: true, extra: '',
    title: 'MCP SERVER DEPTH — expose resources + prompts, validate inputSchema at the gate, per-consumer identity',
    seams: 'mcp_server.py (handle — the JSON-RPC 2.0 request handler; list_tools/to_mcp_tool; _resolve_cap; capabilities advertises {"tools":{}} only; every tools/call already routes through kernel.invoke = authorize + Morta), manifest.py (registry — what to expose; input_schema on a manifest), workspace.py or the cell projections (what read-only RESOURCES Decima can expose — docs/cells), capability.py (attenuate — a per-consumer principal holds an attenuated envelope), kernel.py (spawn a per-consumer principal), the existing MCP server check (grep mcp_server / expose).',
    spec: `
PURPOSE: the MCP SERVER advertises capabilities.tools only. Deepen it so other agents can consume Decima richly WITHOUT weakening the gate:
  - resources exposure: expose selected Decima docs/cells as READ-ONLY MCP resources (resources/list + resources/read) — read-only, no authority, a consumer reads DATA.
  - prompts: expose prompts/list (Decima's own prompt templates) as data.
  - inputSchema GATE: validate a tools/call's arguments against the tool's declared inputSchema BEFORE kernel.invoke — a call whose arguments violate the schema is refused at the door (fail closed), never passed to the effect.
  - per-consumer identity: each MCP consumer acts as its OWN principal (an attenuated per-consumer envelope), so its calls are attributed and gated as ITS authority — one consumer can never act as another or as Decima itself.

HARDEN decima/mcp_server.py (edit ONLY mcp_server.py): advertise + serve resources/prompts (read-only), add the inputSchema validation gate before invoke, and thread a per-consumer principal through handle so authorize runs against the CONSUMER's envelope. Keep tools/call routing through kernel.invoke (authorize + Morta) and every existing check green.

CHECK checks/470_mcpserver.py proves, offline + deterministically:
  (a) SCHEMA GATE FAILS CLOSED (load-bearing): a tools/call whose arguments violate the tool's inputSchema is REFUSED before kernel.invoke (no effect fires); a well-formed call still works.
  (b) PER-CONSUMER AUTHORITY: consumer A (its attenuated principal) calling a tool it is NOT granted is DENIED at the gate; it cannot act as Decima or as consumer B (no cross-consumer authority).
  (c) RESOURCES/PROMPTS ARE READ-ONLY DATA: resources/read + prompts/list return data and confer no authority / fire no effect.
  (d) GATE NOT BYPASSED: a tools/call still routes through authorize + Morta (an ungranted/gated call is refused/queued, not auto-run).
  Mutation: drop the inputSchema validation (pass unchecked arguments to invoke) → (a) goes RED (a schema-violating call reaches the effect). State the load_bearing_line.
This lane EDITS mcp_server.py: module_path = heartbeat/decima/mcp_server.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'mcps_probe'), never 'echo'.`
  },
  {
    name: 'mailwire', module: 'mail_engine.py', nn: '472', model: 'fable', effort: 'high', edits: false, extra: '',
    title: 'INBOUND MAIL ENGINE — receive real mail through the gated transport; feed the digest; a real-cap ask enacts only on approval',
    seams: 'maildigest.py (ingest_email, digest, propose_action, MAIL_SCOPE, _mail_action_handler, _mail_action_cap — TODAY messages arrive as trusted-caller dicts and propose_action enacts only the mail_probe stand-in), live_wire.py + wire.py (the gated transport — receiving mail is EGRESS/ingress through the gate; a bare/unwired path raises NoGatedTransport; offline-stub-testable like the wrapped engines), the wrapped-engine offline-stub idiom (shipping/weather/sms engine checks), redact.py (scrub), disposition.py, inbox.py (Morta-gated enactment).',
    spec: `
PURPOSE: maildigest's TRUST LAW is real but its I/O is not — messages arrive as trusted-caller dicts and propose_action enacts only the mail_probe stand-in. Build the real INBOUND MAIL ENGINE that wraps a mail source THROUGH the gated transport (like every other engine: no bare socket; NoGatedTransport if unwired; offline-stub-testable), parses fetched messages into the SAME untrusted-DATA shape maildigest.ingest_email consumes, and wires a surfaced "ask" to a REAL capability enactment (still Morta-gated) instead of the probe stand-in.

BUILD decima/mail_engine.py composing PUBLIC APIs only (maildigest / live_wire+wire / redact / inbox — do NOT edit them):
  - receive(k, agent_cell, cap_id, *, transport=None) -> fetch inbound messages THROUGH the gated transport (a wire_decision lands before the socket; unwired → NoGatedTransport), parse each into maildigest's message shape, and feed maildigest.ingest_email so each lands as an UNTRUSTED observation (instruction_eligible=False, redact-scrubbed, provenance to sender/msg-id). (transport is an injectable STUB for offline tests, mirroring the wrapped engines.)
  - enact_ask(k, msg_id, action, real_cap) -> turn a surfaced ask into a Morta-gated inbox item bound to a REAL capability (not mail_probe); it fires NOTHING until a human approves, then runs through the ordinary authorize/Morta spine.
  Deterministic; int counts; no secret on the Weft.

CHECK checks/472_mailwire.py proves, offline + deterministically (STUB mail transport — no real network):
  (a) INBOUND MAIL IS DATA (load-bearing): a received message whose body is an injection is ingested instruction_eligible=False (via maildigest), appears in the digest as DATA, and NOTHING is invoked by receiving it.
  (b) RECEIVE IS GATED: receive runs through the gated transport (a wire_decision lands); an UNWIRED receive is refused NoGatedTransport (no message stored).
  (c) A REAL ASK IS MORTA-GATED: enact_ask enqueues a requires_approval item bound to a real cap and fires nothing; only explicit approval enacts it (prove no-fire pre-approval, fire post-approval).
  (d) INTS / no secret on the Weft (a secret in a message is scrubbed).
  Mutation: flip ingest to instruction_eligible=True OR make enact_ask invoke directly → (a)/(c) goes RED. State the load_bearing_line.
This lane ADDS decima/mail_engine.py (new). Register your OWN hermetic effect if needed (e.g. 'mailw_probe'), never 'echo'.`
  },
  {
    name: 'corpusfeed', module: 'corpus.py', nn: '474', model: 'sonnet', effort: 'medium', edits: true, extra: '',
    title: 'CORPUS FEED — a file/directory walker with format handling + chunking + better-than-substring recall',
    seams: 'corpus.py (ingest(k, source, text, *, scope, ...), recall_corpus — TODAY ingestion takes caller-supplied strings only and recall is substring-only; extend it), memory.py (remember instruction_eligible=False, recall, claim_id content-address dedup), hashing.py (content_id), redact.py (scrub before a claim lands), the existing corpus check (checks/442_corpus.py — MUST stay green).',
    spec: `
PURPOSE: corpus ingestion takes caller-supplied strings only and recall is substring-only. Make it feed from real files: a file/directory WALKER, simple format handling, CHUNKING for long documents, and a better-than-substring recall — while keeping every ingested chunk UNTRUSTED DATA (instruction_eligible=False) and content-addressed (dedup).

HARDEN decima/corpus.py (edit ONLY corpus.py):
  - ingest_path(k, path, *, scope) -> walk a file OR directory (stdlib os/pathlib), read text files (simple text/markdown handling; skip/represent binary safely), CHUNK long text into bounded pieces, and ingest each chunk via the existing ingest()/memory.remember (instruction_eligible=False, content-addressed dedup so re-walking the same tree adds 0 new claims). Returns int counts {files, chunks, ingested, deduped}.
  - improve recall_corpus to a better-than-substring match (e.g. token-overlap / normalized scoring — deterministic, stdlib only, NO vector dep), still returning hits AS DATA with provenance and honoring recallable/scope.
  Keep ingest()/recall_corpus back-compatible; keep checks/442_corpus.py green.

CHECK checks/474_corpusfeed.py proves, offline + deterministically (write files to a tempdir, walk them):
  (a) WALKED FILES ARE DATA, NEVER INSTRUCTION (load-bearing): ingest_path over a dir containing a file whose text is an injection — assert every ingested chunk is instruction_eligible=False, recall returns them as DATA with provenance (the file path), NOTHING is invoked.
  (b) CHUNKING + DEDUP: a long file is chunked into >1 bounded claims; re-walking the SAME tree adds 0 new claims (content-addressed dedup); counts are ints.
  (c) BETTER RECALL: a multi-token query ranks a relevant chunk above an irrelevant one (better than raw substring); scope filtering honored.
  Mutation: flip the walked-chunk ingest to instruction_eligible=True → (a) goes RED (a walked file becomes instruction-eligible). State the load_bearing_line.
This lane EDITS corpus.py: module_path = heartbeat/decima/corpus.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'feed_probe'), never 'echo'.`
  },
]

const modulePath = (l) => `heartbeat/decima/${l.module}`
const checkPath = (l) => `heartbeat/checks/${l.nn}_${l.name}.py`

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['name', 'oracle_green', 'module_path', 'module_source', 'check_path', 'check_source', 'board_entry', 'load_bearing_line', 'summary'],
  properties: {
    name: { type: 'string' },
    oracle_green: { type: 'boolean', description: 'did `python3 smoke.py` end "heartbeat: alive. ✓" exit 0 with your lane added, ALL existing checks green' },
    self_test_tail: { type: 'string' },
    module_path: { type: 'string' },
    module_source: { type: 'string', description: 'FULL source of the new/edited module' },
    check_path: { type: 'string' },
    check_source: { type: 'string', description: 'FULL source of the new check' },
    extra_file_path: { type: 'string', description: 'ONLY if you edited a disclosed second file; else ""' },
    extra_file_source: { type: 'string', description: 'FULL source of the extra file, else ""' },
    board_entry: { type: 'string' },
    load_bearing_line: { type: 'string' },
    core_files_touched: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    notes: { type: 'string' },
  },
}

const REVIEW_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['name', 'verdict', 'reproduced_green', 'mutation_caught'],
  properties: {
    name: { type: 'string' },
    verdict: { type: 'string', enum: ['APPROVE', 'BLOCK'] },
    reproduced_green: { type: 'boolean' },
    mutation_caught: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } },
    must_fix: { type: 'string' },
    corrected_module_source: { type: 'string' },
    corrected_check_source: { type: 'string' },
  },
}

function implPrompt(l) {
  const editNote = l.edits
    ? `\nTHIS LANE EDITS ${modulePath(l)} (return FULL updated source as module_source with that module_path). Edit ONLY that file. Add a NEW check at ${checkPath(l)}. Keep EVERY existing check green (${l.name === 'corpusfeed' ? 'checks/442_corpus.py especially' : 'the existing MCP checks especially'}).`
    : `\nTHIS LANE ADDS A NEW MODULE ${modulePath(l)} + its check ${checkPath(l)}.`
  return `You are building ONE lane of Decima Batch C — P5 DEPTH (the audit found P5's surface real on trust-law but thin on substance: MCP is tools-only, mail/corpus I/O is unreal). Model role: ${l.model === 'fable' ? 'correctness/security-heavy (Fable 5) — reason carefully about the gate, per-consumer authority, and untrusted-data admission' : 'mechanical composition (Sonnet 5) — a stdlib file walker + chunking + deterministic recall'}.

LANE: ${l.title}
${editNote}
STUDY FIRST (Read in ${REPO}/heartbeat): ${l.seams}
Also read checks/424_resume.py and a check proving the untrusted-data law.

SPEC:${l.spec}
${HOUSE}
Now: isolated copy, study the seams, implement, self-test until smoke.py is green (ALL existing checks pass), then return full sources + board_entry + load_bearing_line + oracle_green. If not green, return best effort with oracle_green=false and explain in notes.`
}

function reviewPrompt(l, impl) {
  const focus = l.name === 'mcpclient' ? 'is a foreign MCP resource/prompt body admitted as untrusted DATA (instruction_eligible=False), never obeyed, and does a server elicitation become a Morta-gated inbox item (never auto-answered)?'
    : l.name === 'mcpserver' ? 'does the inputSchema gate REALLY refuse a schema-violating tools/call before invoke, and does a per-consumer principal prevent cross-consumer / act-as-Decima authority? is the authorize+Morta gate still not bypassed?'
    : l.name === 'mailwire' ? 'is inbound mail admitted as untrusted DATA, is receive actually gated (NoGatedTransport when unwired), and does a real-cap ask fire ONLY on approval (never auto-enact)?'
    : 'are walked files ingested instruction_eligible=False (never obeyed), is chunking+content-addressed dedup real, and is recall deterministic? did 442 stay green?'
  return `You are an ADVERSARIAL reviewer for Decima Batch-C lane "${l.name}". Be skeptical; catch a check that proves nothing or a module that breaks a law. Default to BLOCK. Focus: ${focus}

Delivered files:
  MODULE ${impl.module_path}:
\`\`\`python
${impl.module_source}
\`\`\`
  CHECK ${impl.check_path}:
\`\`\`python
${impl.check_source}
\`\`\`
${impl.extra_file_path ? `  EXTRA FILE ${impl.extra_file_path}:\n\`\`\`python\n${impl.extra_file_source}\n\`\`\`\n` : ''}Load-bearing line: ${JSON.stringify(impl.load_bearing_line)}

DO THIS:
1. WORK=$(mktemp -d); cp -r ${REPO}/heartbeat "$WORK/heartbeat"; write module → $WORK/${impl.module_path}, check → $WORK/${impl.check_path}${impl.extra_file_path ? `, extra → $WORK/${impl.extra_file_path}` : ''}.
2. GREEN: cd "$WORK/heartbeat" && python3 smoke.py — must end "heartbeat: alive. ✓" exit 0, ALL existing checks green. Minutes; be patient. (reproduced_green)
3. MUTATION TEST: revert the load-bearing line (flip instruction_eligible to True / drop the schema gate / let an elicitation auto-answer / let an ask auto-invoke), re-run ONLY this lane's check, confirm it FAILS. If decorative → mutation_caught=false → BLOCK. Restore after.
4. LAW AUDIT: untrusted content (resource/prompt/mail/file) written instruction_eligible=True or able to fire an invoke; a schema-violating call reaching the effect; cross-consumer / act-as-Decima authority; ambient authority; floats/wall-clock in recorded content; edits beyond the assigned file; a bare (ungated) socket for mail; secret on the Weft; a check that does not fail loud; board_entry overclaim; any regression in existing checks.
5. If you can cleanly fix a real defect without changing intent, do so and return FULL corrected source(s). Else BLOCK with must_fix.

Return the structured verdict reflecting what you OBSERVED running the code.`
}

const results = await pipeline(
  LANES,
  (l) => agent(implPrompt(l), {
    label: `impl:${l.name}`, phase: 'Implement', agentType: 'general-purpose',
    model: l.model, effort: l.effort, schema: IMPL_SCHEMA,
  }),
  (impl, l) => {
    if (!impl) { log(`impl:${l.name} produced no result — skipping review`); return null }
    log(`impl:${l.name} → oracle_green=${impl.oracle_green}; reviewing`)
    return agent(reviewPrompt(l, impl), {
      label: `review:${l.name}`, phase: 'Review', agentType: 'general-purpose',
      model: 'fable', effort: 'high', schema: REVIEW_SCHEMA,
    }).then((review) => ({ name: l.name, nn: l.nn, module_path: impl.module_path || modulePath(l), check_path: checkPath(l), impl, review }))
  }
)

const lanes = results.filter(Boolean)
const approved = lanes.filter((x) => x.review && x.review.verdict === 'APPROVE' && x.review.mutation_caught)
log(`Batch C done: ${approved.length}/${lanes.length} lanes APPROVED with a load-bearing check`)
return { lanes, approved: approved.map((x) => x.name) }
