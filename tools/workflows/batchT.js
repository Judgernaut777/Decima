export const meta = {
  name: 'decima-batchT-running-path-correctness',
  description: 'Batch T (Cycle 66) — running-path correctness: close the red core the 4th-quality re-audit found. Five DISJOINT-file lanes: livecodegen (candidate.py+golive.py — the P3 live self-extension + register_builtins@boot + activate_engine installs a real engine consumer), catalog-activation (discovery.py+inbox.py — suggestion→approval→activation installer), surface (shell.py — engine + sync verbs), contextfold (agent.py — wire context_fold into ModelBrain history), presentwire (research.py+mailpoll.py — route engine/mail output through the present() quarantine chokepoint). All Fable 5; each adversarially mutation-reviewed by a Fable 5 skeptic.',
  phases: [
    { title: 'Implement', detail: 'one Fable 5 agent per lane, isolated heartbeat copy; DISJOINT files per lane', model: 'fable' },
    { title: 'Review', detail: 'adversarial mutation-test + REAL-CALLER audit per lane (Fable 5)', model: 'fable' },
  ],
}

const REPO = '/home/mini/decima-claude'

const HOUSE = `
DECIMA HOUSE RULES (obey exactly):
- Repo: ${REPO}. The heartbeat is at ${REPO}/heartbeat; the package ${REPO}/heartbeat/decima; checks auto-run from ${REPO}/heartbeat/checks/NN_*.py by smoke.py.
- WORK IN ISOLATION: WORK=$(mktemp -d); cp -r ${REPO}/heartbeat "$WORK/heartbeat"; cd "$WORK/heartbeat". NEVER edit the canonical ${REPO} tree.
- Pure Python STDLIB only (PyNaCl already present). NO new pip deps.
- Five Laws by SHAPE: everything on the Weft (append-only, model.assert_content); ZERO ambient authority (a shell verb / default / server / boot hook mints NOTHING; outward+gated actions still route through kernel.invoke = authorize + Morta); UNTRUSTED CONTENT IS DATA (instruction_eligible=False); INTS-NOT-FLOATS in recorded content; FAIL CLOSED + DETERMINISTIC (no wall-clock/unseeded-random in recorded content).
- CORE-SERIALIZED — NEVER edit these: smoke.py, weave.py, weft.py, kernel.py, executor.py. If your fix seems to need a core edit, fix it at YOUR OWNED callee seam so the core call-site inherits it WITHOUT editing core (this is exactly how Batch S forgerealdefault fixed production discover() without touching kernel.py:544).
- EDIT ONLY YOUR OWNED FILES (stated per lane). Compose everything else over PUBLIC APIs. Lanes have DISJOINT file ownership — another lane owns the files you do not.
- STUDY the seams with Read before writing. For a shell verb match the EXACT do_ idiom in shell.py (def do_x(self, arg); self.k the Kernel; self.inbox; print "   " prefix). Model checks on checks/480_shellsurface.py, checks/424_resume.py, checks/460_liveflip.py, checks/418_golive.py, checks/392_quarantine.py.
- Your check heartbeat/checks/<NN>_<name>.py: def run(k, line): ... FAILS LOUD via assert; prints line("  ..."); ends "  → ...". Register your OWN hermetic effect via executor.register if needed — never 'echo'.
- SELF-TEST: cd "$WORK/heartbeat" && python3 smoke.py → must end "heartbeat: alive. ✓" exit 0, ALL existing checks green (216+ checks; minutes — be patient).
- Report the ONE load-bearing line whose reversion makes YOUR check go red, verbatim.
- DELIVERABLE = returned source(s) in the files[] array (each {path, source} for EVERY file your lane edits) + check_path/check_source. Do NOT commit/modify ${REPO}.
`

