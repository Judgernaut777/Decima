export const meta = {
  name: 'decima-batchA-roadmap-green',
  description: 'Batch A — make Phase-4 claims TRUE + close P2 wiring debt: live-world harness, live spend+provider wiring on the real brain path, production beat driver, live-engine flip. Fable 5 for correctness lanes, Sonnet 5 for surface wiring; each adversarially mutation-reviewed (Fable 5).',
  phases: [
    { title: 'Implement', detail: 'one agent per lane, isolated heartbeat copy; Fable 5 for correctness-heavy wiring, Sonnet 5 for surface/driver', model: 'mixed' },
    { title: 'Review', detail: 'adversarial mutation-test + law audit per lane (Fable 5)', model: 'fable' },
  ],
}

const REPO = '/home/mini/decima-claude'

const HOUSE = `
DECIMA HOUSE RULES (obey exactly):
- Repo: ${REPO}. The heartbeat (pure-stdlib reference) is at ${REPO}/heartbeat. The decima package is ${REPO}/heartbeat/decima. Checks are auto-discovered from ${REPO}/heartbeat/checks/NN_*.py by smoke.py.
- WORK IN ISOLATION so parallel lanes never collide:
    WORK=$(mktemp -d)
    cp -r ${REPO}/heartbeat "$WORK/heartbeat"
    cd "$WORK/heartbeat"
  Build/edit your files inside "$WORK/heartbeat" and self-test there.
- Pure Python STDLIB only (PyNaCl is the sole allowed dep and already present). NO new pip deps.
- The Five Laws are enforced by SHAPE, not convention. Your lane MUST honor:
    * Everything on the Weft (append-only). New state = Cells via decima.model.assert_content / assert_edge, authored through existing kernel principals. Never mutate history.
    * ZERO ambient authority: mint/flip authority ONLY through existing kernel APIs (grant/approve/authorize; a live engine flips ONLY behind an APPROVED egress grant). A metering/routing/driver step confers NO authority.
    * UNTRUSTED CONTENT IS DATA. Anything from outside stays instruction_eligible=False.
    * INTS-NOT-FLOATS in any recorded/signed content (micro-cents, tokens, ticks, counts as ints). Reject floats at the door.
    * FAIL CLOSED and DETERMINISTIC: no wall-clock, no unseeded randomness in RECORDED content; "now" is a logical int the caller supplies; outward/paid/irreversible actions are Morta-gated and fail closed over budget / without an approved grant.
- Do NOT edit smoke.py. Do NOT edit the CORE-SERIALIZED files (weave.py, weft.py, kernel.py, executor.py). Your lane edits ONLY its assigned file(s) below and composes everything else over PUBLIC APIs. The 4 lanes have DISJOINT files.
- STUDY the named seams with Read before writing. Match idiom + docstring style (explain the LAW/behavior). Model checks on checks/424_resume.py, checks/416_spend.py.
- Your check heartbeat/checks/<NN>_<name>.py defines def run(k, line): ..., FAILS LOUD via assert, prints line("  ..."), ends with "  → ...". Register your OWN uniquely-named effect via executor.register if needed — never 'echo'.
- SELF-TEST: from "$WORK/heartbeat" run  python3 smoke.py  → must end "heartbeat: alive. ✓" exit 0, ALL existing checks green. The full run is 190+ checks / minutes — be patient. Capture the last ~15 lines.
- Report the ONE load-bearing line whose reversion makes YOUR check go red, verbatim.
- DELIVERABLE = returned source (module_source + check_source; extra_file_path/extra_file_source ONLY for a disclosed second file). Do NOT commit/push/modify ${REPO}. The integrator lands it.
`

