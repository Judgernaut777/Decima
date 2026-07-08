export const meta = {
  name: 'decima-phase4-close',
  description: 'Phase 4 close-out: 2 correctness-heavy lanes (durable resumable run-loop, safe concurrency) each implemented (Fable 5) then adversarially mutation-reviewed (Fable 5)',
  phases: [
    { title: 'Implement', detail: 'one agent per lane, isolated heartbeat copy; Fable 5 (both correctness-heavy)', model: 'fable' },
    { title: 'Review', detail: 'adversarial mutation-test + law audit per lane (Fable 5)', model: 'fable' },
  ],
}

const REPO = '/home/mini/decima-claude'

const HOUSE = `
DECIMA HOUSE RULES (obey exactly):
- Repo: ${REPO}. The heartbeat (pure-stdlib reference) is at ${REPO}/heartbeat. The decima package is ${REPO}/heartbeat/decima. Checks are auto-discovered from ${REPO}/heartbeat/checks/NN_*.py by smoke.py.
- WORK IN ISOLATION so parallel lanes never collide. Do NOT edit the canonical repo. Instead:
    WORK=$(mktemp -d)
    cp -r ${REPO}/heartbeat "$WORK/heartbeat"
    cd "$WORK/heartbeat"
  Build your files inside "$WORK/heartbeat" and self-test there.
- Pure Python STDLIB only (PyNaCl is the sole allowed dep and already present). NO new pip deps. (threading / queue / concurrent.futures ARE stdlib and allowed.)
- The Five Laws are enforced by SHAPE, not convention. Your lane MUST honor:
    * Everything on the Weft (append-only). New state = Cells asserted via decima.model.assert_content / assert_edge, authored through existing kernel principals (k.decima.id etc). Never mutate history.
    * ZERO ambient authority: mint NO capability/grant except through existing kernel APIs (k._assert_cap + k.grant, k.spawn). A projection/analysis/loop-driver confers no authority.
    * INTS-NOT-FLOATS in any recorded/signed content (money in micro-cents, ticks as logical ints, counts as ints). Reject floats at the door.
    * FAIL CLOSED and DETERMINISTIC: no wall-clock, no unseeded randomness in RECORDED content; "now"/frontiers are logical ints the caller supplies. Untrusted input is DATA, never instruction.
- Do NOT edit smoke.py. Do NOT edit the CORE-SERIALIZED files (weave.py, weft.py, kernel.py, executor.py) — compose over their PUBLIC APIs from your new module. Do NOT edit reactor.py, jobs.py, scheduling.py, resume.py either — COMPOSE over their public APIs from your NEW module (the two lanes must have DISJOINT files).
- STUDY the named existing modules with Read before writing a line; match their idiom, docstring style (explain the LAW the lane keeps), and the adversarial check style.
- Your check file heartbeat/checks/<NN>_<name>.py defines exactly: def run(k, line): ... It must FAIL LOUD via assert, print progress with line("  ..."), and end with a "  → ..." summary line. It owns NN as assigned. Model it on an existing check (e.g. 424_resume.py, 416_spend.py). If your check needs an effect handler, register your OWN uniquely-named effect via executor.register (the module-global registry persists across checks — never depend on a shared effect like 'echo').
- SELF-TEST before returning: from "$WORK/heartbeat" run  python3 smoke.py  and confirm it ends with the line "heartbeat: alive. ✓" and exits 0 (echo $?). Capture the last ~15 lines. The FULL smoke run can take a few minutes (200+ checks) — be patient, do not assume a hang.
- Identify the ONE load-bearing line in your module — the enforcement line that, if reverted/neutered, makes YOUR check go red. Report it verbatim.
- The DELIVERABLE is the returned source (full module_source + full check_source) — do NOT git commit, git push, or modify ${REPO}. The integrator will land it.
`

