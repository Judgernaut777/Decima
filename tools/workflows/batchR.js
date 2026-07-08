export const meta = {
  name: 'decima-batchR-roadmap-green',
  description: 'Batch R — the final roadmap-green batch: wire the proven libraries into the running system. strategywire (bind_strategy at boot), privacymap (reconcile privacy map), shellsurface (all missing operator verbs), mailpoll (recurring mail receive), viewcell (accreting views), researchbrain (real synthesis). Fable 5 for correctness/breadth, Sonnet 5 for mechanical; each adversarially mutation-reviewed (Fable 5).',
  phases: [
    { title: 'Implement', detail: 'one agent per lane, isolated heartbeat copy; Fable 5 for the live-path/breadth lanes, Sonnet 5 for mechanical', model: 'mixed' },
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
  Build/edit inside "$WORK/heartbeat" and self-test there. NEVER edit the canonical ${REPO} tree — the integrator lands your returned source.
- Pure Python STDLIB only (PyNaCl is the sole allowed dep and already present). NO new pip deps.
- The Five Laws are enforced by SHAPE:
    * Everything on the Weft (append-only). New state = Cells via decima.model.assert_content/assert_edge. Never mutate history.
    * ZERO ambient authority: a shell verb / boot step / poller mints NOTHING; every outward/gated action still routes through kernel.invoke (authorize + Morta). A live engine/brain-strategy binds ONLY behind an APPROVED grant / the operator key, fail closed without it.
    * UNTRUSTED CONTENT IS DATA. INTS-NOT-FLOATS in recorded/signed content. FAIL CLOSED + DETERMINISTIC: no wall-clock, no unseeded randomness in RECORDED content; outward/paid actions Morta-gated.
- Do NOT edit smoke.py. Do NOT edit the CORE-SERIALIZED files (weave.py, weft.py, kernel.py, executor.py). Your lane edits ONLY its assigned file below and composes everything else over PUBLIC APIs. The 6 lanes have DISJOINT files.
- STUDY the named seams with Read before writing. Match idiom + docstring style. For a shell lane, match the exact do_ command idiom in shell.py. Model checks on checks/424_resume.py, checks/416_spend.py.
- Your check heartbeat/checks/<NN>_<name>.py defines def run(k, line): ..., FAILS LOUD via assert, prints line("  ..."), ends with "  → ...". Register your OWN uniquely-named effect via executor.register if needed — never 'echo'.
- SELF-TEST: from "$WORK/heartbeat" run  python3 smoke.py  → must end "heartbeat: alive. ✓" exit 0, ALL existing checks green (200+ checks; minutes — be patient). Capture the last ~15 lines.
- Report the ONE load-bearing line whose reversion makes YOUR check go red, verbatim.
- DELIVERABLE = returned source (module_source + check_source; extra_file_path/extra_file_source ONLY for a disclosed second file). Do NOT commit/push/modify ${REPO}.
`

const LANES = [
  {
    name: 'strategywire', module: 'golive.py', nn: '476', model: 'fable', effort: 'high', edits: true, extra: '',
    title: 'STRATEGY-WIRE — the boot path actually binds the model-strategy plane onto the live brain (bind_strategy has zero callers)',
    seams: 'golive.py (bind_brain at ~239 — binds an APPROVED egress grant to the brain at boot; boot at ~498 — the operator boot sequence; this is where the strategy must ALSO be bound), agent.py (ModelBrain.bind_strategy at ~642 — EXISTS but has ZERO production callers; _route_and_meter at ~896 is a no-op until bind_strategy runs; DO NOT edit agent.py — only CALL bind_strategy from golive), spend.py (SpendMeter — the meter to pass), provider_router.py (the fleet/providers descriptor), inbox.py (ApprovalInbox), checks/456_spendwire.py (how bind_strategy is called + what metering proves).',
    spec: `
PURPOSE: Cycle-58 spendwire wired ModelBrain._post to call _route_and_meter, but NOTHING calls ModelBrain.bind_strategy — verified: zero production callers, only checks/456. So the shipped boot path runs live LLM calls with strategy=None: unmetered, unrouted, metrics reporting 0 spend. Close it: the boot/bind_brain path binds the strategy plane onto the live brain so _route_and_meter actually engages.

HARDEN decima/golive.py (edit ONLY golive.py — do NOT edit agent.py; CALL the existing ModelBrain.bind_strategy): in bind_brain (and/or boot), after binding the brain, also bind the model-strategy plane — construct a SpendMeter + supply the ApprovalInbox + a providers/fleet descriptor (a sensible default fleet folded from status, or an empty-but-present default so metering engages even before the operator populates a real fleet) — and call brain.bind_strategy(...). Fail closed exactly like bind_brain: bind the strategy only in the same conditions bind_brain binds the live brain (an approved key/grant); a keyless boot stays behavior-identical (RuleBrain, no strategy needed). Confer NO authority. Keep every existing check green (400_brain, 456_spendwire, golive checks).

CHECK checks/476_strategywire.py proves, offline + deterministically:
  (a) BOOT BINDS THE STRATEGY (load-bearing): after the boot/bind path runs on a kernel with a (stub) live brain, ModelBrain.strategy is NO LONGER None — it is bound, so a subsequent live-path _post is metered+routed (assert strategy present AND a metered call records spend / routes a provider, was strategy=None before).
  (b) KEYLESS BOOT UNCHANGED: with no key, boot leaves the deterministic RuleBrain and binds no strategy (behavior-identical, fail closed).
  (c) NO AMBIENT AUTHORITY: binding the strategy mints no capability.
  Mutation: remove the bind_strategy call from the boot path → (a) goes RED (strategy stays None; a live call is unmetered/unrouted, spend 0). State the load_bearing_line.
This lane EDITS golive.py: module_path = heartbeat/decima/golive.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'sw_boot_probe'), never 'echo'.`
  },
  {
    name: 'privacymap', module: 'agent.py', nn: '478', model: 'fable', effort: 'high', edits: true, extra: '',
    title: 'PRIVACY-MAP RECONCILE — agent.py privacy→router mapping must not eligibility-widen repo_sensitive/restricted (reconcile with redact.py)',
    seams: 'agent.py (_PRIVACY_TO_ROUTER_CLASS at ~557 — the map _route_and_meter uses to turn a redact privacy class into a router privacy tier; it DIVERGES from redact for repo_sensitive/restricted, eligibility-widening those dispatches), redact.py (classify_privacy → public|low_sensitive|repo_sensitive|secret_sensitive|restricted; to_router_privacy / _r_private — the CANONICAL mapping: repo_sensitive AND restricted are LOCAL-ONLY, never widened to an external-eligible tier), provider_router.py (allowed_privacy_tiers — how the router privacy tier gates provider eligibility; a widened class wrongly admits external providers), checks/456_spendwire.py + 414/redact checks.',
    spec: `
PURPOSE: agent.py's _PRIVACY_TO_ROUTER_CLASS diverges from redact.py's canonical privacy classification for repo_sensitive/restricted — so a repo_sensitive or restricted dispatch is eligibility-WIDENED (mapped to a tier that admits external providers) instead of held local-only. That is a privacy-boundary correctness bug (an infra/repo-sensitive prompt could reach an external provider). Reconcile agent.py's map with redact's canonical one so repo_sensitive AND restricted map to the LOCAL-ONLY tier, exactly as redact.to_router_privacy / router._r_private intend.

HARDEN decima/agent.py (edit ONLY agent.py): make _PRIVACY_TO_ROUTER_CLASS (or the code that uses it) agree with redact's canonical mapping — ideally COMPOSE redact.to_router_privacy rather than duplicate it, so there is one source of truth; repo_sensitive and restricted must both resolve to the local-only router class (never an external-eligible tier). Keep the Cycle-52 redaction gate, _route_and_meter, and RRuleBrain fallback intact; keep every existing check green (400/416/422/456, redact checks).

CHECK checks/478_privacymap.py proves, offline + deterministically:
  (a) REPO/RESTRICTED STAY LOCAL-ONLY (load-bearing): a repo_sensitive (and a restricted) classified payload maps to the LOCAL-ONLY router tier — provider_router with that tier admits NO external provider (an external-only fleet yields zero eligible / forces the on-device lane). Assert both classes are held local, matching redact's canonical classification.
  (b) AGREEMENT WITH REDACT: for every redact privacy class, agent's mapping == redact's canonical to_router_privacy (no divergence).
  (c) NO REGRESSION: a public/low_sensitive payload still routes normally; existing spend/redact checks green.
  Mutation: revert the map to the divergent version (widen repo_sensitive to an external-eligible tier) → (a) goes RED (a repo_sensitive dispatch becomes external-eligible). State the load_bearing_line.
This lane EDITS agent.py: module_path = heartbeat/decima/agent.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'pm_probe'), never 'echo'.`
  },
  {
    name: 'shellsurface', module: 'shell.py', nn: '480', model: 'fable', effort: 'xhigh', edits: true, extra: '',
    title: 'SHELL SURFACE — every P4/P5 module reachable from the operator Shell (the libraries get their callers)',
    seams: 'shell.py (the cmd.Cmd surface — do_say/do_inbox/do_live/do_grant/do_secrets/do_view/do_beat/do_metrics/do_backup/do_forge etc.; match the do_ idiom EXACTLY: def do_x(self, arg), self.k is the Kernel, self.inbox the ApprovalInbox, print with "   " prefix), golive.py (activate_engine — do_flip), mcp.py + mcp_server.py (mount/list_tools/resources — do_mcp), mail_engine.py (receive — do_mail) + maildigest.py (digest), corpus.py (ingest_path/recall_corpus — do_corpus), mediated_browser.py (fetch/read — do_browse), citizens.py (admit_citizen/list — do_citizen), multihuman.py (register_human/whoami/view_of — do_human), selfupdate.py (propose_update/promote_update/activate/rollback — do_update), voice_shell.py (turn/say — do_voice), migrate.py (define_migration/migrate — do_migrate), concurrency.py (run_concurrent — do_beat should drive it), daemon.py (checkpoint/resume).',
    spec: `
