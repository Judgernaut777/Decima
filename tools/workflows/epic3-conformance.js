export const meta = {
  name: 'decima-epic3-conformance',
  description: 'Decima 0.3 Phase 3 (Epic 3) — build the conformance + adversarial test suite over the extracted decima.kernel. Five disjoint-file lanes: event-fixtures (golden event vectors + validation), fold-props (fold determinism/order-independence/idempotence/rebuild), capability-props (attenuation monotonicity + revocation invalidates descendants + proof binding), adversarial (hostile-input fail-closed), checkpoints (signed local checkpoint over snapshot). Each lane self-verifies GREEN under pytest, then is adversarially reviewed for a real, mutation-caught invariant.',
  phases: [
    { title: 'Implement', detail: 'one agent per lane writes its test/source file and iterates to green under pytest' },
    { title: 'Review', detail: 'adversarial reviewer confirms the invariant is real + a mutation would break it' },
  ],
}

const REPO = '/home/mini/decima-claude'
const TESTENV = '/tmp/claude-1000/-home-mini/56d98ee8-eef4-4e7a-845d-004995e016ad/scratchpad/testenv'
const PYT = `PYTHONPATH=${TESTENV}:${REPO} python3 -m pytest`

const HOUSE = `
DECIMA 0.3 — EPIC 3 HOUSE RULES (obey exactly):
- Repo: ${REPO}. The EXTRACTED kernel under test is the package \`decima.kernel\` (at ${REPO}/decima/kernel/). Do NOT touch heartbeat/ (the frozen reference) or any decima.kernel source EXCEPT the checkpoints lane which adds ONE new file it owns.
- You are writing TESTS against the extracted kernel's REAL API. STUDY the actual code before writing: Read the decima/kernel modules you exercise (weft.py, weave.py, capability.py, crypto.py, model.py, snapshot.py, inbox.py). Also study the reference checks as working API templates: heartbeat/checks/108_incremental_fold.py (fold+ingest), 122_cascade.py (revocation cascade), 200_leases.py (leases/authorize), 278_retraction_modes.py (RETRACT modes), 110_secrets.py. The API in decima.kernel is IDENTICAL to heartbeat/decima (same functions, only import paths differ: import from decima.kernel.X).
- Build a Weft in-process like the conformance test does: from decima.kernel.crypto import Keyring; kr=Keyring(seed=bytes(32)); author=kr.mint('name','human').id; from decima.kernel.weft import Weft, ASSERT, RETRACT, INVOKE, ATTEST; weft=Weft(tmpdb, kr); from decima.kernel.weave import Weave; Weave.fold(weft).state_root(). Use tempfile for the db. Deterministic: fixed seeds, NO wall-clock, NO unseeded random in recorded content.
- Pure Python stdlib + the installed pytest/hypothesis. For property lanes use Hypothesis (import hypothesis) with a small, deterministic strategy space; keep examples bounded (max_examples<=100) and derandomize where it matters.
- Tests MUST FAIL LOUD via assert and MUST actually exercise the kernel (no trivially-true tests). Every test you write MUST PASS.
- SELF-VERIFY before returning: run \`${PYT} <your_file> -q\` from ${REPO} and confirm it exits 0, all tests pass. Iterate until green. Report the exact pass count you observed.
- Report the ONE load-bearing assertion whose reversion (a mutation to the kernel or to the expected value) makes your test go RED — this proves the test asserts something real.
- DELIVERABLE = the file(s) you wrote, each as {path, source}, plus test_path, passed (int), invariant (the load-bearing property), mutation (what reversion makes it red). Do NOT commit. Do NOT edit another lane's file.
`