const LANES = [
  {
    name: 'daemon', module: 'daemon.py', nn: '434', model: 'fable', effort: 'high',
    title: 'DURABLE RUN-LOOP — the heartbeat resumes across a restart, never re-beating or skipping',
    seams: 'reactor.py (tick(k, now) — the single deterministic pass firing watchers+events+jobs+crash-recovery; run_until(k, ticks) — the stub loop that ticks a sequence of logical frontiers, defaulting start to k.weft.lamport), scheduling.py (due/fire — how a future beat is a Cell), jobs.py + resume.py (durable jobs already fold back; recover() runs inside tick step 3a), weft.py (the .lamport logical frontier and the append-only fold), model.py (assert_content for a durable checkpoint Cell), kernel.py (k.decima.id author, k.weave()).',
    spec: `
PURPOSE: close the "durable scheduling across restart at the LOOP level" gap. Today durable JOBS survive a restart (they are Cells), and tick() is idempotent, but the RUN-LOOP itself has no durable memory of HOW FAR it has beaten: run_until() just ticks an in-memory sequence and defaults its start to k.weft.lamport. So after a crash there is no on-Weft cursor saying "the loop has fully processed up to logical frontier N" — a naive restart can either RE-SCAN from an arbitrary start or SKIP the beats between the last processed frontier and now. Make the loop's PROGRESS itself durable, on the Weft, so a fresh process resumes exactly where the last one stopped: no beat re-fired, no beat skipped.

BUILD decima/daemon.py composing PUBLIC APIs only (it OWNS a durable loop-cursor Cell and drives reactor.tick; it does NOT reimplement tick and does NOT edit reactor.py):
  - a durable checkpoint: a 'loop_checkpoint' Cell (LWW, asserted via model.assert_content, authored by k.decima.id) recording the highest logical frontier the loop has FULLY ticked. Content is int-only, e.g. {frontier:int, beats:int}.
  - checkpoint(k) -> int : fold the latest loop_checkpoint; return the highest fully-ticked frontier, or a sentinel (e.g. -1) if the loop has never run. Pure read.
  - advance(k, upto:int, *, author=None) -> dict : drive the loop from checkpoint(k)+1 THROUGH upto (inclusive), calling reactor.tick(k, f) at each frontier f in order, then record ONE new durable loop_checkpoint at upto. MUST NOT re-tick any frontier <= checkpoint(k) (idempotence across restart — the load-bearing guard). Fail closed on a float/bool upto or an upto < checkpoint (never move the cursor backward). Return a summary {from, to, ticked:[frontiers], fired:int, quiet:bool}.
  - resume(k, upto:int, ...) : convenience = advance from the DURABLE checkpoint (i.e. the value a fresh Kernel folds from the same db), proving a restart continues rather than restarts.
  Keep everything a logical int; NO wall-clock; the cursor is a fold, not a variable.

CHECK checks/434_daemon.py proves, offline + deterministically (fresh Kernels reconstructed over ONE temp db, logical int frontiers, no clock):
  (a) DURABLE CURSOR: advance(k, N) records a checkpoint; a NEW Kernel(db, fresh=False) over the SAME db folds checkpoint == N (the loop's progress survives a restart, as a Cell).
  (b) RESUME-NOT-RESTART (load-bearing): arm a beat that becomes due at a frontier (e.g. schedule an event / enqueue a job due at frontier M > N). Advance the loop to N (checkpoint N), then RECONSTRUCT the Kernel (simulating a crash+restart) and resume(k, M). Prove: the beats in (N, M] fire EXACTLY ONCE (the pending one fires), and NO frontier <= N is re-ticked (already-fired reactions are not re-fired — assert via a counter/effect-use that would double if re-ticked).
  (c) NO SKIP: no due beat between the checkpoint and upto is missed — the thing armed for frontier M actually fires by the time resume(k, M) returns.
  (d) IDEMPOTENT: advance(k, N) again (already checkpointed at >= N) is a NO-OP — ticks nothing, moves no cursor, fires nothing.
  (e) FAIL CLOSED: a float upto is rejected (TypeError); an upto < current checkpoint is refused (never rewind the cursor).
  Mutation: reverting the "skip frontiers <= checkpoint" guard (so advance re-ticks from a fixed start every call) makes (b)/(d) go RED — a restart re-fires already-processed beats (double-fire / lost idempotence). State that as the load_bearing_line.
Contract: run(k, line). Fail loud (assert). Owns fresh Kernels reconstructed over one db. Register your OWN hermetic effect (e.g. 'daemon_probe') if the check needs one — never reuse 'echo'.`
  },
  {
    name: 'concurrency', module: 'concurrency.py', nn: '436', model: 'fable', effort: 'high',
    title: 'SAFE CONCURRENCY — parallel job execution that can never double-fire or corrupt the log',
    seams: 'jobs.py (due/run — a job runs through ONLY its pre-fixed single-use lease; JOB/ENQUEUED/DONE/FAILED), kernel.py (invoke — appends the INVOKE event BEFORE the effect; lease_uses folds INVOKE events; the single-use lease is the exactly-once use-record), reactor.py (tick — the current SERIAL pass), weft.py (append/fold — how seq+parents are assigned; the append is the serialization point), executor.py (register — for a hermetic probe effect), resume.py (recover, for the crash-window context).',
    spec: `
PURPOSE: close the "concurrency" gap of the always-on substrate. Today reactor.tick runs due jobs in ONE serial pass. Real always-on wants to run INDEPENDENT due jobs in parallel — but parallelism must not (1) double-fire a single job's effect if two workers both pick it up, nor (2) corrupt the append-only Weft (seq/parents) under concurrent appends. The single-use lease + INVOKE-fold ALREADY make each effect exactly-once at the Weft; this lane provides a concurrent RUNNER that preserves that guarantee under real contention and keeps the append-only log consistent, with a RECORDED audit trail that is deterministic (identical fired-SET to a serial run; only wall-clock ordering differs, and NO wall-clock is ever recorded).

BUILD decima/concurrency.py composing PUBLIC APIs only (a new runner; it does NOT edit reactor.py/jobs.py/kernel.py):
  - run_concurrent(k, now:int, *, workers:int) -> dict : run every job jobs.due(k, now) across up to 'workers' threads (stdlib threading / concurrent.futures), each job through jobs.run on its OWN lease. Serialize the WEFT MUTATION so the append-only log never interleaves-corrupts — hold a lock around the kernel commit/append path (the smallest correct critical section: the invoke/append+status transition), letting only the pre-effect work overlap; OR, if the SQLite connection cannot be shared across threads, run effects concurrently and serialize the commit through a single-owner queue. Either way the INVARIANT is: each due job fires AT MOST ONCE (its single-use lease admits one INVOKE; a second worker that races the same job is denied by the exhausted lease), and the final Weave folds cleanly. Return {ran:[{job,status}], fired:int, denied:int} — int-only.
  - (optional) a helper claim/guard making the "one job → one worker" hand-off explicit, but the lease is the ground truth of exactly-once; do not invent a second authority.
  IMPORTANT — determinism of the RECORD: nothing wall-clock or thread-id goes into any Cell. The check's ASSERTIONS must hold for EVERY interleaving (they assert the INVARIANT — exactly-once, clean fold, same fired-set — not a specific timing), so the check is NOT flaky.

CHECK checks/436_concurrency.py proves, offline (real stdlib threads OK, but assertions deterministic for all interleavings):
  (a) EXACTLY-ONCE UNDER CONTENTION (load-bearing): mint ONE due job on a single-use lease and have K (>=8) workers RACE to run that SAME job concurrently. Assert: the effect fired EXACTLY ONCE (kernel.lease_uses == 1 afterward; exactly one worker reports DONE, the rest denied by the exhausted lease), and the job ends DONE — never a double-fire. Run this many times / with many workers to stress interleavings; it must ALWAYS hold.
  (b) PARALLEL INDEPENDENT JOBS: enqueue M independent due jobs (each its own lease+effect), run_concurrent across W workers; assert ALL reach DONE, the Weft still folds cleanly on a fresh Kernel over the same db (no id/parent/seq corruption — a reconstruct + fold raises nothing), and the SET of fired effects EQUALS what a serial reactor.tick would fire (same outcome, different wall-clock order).
  (c) LOG INTEGRITY: after the concurrent run, a fresh Kernel(db, fresh=False) folds the whole log with no WeftError (the serialized append kept seq/parents honest).
  (d) INTS / NO WALL-CLOCK in any recorded Cell.
  Mutation: removing the append/commit serialization lock (or the lease-claim reliance) makes (a) or (c) go RED — two workers double-fire the same job, or the concurrent appends corrupt the fold. State that as the load_bearing_line.
Contract: run(k, line). Fail loud (assert). Register your OWN hermetic effect (e.g. 'conc_probe') via executor.register — never reuse 'echo'. If real cross-thread SQLite writes prove unworkable in stdlib, a single-writer-queue design (effects overlap, commits serialized through one owner) is the intended fallback and is fully acceptable — the INVARIANT (exactly-once, clean fold) is what matters, not literal parallel writes.`
  },
]