PURPOSE: the re-audit found the SAME libraries-without-callers pattern that demoted P4 at Cycle 57, now in P5 — ZERO P5 modules are reachable from shell.py/run.py. Land the operator SURFACE: a do_ verb for each landed P4/P5 module so the "full surface" is actually a surface a human can drive. Every verb routes through the ordinary gates (authorize + Morta) — a verb confers no authority; it just exposes the module to the operator.

HARDEN decima/shell.py (edit ONLY shell.py) — add, matching the exact existing do_ idiom, verbs that COMPOSE the public APIs (do not reimplement):
  - do_flip(arg): golive.activate_engine (flip a named engine live behind an approved grant; fail closed without one).
  - do_mcp(arg): mount / list a mounted MCP server's tools+resources (foreign content shown as DATA).
  - do_mail(arg): mail_engine.receive (through the gated transport) + maildigest.digest (show the digest).
  - do_corpus(arg): corpus.ingest_path (walk a path) / recall_corpus (query) — results shown as DATA with provenance.
  - do_browse(arg): mediated_browser.fetch/read (page shown as DATA).
  - do_citizen(arg): citizens.admit_citizen / list (show the realm's citizens + narrowed envelopes).
  - do_human(arg): multihuman.register_human / whoami / view_of (a per-human scoped view).
  - do_update(arg): selfupdate.propose_update/promote_update/activate/rollback (self-update, Morta-gated activate).
  - do_voice(arg): voice_shell.turn / say (owner utterance = proposal, ambient = DATA, speech Morta-gated).
  - do_migrate(arg): migrate.define_migration / migrate (schema migration).
  - do_beat: ENHANCE it to drive concurrency.run_concurrent for due jobs (real parallel workers), keeping daemon.resume's checkpoint semantics.
  Keep EVERY existing command working; keep all checks green. Untrusted results (mcp/mail/corpus/browse) are shown/recorded as DATA, never obeyed. Outward/gated verbs (flip/update/voice-say) fail closed / route to the inbox exactly as their modules require.

CHECK checks/480_shellsurface.py proves, offline + deterministically (instantiate the Shell over a fresh Kernel — Shell(db, fresh=True) or drive its do_ methods directly; you may need a temp db):
  (a) EVERY P5/P4 MODULE IS REACHABLE (load-bearing): assert the Shell now HAS each new verb (do_flip/do_mcp/do_mail/do_corpus/do_browse/do_citizen/do_human/do_update/do_voice/do_migrate) AND driving a representative subset actually invokes the underlying module (e.g. do_corpus ingests via corpus, do_citizen admits via citizens) — routing through the gate, results as DATA. (A verb that only prints a help string is NOT reachable — it must call the module.)
  (b) GATE PRESERVED: an outward/gated verb (do_flip without an approved grant, do_update activate without approval) fails closed / does not fire.
  (c) UNTRUSTED RESULT IS DATA: a do_browse/do_mail/do_corpus/do_mcp result is recorded instruction_eligible=False, never obeyed.
  (d) do_beat drives run_concurrent (parallel workers) while advancing the checkpoint.
  Mutation: make the new verbs no-ops (stub bodies that don't call their module) → (a) goes RED (the modules are unreachable again). State the load_bearing_line (the one verb→module call your check pins).
This lane EDITS shell.py: module_path = heartbeat/decima/shell.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'shell_probe'), never 'echo'. This is a BREADTH lane — be thorough and keep smoke green.`
  },
  {
    name: 'mailpoll', module: 'mailpoll.py', nn: '482', model: 'sonnet', effort: 'medium', edits: false, extra: '',
    title: 'MAIL POLL — a recurring driver so inbound mail is received on the always-on beat (mail_engine.receive gets a caller)',
    seams: 'mail_engine.py (receive(k, agent_cell, cap_id, *, transport) — has NO recurring caller today), scheduling.py (a recurring scheduled event — how a repeating reminder reschedules itself) + jobs.py (a durable job), reactor.py (tick fires scheduled events + jobs — do NOT edit reactor.py; register a recurring job/event that the tick drives), daemon.py (the beat drives the tick), maildigest.py (ingested mail), checks/424_resume.py + a scheduling check for the recurring idiom.',
    spec: `
PURPOSE: mail_engine.receive has no recurring driver — inbound mail is only received on an explicit call. Make it always-on: a recurring poll so each beat of the loop receives new mail through the gated transport and folds it into the digest as untrusted DATA. Compose scheduling/jobs so the existing reactor.tick (driven by do_beat) fires it — do NOT edit reactor.py.

BUILD decima/mailpoll.py composing PUBLIC APIs only (scheduling/jobs/mail_engine/kernel — do NOT edit them):
  - schedule_poll(k, agent_cell, cap_id, *, interval, transport=None) -> register a RECURRING scheduled event/job that calls mail_engine.receive each period (through the gated transport; a stub transport for offline tests). The poll confers no authority (it uses the pre-fixed mail cap); receiving mail keeps it untrusted DATA (via mail_engine → maildigest). Deterministic: logical-int period, no wall-clock.
  - poll_once(k, agent_cell, cap_id, *, transport) -> the single receive step the recurring driver runs (so a check can drive it deterministically), returning int counts.
  Reuse the scheduling recurring idiom (a fired repeating event reschedules to at+interval); the beat/tick fires it.

CHECK checks/482_mailpoll.py proves, offline + deterministically (STUB mail transport):
  (a) THE BEAT RECEIVES MAIL (load-bearing): schedule a recurring poll, advance the loop (reactor.tick / daemon over the frontiers), and assert mail_engine.receive RAN on the beat — new messages are ingested as untrusted DATA (instruction_eligible=False) and appear in the digest, WITHOUT an explicit manual receive call. The recurring event reschedules (it fires again next period).
  (b) STILL GATED / DATA: the poll goes through the gated transport; an injection in a polled message is DATA, never obeyed; an unwired poll fails closed.
  (c) INTS / no wall-clock.
  Mutation: neuter the scheduled poll so the beat no longer calls receive → (a) goes RED (mail is never received on the beat). State the load_bearing_line.
This lane ADDS decima/mailpoll.py (new). Register your OWN hermetic effect if needed (e.g. 'poll_probe'), never 'echo'.`
  },
  {
    name: 'viewcell', module: 'workspace.py', nn: '484', model: 'fable', effort: 'high', edits: true, extra: '',
    title: 'ACCRETING VIEWS — the workspace grows: a user-defined view is a Cell, not one of four hardcoded lenses',
    seams: 'workspace.py (notes/board/graph/timeline — FOUR hardcoded lenses today; "the accreting Shell" of the roadmap does not exist — views cannot be user-created), model.py (assert_content — a view definition is a Cell), weave.py (of_type / projections a view queries — read only), the shell do_view command (how a view is rendered), memory.py (scope).',
    spec: `
PURPOSE: the roadmap names an "accreting voice-first Shell", but the workspace has exactly FOUR hardcoded lenses (notes/board/graph/timeline) — a user cannot define a new view; the workspace does not ACCRETE. Make a view a Cell: a user defines a view (a named lens over the Weave — a type/scope/edge filter), it is recorded on the Weft, and it renders like the built-ins. The workspace grows the longer it runs (the VISION promise), and every view is still a pure Law-5 projection (no stored state, rebuilt from the log).

HARDEN decima/workspace.py (edit ONLY workspace.py):
  - define_view(k, name, spec) -> record a user-defined view as a Cell (a declarative lens spec: which cell type(s), scope, edge/backlink filter — DATA, deterministic, no code execution; a view spec is config, never authority). Content-addressed; int-only counts.
  - render(k, name) -> render a defined view: fold the Weave through the view's declarative spec into display lines (like notes/board), rebuilt from the log every time (Law 5 — nothing stored). An unknown view fails closed.
  - views(k) -> list the defined views (folded). Keep notes/board/graph/timeline working (built-in lenses).
  Deterministic; a view confers no authority and executes no user code (spec is declarative data only).

CHECK checks/484_viewcell.py proves, offline + deterministically:
  (a) A USER-DEFINED VIEW ACCRETES + RENDERS (load-bearing): define a new view over a chosen cell type/scope, add matching cells, and assert render(name) shows exactly the matching cells (and NOT non-matching ones) — a lens that did not exist before now works, folded from the log; views(k) lists it. Reconstruct the Kernel and prove the view folds back (it is a durable Cell, the workspace accreted).
  (b) PURE PROJECTION: rendering adds ZERO cells (Law 5 lens); a view spec is declarative DATA (no code executed); an unknown view fails closed.
  (c) BUILT-INS UNAFFECTED: notes/board/graph/timeline still render.
  Mutation: neuter define_view so a defined view does not persist / render returns nothing → (a) goes RED (the workspace does not accrete). State the load_bearing_line.
This lane EDITS workspace.py: module_path = heartbeat/decima/workspace.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'view_probe'), never 'echo'.`
  },
  {
    name: 'researchbrain', module: 'research.py', nn: '486', model: 'sonnet', effort: 'medium', edits: true, extra: '',
    title: 'RESEARCH SYNTHESIS — a real cited synthesis over untrusted observations, not a 120-char excerpt list',
    seams: 'research.py (research(k, agent, question, urls) at ~41 — TODAY synthesis is a list of 120-char excerpts (research.py:72); sources at ~97), corpus.py (recall_corpus — the token-overlap recall from Cycle 62, compose it), memory.py (remember instruction_eligible=False / recall — observations are untrusted DATA), disposition.py (observed = data), the mediated_browser / browser.observe idiom (untrusted observation → cited, never obeyed).',
    spec: `