const LANES = [
  {
    name: 'event-fixtures', dir: 'tests/kernel', file: 'tests/kernel/test_event_fixtures.py',
    title: 'EVENT-FIXTURES (DEC-030) — golden event vectors + acceptance validation',
    spec: `Golden-vector coverage of the four verbs and the Weft acceptance gate (weft.ingest / weft.events verification). Cover, as explicit deterministic cases: all four verbs (ASSERT/RETRACT/INVOKE/ATTEST) round-trip through append→events() with matching ids; unicode (NFC), empty collections, deeply nested maps, large integers in bodies produce stable content ids; and the ingest() acceptance gate returns the RIGHT status for: a well-formed foreign event ('ingested'/'duplicate'), a tampered payload (id-mismatch → 'rejected:id-mismatch'), a missing parent ('orphan'), a non-canonical parents list ('rejected:parents-not-canonical'), an author mismatch, a bad verb, and a forged/bad signature. Study weft.ingest's docstring — it enumerates the exact statuses. Assert the exact status strings. Optionally emit protocol/fixtures/events.json but the TEST is the deliverable.`,
  },
  {
    name: 'fold-props', dir: 'tests/property', file: 'tests/property/test_fold_properties.py',
    title: 'FOLD-PROPS (DEC-033/035) — fold determinism, order-independence, idempotence, rebuild',
    spec: `Hypothesis property tests over the deterministic fold. Generate a bounded random script of ASSERTs (a few cells/types, small int/text content) and optional RETRACTs, applied to a Weft. PROVE: (1) DETERMINISM — folding the same Weft twice gives the identical state_root; (2) REBUILD == INCREMENTAL — a full Weave.fold from genesis equals the state you get folding up to head (and, if fold_incremental is available and tractable, that an incremental fold over a checkpoint matches a full fold — else skip that sub-case honestly); (3) DUPLICATE-DELIVERY IDEMPOTENCE — ingest()ing an already-present event returns 'duplicate' and does NOT change state_root; (4) RETRACTION STABILITY — a WITHDRAW tombstones its cell out of of_type() and re-folding is stable. Keep strategies small (max_examples<=50). If a property needs a valid signed foreign event for ingest, build it by appending on a SECOND weft with the SAME keyring and reading the row back.`,
  },
  {
    name: 'capability-props', dir: 'tests/property', file: 'tests/property/test_capability_properties.py', effort: 'xhigh',
    title: 'CAPABILITY-PROPS (DEC-032/033) — attenuation monotonicity, revocation invalidates descendants, proof binding',
    spec: `Property + example tests over the capability/authorization model in decima.kernel.capability. STUDY capability.py (capability_content, attenuate, attenuation_valid, _caveats_downhill, verify_delegation, authorize, build_proof, verify_proof, lease_status, morta_floor) AND heartbeat/checks/122_cascade.py + 200_leases.py for the real construction idiom (they build a Kernel; you must reconstruct the minimal pieces: a Weave over a Weft with a grant cell + agent cell). PROVE: (1) ATTENUATION MONOTONICITY / DESCENDANT <= PARENT — attenuate() only ever narrows (a child caveat set never widens effect/target/uses/expiry beyond its parent); attenuation_valid rejects a widening child; (2) PROOF BINDING — verify_proof succeeds for the exact (verb, body, nonce, parents) build_proof was made for, and FAILS when any of those args is changed (changed-args invalidates the proof); (3) REVOCATION INVALIDATES DESCENDANTS — after a RETRACT with DERIVED_AUTHORITY cascade on a parent grant, authorize() of an invocation under a descendant grant fails closed (study 122_cascade.py for the cascade construction). This is the hardest lane — be rigorous; if a full authorize() setup is too heavy for a property test, use a small number of hand-built EXAMPLE tests that construct the real objects, but they MUST drive the real capability functions (no mocks of the logic under test).`,
  },
  {
    name: 'adversarial', dir: 'tests/adversarial', file: 'tests/adversarial/test_hostile_input.py',
    title: 'ADVERSARIAL (DEC-034) — hostile input fails closed, kernel never crashes',
    spec: `Hostile-input tests proving the kernel FAILS CLOSED (a defined rejection/exception, never a silent accept or an interpreter crash). Drive weft.ingest() and weft.events() and Weave.fold() with: malformed JSON payloads; missing required fields; a cyclic/self parent reference; an impossible lamport (not 1+max(parents)); duplicate parent ids / non-sorted parents; a forged signature; a payload whose id does not recompute; unicode-normalization ambiguity (a decomposed vs composed string must canonicalize to the SAME id — NFC); and log truncation/tamper (edit a stored payload byte → events() raises WeftError). For each: assert the SPECIFIC rejection status string or exception type (WeftError), and assert NOTHING was inserted on a terminal rejection. No test may leave the process in a crashed/undefined state.`,
  },
  {
    name: 'checkpoints', dir: 'tests/kernel', file: 'decima/kernel/checkpoints.py', extraFile: 'tests/kernel/test_checkpoints.py',
    title: 'CHECKPOINTS (DEC-020) — a signed local checkpoint over the snapshot frontier',
    spec: `Add ONE new kernel module decima/kernel/checkpoints.py (you OWN it; pure stdlib + decima.kernel + nacl signing via the keyring — NO network/subprocess/provider imports, the import-boundary guard scans it). It builds a SIGNED local checkpoint containing: the Weft frontier (head id + event count), the fold state_root, the protocol version string, the signer principal id, and a signature over the canonical checkpoint bytes (sign via the keyring, hash via decima.kernel.hashing). Provide make_checkpoint(weft, weave, keyring, signer_pid, *, protocol_version) -> dict and verify_checkpoint(checkpoint, weave, keyring) -> (bool, reason). It is LOCAL integrity evidence (external anchoring deferred). Compose snapshot.py's _frontier if useful. THEN write tests/kernel/test_checkpoints.py proving: a fresh checkpoint verifies; a checkpoint whose state_root/frontier is altered FAILS verification; a checkpoint signed by the wrong key FAILS; the checkpoint records the exact event count + protocol version. Deterministic (fixed seed). Keep it pure — deterministic, ints, no wall-clock in the signed content (if you record a time it must be passed in / fixed, never read the clock).`,
  },
]

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['files', 'test_path', 'passed', 'invariant', 'mutation'],
  properties: {
    files: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['path', 'source'], properties: { path: { type: 'string' }, source: { type: 'string' } } } },
    test_path: { type: 'string' },
    passed: { type: 'integer' },
    invariant: { type: 'string' },
    mutation: { type: 'string' },
    notes: { type: 'string' },
  },
}