const modulePath = (l) => `heartbeat/decima/${l.module}`
const checkPath = (l) => `heartbeat/checks/${l.nn}_${l.name}.py`

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['name', 'oracle_green', 'module_path', 'module_source', 'check_path', 'check_source', 'board_entry', 'load_bearing_line', 'summary'],
  properties: {
    name: { type: 'string' },
    oracle_green: { type: 'boolean', description: 'did `python3 smoke.py` end "heartbeat: alive. ✓" and exit 0 with your lane added' },
    self_test_tail: { type: 'string', description: 'last ~15 lines of smoke.py output' },
    module_path: { type: 'string' },
    module_source: { type: 'string', description: 'the FULL source of the new module' },
    check_path: { type: 'string' },
    check_source: { type: 'string', description: 'the FULL source of the new check' },
    board_entry: { type: 'string', description: 'one house-style paragraph recording the lane, for docs/BACKLOG.md' },
    load_bearing_line: { type: 'string', description: 'the exact enforcement line that, if reverted, makes your check go red' },
    core_files_touched: { type: 'array', items: { type: 'string' }, description: 'ideally empty; list any core/crypto file you had to edit and why' },
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
    reproduced_green: { type: 'boolean', description: 'did the delivered check pass and the full smoke.py stay green in a fresh copy' },
    mutation_caught: { type: 'boolean', description: 'did reverting the load-bearing line make the check go RED (assert fail)' },
    issues: { type: 'array', items: { type: 'string' } },
    must_fix: { type: 'string', description: 'blocking issue, if verdict BLOCK' },
    corrected_module_source: { type: 'string', description: 'FULL corrected module source if you fixed a defect, else ""' },
    corrected_check_source: { type: 'string', description: 'FULL corrected check source if you fixed a defect, else ""' },
  },
}

