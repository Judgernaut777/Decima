# Design: Nona as the Running Product — the Self-Extension Engine

**Status:** **implemented — waves N0–N7 all landed** (§7 table, outcome in §7.1). The §9
decisions were locked in N0; of them, **Decision 3** landed as derived quarantine
(`weave.py::_cascade_retractions` step 3) and **Decision 7** landed as yes (N7), while
**Decision 4** (a promotion vector in the fold fixture) was never taken and is still open.
Residual risk after N7 is enumerated in `SECURITY.md`, which supersedes §6's list.
**Date:** 2026-07-25 (plan) / 2026-07-26 (outcome recorded)
**Scope:** turning the self-extension loop (`VISION.md` "Phase 3 — Self-extension",
`VISION.md:403`) into the shipping product's spine, on top of the primitives already in
`decima/`.
**Content-addressed bytes:** **`needs_fixture_regen = false`** for the plan as written —
see §7 for the two decisions (Decision 4, and wave N7) that *would* flip it, and how each is
bounded. **Outcome: it stayed false.** N7 landed as a pure rejection rule and Decision 4 was
not taken, so no golden vector changed and `protocol/fixtures/` is byte-identical to N0 (§7.1).

---

## 1. One-paragraph summary

The promotion boundary Nona needs is **already in the shipping trusted core, and nothing
calls it.** `decima/kernel/capability.py:196` denies a quarantined capability to a
non-sandbox agent; `decima/kernel/weave.py:498-513` lifts that quarantine on a
promote-`ATTEST`, but only when `_is_trusted_promoter` (`weave.py:527-552`) says the
attesting principal is root-declared for the candidate's declared tier;
`weave.py:555-589` folds canary health from receipts and findings. Meanwhile
`decima/workers/execution.py:914` is a real, digest-bound, namespace-jailed sandbox that
`decima/capabilities/workspace.py:414` already uses to run *caller-supplied source*
outside the kernel process. What is missing is not the boundary and not the sandbox — it
is **everything between them**: nothing in `decima/` ever mints a `sandbox: True` agent
(`decima/runtime/cells.py:144-180` has no such field), nothing ever asserts a `promoter`
anchor cell, no module authors an `ExtensionCandidate`, no module runs an evaluation
suite, and the API's command table (`decima/services/api/commands.py:42`) exposes no
authoring, evaluation, promotion, or rollback command. This document sequences the work
that closes the loop — candidate → quarantine → Reckoner → attested promotion → invoke →
rollback — as eight gate-green waves (N0–N7), names the four places the shipping kernel's
promotion boundary is weaker than the spec claims, and is explicit that test-gated
promotion buys defense in depth, never safety.

## 2. Why this is a decision, not a patch

Three of the constraints this repo runs under collide directly with self-extension, and
the collision is the whole design problem:

1. **"Untrusted code never executes in the kernel process"** (`docs/DECIMA-0.3-HANDOFF.md`
   invariant 6/7). Nona's product *is* untrusted code. The only lawful executor is
   `decima/workers/execution.py:914`, and the only lawful shape is digest-bound
   (`compute_digest`, `execution.py:107`).
2. **"No arbitrary Python from the wire."** `tests/api/test_no_arbitrary_python.py`
   asserts the command table is closed and that code-shaped payloads are stored as inert
   data; `decima/services/api/workspace_service.py:170-173` deliberately restricts
   executed check source to a **declared `CHECKS` catalogue** and refuses caller-supplied
   source. A self-extension lane accepts *generated* source by construction. That is not a
   violation of the invariant — but it is a *deliberate, named exception* to the way the
   0.3 lanes were built, and it must be designed as one rather than smuggled in.
3. **"Automatic Nona promotion into production" is explicitly deferred in 0.3**
   (`docs/DECIMA-0.3-HANDOFF.md:58`). Un-deferring it is an owner decision about the
   product's risk posture, not a refactor.

So this is a scope decision with a security argument attached, exactly like the durable
protocol re-freeze was a scope decision with a collision-resistance argument attached.

## 3. Motivation

- **It is the thesis.** `KERNEL.md:169` — "the set of things Decima can do is *itself a
  fold over the Weft*." `KERNEL.md:250` — "the first capability Decima ships with is the
  capability to author capabilities." Today the shipping app is three fixed workflows
  (`decima/capabilities/__init__.py:9-24`: documents, qa, workspace) wired to a fixed
  route table (`decima/services/api/routes.py:44-101`). Every new capability is a human
  writing Python and shipping a release. The product does not grow; it is grown.
- **The expensive half is already built and paid for.** The isolation story is real, not
  a seam: mandatory user+mount+PID+network namespaces with a chroot jail, read-back
  rlimits, a scrubbed environment, and a best-effort aarch64 seccomp filter
  (`decima/workers/execution.py:1-46`), with a containment matrix pinned as data
  (`containment_report`, `execution.py:138`) and adversarially tested
  (`tests/adversarial/test_worker_isolation.py`, `test_containment_matrix.py`,
  `test_workspace_containment.py`). The promotion fold is built. The approval inbox is
  built (`decima/kernel/inbox.py`). What is missing is glue, a service, and a surface.
- **Dead safety code is a liability.** `weave.canary_health` has **zero callers and zero
  tests** in `decima/` and `tests/` (its only exercisers are `heartbeat/checks/408_*` and
  `heartbeat/checks/428_*`). The tiered-promoter fold is likewise uncalled: nothing in
  `decima/` asserts a `promoter` cell. Un-exercised security code drifts. Landing the loop
  is how the boundary gets *tested* rather than merely present.
- **The prototype proved the shape.** `heartbeat/decima/candidate.py`,
  `reckoner.py`, `promotion.py`, `forge.py`, `discovery.py`, `powerbox.py` are a working
  end-to-end loop under the frozen oracle (`heartbeat/checks/406_reckoner_real.py`,
  `408_promotion_canary.py`). This design is a port with four deliberate divergences
  (§5.9), not a new invention.

## 4. What exists today vs. what is missing

### 4.1 Exists in the shipping product (`decima/`)