const REVIEW_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'reproduced_green', 'invariant_is_real', 'mutation_caught', 'reasons'],
  properties: {
    verdict: { type: 'string', enum: ['APPROVE', 'REJECT'] },
    reproduced_green: { type: 'boolean' },
    invariant_is_real: { type: 'boolean' },
    mutation_caught: { type: 'boolean' },
    reasons: { type: 'string' },
  },
}

const results = await pipeline(
  LANES,
  (lane) => agent(
    `${HOUSE}\n\n=== YOUR LANE: ${lane.name} — ${lane.title} ===\nOwned file(s): ${lane.file}${lane.extraFile ? ' + ' + lane.extraFile : ''}\n\n${lane.spec}\n\nWrite the file(s), self-verify GREEN with pytest, and return the structured result (files[] with FULL source of every file you wrote, test_path, passed count, invariant, mutation).`,
    { label: `impl:${lane.name}`, phase: 'Implement', schema: IMPL_SCHEMA, effort: lane.effort || 'high' }
  ).then((impl) => ({ lane, impl })),
  ({ lane, impl }) => {
    if (!impl) return { lane: lane.name, impl: null, review: null }
    return agent(
      `${HOUSE}\n\n=== ADVERSARIAL REVIEW of lane ${lane.name} ===\nThe implementer wrote ${impl.test_path} and claims ${impl.passed} tests pass, asserting invariant: "${impl.invariant}" (mutation that should break it: "${impl.mutation}").\n\nVERIFY INDEPENDENTLY: (1) Re-run \`${PYT} ${impl.test_path} -q\` from ${REPO} — does it actually pass? (2) Read the test file — does it genuinely exercise decima.kernel and assert the claimed invariant, or is it trivially/vacuously true? (3) Apply the claimed mutation mentally (or by a scratch edit you REVERT) — would the test actually go red? A test that passes even under the mutation is WORTHLESS. Return APPROVE only if reproduced_green AND invariant_is_real AND mutation_caught.`,
      { label: `review:${lane.name}`, phase: 'Review', schema: REVIEW_SCHEMA, effort: 'high' }
    ).then((review) => ({ lane: lane.name, impl, review }))
  }
)

const approved = results.filter((r) => r && r.impl && r.review && r.review.verdict === 'APPROVE' && r.review.reproduced_green)
log(`Epic 3: ${approved.length}/${LANES.length} lanes APPROVED green`)
return {
  approved: approved.map((r) => ({ lane: r.lane, test_path: r.impl.test_path, passed: r.impl.passed, invariant: r.impl.invariant })),
  rejected: results.filter((r) => !(r && r.review && r.review.verdict === 'APPROVE')).map((r) => r && ({ lane: r.lane, reasons: r.review && r.review.reasons, impl_notes: r.impl && r.impl.notes })),
}