const LANES = [
  {
    name: 'liveworld', module: 'liveworld.py', nn: '454', model: 'fable', effort: 'high', edits: false, extra: '',
    title: 'LIVE-WORLD TEST HARNESS — the named P4 item that was never built: deterministic fault injection over the gated live paths',
    seams: 'wire.py + live_wire.py (the gated egress transport + adapters; how a live call is constructed and how it fails closed — NoGatedTransport/EgressDenied), tracing.py (the UNKNOWN/PENDING receipt idiom — an unobservable outcome is NEVER fabricated as SUCCEEDED), the wrapped-engine OFFLINE-STUB idiom (shipping/weather/sms engine checks — how a live-constructed transport is exercised offline with an injectable stub), executor.py (register/receipts), kernel.py (invoke → receipt status).',
    spec: `
PURPOSE: build the "live-world test harness" — a NAMED Phase-4 roadmap item that does NOT exist (only VISION + the roadmap line mention it). A reusable, deterministic FAULT-INJECTION harness that drives an effect/engine through the gated transport under adverse conditions and PROVES the system degrades HONESTLY: fails closed, records a truthful FAILED/UNKNOWN receipt (never a fabricated SUCCEEDED), the gate holds, nothing leaks.

BUILD decima/liveworld.py composing PUBLIC APIs only (wire/live_wire/tracing/executor/kernel):
  - a set of injectable FAULT transports (stubs, mirroring the wrapped-engine offline tests): TIMEOUT, CONNECTION-REFUSED, TAMPERED-RESPONSE, PARTIAL/EMPTY, and REVOKED-MID-FLIGHT (the cap approved then revoked before the call). Each is a deterministic stand-in for a real network fault — NO real socket, NO wall-clock.
  - scenario(k, agent_cell, cap_id, fault, args) -> runs one effect through the gated path with the injected fault; returns the honest outcome {status, receipt, gate_held, leaked}.
  - run_suite(k, agent_cell, cap_id) -> runs the full battery and returns an int-keyed report {scenarios:int, honest:int, fabricated:int(=0 expected), denials:int}.
  The harness ASSERTS the invariant itself is not the point — it EXPOSES outcomes so a check can assert them; but it must itself never fabricate a status.

CHECK checks/454_liveworld.py proves, offline + deterministically:
  (a) EVERY FAULT DEGRADES HONESTLY (load-bearing): each injected fault yields a truthful receipt — a timeout/refused/tampered/partial call is FAILED or UNKNOWN (per tracing's rule), NEVER a fabricated SUCCEEDED; run_suite reports fabricated == 0.
  (b) THE GATE HOLDS UNDER FAULT: a revoked-mid-flight cap is DENIED (fail closed) — the effect does not fire after revocation; an ungated/bare path raises (no bypass under stress).
  (c) NO LEAK: a secret in a faulted request/response is not recorded raw on the Weft.
  (d) A REGRESSION IS CAUGHT: demonstrate the harness catches a FABRICATED-SUCCESS mutation (i.e. if some effect lied SUCCEEDED under a timeout, run_suite's fabricated count would rise and the check would fail).
  Mutation: neuter the honest-outcome assertion path (let the harness count a fabricated SUCCEEDED under a fault as honest) → (a)/(d) goes RED. State the load_bearing_line.
Register your OWN hermetic effect(s) (e.g. 'lw_probe'), never 'echo'.`
  },
  {
    name: 'spendwire', module: 'agent.py', nn: '456', model: 'fable', effort: 'high', edits: true, extra: '',
    title: 'SPEND + PROVIDER ON THE LIVE BRAIN PATH — wire spend.SpendMeter + provider_router onto ModelBrain, not just checks',
    seams: 'agent.py (ModelBrain._post at ~769 — it already calls _screen_egress (Cycle 52 redaction) then _transport(); the live_engine_fn seam at ~851; make_brain), spend.py (SpendMeter at line 82, record_dispatch at 259, is_paid, microcents_for — the budget/confirm-charge meter), provider_router.py (eligibility line 150, score 250, providers_from_status 121, the RoutingDecision at 294 — the selection plane), redact.py (already wired), inbox.py (a paid over-budget dispatch enqueues for approval), checks/416_spend.py + 412 for the intended contract.',
    spec: `
PURPOSE: Cycle 50 built the model-strategy plane (provider_router.select + spend.SpendMeter) and Cycle 52 wired ONLY the redaction stage onto ModelBrain._post. Provider SELECTION and SPEND metering are still exercised ONLY by checks — a real live-path model/engine call is neither routed nor metered (a live call would spend money while observ.metrics reports spend 0). Close BOTH P4's "live spend metering" item and P2's provider_router/SpendMeter gap on the SAME agent.py seam.

HARDEN decima/agent.py (edit ONLY agent.py):
  - in ModelBrain._post (or the live_engine_fn dispatch seam), AFTER _screen_egress and BEFORE the transport call: consult provider_router to select the provider/tier for the (privacy-classified) request, and record the spend via a SpendMeter — a PAID provider dispatch must fail closed when unconfigured/over-budget (route to the ApprovalInbox, spending nothing until approved, exactly as spend.py already models), and a permitted dispatch records a spend_charge/dispatch Cell (ints, micro-cents). The provider selection + meter live on the LIVE path now, not only in a check.
  - keep the Cycle-52 redaction gate and the RuleBrain fallback intact; keep every existing check green (400_brain, 416_spend, 422_redact_egress, etc.). All amounts ints.

CHECK checks/456_spendwire.py proves, offline + deterministically (stub transport / injected meter+router):
  (a) LIVE CALL IS METERED + ROUTED (load-bearing): a ModelBrain live-path dispatch now consults provider_router (a provider is selected / an ineligible one rejected) AND records spend through the meter — after a metered call, observ.metrics (or the meter) reports the spend as a NON-ZERO int (was 0).
  (b) PAID OVER-BUDGET FAILS CLOSED: a paid dispatch over budget / unconfigured spends NOTHING and routes to the ApprovalInbox (no charge Cell, budget unchanged) until a human approves.
  (c) NO REGRESSION: the redaction egress gate (Cycle 52) still blocks a secret turn; the RuleBrain fallback still engages on transport failure; existing brain/spend checks stay green.
  Mutation: remove the meter/router consult from the live path (revert to Cycle-52 behavior) → (a) goes RED (a live call is unmetered/unrouted; spend stays 0). State the load_bearing_line.
This lane EDITS agent.py: module_path = heartbeat/decima/agent.py, module_source = the FULL updated agent.py. Register your OWN hermetic effect if needed (e.g. 'sw_probe'), never 'echo'.`
  },
  {
    name: 'beat', module: 'shell.py', nn: '458', model: 'sonnet', effort: 'medium', edits: true, extra: 'heartbeat/run.py',
    title: 'PRODUCTION BEAT DRIVER — the always-on loop gets a real caller: a beat command + substrate surface, and a boot-resume hook',
    seams: 'daemon.py (checkpoint(k) line 83, advance(k, upto) 103, resume(k, upto) 153 — the durable loop cursor + driver), reactor.py (tick — what a beat fires), observ.py (dashboard_lines / metrics — the operator metrics view), backup.py (backup(k)/verify/restore — the substrate surface), shell.py (the Cmd surface: do_say/do_inbox/do_live/do_view etc. at 33-281 — match the do_ idiom exactly; the shell holds self.k), run.py (the boot entrypoint — add a durable-loop resume on start).',
    spec: `
PURPOSE: the always-on substrate (daemon.advance/resume, reactor.tick, observ, backup) has ZERO production callers — "always-on" is a library, not a behavior; run.py is a REPL that never beats. Give it a real driver on the operator surface, and resume the durable loop at boot.

HARDEN decima/shell.py (edit ONLY shell.py) — add, matching the existing do_ idiom:
  - do_beat(arg): advance the durable run-loop to the current logical frontier (daemon.advance/resume off daemon.checkpoint(self.k)+the current lamport), printing the tick summary (fired / recovered / jobs). This is the command that actually MAKES the heartbeat beat.
  - do_metrics(arg): print observ.dashboard_lines(self.k) — the folded operational report.
  - do_backup(arg)/do_restore(arg): drive backup.backup/verify/restore (backup to a path; restore fails closed on a tampered blob).
  Keep every existing command working; keep all checks green.
EXTRA FILE heartbeat/run.py (disclosed): add a boot-time durable-loop RESUME (daemon.resume to the current frontier) so a restart CONTINUES the loop (idempotent, additive; keyless boot behavior otherwise unchanged). Return it via extra_file_path/extra_file_source.

CHECK checks/458_beat.py proves, offline + deterministically (drive the shell object / its methods directly over a fresh Kernel):
  (a) BEAT DRIVES THE LOOP (load-bearing): after arming due work (a scheduled event / an enqueued job), invoking the beat path advances daemon's checkpoint AND fires the due work (reactor.tick ran — the job runs / event fires / a crash-fired job is recovered); the checkpoint moved forward.
  (b) SUBSTRATE SURFACE WORKS: metrics prints a folded report; backup→restore round-trips and a tampered blob is refused.
  (c) BOOT RESUME: the run.py resume hook continues from the durable checkpoint (no beat re-fired, none skipped) — you may test the underlying daemon.resume call the hook makes.
  Mutation: make do_beat a no-op (drop the daemon.advance/reactor.tick call) → (a) goes RED (the checkpoint does not move; due work never fires from the command). State the load_bearing_line.
This lane EDITS shell.py + run.py. module_path = heartbeat/decima/shell.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'beat_probe'), never 'echo'.`
  },
  {
    name: 'liveflip', module: 'golive.py', nn: '460', model: 'sonnet', effort: 'medium', edits: true, extra: '',
    title: 'LIVE-ENGINE FLIP — populate k.live_engines: an approved credential actually turns a named engine live (or it stays offline)',
    seams: 'golive.py (doctor at 247 + line 299 reports k.live_engines as an unpopulated "Lane B" seam; request_grant at 131 — the per-host egress grant through the inbox; bind_brain at 222 — the SAME pattern for the anthropic brain, copy its shape), live_wire.py (the gated-transport adapters — constructing a live transport for an engine), inbox.py (grant approval), the engine modules (e.g. shipping.py/weather_engine.py — a NoGatedTransport default that a live transport replaces), kernel.py (the k object that would carry live_engines).',
    spec: `
PURPOSE: golive.doctor reports k.live_engines (which engines are ACTUALLY live) but NOTHING populates it — "absent today means nothing is live" (golive.py:299). So even after an operator supplies a key + approves a grant, no code path flips a named engine live. This is P2's real code gap hiding behind the operator-key excuse. Close it: an APPROVED egress grant for an engine's host actually constructs its gated transport and REGISTERS it in k.live_engines; without an approved grant the engine stays offline (fail closed). Mirror bind_brain's existing shape.

HARDEN decima/golive.py (edit ONLY golive.py):
  - activate_engine(k, name, host, ...) -> if (and ONLY if) an APPROVED egress grant for host is held (the same approved-grant test bind_brain uses), construct the engine's gated transport (live_wire) and register the engine in k.live_engines (a set/dict seam on k, created on demand — do NOT mint authority, only record which approved engine is live). An engine with NO approved grant CANNOT flip (fail closed, returns not-live). Record the flip on the Weft (an engine_live Cell, redacted — no secret).
  - doctor(k) now truthfully reports the populated live set.
  - keep bind_brain and every existing golive check green.

CHECK checks/460_liveflip.py proves, offline + deterministically:
  (a) APPROVED GRANT FLIPS THE ENGINE LIVE (load-bearing): grant+APPROVE an egress cap for an engine host, call activate_engine → the engine appears in k.live_engines and doctor reports it live.
  (b) NO APPROVAL → STAYS OFFLINE (fail closed): activate_engine for a host with no approved grant does NOT register the engine (not-live); doctor still reports it absent.
  (c) NO SECRET / NO AMBIENT: the flip records no secret on the Weft and mints no capability; a revoked grant un-lives the engine on the next doctor/flip.
  Mutation: drop the approved-grant check in activate_engine (register live unconditionally) → (b) goes RED (an unapproved engine flips live). State the load_bearing_line.
This lane EDITS golive.py: module_path = heartbeat/decima/golive.py (FULL updated). Register your OWN hermetic effect if needed (e.g. 'flip_probe'), never 'echo'.`
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
    extra_file_path: { type: 'string', description: 'ONLY if you edited a disclosed second file (e.g. run.py for the beat lane); else ""' },
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
    ? `\nTHIS LANE EDITS AN EXISTING MODULE: ${modulePath(l)}. Return its FULL updated source as module_source with that module_path. Edit ONLY that file${l.extra ? ` plus ${l.extra} (return via extra_file_path/extra_file_source, disclosed)` : ''} — nothing else. Add a NEW check at ${checkPath(l)}. Keep EVERY existing check green.`
    : `\nTHIS LANE ADDS A NEW MODULE: ${modulePath(l)} + its check ${checkPath(l)}.`
  return `You are building ONE lane of Decima Batch A — the batch that makes Phase-4's roadmap claims TRUE and closes the Phase-2 wiring debt (an audit found cycles 53-56 built check-proven LIBRARIES that nothing WIRES into the running system; this batch wires them). Model role: ${l.model === 'fable' ? 'correctness/security-heavy (Fable 5) — reason carefully about fail-closed on the LIVE path' : 'surface/driver wiring (Sonnet 5) — compose the existing substrate APIs onto the operator surface cleanly'}.

LANE: ${l.title}
${editNote}
STUDY FIRST (Read these in ${REPO}/heartbeat): ${l.seams}
Also read checks/424_resume.py and checks/416_spend.py for the check idiom.

SPEC:${l.spec}
${HOUSE}
Now: isolated copy, study the seams, implement, self-test until smoke.py is green (ALL existing checks pass), then return full sources + board_entry + load_bearing_line + oracle_green via the schema. If not green, return best effort with oracle_green=false and explain in notes.`
}

function reviewPrompt(l, impl) {
  const focus = l.name === 'liveworld' ? 'does every injected fault (timeout/refused/tampered/partial/revoked) yield a truthful FAILED/UNKNOWN receipt and NEVER a fabricated SUCCEEDED? does the gate hold under fault?'
    : l.name === 'spendwire' ? 'is spend actually metered + provider actually selected on the LIVE ModelBrain path (not just in the check)? does a paid over-budget dispatch fail closed to the inbox and spend nothing? did the Cycle-52 redaction gate and RuleBrain fallback survive?'
    : l.name === 'beat' ? 'does the beat command REALLY drive daemon.advance/reactor.tick (checkpoint moves, due work fires)? or is it cosmetic? did existing checks stay green?'
    : 'does an engine flip live ONLY behind an APPROVED grant, and stay offline without one (fail closed)? no secret on the Weft, no minted authority?'
  return `You are an ADVERSARIAL reviewer for Decima Batch-A lane "${l.name}". Be skeptical; catch a check that proves nothing or a wiring that is cosmetic / breaks a law. Default to BLOCK if the load-bearing line is not load-bearing. This batch exists because prior cycles shipped libraries nothing wired in — so your PRIME question: is this lane REALLY wired into the live/running path, or did it just add another unwired helper + a check that exercises the helper in isolation? Focus: ${focus}

Delivered files:
  MODULE ${impl.module_path}:
\`\`\`python
${impl.module_source}
\`\`\`
  CHECK ${impl.check_path}:
\`\`\`python
${impl.check_source}
\`\`\`
${impl.extra_file_path ? `  EXTRA FILE ${impl.extra_file_path}:\n\`\`\`python\n${impl.extra_file_source}\n\`\`\`\n` : ''}Load-bearing line they named: ${JSON.stringify(impl.load_bearing_line)}

DO THIS:
1. WORK=$(mktemp -d); cp -r ${REPO}/heartbeat "$WORK/heartbeat"; write module → $WORK/${impl.module_path}, check → $WORK/${impl.check_path}${impl.extra_file_path ? `, extra → $WORK/${impl.extra_file_path}` : ''}.
2. GREEN: cd "$WORK/heartbeat" && python3 smoke.py — must end "heartbeat: alive. ✓" exit 0, ALL existing checks green. (reproduced_green). Minutes; be patient.
3. MUTATION TEST: revert the load-bearing line (make the wiring a no-op / the gate always-true), re-run ONLY this lane's check, confirm it FAILS. Also confirm the wiring is on the REAL path: for spendwire/beat/liveflip, grep the edited module to confirm the live call site (ModelBrain._post / do_beat / activate_engine) actually invokes the meter/router / daemon / grant-check — not a dead helper. If decorative → mutation_caught=false → BLOCK. Restore after.
4. LAW AUDIT: ambient authority (an engine/brain going live without an approved grant; minting a cap outside kernel APIs); floats in recorded content; wall-clock/unseeded random; a fabricated SUCCEEDED under fault; edits beyond the assigned file(s); secret on the Weft; a check that does not fail loud; board_entry overclaim; any regression in existing checks.
5. If you can cleanly fix a real defect without changing intent, do so and return FULL corrected source(s); keep green + mutation caught. Else BLOCK with must_fix.

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
log(`Batch A done: ${approved.length}/${lanes.length} lanes APPROVED with a load-bearing check`)
return { lanes, approved: approved.map((x) => x.name) }