| Piece | Where | State |
|---|---|---|
| Quarantine denial on INVOKE | `decima/kernel/capability.py:196-197` (`DenialCode.QUARANTINED`) | **live + tested** (`tests/kernel/test_denial_codes.py:76`) |
| `sandbox_only` caveat denial | `capability.py:226-231` (`DenialCode.SANDBOX_ONLY`) | live; no shipping caller ever sets it |
| Sandbox-principal read | `capability.py:195` — `agent_cell.content.get("sandbox", False)` | live; **nothing in `decima/` ever sets it True** |
| `impl` + `quarantined` on a capability Cell | `capability.py:27-50` | live shape, unused fields |
| Promote-`ATTEST` quarantine lift | `weave.py:498-513` | live fold, **no caller, no test** |
| Tier → trusted-promoter check | `weave.py:516-552` (`_candidate_tier`, `_is_trusted_promoter`) | live fold, **no `promoter` cell is ever asserted** |
| Unforgeable root anchor (seq-based genesis) | `weave.py:345-349`, `weave.py:412-416` | live, with the grinding attack documented in-comment |
| Canary health fold | `weave.py:555-589` | live, **no caller, no test** |
| Morta permanent floors | `capability.py:275-294` (`MORTA_FLOORS`, `with_morta_floor`) | live; `shell` and `financial` floored |
| Structural narrowing proof | `capability.py:297-310` (`attenuation_valid`) | live |
| Lease caveats (`expires_at`, `max_uses`) | `capability.py:99-122`, folded via `weave._invoke_counts` | live |
| Request-bound authorization proof | `capability.py:350-353`, `427-501` | live |
| Approvals as Weft events (cap- and invocation-scoped) | `capability.py:365-405` | live |
| Durable approval inbox | `decima/kernel/inbox.py` (`ITEM`, `DECISION`) | live, in the Shell |
| Revoke / supersede / terminate + cascade | `decima/kernel/lifecycle.py:26-49`; cascade at `weave.py:435-454` | live |
| Digest-bound isolated execution | `decima/workers/execution.py:914-1017`; digest at `:107` | live, adversarially tested |
| Worker profiles; network-permitted refused | `decima/workers/profiles.py:40-75`; refusal at `execution.py:961` | live (`PROVIDER` refused until egress mediation lands) |
| Worker IPC that refuses key material | `decima/workers/protocol.py:37-48` | live |
| Running untrusted source in a worker, end to end | `decima/capabilities/workspace.py:57,110,262,414,455` | live, with durable anti-replay |
| Model providers that only propose | `decima/models/providers.py:173,193`; schema validation `decima/models/validation.py:84-190` | live |
| Deterministic integer retrieval/ranking | `decima/projections/search.py`, `decima/capabilities/qa.py:108` | live |

### 4.2 Missing (the whole loop's connective tissue)

| Missing | Consequence today |
|---|---|
| A **sandbox principal / sandbox Agent Cell** | The quarantine caveat is a *pure denial*: a quarantined capability is unrunnable by **anyone**, so a candidate can never be evaluated in-band. `capability.py:195` reads a field `decima/runtime/cells.py:144-180` never writes. |
| **`promoter` anchor installation** | `_is_trusted_promoter` finds no anchors ⇒ any tiered candidate can never be promoted; only a tier-less (`declared_effect_class` absent) cap gets the legacy any-attest lift (`weave.py:543-544`). |
| An **`ExtensionCandidate` module** (`specs/NONA_RECKONER.md:8-23`) | No Cell type carries manifest + source blob + `implementation_digest` + schemas + declared effect class + eval plan + rollback plan. |
| An **`EvaluationSuite` / `EvaluationResult`** pair (`NONA_RECKONER.md:66-91`) | Evaluation evidence has nowhere to live on the Weft; there is nothing to attest *about*. |
| A **Reckoner** (verifier hierarchy, `NONA_RECKONER.md:110-121`) | No deterministic gate; no adversarial lane for candidates; no differential regression check. |
| A **promotion service** (`NONA_RECKONER.md:167-190`) | Nothing builds the capability, chooses the tier's signer, or writes the promote-`ATTEST`. |
| An **effect-dispatch path for a promoted organ** | A promoted `capability.impl` naming generated source has no runner: `decima/runtime/supervisor.py:23` takes an *injected* runner, and the only wired runner is the workspace check runner. |
| **Discovery + a powerbox** | `capability.py:271` names `powerbox.py` as the broker that merges Morta floors — **that file does not exist in `decima/`** (only `heartbeat/decima/powerbox.py`). |
| **Surface**: authoring/eval/promote/rollback commands, routes, Shell screen | `routes.py:44-101` and `commands.py:42` expose none of it. |
| **Codegen seam** bound to the shipping model layer | `decima/models/providers.py` has no codegen request shape; `heartbeat/decima/candidate.py:89` is the reference. |

### 4.3 Where the reference cannot simply be copied

`heartbeat/decima/{forge,promotion,candidate,reckoner,discovery}.py` are written against a
**`Kernel` god-object** — `k.integrate_tool`, `k.grant`, `k.revoke`, `k.supersede`,
`k.invoke`, `k.approve_invocation`, `k.reckoner`, `k.human`, `k.root`,
`k.decima_agent_id`, `k.weave()`. **None of those exist in `decima/`**: a grep for
`integrate_tool` / `def grant` / `def invoke` across `decima/` finds only
`decima/kernel/inbox.py:57` (a *Protocol* describing a kernel-like object the inbox types
against) and `decima/runtime/cancellation.py:128` / `decima/kernel/lifecycle.py:26`
(free functions taking an explicit `weft` + author). The shipping package is deliberately
function-shaped over an explicit `Weft`. So the port is a **rewrite against the extracted
seams**, and the missing `Kernel` methods must be re-expressed as:

- `grant` → assert a capability Cell + rewrite the holder Agent Cell's `envelope`
  (the list `capability.envelope_holds`, `capability.py:53-56`, reads);
- `revoke` → `lifecycle.revoke` (`lifecycle.py:26`) — the fold defaults a capability
  RETRACT to a `DERIVED_AUTHORITY` cascade (`weave.py:450-451`);
- `integrate_tool` → a **declared effect-handler registry in trusted code** plus a
  capability Cell whose `impl` names the handler by name and the source by digest;
- `invoke` → the existing authorized invoke seam. Note
  `tests/architecture/test_no_raw_invoke_append.py` **fails the build** if any module
  outside `decima/kernel/` appends a raw `INVOKE`. Any Nona invoke path must therefore go
  through the kernel seam, not a service-level `weft.append(..., INVOKE, ...)`.

## 5. Architecture

### 5.1 The loop, as events on one log

The bootstrap test in `specs/NONA_RECKONER.md:234-244` is the acceptance criterion.
Expressed over the shipping primitives, one full cycle is:

```
e1  ASSERT   author=nona     Cell(candidate)        # source as DATA; born QUARANTINED
e2  ASSERT   author=nona     Cell(evaluation_suite) # versioned, content-addressed
e3  ASSERT   author=nona     Cell(capability)       # quarantined=True, sandbox_only,
                                                    # impl={digest, entrypoint, limits}
e4  ASSERT   author=nona     EDGE cap --impl_of--> candidate
e5  INVOKE   author=sandbox  cap(...)               # authorized ONLY because the agent
                                                    # cell carries sandbox:True (cap.py:195/226)
e6  ASSERT   author=nona     Cell(evaluation_result)# metrics/receipts/findings — INTS ONLY
e7  ATTEST   author=<tier signer> target=cap        # promote:True → weave.py:498 lift
e8  ASSERT   author=nona     Cell(promotion)        # the retractable promotion record (§5.7)
e9  ASSERT   author=nona     Cell(agent)            # holder envelope now includes cap
e10 INVOKE   author=holder   cap(...)               # the ordinary ocap spine gates it
e11 RETRACT  author=morta    Cell(promotion)        # rollback → re-quarantine (§5.7)
e12 RETRACT  author=morta    Cell(capability)       # revoke → DERIVED_AUTHORITY cascade
```

