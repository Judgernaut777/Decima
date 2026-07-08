export const meta = {
  name: 'decima-batchB2-rotwire',
  description: 'Batch B part 2 (re-run of the core lane): rotation-aware event verification in weft.py (Fable 5, xhigh), adversarially mutation-reviewed (Fable 5).',
  phases: [
    { title: 'Implement', detail: 'one Fable 5 agent per lane, isolated heartbeat copy; rotwire is the ONE core lane (weft verification)', model: 'fable' },
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
- Pure Python STDLIB only (PyNaCl is the sole allowed dep and already present). NO new pip deps. threading/queue/concurrent.futures are stdlib and allowed.
- The Five Laws are enforced by SHAPE:
    * Everything on the Weft (append-only). New state = Cells via decima.model.assert_content/assert_edge. Never mutate history.
    * ZERO ambient authority: mint/flip authority ONLY through existing kernel APIs. Registering/rotating a key confers NO authority (Law 2).
    * INTS-NOT-FLOATS in recorded/signed content. FAIL CLOSED + DETERMINISTIC: no wall-clock, no unseeded randomness in RECORDED content; "now"/point are logical ints; a fabricated SUCCEEDED under fault is forbidden.
- CORE-SERIALIZED files are weave.py, weft.py, kernel.py, executor.py. Only the ONE lane explicitly marked THE CORE LANE below may edit its named core file(s); every other lane edits ONLY its assigned non-core file and composes the rest over PUBLIC APIs. The 3 lanes have DISJOINT files.
- Do NOT edit smoke.py. STUDY the named seams with Read before writing. Match idiom + docstring style. Model checks on checks/424_resume.py, checks/430_rotation.py, checks/436_concurrency.py.
- Your check heartbeat/checks/<NN>_<name>.py defines def run(k, line): ..., FAILS LOUD via assert, prints line("  ..."), ends with "  → ...". Register your OWN uniquely-named effect via executor.register if needed — never 'echo'.
- SELF-TEST: from "$WORK/heartbeat" run  python3 smoke.py  → must end "heartbeat: alive. ✓" exit 0, ALL existing checks green (200+ checks; minutes — be patient). This is the hard bar: a core change that breaks ANY existing check is not done.
- Report the ONE load-bearing line whose reversion makes YOUR check go red, verbatim.
- DELIVERABLE = returned source (module_source + check_source; extra_file_path/extra_file_source ONLY for a disclosed second file). Do NOT commit/push/modify ${REPO}.
`

const LANES = [
  {
    name: 'rotwire', module: 'weft.py', nn: '462', model: 'fable', effort: 'xhigh', edits: true, core: true, extra: 'heartbeat/decima/kernel.py',
    title: 'ROTATION-AWARE EVENT VERIFICATION — Decima’s own signing keys can rotate/recover; the Weft consults the succession chain (THE CORE LANE)',
    seams: 'weft.py (line 184: `if not self.keyring.verify(author, eid, sig): raise WeftError(...)` inside events() — verification of EVERY folded event against the author’s ONE key; append() at 99-136 signs with keyring.sign), rotation.py (register/rotate/recover, valid_key_at(chain→key valid AT a logical point), verify_event, the succession-chain model over crypto.py — from Cycle 54), crypto.py (Keyring.verify/sign/keybook, keyed_pid/mint_keyed, verify_keyed — NOT core, composable), kernel.py (__init__ genesis at 31-54 mints root/executor/decima/human — where a genesis principal would ENROLL as its own chain root), checks/430_rotation.py (the existing rotation check — MUST stay green).',
    spec: `
PURPOSE: Cycle 54 built rotation.py (a Keybase-style succession chain so an identity survives its keys) but it has ZERO production callers — the kernel/Weft still verify every event against a one-key-forever Keyring (rotation.py is decorative until the Weft consults it). Make the promise REAL: event verification consults the rotation succession chain, so an authority (Decima’s own principals included) can rotate or recover its signing key and its whole history still verifies — old events under the old key, post-rotation events under the new key, an event signed by a RETIRED key REFUSED.

THE CORE LANE — you MAY edit weft.py (and kernel.py genesis enrollment if strictly required; return kernel.py via extra_file). Change the verification seam so that verifying an event consults the key VALID AT THAT EVENT’S logical point (rotation.valid_key_at), not merely the author’s latest/only key. The architectural care: weft is BELOW the weave, and rotation cells are themselves weft events — resolve the layering cleanly (e.g. make the Keyring/keybook rotation-aware as rotation links are folded/enrolled, so keyring.verify(author, eid, sig, point=...) checks the key valid at that point; OR a kernel-level enrollment at genesis that seeds each principal’s chain root). Pick the cleanest seam.
HARD CONSTRAINTS:
  - BACKWARD COMPATIBLE: an author that NEVER rotates (every existing principal) verifies EXACTLY as before — all 200+ existing checks stay green, checks/430_rotation.py included. This is the bar.
  - FAIL CLOSED: an event signed by a retired key is refused (WeftError/verification failure); a forged rotation link not endorsed by the current (or pre-designated recovery) key is refused; recovery works ONLY through the pre-designated authority.
  - Registering/rotating confers NO authority; all points are logical ints.

CHECK checks/462_rotwire.py proves, offline + deterministically (fresh Kernel, logical int points):
  (a) HISTORY SURVIVES A ROTATION (load-bearing): an authority signs an event, ROTATES its signing key (a proper endorsed succession link), signs another event; on a full fold BOTH verify — the pre-rotation event under the OLD key, the post-rotation event under the NEW key — and the identity ref is byte-identical across the rotation.
  (b) A RETIRED KEY IS REFUSED: an event signed by the pre-rotation key AFTER the rotation point fails verification (fail closed); a forged succession link not endorsed by the current key is refused.
  (c) RECOVERY: a lost key recovers only through the pre-designated recovery authority; wrong/no authority fails closed.
  (d) NON-ROTATING IDENTITIES UNAFFECTED: a principal that never rotates verifies exactly as before (and the full existing suite stays green — state this).
  Mutation: revert the verification seam to the one-key-forever check (ignore valid_key_at / the point) → (a) or (b) goes RED (a post-rotation event fails to verify, or a retired-key event wrongly passes). State the load_bearing_line.
This lane EDITS weft.py (module_path = heartbeat/decima/weft.py, FULL updated) + optionally kernel.py (extra_file). Register your OWN hermetic effect if needed (e.g. 'rot_probe'), never 'echo'.`
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
    module_source: { type: 'string', description: 'FULL source of the edited module' },
    check_path: { type: 'string' },
    check_source: { type: 'string', description: 'FULL source of the new check' },
    extra_file_path: { type: 'string', description: 'ONLY if you edited a disclosed second file (kernel.py for rotwire / discovery.py for forgereal); else ""' },
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
  const editNote = `\nTHIS LANE EDITS ${modulePath(l)} (return FULL updated source as module_source with that module_path).${l.core ? ' THIS IS THE ONE CORE LANE — you may edit weft.py and (only if strictly required) kernel.py genesis, returning kernel.py via extra_file. A core change that breaks ANY existing check is NOT done.' : ''}${l.extra ? ` If a single disclosed wiring line in ${l.extra} is strictly required, return it via extra_file_path/extra_file_source.` : ''} Add a NEW check at ${checkPath(l)}. Edit nothing else.`
  return `You are building ONE lane of Decima Batch B — the correctness debts the coming Rust port must NOT fossilize (an audit found these three shapes are wrong-by-omission: the kernel verifies against a one-key-forever Keyring so key rotation is decorative; discovery hands out honest STUBS instead of the real forge pipeline; "concurrency" never overlaps any actual work). All three are correctness-heavy → you are Fable 5; reason carefully.

LANE: ${l.title}
${editNote}
STUDY FIRST (Read in ${REPO}/heartbeat): ${l.seams}
Also read checks/424_resume.py + the named existing check your lane must keep green.

SPEC:${l.spec}
${HOUSE}
Now: isolated copy, study the seams, implement, self-test until smoke.py is green (ALL existing checks pass — this is the hard bar for a correctness lane), then return full sources + board_entry + load_bearing_line + oracle_green. If not green, return best effort with oracle_green=false and explain precisely in notes what breaks.`
}

function reviewPrompt(l, impl) {
  const focus = l.name === 'rotwire' ? 'is event verification REALLY consulting the rotation chain now (a post-rotation event verifies under the NEW key, a retired-key event is REFUSED), and are ALL 200+ existing checks still green (non-rotating identities unaffected)? A core change that breaks any check, or that does not actually make rotation load-bearing, is a BLOCK.'
    : l.name === 'forgereal' ? 'does a discovery-forge now produce a REAL promoted (born-quarantined, evaluated, attested) capability rather than a stub, and is a failing candidate refused (fail closed)? Or is it still a stub with a check that pretends?'
    : 'do effects REALLY overlap (the Barrier(parties=K) trips — impossible under serial execution), while exactly-once + clean fold hold, and is the proof deterministic (not timing-flaky)? Run it several times.'
  return `You are an ADVERSARIAL reviewer for Decima Batch-B lane "${l.name}". Be skeptical; catch a check that proves nothing, a core change that breaks the suite, or a wiring that is cosmetic. Default to BLOCK. Focus: ${focus}

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
2. GREEN: cd "$WORK/heartbeat" && python3 smoke.py — MUST end "heartbeat: alive. ✓" exit 0 with ALL existing checks green (for rotwire especially — a core verification change that reddens any check is an automatic BLOCK). Minutes; be patient. (reproduced_green)
3. MUTATION TEST: revert the load-bearing line, re-run ONLY this lane's check, confirm it FAILS. Confirm the change is on the REAL path (rotwire: grep weft/keyring verify actually consults the point/chain; forgereal: forge actually calls author_candidate/evaluate/promote; parfx: the effect handler runs OUTSIDE the commit lock). If decorative → mutation_caught=false → BLOCK. For parfx also RE-RUN a few times to confirm the barrier proof is not flaky.
4. LAW AUDIT: a retired key wrongly verifying; a stub passed off as promoted; a fabricated fold; ambient authority; floats/wall-clock/thread-id in recorded content; edits beyond the assigned file(s); any regression in an existing check; board_entry overclaim.
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
log(`Batch B done: ${approved.length}/${lanes.length} lanes APPROVED with a load-bearing check`)
return { lanes, approved: approved.map((x) => x.name) }
