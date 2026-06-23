# Nona and the Reckoner

Nona authors candidate organs. The Reckoner determines what evidence exists for trusting them. Morta controls promotion boundaries and rollback.

## 1. Extension package

```text
ExtensionCandidate {
  manifest
  source_blobs
  build_recipe
  implementation_digest
  requested_capability_template
  input_schema
  output_schema
  declared_effect_class
  data_handling
  dependencies_and_licenses
  threat_model
  eval_plan
  rollback_or_compensation
  author
}
```

Candidates may define a skill, capability implementation, workflow, reducer, view, connector, model adapter, or agent role. Kernel reducers and constitutional policy have a stricter release track than ordinary extensions.

## 2. Lifecycle state machine

```text
DRAFT
  → QUARANTINED
  → BUILT
  → EVALUATED
  → CANARY
  → PROMOTED
  → DEPRECATED
  → RETRACTED

Any active state → SUSPENDED
```

Transitions are assertions plus required attestations. State cannot be changed by editing a row.

## 3. Quarantine baseline

Every candidate begins with:

- `sandbox_only`
- `no_outward_effects`
- `network_allow([])`
- Read-only access to synthetic fixtures
- No user secrets
- No durable memory writes outside its evaluation namespace
- Fixed CPU, memory, disk, token, GPU, time, and cost limits
- Dependency lock and content-addressed build
- Full invocation/receipt tracing
- Nondeterminism declaration and seeded tests where possible

Additional permissions are introduced one at a time during canary stages.

## 4. Reckoner evaluation contract

An evaluation suite is itself a versioned Cell:

```text
EvaluationSuite {
  subject_schema
  environment_digest
  datasets
  cases
  verifiers
  adversaries
  metrics
  thresholds
  repetitions
  baseline_subjects
  contamination_policy
}
EvaluationResult {
  candidate
  suite
  environment
  case_receipts
  aggregate_metrics
  failures
  security_findings
  cost_latency_profile
  reproducibility
}
```

Mandatory evaluation dimensions:

- Functional correctness
- Schema and contract compliance
- Permission-use conformance
- Prompt-injection and hostile-input behavior
- Secret and data-exfiltration tests
- Resource exhaustion and cancellation
- Retry/idempotency behavior
- Failure transparency: no fabricated success
- Cost and latency
- Regression against current promoted implementation
- License and dependency policy
- Accessibility/usability where the candidate creates a view

## 5. Verifier hierarchy

Prefer evidence in this order:

1. Formal/type/schema checks
2. Deterministic tests and exact expected outputs
3. Sandboxed execution with invariant checks
4. Differential tests against trusted implementations
5. Property-based and fuzz testing
6. Static/security analysis
7. Human-authored rubric with blinded samples
8. Independent model judges

Model judgments never override deterministic failures. Judge prompts, models, seeds/settings, and outputs are recorded.

### SkillSpector adapter

NVIDIA SkillSpector is a suitable Apache-2.0 scanner inside the static-analysis stage. Run it as a quarantined, network-denied worker by default:

- Pin the scanner version, analyzer registry, YARA corpus, and report schema by content digest.
- Invoke static mode first and ingest JSON/SARIF findings as immutable evidence Cells.
- Record every skipped file, size limit, unavailable analyzer, dependency-lookup failure, and analysis-completeness field.
- Treat OSV network lookup as an explicit capability; offline fallback results must be labeled as incomplete.
- Enable LLM semantic analysis only under a data-export capability that names the provider, model, files, retention policy, and user approval where required.
- Compare the current candidate with its previously promoted manifest and implementation so rug-pull checks receive real historical input.
- Map findings to requested Decima capabilities. A suspicious shell pattern matters more when the candidate requests `shell`, and an undeclared network operation blocks promotion until the manifest and implementation agree.

SkillSpector produces evidence, never promotion authority. A low score cannot prove safety, and a high score cannot by itself prove malicious intent. Its result is combined with deterministic tests, sandbox execution, capability-use tracing, dependency/license verification, adversarial inputs, and Morta policy.

## 6. Cheap local reasoner role

A VibeThinker-class model may:

- Generate candidate implementations or tests.
- Decompose verifiable problems.
- Propose counterexamples and edge cases.
- Triage failures.
- Check bounded math/code claims against tools.

It may not:

- Grant or promote capabilities.
- Judge its own candidate as sufficient.
- Replace deterministic verification.
- Hold outward-effect capabilities during evaluation.
- Be treated as reliable for broad factual coverage without retrieval.

Useful pattern:

```text
frontier model: define intent and constraints
local reasoner: generate N candidates/tests
deterministic tools: execute and reject
independent critic: search for missing failure modes
trusted attestor/human: authorize promotion tier
```

## 7. Promotion attestation

```text
PromotionAttestation {
  candidate_digest
  from_state
  to_state
  evaluation_results[]
  granted_capability_template
  residual_caveats
  allowed_realms / users / traffic_fraction
  risk_acceptances
  monitoring_policy
  expiry_or_review_date
  rollback_target
}
```

Required signers depend on effect class:

- Pure/read-only local capability: automated trusted Reckoner policy may promote.
- Reversible workspace write: automated promotion plus canary and rollback.
- Network communication or production mutation: human attestation remains required.
- Financial, identity, constitutional, secret export, or destructive capability: Morta’s permanent gate and strong user authentication.

Promotion does not mutate candidate code. It grants a new, still-attenuated capability edge to the immutable implementation digest.

## 8. Canary

Canary controls include:

- Shadow mode: observe and propose without acting.
- Synthetic realm.
- Read-only real data.
- User-confirmed execution.
- Limited targets.
- Traffic percentage or task-type subset.
- Tight budget and expiry.
- Automatic comparison to incumbent.

Canary health is folded from receipts and attestations. Threshold breach asserts a suspension proposal; high-severity security findings trigger automatic lease revocation under pre-authorized Morta policy.

## 9. Rollback

Rollback consists of:

1. Retract promotion/grant edges.
2. Stop lease renewal and cancel active runs.
3. Route new invocations to the last healthy implementation.
4. Run declared compensation for reversible effects.
5. Assert an incident Cell linking affected invocations and artifacts.
6. Preserve evidence; redact sensitive payloads under policy.

Rollback never claims to undo an irreversible external action. It contains and compensates.

## 10. Learning without drift

Nona may propose changes from traces, but traces do not automatically become training truth.

- Separate user preference learning from factual memory.
- Separate routing updates from capability-code changes.
- Maintain holdout evaluations and contamination checks.
- Require statistically meaningful improvement, not one successful anecdote.
- Track model-routing regret, cost, latency, intervention rate, and safety violations.
- Every promoted revision has a predecessor, evaluation delta, and rollback target.
- Periodically re-evaluate promoted capabilities against updated threats and providers.

## 11. Bootstrap test

The First Heartbeat is complete when:

1. An agent asserts a candidate pure capability, such as deterministic text normalization.
2. The candidate is built content-addressably in quarantine.
3. The Reckoner executes tests, property checks, and a hostile-input case.
4. Independent attestations satisfy promotion policy.
5. A grant exposes the promoted capability to another agent.
6. That agent invokes it successfully.
7. The system retracts promotion, proves the invocation is then denied, and can replay the entire history to the same state root.

That test exercises Nona, Decima, Morta, Weft, Weave, authorization, receipts, and replay without requiring unsafe real-world effects.