Everything above is `ASSERT` / `INVOKE` / `ATTEST` / `RETRACT`. **No new verb, no new
store, no kernel-internal state.** Capability is a fold over the Weft, and it only grows
behind the gate.

### 5.2 New Cell types

All are ordinary content Cells; all content is int-and-string only (no floats, no
wall-clock) so they are replayable and hashable:

| Type | Content (abridged) | Notes |
|---|---|---|
| `candidate` | `intent`, `author`, `manifest`, `source_blobs`, `implementation_digest`, `input_schema`, `output_schema`, `declared_effect_class`, `eval_plan`, `quarantine`, `source_is_data: True`, `lifecycle`, `states[]` | Two events per candidate (`DRAFT` then `QUARANTINED`) on the **same** content-addressed cell, so the transition is provenance, not an edited row (`NONA_RECKONER.md:43`; reference: `heartbeat/decima/candidate.py:272-276`). |
| `evaluation_suite` | `subject_schema`, `environment_digest`, `cases[]`, `verifiers[]`, `adversaries[]`, `metrics[]`, `thresholds{}` (ints), `repetitions`, `contamination_policy`, `version` | Versioned Cell (`NONA_RECKONER.md:66-79`). |
| `evaluation_result` | `candidate`, `suite`, `environment`, `implementation_digest`, `case_receipts[]`, `aggregate_metrics{}` (ints), `failures[]`, `security_findings[]`, `reproducibility{seed,…}`, `promote_eligible`, `verdict_reason`, `model_judge` | The evidence. `model_judge` records verdict + `authority: False`. |
| `promoter` | `principal`, `role`, `tiers[]` | **Root-asserted only** — `weave.py:548` filters any anchor whose asserting author ≠ `_genesis_author`. |
| `promotion` | `capability`, `candidate`, `evaluation_result`, `tier`, `signer`, `from_state`, `to_state`, `residual_caveats`, `expires_at`, `rollback_target` | The retractable promotion record — see §5.7 and Decision 3. |
| `finding` | `severity`, `rule`, `detail` + EDGE `found_in → cap` | The shape `weave.canary_health` already looks for (`weave.py:574-579`). |
| `incident` / `suspension` | `capability`, `reason`, `health`, `from_state`, `to_state` | `NONA_RECKONER.md:198-226`. |

### 5.3 Quarantine's missing half: the sandbox principal

`capability.py:195-197,226-231` is *half* a mechanism. It denies a quarantined capability
to any agent whose Cell lacks `sandbox: True` — and no shipping code path writes that
field. So today quarantine means "unrunnable, full stop," which makes evaluation
impossible in-band.

The design adds a **sandbox Agent Cell** minted by the Reckoner service, and it must be a
genuinely weaker principal, not a flag:

- `sandbox: True` in its content (the field `capability.py:195` reads);
- its own principal (a `Principal(kind="reckoner")` — `decima/kernel/crypto.py:39`
  already enumerates that kind), never the operator's and never root's;
- an `envelope` containing **only** candidate capabilities under evaluation, each
  `sandbox_only` + `no_outward_effects` + `network_allow: []`
  (`NONA_RECKONER.md:47-59`; reference constant `heartbeat/decima/candidate.py:38-42`);
- a `visible_horizon` naming **only** the evaluation namespace — the field already exists
  on the Agent Cell (`decima/runtime/cells.py:173`) and `decima/capabilities/qa.py:92-107`
  already enforces horizon scoping in retrieval, so synthetic-fixtures-only is
  expressible with existing machinery;
- a hard `token_budget` / `monetary_budget` / `deadline` (ints — `cells.py:175-177`);
- **no key custody**: `decima/workers/protocol.py:37-48` structurally refuses to serialize
  private-key-shaped fields, so the worker half already cannot receive signing authority.

The important property: **the sandbox agent is a principal that holds only quarantined
grants.** It is not a privilege level. If it is compromised, its blast radius is exactly
its envelope (`capability.py:11-13`), and every capability in that envelope is
`sandbox_only`, so even an escaped grant is refused outside a sandbox agent.

### 5.4 The Reckoner: a worker-isolated, fold-recorded gate

The Reckoner is a **service** (`decima/services/nona/reckoner.py`), not kernel code:
it executes untrusted source, so it must live outside the TCB (the import-boundary guard,
`tests/architecture/test_import_boundaries.py:56`, permits only
`nacl / sqlite3 / blake3 / cbor2` inside `decima/kernel/`). It needs **no new third-party
dependency** — every stage is stdlib plus existing `decima.workers`.

Stages, in the spec's evidence order (`NONA_RECKONER.md:110-121`):

| # | Stage | Mechanism | Existing primitive |
|---|---|---|---|
| 1 | Schema / contract | validate the candidate's declared `input_schema`/`output_schema` and each case against it | `decima/models/validation.py:84-138` |
| 2 | Deterministic cases | exact-output over seeded cases | `run_worker` (PURE profile) |
| 3 | Sandboxed hostile input | ≥1 adversarial case; containment invariants asserted from the **in-child manifest** | `run_worker` diagnostics `isolation` (`execution.py:1007-1012`) |
| 4 | Differential regression | same inputs against the promoted incumbent's digest | two `run_worker` calls |
| 5 | Property / fuzz | `random.Random(seed)` from the suite — **seed is Cell data**, never `os.urandom`/time | stdlib |
| 6 | Static / security scan | AST + token scan; **rug-pull check**: an import implying network/process use the manifest did not declare is a high finding | reference: `heartbeat/decima/reckoner.py:137-176` |
| 7 | Human rubric | out of scope for N0–N7 (Decision 6) | — |
| 8 | Model judge | recorded with `authority: False`; **never** enters the gate | `decima/models/providers.py` |

**The gate is a pure function of the recorded metrics**, computed in one place:

```
promote_eligible ==
      deterministic_pass  == deterministic_cases
  and hostile_cases >= 1 and hostile_contained == hostile_cases
  and property_pass       == property_cases
  and (no incumbent or differential_agree == differential_cases)
  and high_findings       == 0
```

Every count is an `int` (`heartbeat/decima/reckoner.py:353-365` is explicit about this and
the shipping port must keep it: no float ever enters signed content). Three honesty rules
carry over verbatim:

- a candidate that **errors** in the sandbox **fails** — it never passes;
- a worker killed by the wall-clock/CPU backstop yields `UNKNOWN`
  (`execution.py:994-1002`), and `UNKNOWN` is **not** a pass;
- a model judgment can never override a deterministic failure
  (`NONA_RECKONER.md:121`).

**Determinism.** The whole evaluation must be replayable: same candidate + same suite ⇒
same `evaluation_result` content ⇒ same `state_root`. That forbids wall-clock, unseeded
randomness, and host-variable data in the recorded result. Two real hazards, both
existing:

- The isolation manifest is host-dependent — the seccomp layer is aarch64-only
  (`execution.py:127-136`) and namespaces can be unavailable. **Record only the
  determinism-safe assertions in the Cell** (e.g. `no_new_privs: true`,
  `network: denied`, `chroot: engaged`) and keep the raw manifest in a diagnostics blob
  that is not part of the gate. Alternatively pin `containment_report`'s
  `CONTAINMENT_MATRIX_VERSION` (`execution.py:123`) into the suite's
  `environment_digest` so a host that cannot deliver the matrix **refuses to evaluate**
  rather than recording a different result. (Decision 5.)
- Cost/latency (`NONA_RECKONER.md:104`) is wall-clock by nature. Keep it in
  diagnostics, out of hashed content, and out of the gate.

### 5.5 Promotion: the tier ladder and the anchors

`weave.py:498-513` already implements exactly the right shape — and the shape has a
subtlety worth stating plainly: **the lift strips `sandbox_only` and clears `quarantined`,
and strips nothing else** (`weave.py:501-505`). Morta's `requires_approval` therefore
**survives promotion by construction**. That is the unstrippable gate `KERNEL.md:200`
promises, implemented as "the lift only ever removes one named caveat."

The tier ladder (`NONA_RECKONER.md:183-188`, reference `heartbeat/decima/promotion.py:47-60`):

| Tier | Required signer | Auto? |
|---|---|---|
| `pure`, `read_only` | Reckoner (automated) | yes |
| `workspace_write` | Reckoner + **canary** + rollback target | yes, canaried |
| `network` | **human** attestation | no |
| `financial`, identity, constitutional, secret-export, destructive | **Morta** (root) + strong reauth | no; `requires_approval` floor pre-applied |

`install_trust_anchors` becomes a first-run step in `decima/services/provision.py`
(root-asserted, idempotent, content-addressed by principal+role). This is load-bearing:
`weave.py:548` honors **only** anchors whose asserting author is `_genesis_author`, and
`_genesis_author` is anchored on the parentless event with the smallest local `seq`
(`weave.py:345-349`) precisely because event ids are grindable and an id-order anchor was
a demonstrated promotion-boundary leak. Provisioning must therefore assert the anchors
**as root, on the root's own log**, and a store whose genesis author is not the local root
must refuse to promote (it already will: `_is_trusted_promoter` filters everything).

`with_morta_floor` (`capability.py:288-294`) is applied when the candidate capability is
*built*, so a `financial` candidate is born with `requires_approval: True` before any
attestation exists. A floor cannot be talked out of by the requested scope.

### 5.6 Invoking a promoted organ