const LANES = [
  {
    name: 'livecodegen', nn: '494', model: 'fable', effort: 'xhigh',
    files: ['heartbeat/decima/candidate.py', 'heartbeat/decima/golive.py'],
    title: 'LIVECODEGEN — the P3 red: make live self-extension REAL when keyed (model_codegen live branch + boot binds it + register_builtins + activate_engine installs a real engine consumer)',
    seams: 'candidate.py:58 model_codegen(intent, *, brain=None) — TODAY raises CodegenUnavailable UNCONDITIONALLY (the live post is a comment, not code) and if it had a brain would use a fresh keyless ModelBrain, not the golive-bound one; golive.py:311 bind_brain(k) — builds+binds the live ModelBrain onto an approved api.anthropic.com egress grant; golive.py:412 activate_engine(k, name, host, ...) — records a wire-gated transport into k.live_engines[name] but NOTHING ever hands that transport to the engine module fn (zero consumers — the flip is doctor decoration); golive.py:578 boot(k, environ) — the production boot that intakes secrets and calls bind_brain; discovery.py:373 bind_default_codegen(codegen) EXISTS with zero production callers (call it, do NOT edit discovery.py — another lane owns it, keep its signature); builtin_manifests.py:160 register_builtins(k) EXISTS with zero production callers → the live discovery catalog is EMPTY (call it, do NOT edit it); kernel.py:482 integrate_tool(name, handler, caveats) — the PUBLIC way to install an invokable, authorize+Morta-gated handler (use it; do NOT edit kernel.py); agent.py ModelBrain._post — the redaction + spend-metered wire-gated transport the live brain already rides. Study checks/418_golive.py, checks/460_liveflip.py, checks/490_forgerealdefault.py.',
    spec: `
PURPOSE: the 4th-quality re-audit ruled P3 RED AT ITS HEART and the SWEEP not-green on two code gaps (NOT operator gates) that all live in candidate.py + golive.py. Close ALL of them (edit ONLY those two files; call the rest over public APIs):
  (1) LIVE MODEL CODEGEN. candidate.model_codegen must, when given an egress-bound brain, POST the codegen intent through that brain (its gated _post transport — redaction + spend metering already ride it) and RETURN the generated source as DATA (instruction_eligible=False). When NO brain / unarmed, it FAILS CLOSED honestly (raise CodegenUnavailable) — never fabricate. Make it INJECTED-TRANSPORT TESTABLE offline: accept the brain/transport as a parameter so the check drives it with a deterministic stub brain (the wrapped-engine idiom) — NO real network.
  (2) BOOT ARMS IT. golive.bind_brain (or boot right after it) must call discovery.bind_default_codegen(<a codegen closure that uses the just-bound live brain>) so production discover()/forge reaches REAL live codegen once keyed — flipping gap (1) from "code gap" to legitimately operator-gated (the green shape). Also call builtin_manifests.register_builtins(k) at boot so the production discovery catalog is NON-EMPTY (today it is empty → 'use' can never fire).
  (3) ENGINE CONSUMER. golive.activate_engine must INSTALL a real invokable handler for the flipped engine — via kernel.integrate_tool (or executor.register of a gated effect) — so that invoking the engine capability actually calls the engine module's entry fn WITH the registered wire-gated transport, routed through the ordinary authorize+Morta invoke path. Fail-closed offline / on-revoke (mirror the existing offline_on_no_grant behavior). This is the CONSUMER the audit found missing: after activate_engine, k.invoke(<engine cap>) must really drive the engine fn over its transport.
  Everything mints nothing; every outward/gated action still routes kernel.invoke; untrusted engine/codegen output is DATA.

CHECK checks/494_livecodegen.py proves, offline + deterministically (inject a deterministic stub brain/transport — NO network):
  (a) LIVE CODEGEN REAL (load-bearing): with an injected egress-bound stub brain, model_codegen returns real generated source as DATA; with NO brain it raises CodegenUnavailable (fail closed). Assert the source came from the brain transport (stub returns a marker), not a hardcoded stub.
  (b) BOOT BINDS IT: after the boot/bind path (with a stub brain), discovery's default codegen is the live one AND the catalog is non-empty (register_builtins ran) — assert a production-shaped discover() for a bundled goal can 'use' a registered engine (not fall to empty).
  (c) ENGINE CONSUMER (load-bearing): after activate_engine(k, <name>, <host>) with an approved grant + injected transport, invoking the engine capability through kernel.invoke actually calls the engine entry fn with that transport (assert the transport was exercised — a weave-level effect / marker), and fails closed with no grant.
  Mutation: revert model_codegen's live post to the unconditional raise (or revert activate_engine's integrate_tool install to a no-op) → (a)/(c) goes RED. State the load_bearing_line.
This lane EDITS heartbeat/decima/candidate.py AND heartbeat/decima/golive.py — return BOTH in files[]. Register your OWN hermetic effect if needed (e.g. 'lcg_probe'), never 'echo'. HARD lane, xhigh — be rigorous; keep smoke green.`,
  },
  {
    name: 'catalogactivation', nn: '495', model: 'fable', effort: 'high',
    files: ['heartbeat/decima/discovery.py', 'heartbeat/decima/inbox.py'],
    title: 'CATALOG-ACTIVATION — a discovery "use" suggestion actually becomes an approvable, installable capability (suggestion → ApprovalInbox → installer)',
    seams: 'discovery.py discover(k, goal, *, threshold, research, forge) — returns suggestions; when it finds a bundled manifest it emits an action=="use" suggestion, but TODAY nothing turns that into an ApprovalInbox item and no installer maps the found manifest to a real handler — kernel.py:553 tells the human "approve to activate it" yet approval has nothing to fire; inbox.py ApprovalInbox — enqueue(...)/approve()/deny() the Morta-gated approval spine (approve_invocation runs the real authorize+Morta path); builtin_manifests.register_builtins(k) — populates the catalog discover() searches (call it in your CHECK setup so a use-suggestion exists to drive); kernel.py:482 integrate_tool(name, handler, caveats) — the PUBLIC installer that maps a manifest to a gated invokable handler (use it; do NOT edit kernel.py). Study checks/418_golive.py and checks/392_quarantine.py for the inbox+gate idiom. Do NOT edit kernel.py, agent.py, golive.py, or builtin_manifests.py — fix at the discovery/inbox seam so kernel.say:553 inherits it.',
    spec: `
PURPOSE: the SWEEP found the discovery "use" path is decorative — discover() surfaces a use-suggestion and kernel.say:553 says "approve to activate", but NO ApprovalInbox item is ever submitted and no installer maps a found manifest to a handler, so approval fires nothing. Close it AT THE DISCOVERY/INBOX SEAM (edit ONLY discovery.py + inbox.py) so kernel.say inherits it without a core edit.
  - Add an ACTIVATION path: a use-suggestion from discover() must carry (or a discovery helper must build) an activation handle that, when approved through ApprovalInbox, INSTALLS the bundled manifest as a real gated capability via kernel.integrate_tool — after which invoking it routes the ordinary authorize+Morta path. The submission of the ApprovalInbox item happens at the discovery seam (a function kernel.say already calls, or a new discovery.submit_activation(k, suggestion) that kernel.say's existing hook shape can call) — DO NOT edit kernel.py; if kernel.say must call it, make it inherit via an existing call shape and state that in notes.
  - Nothing auto-activates: activation REQUIRES an explicit approve() (fail closed); a denied activation installs nothing and records a denial Cell. Untrusted manifest content stays DATA (instruction_eligible=False).
  - If (and only if) the suggestion→say wiring genuinely cannot be closed without a kernel.py edit, deliver the full discovery/inbox seam (submit + installer) that a one-line kernel.say call would use, prove it via direct call in your check, and flag the exact kernel.say line needed in notes (it will be picked up in Batch U forgesurface which edits kernel.say). Prefer the no-core-edit path.

CHECK checks/495_catalogactivation.py proves, offline + deterministically:
  (a) SUGGESTION → APPROVAL → INSTALL (load-bearing): register_builtins(k); drive discover() for a bundled goal → a use-suggestion; submit it → an ApprovalInbox item exists (nothing installed yet); approve() → the manifest is installed as a gated capability (integrate_tool ran) and is now invokable through kernel.invoke; assert the capability did NOT exist before approval and DOES after.
  (b) FAIL CLOSED: without approval nothing is installed; deny() installs nothing and records the denial; no auto-activation.
  (c) DATA: manifest/suggestion content is instruction_eligible=False.
  Mutation: revert the installer (approve() no longer calls integrate_tool) → (a) goes RED (approval installs nothing). State the load_bearing_line.
This lane EDITS heartbeat/decima/discovery.py AND heartbeat/decima/inbox.py — return BOTH in files[]. Do NOT change bind_default_codegen's existing signature (another lane calls it). Register your OWN hermetic effect if needed (e.g. 'cav_probe'), never 'echo'.`,
  },
  {
    name: 'surface', nn: '496', model: 'fable', effort: 'high',
    files: ['heartbeat/decima/shell.py'],
    title: 'SURFACE — operator verbs put the flipped engine and the sync stack on the running path (engine <name> <op> <json> + sync <peer>)',
    seams: 'shell.py cmd.Cmd surface (do_flip at ~203 activates an engine via golive.activate_engine; do_live prints doctor_lines) — TODAY there is NO verb that INVOKES a flipped engine, and NO verb over the sync stack; kernel.invoke — the standard authorize+Morta invoke path (drive engines through THIS, not a private call); golive.activate_engine installs the engine handler (another lane wires it — your verb must drive via kernel.invoke so it does NOT hard-depend on that new function to be GREEN in isolation); sync.py serve_once / sync_socket / SecureChannel (mutual-auth encrypted channel, check 398) + vault.py — proven, no operator surface. Study checks/480_shellsurface.py (the do_ + capture idiom) and checks/398 / the sync API. Do NOT edit golive.py, sync.py, vault.py, or kernel.py — only ADD verbs in shell.py that CALL their public APIs.',
    spec: `
PURPOSE: the SWEEP found two proven stacks with no operator surface — a flipped engine has no verb to INVOKE it, and the whole sync/merkle/gossip/vault stack (P1 "sync channel confidentiality + peer auth") is check-only. Add both verbs in shell.py (edit ONLY shell.py; call existing public APIs):
  - do_engine: "engine <name> <op> <json-args>" → invoke the named live/flipped engine's op through the STANDARD kernel.invoke path (authorize + Morta + the wire gate all still run — the verb mints nothing and does NOT bypass the gate). Foreign args validated; result is DATA (instruction_eligible=False). A no-such-engine / ungranted call fails closed. IMPORTANT: drive it through kernel.invoke of the installed engine capability so this verb is GREEN on the current HEAD (your check registers a test engine handler via the PUBLIC integrate_tool over kernel.invoke — do NOT depend on another lane's activate_engine to pass; the real flipped engine is validated at integration once livecodegen lands).
  - do_sync: "sync <host:port>" (connect) and "sync listen [port]" (serve) → drive sync.serve_once / sync_socket / SecureChannel so a second instance can reconcile over the proven mutual-auth encrypted channel. Offline-testable (injected socket/loopback or a direct SecureChannel handshake in-process — NO real long-lived network in the check). Gated; fails closed without the peer handshake.
  Both verbs route the ordinary gates and mint nothing; untrusted peer/engine content is DATA. Keep EVERY existing verb + check green.

CHECK checks/496_surface.py proves, offline + deterministically (instantiate the Shell over a fresh Kernel; drive do_ methods; capture output / assert weave-level effects):
  (a) ENGINE VERB INVOKES THROUGH THE GATE (load-bearing): register a test engine capability via integrate_tool; driving "engine <name> <op> <json>" actually routes kernel.invoke (the handler ran) with authorize+Morta NOT bypassed (an ungranted call is refused/queued, a malformed-json arg fails closed), result is DATA.
  (b) SYNC VERB DRIVES THE CHANNEL (load-bearing): "sync ..." exercises the real SecureChannel handshake (a mutual-auth reconcile happens in-process / over loopback), fails closed without a valid peer.
  (c) NO REGRESSION: existing verbs + all existing checks green.
  Mutation: make do_engine answer directly without kernel.invoke (bypass the gate) OR make do_sync a help-string no-op → (a) or (b) goes RED. State the load_bearing_line.
This lane EDITS heartbeat/decima/shell.py — return it in files[]. Register your OWN hermetic effect if needed (e.g. 'srf_probe'), never 'echo'.`,
  },
  {
    name: 'contextfold', nn: '497', model: 'fable', effort: 'high',
    files: ['heartbeat/decima/agent.py'],
    title: 'CONTEXTFOLD — wire context_fold into ModelBrain history so the live context window is bounded (Law 5 on the message window)',
    seams: 'agent.py:402 ModelBrain.__init__ sets self.messages = [] and it GROWS UNBOUNDED — decide()/say append user+assistant turns (agent.py:411,424-426) and history=list(self.messages) is sent to the model every turn with NO folding; context_fold.py:133 fold(history, *, keep_recent, budget) EXISTS (Law-5 fold of the LLM window: keeps recent turns, folds older ones into a summary Cell projection) with NO caller in ModelBrain; agent.py ModelBrain.decide / _post — where history is assembled before the wire-gated post. Study context_fold.fold signature + return shape and agent.py ModelBrain end-to-end. Edit ONLY agent.py.',
    spec: `
PURPOSE: the audit found context_fold (Law-5 folding of the context window) is never wired into ModelBrain — self.messages grows unbounded on the LIVE call path, a real correctness/cost hole once keyed. Wire it (edit ONLY agent.py):
  - In ModelBrain, before the history is sent to the model each turn (in decide / wherever history=list(self.messages) is assembled), fold it via context_fold.fold(history, keep_recent=<int>, budget=<int|None>) so the OUTBOUND window is bounded — recent turns kept verbatim, older turns folded into a summary projection. The fold is a PURE Law-5 projection: it does NOT mutate the Weft-of-record / the append-only truth; self.messages (or the record) stays complete — only the window SENT is folded. Deterministic; ints (keep_recent/budget), no wall-clock in recorded content. Untrusted/DATA turns stay instruction_eligible=False through the fold (folding must not launder DATA into trusted text).
  - Preserve every existing agent/brain check green; the RuleBrain path and non-keyed path are unaffected.

CHECK checks/497_contextfold.py proves, offline + deterministically (a stub brain capturing the history it receives — NO network):
  (a) OUTBOUND WINDOW BOUNDED (load-bearing): drive ModelBrain through many turns (well past keep_recent) with an injected stub transport that records the history it is handed; assert the sent window is FOLDED (bounded to ~keep_recent + a summary), NOT the full unbounded list — assert len(sent_history) stays bounded as turns grow.
  (b) TRUTH PRESERVED: the fold does not drop the append-only record / does not mutate prior recorded Cells; a DATA turn stays instruction_eligible=False after folding.
  (c) NO REGRESSION: existing agent/brain checks green.
  Mutation: revert the fold call (send list(self.messages) raw) → (a) goes RED (the window grows unbounded again). State the load_bearing_line.
This lane EDITS heartbeat/decima/agent.py — return it in files[]. Register your OWN hermetic effect if needed (e.g. 'cf_probe'), never 'echo'.`,
  },
  {
    name: 'presentwire', nn: '498', model: 'fable', effort: 'high',
    files: ['heartbeat/decima/research.py', 'heartbeat/decima/mailpoll.py'],
    title: 'PRESENTWIRE — engine/research/mail output flows through the present() quarantine chokepoint before any re-injection (P1 "the ONLY door" gets real callers)',
    seams: 'agent.py:76 present(k, agent_cell, brain, external, *, question) — P1 quarantine chokepoint ("present() is the ONLY door"; admit_engine_output:94; run_task:1067) — EXISTS, proven in checks/392_quarantine.py, but has ZERO production callers: every real ingestion path stores engine/web/mail content as DATA and never re-presents it to a brain; research.py research(k, agent, question, urls) — produces cited synthesis from fetched (untrusted) web content; mailpoll.py the poll handler that receives (untrusted) mail bodies. Study agent.present/admit_engine_output signature + return, checks/392_quarantine.py, and how research/mailpoll currently handle fetched/received content. Edit ONLY research.py + mailpoll.py — do NOT edit agent.py (present() already exists and works; another lane owns agent.py). CALL agent.present()/admit_engine_output().',
    spec: `
PURPOSE: P1 claims present() is "the ONLY door" for untrusted content re-entering a brain, but it has ZERO production callers — the guarantee is exercised only in checks/392, never on a live path. Give it REAL callers where untrusted engine output would re-enter reasoning (edit ONLY research.py + mailpoll.py; CALL agent.present()/admit_engine_output(), do NOT edit agent.py):
  - research.research: when fetched web content is turned into synthesis that could feed back into a brain/decide, route that engine output through agent.present()/admit_engine_output so it enters as QUARANTINED DATA (instruction_eligible=False) through the mandated chokepoint — not stored-and-later-silently-reinjected. The cited synthesis remains DATA.
  - mailpoll poll handler: received mail bodies (untrusted) that get surfaced to reasoning route through the same present()/admit_engine_output door.
  Fail closed: content that would be re-injected without going through present() must not exist on the live path after this lane. Nothing mints authority; DATA stays instruction_eligible=False; deterministic, ints, no wall-clock in recorded content. Keep every existing research/mailpoll/quarantine check green.

CHECK checks/498_presentwire.py proves, offline + deterministically (stub brain/fetcher — NO network):
  (a) REAL CALLER ON THE RUNNING PATH (load-bearing): drive research.research (with an injected fetcher returning untrusted content) and the mailpoll handler (with an injected untrusted mail body); assert agent.present()/admit_engine_output was ACTUALLY invoked (a weave-level quarantine effect / the quarantined-DATA Cell present() mints) — i.e. the engine output entered through the chokepoint, not around it.
  (b) DATA + FAIL CLOSED: the presented content is instruction_eligible=False; an attempt to re-inject engine output that bypasses present() is absent from the live path.
  (c) NO REGRESSION: existing research/mailpoll/quarantine checks green.
  Mutation: revert the present() call in research (store the synthesis directly, bypassing the chokepoint) → (a) goes RED (the module output no longer flows through the ONLY door). State the load_bearing_line.
This lane EDITS heartbeat/decima/research.py AND heartbeat/decima/mailpoll.py — return BOTH in files[]. Register your OWN hermetic effect if needed (e.g. 'pw_probe'), never 'echo'.`,
  },
]