PURPOSE: the knowledge strand is thin — research "synthesis" is just a list of 120-char excerpts with no real synthesis. Make it a genuine CITED synthesis over UNTRUSTED observations: group/rank the observed material (reuse corpus's token-overlap recall), produce a structured answer to the question that CITES each source, while every observation remains untrusted DATA (instruction_eligible=False) — a synthesis cites, it never obeys the sources.

HARDEN decima/research.py (edit ONLY research.py):
  - improve research(...) so the report is a real synthesis: relevance-rank the observations against the question (compose corpus.recall_corpus / a deterministic token-overlap score — stdlib, NO vector dep), assemble a structured answer that CITES each contributing source (provenance), and record it as a knowledge cell that is itself grounded in the untrusted observations (instruction_eligible=False — the synthesis is derived from DATA, and an injection in a source is cited/quoted, never executed as an instruction).
  - keep sources(report) returning the cited source list; keep every existing research check green.
  Deterministic (no wall-clock/model nondeterminism in recorded content); the answer is longer/structured but still DATA.

CHECK checks/486_researchbrain.py proves, offline + deterministically (stub/observed sources):
  (a) SYNTHESIS CITES + STAYS DATA (load-bearing): research over a few observed sources produces a structured, question-relevant synthesis that CITES its sources (each claim traceable to a source), and the whole report is instruction_eligible=False — an injection embedded in a source is quoted/cited but NEVER obeyed (nothing is invoked by synthesizing).
  (b) RELEVANCE: a source relevant to the question ranks above an irrelevant one (better than a flat excerpt dump); sources(report) lists the cited sources.
  (c) DETERMINISM: same inputs → same report.
  Mutation: flip the synthesis to instruction_eligible=True (treat sources as trusted) → (a) goes RED (an injected source becomes instruction-eligible). State the load_bearing_line.
This lane EDITS research.py: module_path = heartbeat/decima/research.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'research_probe'), never 'echo'.`
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
    ? `\nTHIS LANE EDITS ${modulePath(l)} (return FULL updated source as module_source with that module_path). Edit ONLY that file. Add a NEW check at ${checkPath(l)}. Keep EVERY existing check green.`
    : `\nTHIS LANE ADDS A NEW MODULE ${modulePath(l)} + its check ${checkPath(l)}.`
  return `You are building ONE lane of Decima Batch R — the FINAL roadmap-green batch. A re-audit found the reference stable (207 checks green) but NOT green because the project's recurring failure mode persists: proven LIBRARIES with no production CALLERS. Your lane WIRES a proven library into the running system (a boot step, a live-path fix, an operator shell verb, a recurring driver, or an accreting view). Model role: ${l.model === 'fable' ? 'correctness/breadth-heavy (Fable 5) — reason carefully about the live path, the gate, and keeping 200+ checks green' : 'mechanical composition (Sonnet 5) — compose the existing APIs cleanly'}.