function implPrompt(l) {
  return `You are building ONE Phase-4 close-out lane of Decima (an agent-native OS reference in pure-stdlib Python). Model role: correctness-heavy (Fable 5) — this lane touches restart/concurrency semantics; reason carefully.

LANE: ${l.title}
Deliver TWO new files:
  - ${modulePath(l)}   (a new module)
  - ${checkPath(l)}    (its adversarial check, def run(k, line), owns NN=${l.nn})

STUDY FIRST (Read these in ${REPO}/heartbeat): ${l.seams}
Also read an existing check (heartbeat/checks/424_resume.py and heartbeat/checks/416_spend.py) to match the check idiom.

SPEC:${l.spec}
${HOUSE}
Now: set up the isolated copy, study the seams, implement the module + check, self-test until smoke.py is green, then return the full sources, the board_entry paragraph, the load_bearing_line, and oracle_green via the structured schema. If you could not make smoke.py green, still return your best sources with oracle_green=false and explain in notes.`
}

function reviewPrompt(l, impl) {
  return `You are an ADVERSARIAL reviewer for Decima Phase-4 lane "${l.name}". Be skeptical; your job is to catch a check that proves nothing or a module that breaks a law. Default to BLOCK if the load-bearing line is not actually load-bearing. For a CONCURRENCY lane especially: confirm the exactly-once / clean-fold assertions actually hold under contention and are NOT flaky (hold for every interleaving), and that removing the serialization/lease guard truly makes it go red.

The implementer delivered these files (paths + full source). Reconstruct and test them:
  MODULE ${impl.module_path}:
\`\`\`python
${impl.module_source}
\`\`\`
  CHECK ${impl.check_path}:
\`\`\`python
${impl.check_source}
\`\`\`
They named the load-bearing line: ${JSON.stringify(impl.load_bearing_line)}

DO THIS:
1. WORK=$(mktemp -d); cp -r ${REPO}/heartbeat "$WORK/heartbeat"; write the module to $WORK/${impl.module_path} and the check to $WORK/${impl.check_path} (create/overwrite exactly).
2. Confirm GREEN: cd "$WORK/heartbeat" && python3 smoke.py — must end "heartbeat: alive. ✓" exit 0. (reproduced_green). The full run is 200+ checks and takes minutes; be patient.
3. MUTATION TEST (the crux): neuter/revert the load-bearing enforcement line in the module (e.g. make the fail-closed check a no-op / always-true / remove the serialization lock), re-run ONLY this lane's check (import it and call run over a fresh Kernel, or run smoke and confirm this NN fails), and confirm it now FAILS (assert error). If it still passes, the check is decorative → mutation_caught=false → BLOCK. Restore afterward. For concurrency, also consider running the check several times to confirm it is not flaky.
4. LAW AUDIT: scan for — floats/bools in recorded/signed content; ambient authority (minting grants outside kernel APIs); edits to smoke.py or core files (weave/weft/kernel/executor) or to reactor/jobs/scheduling/resume not disclosed; secret material recorded on the Weft; non-determinism (wall-clock, thread-id, unseeded random) in RECORDED content; a check that does not fail loud; claims in the board_entry the code does not back.
5. If you find a REAL defect you can fix cleanly WITHOUT changing the lane's intent, fix it and return the FULL corrected source(s); keep smoke.py green and the mutation test catching. Otherwise, if it is a genuine blocker, BLOCK with must_fix.

Return the structured verdict. reproduced_green and mutation_caught must reflect what you actually observed running the code, not what the implementer claimed.`
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
    }).then((review) => ({ name: l.name, nn: l.nn, module_path: modulePath(l), check_path: checkPath(l), impl, review }))
  }
)

const lanes = results.filter(Boolean)
const approved = lanes.filter((x) => x.review && x.review.verdict === 'APPROVE' && x.review.mutation_caught)
log(`fleet done: ${approved.length}/${lanes.length} lanes APPROVED with a load-bearing check`)
return { lanes, approved: approved.map((x) => x.name) }