const checkPath = (l) => `heartbeat/checks/${l.nn}_${l.name}.py`

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['name', 'oracle_green', 'files', 'check_path', 'check_source', 'board_entry', 'load_bearing_line', 'summary'],
  properties: {
    name: { type: 'string' }, oracle_green: { type: 'boolean' }, self_test_tail: { type: 'string' },
    files: {
      type: 'array', minItems: 1,
      items: {
        type: 'object', additionalProperties: false, required: ['path', 'source'],
        properties: { path: { type: 'string' }, source: { type: 'string' } },
      },
    },
    check_path: { type: 'string' }, check_source: { type: 'string' },
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
    corrected_files: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false, required: ['path', 'source'],
        properties: { path: { type: 'string' }, source: { type: 'string' } },
      },
    },
    corrected_check_source: { type: 'string' },
  },
}

function ownedFiles(l) { return l.files.join(' AND ') }

function implPrompt(l) {
  return `You are building ONE lane of Decima Batch T — the running-path CORRECTNESS pass that closes the red core the 4th-quality re-audit found (P3 live self-extension is a placeholder; the callerless-module pattern recurs at scale). Your lane wires proven-but-unreached mechanism onto the RUNNING path using functions that ALREADY EXIST at HEAD. You are Fable 5.

LANE: ${l.title}
THIS LANE EDITS EXACTLY: ${ownedFiles(l)}. Return the FULL updated source for EACH in files[] (one {path, source} per owned file). Edit ONLY those files. Add a NEW check at ${checkPath(l)}. Keep EVERY existing check green.
STUDY FIRST (Read in ${REPO}/heartbeat): ${l.seams}

SPEC:${l.spec}
${HOUSE}
KEY BAR: your wiring must be a REAL caller on the running path (the mechanism is actually reached when the verb/boot/default/brain runs), not another unwired helper — your check must prove the running system now REACHES it, and your mutation must make that check go RED. Self-test until smoke.py is green, then return full sources + board_entry + load_bearing_line + oracle_green=true. If not green, return best effort with oracle_green=false and explain in notes.`
}