A promoted capability's `impl` is `{"source_blobs": <digest-bound source>, "entrypoint":
<name>, "limits": {...}}`. Execution must reuse the one lawful door:

1. the kernel's authorized invoke seam runs `verify_proof` (`capability.py:450-501`) and
   clears the approval gate;
2. a **declared effect handler** in trusted code — `effect == "generated_code"` — mints a
   `WorkerRequest` (`decima/workers/protocol.py:83`) whose `implementation_digest` is the
   candidate's digest and whose `capability_proof` is the proof already verified;
3. `run_worker` re-computes the digest and refuses a mismatch
   (`execution.py:973-982`, `DigestMismatch`), so an undigested implementation cannot run
   even if the Cell was tampered with;
4. the response becomes an `EffectReceipt`-shaped `result` Cell — which is exactly what
   `weave.canary_health` folds (`weave.py:567-573`).

Note the profile constraint: `PROVIDER` (network-permitted) is **refused at the primitive**
until egress mediation lands (`profiles.py:64-75`; refusal at `execution.py:961`). Therefore
the `network` tier **cannot be promoted to a runnable organ in this plan at all** — not
"is gated," but "has no executor." That is the honest state and it should be said in the
UI, not papered over. (Decision 2.)

### 5.7 Storage, versioning, and rollback — and the four places the boundary is weaker than it looks

**Storage.** A forged organ is stored exactly like any Cell: `candidate.source_blobs` is
the source text as DATA; `implementation_digest = blob/content id of those bytes`; the
capability Cell holds an EDGE `impl_of → candidate` rather than a copy. Promotion never
mutates code (`NONA_RECKONER.md:190`); it grants an edge to an immutable digest. Because
ids are content-addressed, re-authoring identical source for the same name+author is
idempotent (same cell).

**Versioning.** A new revision is a new candidate cell (new digest ⇒ new id) promoted at a
higher manifest version, and the incumbent is `SUPERSEDE`d — `lifecycle.supersede`
(`lifecycle.py:38-43`) tombstones the old cap and records `superseded_by`
(`weave.py:434`) **without** cascading, which is the correct semantics: superseding a
version must not fail closed the grants derived from the replacement. Differential
regression gating is upstream (stage 4), so only a non-regressing revision reaches
supersession.

**Rollback.** `VISION.md:184` says "Rollback is RETRACT-ing it [the attestation]." **That
is not implementable as written today**, and this is the most important honest finding in
this document:

1. **An attestation is not a Cell.** `RETRACT` takes `body["cell"]` (`weave.py:418-421`);
   an `ATTEST` is folded into `target.attestations` (`weave.py:464-469`) and has no cell
   id of its own. There is nothing to retract.
2. **The lift is a content mutation, not a derived flag.** `weave.py:506-511` writes
   `quarantined: False` into `target.content` *and* `content_heads`, while an ordinary
   `ASSERT` re-materializes content through `_apply_register` (`weave.py:402-403`). Fold
   order is `(lamport, event_id)` (`weave.py:174`). So an `ASSERT` of the capability cell
   that folds **after** the promote-`ATTEST` silently **re-quarantines** the capability,
   and the reference works around this by re-asserting the *lifted* content
   (`heartbeat/decima/promotion.py:175-177`) — which turns `quarantined: False` into an
   ordinary register value.
3. **`ASSERT` is not authorized at the Weft door.** `Weft.append` (`decima/kernel/weft.py:272-332`)
   validates the verb, computes lamport/id, and signature-checks the author's key — and
   nothing else. `ingest` says so outright: *"Authority is NOT re-judged here"*
   (`weft.py:447-449`). Combined with (2), any principal whose key the keyring holds can
   `ASSERT` a capability Cell with `quarantined: False`, `grantee: <self>`, `parent: None`
   **and** an Agent Cell whose `envelope` contains it — and `authorize` will pass, because
   every check it makes (`capability.py:176-238`) is a read of the graph that the attacker
   just wrote. The tiered-promoter fold guards *the ATTEST path*; it does not guard *the
   ASSERT path*, and the ASSERT path is the wider one.
4. **Retracting the capability is the only working rollback.** `lifecycle.revoke`
   (`lifecycle.py:26`) → default `DERIVED_AUTHORITY` cascade (`weave.py:450-451`) → the
   next INVOKE is denied `REVOKED` (`capability.py:194`). That works. It is also
   *terminal*: there is no "demote back to quarantine and re-evaluate."

The design therefore introduces a **`promotion` Cell** (§5.2) and recommends making
quarantine a **derived** flag: a capability is unquarantined iff a **live** `promotion`
Cell exists for it, signed by a trusted promoter for its tier, referencing a
promote-eligible `evaluation_result`. Then:

- **rollback is literally `RETRACT` of the promotion Cell** — `VISION.md:184` becomes true;
- the flag is **order-independent and idempotent** (a pure function of the folded set), so
  merge/sync cannot silently re-quarantine or silently promote;
- demotion (`PROMOTED → QUARANTINED`) becomes expressible without revoking the organ;
- point (3) is *narrowed but not closed* — see §6.

This is a **fold-semantics change** in `decima/kernel/weave.py`. Decision 3 is whether to
take it now (recommended) or ship the reference's content-mutating lift and accept the
merge hazard.

### 5.8 Discovery and the powerbox: not drowning the user in grant prompts

`KERNEL.md:232` names this exactly: *"ocap is secure but can be a UX nightmare… You need
a powerbox / capability-broker… The difference between 'secure' and 'usable.'"*
`capability.py:271` already refers to `powerbox.py` as the module that merges Morta floors
before issuing — and that module **does not exist in `decima/`**.

Four mechanisms, in order of how much they reduce prompt volume:

1. **Plug-in-or-forge, in strict order** (`heartbeat/decima/discovery.py:3-14`): rank the
   existing capability catalogue against the goal, then consult a research seam, and
   **only then** forge. Most gaps are not gaps. Ranking is already solved deterministically
   and with integers in the shipping tree (`decima/projections/search.py`,
   `decima/capabilities/qa.py:108`) — reuse it rather than porting the reference's hashed
   bag-of-words (`discovery.py:52-88`).
2. **The broker asks for scope, not for consent.** The powerbox flow
   (`specs/MORTA_CAPABILITIES.md:130-143`) is: agent asserts a request with purpose,
   target, duration, minimum scope → broker searches grants it already holds → proposes
   the **narrowest** attenuation → policy auto-approves low-risk or routes **one** human
   decision → the grant event binds holder + caveats. The broker proves narrowing
   structurally with `attenuation_valid` (`capability.py:297-310`) *before* issuing, so it
   can never widen authority, and it merges the Morta floor first
   (`with_morta_floor`) so a floored effect can never be auto-approved
   (`heartbeat/decima/powerbox.py:46-54` is the reference policy).
3. **Approve the operation, not the capability.** The kernel already distinguishes
   capability-scoped from invocation-scoped approvals (`capability.py:365-405`):
   `op_bind` (`capability.py:368`) is frontier-independent, so an approval for "pay 5" can
   never authorize "pay 500," and it is single-use. The right default for a promoted organ
   is one **capability-scoped** approval at promotion time (a durable "yes, this organ may
   act") plus **invocation-scoped** approvals only for floored effects. That is one prompt
   per organ, not one per call.
4. **Batching + tiering the surface.** The durable inbox (`decima/kernel/inbox.py`) is the
   only place decisions land. Group by tier: `pure`/`read_only` promotions land as
   *notifications* (auto-promoted, listed, revocable); `workspace_write` lands as a
   canary notification with a visible rollback button; `network`/`financial` land as
   explicit approvals with the evidence summary inline. The failure mode to design against
   is the one `VISION.md:428-429` already flags — "approval-UX calibration so the autonomy
   ladder spares you rubber-stamping." A prompt the user always clicks yes on has negative
   security value.

**What must *not* be done for UX:** auto-activation. The reference is explicit and right —
a discovered capability becomes an *approvable* inbox item, the enactor itself carries
`requires_approval`, and an unbound handler **fails closed rather than installing a stub**
(`heartbeat/decima/discovery.py:437-441,530-535`).

### 5.9 Deliberate divergences from the heartbeat reference

1. **No stub fallback.** `heartbeat/decima/forge.py:149-169` degrades to an honest STUB
   when no codegen is reachable. Honest, but it is a fake organ in a real product's
   catalogue. The shipping lane should **refuse** (`NOT_AVAILABLE`) instead.
2. **No `Kernel` god-object** (§4.3) — free functions over an explicit `Weft`.
3. **Reuse the shipping retrieval scorer** rather than a second ranking implementation.
4. **Derived quarantine** rather than a content-mutating lift (§5.7, Decision 3).
5. **`workspace_write` has an executor before it has an automated promoter.** §5.5's ladder
   pairs the tier with "Reckoner + canary + rollback target / yes, canaried", i.e. two
   changes at once: a jail that can write, and an automated signer for it. Those are
   separable, and they carry very different risk, so they landed separately. What is now
   real is the JAIL: `profiles.WORKSPACE` carries `workspace_bind=True`,
   `execution._BOOTSTRAP bind_workspace` performs an `MS_BIND` of one declared host subtree
   at `/workspace` inside the mount namespace (nosuid/nodev/noexec, `MS_RDONLY` when the
   mount says so, the mounted inode re-verified against an `O_PATH` fd the parent pinned).
   The tier was WITHHELD for one wave, because scoping the syscall filter found that the
   chroot was escapable on every arch and every profile — filesystem containment was not a
   boundary for any tier, so conceding a declared subtree would have promised an operator
   something the jail could not keep. Wave S0 closed that (`pivot_root` + a detached old
   root), and `executor.TIER_PROFILES` now maps the tier. `run_worker` REFUSES a `WORKSPACE`
   dispatch that is handed no subtree, so the mapping — when it lands — cannot decay into "PURE with a
   different receipt". The canary and the rollback affordance the design asks for are
   already in place (`powerbox.py` maps the tier to `CANARY`; `monitor.monitor_canary`
   demotes on a breach and revokes on a high finding).

   What did NOT land, deliberately: `promotion.SIGNER_POLICY` still requires a **human**
   attestation for `workspace_write`, and `anchors.SIGNABLE_TIERS` still excludes it.
   Widening who may promote is an authority change, not an implementation detail, and it is
   not the same act as building the executor. Two further bounds are worth stating because
   they are load-bearing: the subtree comes from an **operator-declared** deployment root
   (`generated_code_effect(workspace_root=…)`, default `None` = concede nothing, which
   yields `NO_EXECUTOR`), and a capability caveat may only name a point *beneath* it —
   otherwise a grant would choose its own blast radius.

## 6. Trust and safety analysis

**The premise is false as stated.** `KERNEL.md:233`: *"Test-gated promotion is only as good
as the tests. A subtly harmful capability can pass a green suite."* The correct posture is
that promotion is one layer among several, and the *other* layers must hold when the tests
are wrong. Ranked by how much they actually buy:

| Layer | What it survives | Where |
|---|---|---|
| **The organ has no authority beyond its grant** | a fully malicious promoted organ; prompt injection into the authoring model | `capability.py:11-13`, envelope check `:200`, grantee check `:203-209` |
| **Downhill-only attenuation, proved structurally** | a broker or agent trying to widen | `capability.py:297-310`, `_caveats_downhill:64-75` |
| **Morta floors survive promotion** | a "self-improvement" that removes its own approval gate — the lift strips only `sandbox_only` | `weave.py:501-505`, `capability.py:275-294` |
| **OS-level containment, verified in-child** | an organ that lies about what it does; a scanner miss | `execution.py:1-46`, adversarial tests in `tests/adversarial/` |
| **Digest binding** | code substitution after promotion | `execution.py:107,973-982` |
| **No egress executor exists** | exfiltration by a promoted organ (there is literally no networked worker) | `profiles.py` PROVIDER + `run_worker` refusal |
| **A write-capable organ is bounded to ONE operator-declared subtree** | a `workspace_write` organ, or a caveat written to widen one, reaching the rest of the host | `mount.py:resolve_bind_source` + `execution.py:_BOOTSTRAP bind_workspace` (inode re-verified against a pinned fd) |
| **Leases** (`expires_at`, `max_uses`) | a promoted organ that is fine now and wrong later; a forgotten grant | `capability.py:99-122` |
| **Canary + auto-revoke on a high finding** | a slow-burn regression in production | `weave.py:555-589` + a monitor service |
| **Revocation cascade** | derived/delegated authority surviving a revoke | `weave.py:435-454` |
| **Total audit** | "why was this allowed" | every event names its authorizing capability |
| **Test gate** | *ordinary* bugs and *obvious* hostility. Nothing more. | §5.4 |

**Named residual risks the design does not close.**

- **R1 — `ASSERT` is unauthorized (§5.7 point 3).** The promotion boundary is only as
  strong as the weakest write path, and today any key-holding principal can write the
  graph that `authorize` reads. Derived quarantine (Decision 3) narrows this to "you must
  also forge a trusted-promoter anchor, which requires being `_genesis_author`" — a real
  improvement — but a self-asserted **tier-less** capability still gets the legacy
  any-attest lift (`weave.py:543-544`), and a self-asserted cap + self-asserted agent
  envelope still passes `authorize` outright. **The real fix is an authorization check on
  `ASSERT` of `capability`/`agent`/`promoter` cells** (only the cell's granter chain, or
  root, may assert it) — that is a kernel change with fixture implications and it is
  scoped here as **N7 / Decision 7**, deliberately last, because it is the one item that
  can change signed-struct handling. Until it lands, **the honest claim is that Nona's
  promotion gate defends against a buggy or hostile *candidate*, not against a hostile
  *principal that already holds a key***. That distinction should be in `SECURITY.md`.
- **R2 — the local reasoner cannot judge its own work.** `NONA_RECKONER.md:148-153` bans
  it: no granting, no self-judgment, no replacing deterministic verification, no outward
  capabilities during evaluation. Enforce structurally: the judge's verdict is recorded
  with `authority: False` and the gate expression (§5.4) does not read it.
- **R3 — evaluation-suite authorship is an attack surface.** If the candidate's author also
  authors its suite, "test-gated" means "gated by tests the author chose." Mitigations:
  suites are versioned Cells with a `contamination_policy`; a **baseline suite per effect
  class is root-declared** and merged with (never replaced by) the candidate's; the
  adversarial cases (`hostile_cases >= 1`) come from the baseline. This is unresolved in
  the reference (`heartbeat/decima/candidate.py:155-168` derives the whole plan from the
  candidate) and it is Decision 6.
- **R4 — scanner findings are evidence, not proof.** `NONA_RECKONER.md:135`: a low score
  cannot prove safety, a high score cannot prove intent. The scan's real value is the
  **rug-pull check** — a finding fires exactly when the manifest and the implementation
  disagree (`heartbeat/decima/reckoner.py:143-146`), which is a *consistency* claim the
  scanner can actually make.
- **R5 — rollback never undoes an external effect.** `NONA_RECKONER.md:218`: it contains
  and compensates. Every rollback asserts an `incident` Cell and says so.
- **R6 — the catalogue becomes an unreviewable pile.** N organs, each with a suite, each
  re-evaluated against updated threats (`NONA_RECKONER.md:229`). Without periodic
  re-evaluation and lease expiry, "only ever grows" becomes "only ever accumulates."
  Default every promotion to a bounded `expires_at` so an unrenewed organ dies quietly.

## 7. Phased plan

Each wave is internally gate-green: `ruff format`+`check` (0.15.20), `mypy --strict`
(no `Any` in `decima.kernel.*`), pytest + coverage ≥ 78%, the adversarial lane, the
release-metadata drift guard, Playwright. New service code lives outside `decima/kernel/`,
so the import-boundary guard (`tests/architecture/test_import_boundaries.py:56`) is
untouched and **no new third-party dependency is required** by N0–N6.

| Wave | Content | Touches kernel? | Fixtures? | Status |
|---|---|---|---|---|
| **N0** | **Decisions locked** (§9). No code. | — | — | **landed** (`eac50b6`) |
| **N1** | **Sandbox principal + anchors.** `sandbox` field on the Agent Cell (`decima/runtime/cells.py`); a `nona` service package minting the Reckoner principal and the sandbox agent; `install_trust_anchors` as an idempotent root-asserted first-run step in `decima/services/provision.py`. **Tests that finally exercise `weave.py:498-552`**: a trusted attest lifts, an untrusted attest does not, a self-asserted `promoter` is filtered, a quarantined cap is invocable by a sandbox agent and denied to everyone else. | reads only | no | **landed** (`e7db9d2`) |
| **N2** | **Candidate + suite Cells.** `decima/services/nona/candidate.py`: `ExtensionCandidate` (§5.2), `implementation_digest`, DRAFT→QUARANTINED as two events, `EvaluationSuite`. Codegen is an **injected callable**; the default fails closed. Nothing executes. | no | no | **landed** (`6308a72`) |
| **N3** | **The Reckoner.** `decima/services/nona/reckoner.py`: the eight stages (§5.4) over `run_worker`; `EvaluationResult` Cell; the integer gate as one pure function. Adversarial-lane additions: a hostile candidate is contained; a timing-out candidate records `UNKNOWN` and **fails**; an undeclared-network candidate produces a high finding; a model judge cannot flip a deterministic failure. | no | **see below** | **landed** (`400724e`) |
| **N4** | **Promotion + rollback.** `decima/services/nona/promotion.py`: tier→signer, `build_capability` with `with_morta_floor`, the promote-`ATTEST`, the `promotion` Cell, `supersede`, `rollback` + `incident`. **If Decision 3 = derived quarantine, the `weave.py` fold change lands here.** Test the full bootstrap (`NONA_RECKONER.md:234-244`) including "retract the promotion, prove the invocation is then denied, and replay to the same `state_root`." | yes (Decision 3) | no | **landed** (`fb95542`) |
| **N5** | **Runnable organ + canary.** the `generated_code` effect handler behind the kernel's authorized invoke seam; `result` Cells shaped for `canary_health`; a monitor service that suspends on breach and auto-revokes on a high finding. **First caller and first test for `weave.py:555-589`.** | no | no | **landed** (`d34184b`, `26e5804`) — monitor unwired, see below |
| **N6** | **Surface: discovery, powerbox, UX.** `decima/services/nona/powerbox.py` (the module `capability.py:271` already names); discovery over the shipping scorer; commands `ProposeCapability` / `EvaluateCandidate` / `PromoteCandidate` (gated) / `RollbackPromotion` (gated) added to `commands.GATED`; routes; a Shell screen showing candidate → evidence → tier → promote/rollback, with generated source rendered as untrusted text (the workspace lane's existing discipline); Playwright spec. | no | no | **landed** (`871f488`, `fef5fc6`, `572f2fb`) |
| **N7** | **`ASSERT` authorization (R1).** Restrict who may assert `capability` / `agent` / `promoter` cells. **This is the wave that can change signed-struct handling** and it is deliberately last, behind its own decision. | yes | **possibly — see below** | **landed** (`f5763cf`, `4969d93`) — R1 closed; residuals below |

**Fixture position.** The fold fixture pins a **6-event script whose `type_counts` are
`{note: 1, type: 1}`** (`protocol/fixtures/fold.json`) — no capability, no `ATTEST`, no
promotion. Consequently: N1–N6, **including the Decision-3 fold change**, do not alter any
golden vector, so **`needs_fixture_regen = false`**. Two things would flip it, and both are
explicit decisions: (a) **adding a promotion vector to the fixture script** (Decision 4 —
recommended eventually, since an unpinned fold path is exactly how the promotion boundary
drifts, but it is a fixture regeneration and must be authorized like one); (b) **N7**, if
it changes the ASSERT body shape rather than adding a rejection rule. Nothing in N1–N6
changes hashing, canonical encoding, id text, pid width, or the signed-event struct.

**Decision points inside the plan.** After N3: does the gate actually reject a plausibly
harmful candidate, or only an obviously hostile one? (Write the harmful candidate first;
if the suite passes it, N4 waits.) After N5: does canary auto-revoke fire on a real
regression, or only on a synthetic finding? After N6: measured prompt volume per week —
if the user rubber-stamps, the UX failed and tiering must change before any tier is
auto-promoted by default.

### 7.1 Outcome — N0–N7 all landed

Recorded after the fact; the plan above is left as written so the two can be compared.

**The fixture prediction held.** `needs_fixture_regen` stayed **false** for the whole
sequence. N7 landed as a *rejection rule* (`decima/kernel/authorship.py::refusal` only ever
refuses; it never reshapes a body), so hashing, canonical encoding, id text, pid width and
the signed-event struct are byte-identical to N0. `protocol/fixtures/` and `heartbeat/` were
never touched. **Decision 4 (adding a promotion vector to the fold fixture) was never taken
and is still open** — the promotion fold path remains unpinned by any golden vector.

**N7 closed R1, and found it worse than this document recorded.** Two attacks beyond the one
described in §6 were live and are now closed: N4's derived-quarantine pass read
`promotion.content['signer']` without asking who *wrote* the record, so any key-holder could
forge a promotion record over a genuinely quarantined capability; and `_is_trusted_promoter`
returned True for *every* principal when a capability declared no tier, so a self-asserted
tier-less grant could lift its own quarantine with its own promote-`ATTEST`. A tier-less
capability now requires an anchored promoter too. The honest claim after N7 is in
`SECURITY.md` — *"a hostile key-holding principal can no longer mint, promote, or self-grant
authority"* — together with the residuals it does **not** close, which are enumerated there
and are the operative list, not this one.

**Residual N7 could not close (documented in `SECURITY.md`, repeated here because it bears on
this design). `SECURITY.md` is the operative list; three of the items that stood here have
since been closed and are marked as such.**

- **`agent` cells are only authorship-bound for the `sandbox` flag.** The powerbox must append
  a grant to the *requesting* agent's envelope, which another principal created, so ordinary
  envelope writes stay open by design. Bounded by the `capability` rule — an envelope can only
  name grants that already trace to root, and `verify_delegation` re-walks that chain at every
  use — but `SECURITY.md` correctly calls it the sharpest remaining edge.
- ~~**Any principal may redeclare a guarded type's merge class.**~~ **CLOSED.**
  `Weave._merge_class_of` pins every guarded type to a register whatever a `TYPE_DEF`
  declares, so the declaration is recorded and honoured nowhere. The justification that stood
  here — that binding it would refuse the type declarations the runtime makes on boot — was
  false: `model.define_type` has no product call site and the runtime declares no types.
- ~~**Morta floors are applied at mint time, not re-derived at read time.**~~ **CLOSED for the
  EFFECT-keyed floors.** `authorize_detail` re-derives `morta_floor(effect)` and answers
  `MORTA_FLOOR_MISSING`, so an unfloored `shell` or `financial` grant authorizes nobody even
  when a minting authority wrote it. Still open for the **tier**: `attenuate` drops
  `declared_effect_class`, so a brokered child of a `financial`-tier organ carries no tier and
  a read-time tier floor is not a pure function of the folded cell.
- ~~**A grantee-less grant is usable by anyone who can name it.**~~ **CLOSED.** `grantee` is a
  required argument of `capability_content` and a grant naming nobody is refused at read with
  `DenialCode.NO_GRANTEE`.
- **A parentless forgery still enters the log** and is refused by every read. Deliberate:
  judging against mutable current state would be non-deterministic under merge.
- **Withdrawal of a non-guarded Cell is unauthenticated in the kernel**, and for a `finding`
  Cell that was suppression of this design's terminal containment action, not a nuisance:
  `canary_health` skips retracted findings, so one unauthenticated RETRACT from a stranger
  made `monitor_canary` decline to revoke a compromised organ. `monitor.high_findings_by_
  auditors` now derives withdrawal from the RETRACT events and honours only an anchored
  auditor's (or root's). The general kernel rule remains open — see `SECURITY.md` for the two
  live cross-principal retractions (`invoke.py`'s approval consumption, `cancellation.py`'s
  lease termination) that a general rule has to carve out deliberately.

**Residual from N5 that N6 did not surface — the one gap the wave reviews missed.**
`decima/services/nona/monitor.py` (suspend-on-breach, auto-revoke-on-high-finding, and
`attributed_health`, the first real caller of `weave.canary_health`) is **fully tested and has
no production caller.** N6 shipped four read routes and four commands, none of which expose
canary health, incidents or suspensions, and the Shell screen renders no organ-health state.
So the canary exists as a correct library, not as product behaviour: nothing sweeps a promoted
organ on a schedule, and an operator cannot see an organ's health in the Shell. This is the
same "dead safety code is a liability" trap §3 names for `canary_health` itself — N5 fixed the
*zero-tests* half and left the *zero-callers* half. Wiring it needs an owner decision, because
the sweep's action is automatic revocation: **who runs it, how often, and whether auto-revoke
is on by default are risk-posture choices, not refactors.** Until that lands, the honest claim
is that Nona detects a bad organ *when something asks*, and nothing asks. Note this also means
the §7 decision point "after N5: does canary auto-revoke fire on a real regression, or only on
a synthetic finding?" is **still unanswered** — only synthetic findings have exercised it.

## 8. Non-goals

- **A public capability marketplace or any remote candidate source.** Deferred in 0.3
  (`docs/DECIMA-0.3-HANDOFF.md:58`) and out of scope here. Candidates originate locally.
- **Promoting a `network`-tier organ to runnable.** There is no networked worker
  (`profiles.py:64-75`); the tier can be *authored and evaluated*, never executed
  (§5.6).
- **Self-modification of the kernel or the constitution.** `NONA_RECKONER.md:26` puts
  kernel reducers and constitutional policy on a stricter track;
  `MORTA_CAPABILITIES.md:93` requires a distinct constitutional capability, delayed
  activation, and strong reauth. Nona forges *organs*, not the TCB.
- **Generated UI / view Cells.** `KERNEL.md:142` promises agent-authored views; that is a
  separate rendering-trust problem (arbitrary generated UI is deferred in 0.3).
- **Learning-from-traces / routing drift.** `NONA_RECKONER.md:220-230` is a later lane;
  this design deliberately keeps preference learning out of capability-code changes.
- **Replacing the three shipped workflows.** They become *the first three organs' worth of
  precedent*, not a migration target.
- **A new dependency, a new store, or a new verb.** None are needed.

## 9. Decisions required (owner sign-off)

> **DECIDED (owner, 2026-07-25).** All four gating decisions took the recommendation:
> **D1 = A** (`pure` + `read_only` auto-promote — the loop compounds without a human in
> each cycle); **D6 = A** (root-declared baseline suite per effect class, merged with but
> never replaced by the candidate's cases; adversarial cases come ONLY from the baseline);
> **D5 = (b) for the gate, (a) for the record** (pin the containment-matrix version + host
> arch into `environment_digest` and REFUSE to evaluate on a host that cannot deliver the
> matrix, rather than recording a weaker result under the same suite id; keep the raw
> manifest in unhashed diagnostics); **D3 = A** (derived quarantine flag + `promotion` Cell,
> rollback = `RETRACT`). D2 and D7 follow their recommendations; D4 (pinning the promotion
> path into the golden fixtures) remains a separate authorized regeneration after N4.
> N0 is therefore complete and N3 is unblocked.

**Decision 1 — Is any tier auto-promoted without a human, in the shipping product?**

| Option | Trade |
|---|---|
| **A. `pure` + `read_only` auto-promote** *(recommended)* | The thesis actually runs; blast radius of a wrong auto-promotion is a pure function with no outward executor. Un-defers `docs/DECIMA-0.3-HANDOFF.md:58` for exactly two tiers. |
| B. Every promotion is human-attested | Maximally safe; the loop is then a *tool* for the operator, not a compounding engine. Honest but it is not Nona. |
| C. Auto-promote nothing until N5's canary data exists | Sequences A behind evidence; costs a release. |

**Decision 2 — What does the UI say about the `network` tier?** Recommendation: present it
as **`NOT EXECUTABLE — no mediated egress`**, not as "requires approval." An approval
prompt for something that cannot run teaches the user to click yes.

**Decision 3 — Derived quarantine, or the reference's content-mutating lift?**

| Option | Trade |
|---|---|
| **A. Derived flag; `promotion` Cell; rollback = `RETRACT`** *(recommended)* | Makes `VISION.md:184` literally true; order-independent under merge/sync; enables demotion. Costs a `decima/kernel/weave.py` fold change (no fixture impact — §7). |
| B. Ship the reference lift as-is | Zero kernel change; inherits the merge hazard in §5.7 point 2 and leaves `quarantined: False` as an ordinary register value. |

**Decision 4 — Pin the promotion path in the golden fixtures?** Recommendation: **yes, but
as its own authorized fixture regeneration after N4 lands**, not inside a feature wave.
An unpinned fold path is how a promotion boundary drifts silently.

**Decision 5 — Host-variance policy for evaluation.** (a) record only
determinism-safe containment assertions and keep the raw manifest in unhashed
diagnostics, or (b) pin `CONTAINMENT_MATRIX_VERSION` + host arch into
`environment_digest` and **refuse to evaluate** on a host that cannot deliver the matrix.
Recommendation: **(b) for the gate, (a) for the record** — refuse rather than record a
weaker result under the same suite id.

**Decision 6 — Who authors the evaluation suite?** Recommendation: a **root-declared
baseline suite per effect class**, merged with (never replaced by) the candidate's cases;
adversarial cases come only from the baseline. This is the difference between "the tests
gate the code" and "the code chose its tests" (R3).

**Decision 7 (follows from R1) — Do we authorize `ASSERT` in N7?** Recommendation: yes,
and until then `SECURITY.md` states the honest scope: the promotion gate defends against a
hostile *candidate*, not a hostile *key-holding principal*.

## 10. Risks

- **The loop lands and nobody uses it.** The most likely failure is not a security breach;
  it is that authoring a candidate is more work than writing the Python. Mitigation: N6's
  discovery path must make *plug-in* the common case and forge the rare one, and the first
  three organs should be things the operator actually wanted (the honest test of the
  discovery scorer is whether it finds the three shipped workflows for their own goals).
- **Codegen quality is unknown.** The default provider is deterministic and offline
  (`README.md`), and the live path is a local llama.cpp endpoint. Whether a local model
  can author a candidate that clears the gate at a useful rate is **genuinely unknown** and
  is not something this design can assert. N2's codegen seam is injected precisely so the
  loop is testable and shippable *before* that question is answered — and so the answer,
  when measured, does not require re-architecting anything.
- **The gate passes something harmful.** Assume it will. The plan's answer is §6's layer
  table and the N3 decision point (write the harmful candidate first). The claim to make in
  release notes is "quarantined by default, tested, attested, revocable, and unable to
  reach the network because no networked executor exists" — **not** "verified safe."
- **Fold-semantics change under Decision 3.** Bounded: the fixture script does not exercise
  the path (§7), replay equality is asserted by the N4 bootstrap test, and the change
  makes the flag *more* deterministic (a pure function of the folded set) rather than less.
- **Coverage.** Six new service modules with adversarial paths will move the coverage
  number in both directions; the ≥78% floor is a gate, so each wave must land its own
  tests, and the dead-code waves (N1's tests for `weave.py:498-552`, N5's for
  `weave.py:555-589`) *raise* kernel coverage rather than lowering it.
- **Scope creep into the marketplace.** "Where do candidates come from" is the door to a
  remote registry, MCP mounting, and a distribution story
  (`heartbeat/decima/{mcp,mcp_server,serve_mcp}.py` all exist as prototypes). Every one is
  out of scope (§8) and each would need its own trust analysis; a remote candidate is an
  untrusted candidate authored by an unknown principal, which is a strictly harder problem
  than the local loop.
- **Unknowns worth stating plainly.** (1) Whether periodic re-evaluation
  (`NONA_RECKONER.md:229`) is affordable at N organs is unmeasured. (2) Whether the
  integer retrieval scorer ranks *capability manifests* as well as it ranks document
  segments is untested — the corpora are very different. (3) Whether `run_worker`'s
  ~10s default timeout and 5s CPU limit (`execution.py:72-81`) are adequate for a real
  evaluation suite of dozens of cases is unknown; the suite may need per-case dispatch
  budgets rather than one worker per stage.