LANE: ${l.title}
${editNote}
STUDY FIRST (Read in ${REPO}/heartbeat): ${l.seams}
Also read checks/424_resume.py and checks/416_spend.py for the check idiom.

SPEC:${l.spec}
${HOUSE}
Now: isolated copy, study the seams, implement, self-test until smoke.py is green (ALL existing checks pass), then return full sources + board_entry + load_bearing_line + oracle_green. The KEY BAR for this batch: your wiring must be REAL (an actual caller on the real path), not another unwired helper — your check must prove the running system now reaches the library. If not green, return best effort with oracle_green=false and explain in notes.`
}

function reviewPrompt(l, impl) {
  const focus = l.name === 'strategywire' ? 'does the BOOT path now actually call bind_strategy so strategy is non-None on a live brain (a real caller, not just the definition)? keyless boot unchanged?'
    : l.name === 'privacymap' ? 'does repo_sensitive AND restricted now map to the LOCAL-ONLY router tier (agreeing with redact), so those dispatches are NOT eligibility-widened to external providers?'
    : l.name === 'shellsurface' ? 'is EACH new do_ verb a REAL caller of its module (not a help-string stub)? does the check prove the module is actually invoked+gated? are untrusted results kept as DATA? did all existing checks stay green?'
    : l.name === 'mailpoll' ? 'does the BEAT actually receive mail (a real recurring caller of mail_engine.receive), gated, mail kept as DATA — not a helper nothing drives?'
    : l.name === 'viewcell' ? 'does a USER-DEFINED view persist as a Cell and render (the workspace genuinely accretes), a declarative spec with no code execution, built-ins unaffected?'
    : 'is the synthesis a real cited, relevance-ranked answer (not a flat excerpt dump) that stays instruction_eligible=False (an injected source cited, never obeyed)?'
  return `You are an ADVERSARIAL reviewer for Decima Batch-R lane "${l.name}". This batch exists BECAUSE prior cycles shipped libraries nothing wired in — so your PRIME question: is this lane a REAL caller on the running path, or another unwired helper + a check that exercises it in isolation? Default to BLOCK if the wiring is cosmetic or the load-bearing line is not load-bearing. Focus: ${focus}

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
3. MUTATION TEST + REAL-CALLER CHECK: revert the load-bearing line, re-run ONLY this lane's check, confirm it FAILS. THEN grep the edited module to confirm the wiring is a REAL caller on the running path (strategywire: bind_brain/boot calls bind_strategy; privacymap: the map agrees with redact; shellsurface: each do_ verb calls its module; mailpoll: the scheduled poll calls receive; viewcell: define_view persists a Cell; researchbrain: synthesis ranks+cites). If cosmetic/decorative → mutation_caught=false → BLOCK.
4. LAW AUDIT: an unwired helper passed off as wired; a privacy class wrongly widened to external; a shell verb that mints authority or bypasses the gate; untrusted content instruction_eligible=True; floats/wall-clock in recorded content; edits beyond the assigned file; a check that does not fail loud; board_entry overclaim; any regression in existing checks.
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
log(`Batch R done: ${approved.length}/${lanes.length} lanes APPROVED with a load-bearing check`)
return { lanes, approved: approved.map((x) => x.name) }