function reviewPrompt(l, impl) {
  const focus = l.name === 'livecodegen' ? 'does model_codegen REALLY post through an injected egress-bound brain and return source as DATA (and fail closed unarmed)? does boot call bind_default_codegen + register_builtins? does activate_engine install a handler that, on kernel.invoke, actually drives the engine fn over its gated transport (the missing CONSUMER)? is it fail-closed without a grant?'
    : l.name === 'catalogactivation' ? 'does a use-suggestion actually become an ApprovalInbox item, and does approve() actually integrate_tool-install the manifest as an invokable gated capability (that did NOT exist before approval)? no auto-activation, deny installs nothing? did it avoid editing kernel.py?'
    : l.name === 'surface' ? 'does do_engine route through kernel.invoke (authorize+Morta NOT bypassed) and do_sync drive the real SecureChannel handshake? green on HEAD without depending on another lane? both fail closed?'
    : l.name === 'contextfold' ? 'is context_fold.fold ACTUALLY called before history is sent, so the OUTBOUND window is bounded as turns grow (assert len stays bounded)? does folding preserve the append-only truth and keep DATA instruction_eligible=False?'
    : 'does research/mailpoll ACTUALLY route untrusted engine/mail output through agent.present()/admit_engine_output (the P1 chokepoint) on the running path — a weave-level quarantine effect — not around it? DATA preserved? did it avoid editing agent.py?'
  return `You are an ADVERSARIAL reviewer for Decima Batch-T lane "${l.name}". This batch exists because the 4th-quality audit found proven mechanism with no production callers AND a P3 loop that is a placeholder even when keyed — so your PRIME question: is this a REAL caller on the running path, or another unwired helper + a check that exercises it in isolation? Default to BLOCK if the wiring is cosmetic, the load-bearing line is not load-bearing, or the mutation would not actually go red. Focus: ${focus}

Delivered files:
${impl.files.map((f) => `  FILE ${f.path}:\n\`\`\`python\n${f.source}\n\`\`\``).join('\n')}
  CHECK ${impl.check_path}:
\`\`\`python
${impl.check_source}
\`\`\`
Load-bearing line: ${JSON.stringify(impl.load_bearing_line)}
Notes: ${JSON.stringify(impl.notes || '')}

DO THIS:
1. WORK=$(mktemp -d); cp -r ${REPO}/heartbeat "$WORK/heartbeat"; write EACH delivered file → $WORK/<path>, and the check → $WORK/${impl.check_path}.
2. GREEN: cd "$WORK/heartbeat" && python3 smoke.py — must end "heartbeat: alive. ✓" exit 0, ALL existing checks green. Minutes; be patient.
3. MUTATION + REAL-CALLER: revert the load-bearing line, re-run ONLY this lane's check, confirm FAIL. THEN grep the edited file(s) to confirm the wiring is a REAL call on the running path (livecodegen: boot→bind_default_codegen/register_builtins, activate_engine→integrate_tool, model_codegen→brain post; catalogactivation: approve→integrate_tool; surface: do_engine→kernel.invoke; contextfold: decide→context_fold.fold; presentwire: research/mailpoll→agent.present). If cosmetic → mutation_caught=false → BLOCK.
4. LAW AUDIT: an unwired helper passed off as wired; a gate bypassed by the new verb/boot/handler; untrusted content instruction_eligible=True; a fold that launders DATA into trusted text; floats/wall-clock in recorded content; edits to a CORE-SERIALIZED file (weave/weft/kernel/executor/smoke) or to a file this lane does NOT own; import cycle; a check that does not fail loud; board_entry overclaim; any regression.
5. If you can cleanly fix a real defect without changing intent, do so and return FULL corrected_files (every file) + corrected_check_source. Else BLOCK with must_fix.

Return the structured verdict reflecting what you OBSERVED running the code.`
}

const results = await pipeline(
  LANES,
  (l) => agent(implPrompt(l), { label: `impl:${l.name}`, phase: 'Implement', agentType: 'general-purpose', model: l.model, effort: l.effort, schema: IMPL_SCHEMA }),
  (impl, l) => {
    if (!impl) { log(`impl:${l.name} produced no result — skipping review`); return null }
    log(`impl:${l.name} → oracle_green=${impl.oracle_green} (${(impl.files || []).length} files); reviewing`)
    return agent(reviewPrompt(l, impl), { label: `review:${l.name}`, phase: 'Review', agentType: 'general-purpose', model: 'fable', effort: 'high', schema: REVIEW_SCHEMA })
      .then((review) => ({ name: l.name, nn: l.nn, files: l.files, check_path: checkPath(l), impl, review }))
  }
)

const lanes = results.filter(Boolean)
const approved = lanes.filter((x) => x.review && x.review.verdict === 'APPROVE' && x.review.mutation_caught)
log(`Batch T done: ${approved.length}/${lanes.length} lanes APPROVED with a load-bearing check`)
return { lanes, approved: approved.map((x) => x.name) }
