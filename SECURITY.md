# Security policy

Decima is a capability-secured personal agent operating layer. Its security model is the
product, so security defects are treated as correctness defects.

## Supported versions

Decima is pre-1.0 and developed on a rolling basis. During the 0.3 milestone only `main`
is supported; there are no backported security releases yet.

| Version | Supported |
|---|---|
| `main` (0.3 dev) | ✅ |
| earlier tags | ❌ |

## Reporting a vulnerability

Report privately — do **not** open a public issue for an exploitable defect.

- Email the maintainer (repository owner) with `SECURITY` in the subject.
- Include: affected component, reproduction, impact, and any suggested mitigation.
- Expect an acknowledgement; coordinated disclosure is preferred over public drops.

Do not include secrets, private keys, or real personal data in a report.

## The invariants a security report is measured against

A finding is in scope if it breaks any of the architectural invariants (handoff §2):

1. **Canonical Weft** — durable state that did not originate from an accepted event on
   the append-only Weft; a second canonical store for tasks/agents/approvals/etc.
2. **Four verbs** — a durable mutation that bypasses ASSERT / RETRACT / INVOKE / ATTEST.
3. **No ambient authority** — an effect that runs without an identified principal, an
   applicable capability, a concrete invocation, an authorization decision, any required
   Morta approval, and a receipt.
4. **Models propose; code authorizes** — any path where model output is itself treated
   as an authorization decision.
5. **Disposable projections** — a projection rebuild that changes canonical meaning.
6. **Kernel executes nothing untrusted** — generated code, shell commands, MCP servers,
   provider adapters, browser automation, or user scripts executing in the kernel
   process (see `docs/architecture/trust-boundaries.md`).

## Who may write the graph authorization reads (Nona N7 / design R1)

`capability.authorize` makes six checks and every one of them is a READ of the folded
graph. Until wave N7, `Weft.append` validated the verb and the author's signature and
nothing else, so **any principal whose key the keyring held could write both sides of every
check**: a `capability` Cell with `quarantined: False`, `grantee: <self>`, `parent: None`,
plus an `agent` Cell whose `envelope` contained it, and `authorize` returned "ok" with no
check skipped. The honest scope statement then was that Nona's promotion gate defended
against a buggy or hostile *candidate*, not against a hostile *principal that already held
a key*.

**That is now closed.** One pure predicate (`decima/kernel/authorship.py`) is enforced at
three places that cannot drift, because they share it:

| Where | What it does | What it is worth |
|---|---|---|
| `Weft.append` | refuses a local ASSERT of `capability` / `agent` / `promoter` / `promotion` that fails the rule; nothing is recorded | hygiene — it protects only what this process writes now |
| `Weft.ingest` → `acceptance.recheck_assert_authority` | the same rule at the §2 acceptance gate, judged at the event's CAUSAL frontier | stops a peer handing over a well-formed forgery |
| the FOLD and the READ (`Weave.cell_asserted_by` → `capability.verify_delegation`, the derived-quarantine pass, the sandbox conferral) | refuses to DERIVE authority from a cell whose asserter had no right to assert it | **the actual boundary** — the only layer that holds for a log already on disk, a restored backup, or a forgery that arrived before this rule existed |

The rule: a grant is asserted by its own `granter`; a ROOT grant (no `parent` — authority
descending from nothing on the log) only by the realm ROOT or by a principal ROOT has
anchored as a promoter; a `promotion` record only by the `signer` it names; a `promoter`
anchor and any `agent` Cell carrying `sandbox` only by ROOT. "ROOT" is the author of the
parentless event with the smallest local `seq` — a non-content AUTOINCREMENT, so unlike a
content-addressed event id it cannot be ground to hijack the anchor.

**Asked of the head that MATERIALIZED, not of the newest write.** All three read-side rules
ask `Weave.cell_asserted_by`, and that answer has to be the author of the bytes in
`cell.content` or the whole rule reads the wrong principal. It is therefore DERIVED: it
resolves `_reg_live(cid)[-1]` — the head `_materialize_register` projected — back to its
author, so content and attribution cannot name two different people. The first cut recorded
authorship per *cell* from the max-`(lamport, event_id)` ASSERT, which an **adjudication
ATTEST** (`MERGE_SEMANTICS.md` §4) falsifies: `select` supersedes heads, so `content` becomes
a concurrent branch while the recorded author still named the branch adjudicated *away*. Two
events — a concurrent self-grant (which the door permits, since you name yourself `granter`)
plus one ATTEST — then reopened R1 through every one of the three rules, up to and including
becoming the realm's root-anchored promoter. The paired half: on a guarded Cell an
adjudication may supersede a head only if the attester is the realm ROOT or the head's own
author (`Weave._may_supersede_head`), because choosing which assertion an authority-bearing
Cell materializes *is* an authority decision — and re-selecting a head runs no
`_caveats_downhill` check, so it silently widened authority too. Adjudication of ordinary
(non-guarded) Cells is unchanged: the signed ATTEST is the authority, exactly as §4 says.

**The honest claim after N7: a hostile key-holding principal can no longer mint, promote, or
borrow authority for itself.** Two further defects N7 closed that the design did not name:
a forged `promotion` record could lift a real quarantine (N4 read `content['signer']`
without asking who wrote the record), and a self-asserted **tier-less** capability could
lift its own quarantine with its own promote-ATTEST.

### Who may take authority AWAY

N7 guarded who may WRITE the graph and left who may **retract** it unguarded, so any
key-holding principal could `RETRACT` root's capability and the fold applied it — plus the
`DERIVED_AUTHORITY` cascade a capability retraction defaults to, which fails closed every
grant descending from it. No escalation (the attacker gains nothing), but a one-event,
unauthenticated shutdown of any organ, any delegation subtree, and — through the `promotion`
record, since quarantine is derived from its liveness — any promotion on the log.

`authorship.retract_refusal` closes it, mirroring the assert rule and adding one clause:

| cell | who may retract it |
|---|---|
| `capability` | its own `granter`, root, or a root-anchored promoter for its tier |
| `promotion` | the `signer` it names, root, or a root-anchored promoter for its tier |
| `promoter` | root only |
| `agent` carrying `sandbox` | root only |

The anchored-promoter clause is what gives an automatic containment action a signature that
means something: `nona/monitor.py` demotes on a canary breach and revokes on a high finding,
and it is not necessarily the principal that signed the promotion.

**This rule is enforced ONLY in the fold**, which is a stronger statement than the assert
rule makes and is deliberate. A `RETRACT` body names a `cell` id and not a type, so judging
one means looking the target up: `Weft.append` cannot (it holds the store lock) and the apply
pass cannot (a concurrent branch may carry the target's ASSERT at a higher order). `Weft.ingest`
deliberately does not gate it either — because the door cannot refuse these, an ordinary
honest log *contains* retractions the fold declines, and on a linear log every later event
names them as parents, so refusing one at the acceptance gate would orphan the whole remainder
of an honest peer's log, including the legitimate rollback that follows a forged one. A forged
retraction is therefore **recorded everywhere and honoured nowhere**.

One consequence worth stating: **retraction of a non-guarded Cell remains unauthenticated at
the KERNEL.** Any key-holder may retract an ordinary Cell — a plan step, a receipt, a note —
and the fold applies it.

An earlier version of this section called that "a denial-of-service surface, not an
escalation", and justified leaving it open on the grounds that binding it would break
right-to-be-forgotten and the runtime's status writes. **Both halves of that were wrong
against the code and are corrected here.** `lifecycle.redact` has exactly one product call
site (`services/api/commands.py`) and it retracts a `note` the *same* principal asserted, so
an "asserter, or root" rule would not touch it; the runtime's status writes are ASSERTs
(`runtime/cells.py::set_status`), not retractions. The two real obstacles are different and
both are live product paths: `kernel/invoke.py` has the grant HOLDER retract the
invocation-scoped approval it just spent (approvals are asserted by the HUMAN principal), and
`runtime/cancellation.py` terminates a `lease` the runtime executor asserted. A general rule
has to design those two clauses deliberately, and getting either wrong breaks invocation or
cancellation in a way the fold cannot even report — its response to an unauthorized retraction
is to silently un-retract it. So the general rule is still open and is stated below.

The consequence that was NOT merely denial of service has been closed where it bit.
`finding` is not a guarded type, so any key-holder could RETRACT an anchored auditor's HIGH
security finding — and `Weave.canary_health` skips retracted findings, so one unauthenticated
event from a principal with no anchor, no relationship to the finding and no root key made
`monitor_canary` decline to revoke a demonstrably compromised organ. That is suppression of
the terminal containment path, not a nuisance. `monitor.high_findings_by_auditors` already
bypassed the folded Cell for the ASSERT events because the Cell is forgeable; it now does the
same for the RETRACT events and honours a withdrawal only from a principal who could have
signed the finding — an anchored auditor for the organ's tier, or root. Every retraction mode
is judged (WITHDRAW, REDACT and TERMINATE all remove the evidence), and the correction path is
intact: an anchored auditor may still withdraw its own finding and the sweep stands down.

**And the verb was not the thing.** Judging the RETRACT left the identical suppression reachable
by an ordinary ASSERT, which is worth recording because the first fix looked complete and was
not. `Weave._apply` upserts `cell.type = body["type"]` on every content assertion, so a stranger
could re-type an anchored auditor's HIGH `finding` as a `note`; `high_findings_by_auditors` still
re-confirmed the shape against the folded Cell and dropped the finding on that line. Same
outcome, bit for bit — `healthy: True`, no revoke — and strictly worse in one respect: the
kernel's `high_findings` folds the same overwritten Cell, so it fell to 0 as well, the clamped
`unattributed_high_findings` reported 0, and the cell was not even marked retracted, so the
suppression did not surface anywhere in the health report. The fix is not a fourth mode to judge.
Nothing is read off the folded Cell in that function now: the severity and type come from the
asserting event's body, the `found_in` edge from an EDGE event that satisfies the same anchored
predicate, and liveness from the judged RETRACTs. The whole verdict is folded from events an
accountable principal signed, which is what leaves no forgeable read for a further variant to
reach — a narrower patch would have closed one keyword and left the next one open.

### Three residuals N7 left, and the rules that replaced them

Each of these was on the list below, was reproduced as a working attack, and is now refused.
Every one is an ADDITIVE refusal or a read-side normalization: no signed body's shape changed,
so no content address moved and no golden vector was regenerated.

- **A grant must name its grantee — refused at the mint AND at the read.** `authorize_detail`
  refused a mismatched grantee only when `grantee is not None`, so a grant naming *nobody*
  passed the check for *everybody*: it could be placed in any agent envelope (an ordinary
  `agent` Cell is not authorship-bound, see below) and used. It is a *content* defect, so no
  authorship rule closed it. Now `capability_content` takes `grantee` as a required
  keyword-only argument and raises on an empty one, and `authorize_detail` produces
  `DenialCode.NO_GRANTEE` for a grant whose `grantee` is absent, empty or not a string — kept
  distinct from `WRONG_GRANTEE`, which says the holder is wrong rather than that the grant
  belongs to no one. The read-side half is the boundary: it holds for a grant already on disk,
  minted before the rule existed. Measured before changing anything: **zero** product mint
  sites lacked a grantee (provisioning mints no authority at all; `executor.build_capability`,
  `powerbox.install_broker_source` and `powerbox.request_capability`→`attenuate` all pass one),
  so the blast radius was test fixtures, which were corrected in the same commit.
- **Morta floors are re-derived when a grant is READ, not merely merged when it is minted.**
  `MORTA_FLOORS` was applied by the two code paths that happen to issue grants and consulted
  by nothing, so the floor was a property of the minter rather than of the grant: a principal
  entitled to mint (root, or a root-anchored promoter) produced an unfloored `shell` or
  `financial` grant just by not calling `with_morta_floor`, and every read honoured it.
  `authorize_detail` now re-derives `morta_floor(cap.content['effect'])` and answers
  `DenialCode.MORTA_FLOOR_MISSING` when the grant does not carry it — a denial an approval
  cannot clear, because the remedy is to re-mint the grant. The mint-time merge is now an
  optimisation. Two honest limits: it is keyed on `effect`, **not** on the Nona tier (see the
  remaining-residual list); and `reversible_only` has no enforcement point anywhere in
  `decima/`, so what this guarantees for that key is PRESENCE — every live `financial` grant
  declares it — not that anything checks reversibility.
- **A hostile `TYPE_DEF` can no longer change how a guarded Cell materializes.** A `TYPE_DEF`
  is not a guarded type, so any principal could declare `capability` (or `agent`, `promoter`,
  `promotion`) to be an OR-set, map, counter, sequence or append-log. Under an OR-set every
  capability on the realm materialized as `{'elements': []}` — `quarantined`,
  `caveats.sandbox_only`, `requires_approval` and `grantee` all gone — and with no single
  asserting head, `cell_asserted_by` answered None and *every* authority read failed closed
  until the declaration was retracted. `Weave._merge_class_of` now returns the default
  register for every name in `authorship.GUARDED_TYPES` whatever the log declares: an
  authority-bearing type is ALWAYS a register. The declaration is still recorded and still
  visible in `merge_classes` — recorded everywhere, honoured nowhere, the same discipline as
  a forged retraction. This was chosen over binding `TYPE_DEF` authorship because it is one
  function instead of three enforcement sites, it needs no body-kind screen the guarded-type
  prefilter cannot express, and it REMOVES the denial of service rather than gating who can
  cause it. The previous justification for leaving it open — *"binding `TYPE_DEF` would refuse
  the ordinary type declarations the runtime makes on every boot"* — was false: `define_type`
  has no product call site and the runtime declares no types at all. Merge semantics for every
  non-guarded type are unchanged (`tests/nona/test_assert_authorization.py` pins both halves).

### What is still open — the residual, stated plainly

- **An ordinary `agent` Cell is not authorship-bound.** Only the `sandbox` flag is. The
  powerbox is why: a broker issuing a grant must append it to the *requesting* agent's
  envelope, which another principal created. What this leaves is bounded by the capability
  rule (a self-written envelope can only name grants that already trace to root) and by the
  grantee check, which now refuses a grant naming nobody as well as one naming somebody
  else — so the envelope write is a narrower edge than it was, but it is still the sharpest
  one.
- **The Morta floor is re-derived from the EFFECT class, not from the Nona TIER.**
  `executor.build_capability` floors an organ grant on its *tier*, and `capability.attenuate`
  rebuilds content through `capability_content`, which drops `declared_effect_class` (along
  with `implementation_digest`, `worker_digest`, `candidate` and `lifecycle`). A brokered
  CHILD of a `financial`-tier organ therefore carries `effect: generated_code` and no tier at
  all, so a read-time tier floor is **not** a pure function of the folded cell and is not
  attempted. Closing it honestly means either walking `content['parent']` to the root grant
  (feasible — `verify_delegation_detail` already walks that chain) or putting
  `declared_effect_class` back into every attenuated child's body, which changes those bodies'
  bytes and re-ids every `broker_grant:` cell. That is not an additive refusal and deserves
  its own wave. Related, and one layer up: `powerbox.request_capability` takes `tier` from the
  CALLER rather than from the source cell, bounded today only by `_caveats_downhill` forcing
  the parent's `requires_approval` to persist.
- **RETRACT of a non-guarded Cell is still unauthenticated in the kernel, and so is ASSERT of
  one.** The narrow slice that mattered — suppression of a security `finding`, which suppressed
  the canary's terminal containment action — is closed in `monitor.high_findings_by_auditors`,
  for the ASSERT and the RETRACT alike: the function reads nothing off the folded Cell, so
  neither retracting the finding nor re-ASSERTing it into another type or a lower severity
  disarms the auto-revoke (see *Who may take authority AWAY* above). The general rule is not, and the obstacles are the two live
  cross-principal retractions named there: `invoke.py`'s approval consumption and
  `cancellation.py`'s lease termination. Retraction of a `result` receipt is likewise still
  open; it is tolerable for the reason `attributed_health` states, namely that the action a
  breach takes is DEMOTION, which is reversible and re-promotable.
- **A forgery can still ENTER the log.** The acceptance gate judges at the event's causal
  frontier, so a parentless forged event — whose frontier contains no root at all — is
  accepted and then refused by every read. The log accumulates inert junk; the boundary
  holds. This is deliberate (judging against mutable current state would be
  non-deterministic under merge) and is asserted by
  `tests/nona/test_assert_authorization.py`.
- **Nothing here defends against a compromised ROOT key.** Root is the constitutional
  authority; see *Key custody* below for why `DirectoryKeyStore` split custody is the
  default and `DerivedKeyStore` must never run for real.

## What the worker jail does NOT contain — the containment residual

The section above is about who may write the authority graph. This one is about what the
jail an authorized effect runs inside actually enforces, and it is here because an operator
deciding whether to turn a lane on should not have to read a runtime data structure to find
out. Each item below is a dimension `decima.workers.containment_report()` reports with
`enforced: False`, and `tests/adversarial/test_containment_matrix.py` PINS that value — so
if someone flips one of these to `True` without wiring the mechanism, the suite goes red
instead of the claim quietly becoming false.

- **Resource bounds are per-process rlimits, not cgroups** (`cgroup_resource_control`).
  `RLIMIT_CPU/AS/NOFILE/NPROC/FSIZE` bind the effect-runner process and are read back with
  `getrlimit`. There is no cgroup v2 accounting, so there is **no aggregate limit across a
  descendant set**. The PID namespace and `RLIMIT_NPROC` bound how much a worker can spawn;
  they do not bound total CPU or memory across everything it did spawn.
- **A network-permitted worker has no egress mediation, and no network namespace either**
  (`egress_mediation`, and `network_isolation` which is enforced for every profile EXCEPT
  that one). The `PROVIDER` profile is STRUCTURE only: it describes a worker allowed one
  outbound call — so it gets no `CLONE_NEWNET` and therefore keeps the host's routes — and
  the redaction/mediation seam that call would have to pass does not exist.
  `run_worker` therefore **refuses every network-permitted profile at the primitive, for
  every caller** — this residual is currently unreachable rather than merely undefended.
  *Do not route real provider traffic through a worker until that seam lands*, and do not
  relax the refusal to get a demo working.
- **The seccomp syscall filter is aarch64-only** (`syscall_filter`, enforced only on
  aarch64). It is best-effort defense-in-depth: on any other architecture it is skipped and
  the worker still runs. The mandatory floors (namespaces, chroot, rlimits, no-new-privs,
  non-dumpable) are unaffected. On a **network-permitted** profile on a non-aarch64 host
  both this layer and egress mediation would be absent at once; `containment_report`
  emits a loud top-level `warnings` entry for exactly that combination.

**What changed, and what an operator may now rely on.** The workspace bind-mount
(`workspace_bind_mount`) used to be on this list. It is now REAL and enforced for the
`WORKSPACE` profile: one caller-declared host subtree is `MS_BIND`-mounted at `/workspace`
inside the worker's mount namespace before the `chroot`, always `nosuid`+`nodev`+`noexec`
(plus `MS_RDONLY` for a read-only mount), with the mounted inode re-verified against an
`O_PATH` fd the parent pinned — so the source cannot be swapped between the containment
check and the mount — and the posture read back from `statvfs`. Three things bound it:

- A `WORKSPACE` dispatch handed **no** subtree is **refused** (`IsolationError`), never
  downgraded to the write-less `PURE` jail under a profile name that promises otherwise.
- The **grant does not choose the blast radius.** For a `workspace_write` organ the root is
  an operator-supplied deployment fact (`executor.generated_code_effect(workspace_root=…)`,
  default `None` = concede nothing); a capability caveat may only name a subtree *beneath*
  it, and an absolute path, a `..` component, or a symlink leaving the root are refused.
- Having an executor is **not** being auto-promotable. `promotion.SIGNER_POLICY` still
  requires a human attestation for `workspace_write`, and `anchors.SIGNABLE_TIERS` still
  excludes it, so the Reckoner cannot promote one on its own.

## Automated guardrails

- `tests/architecture/test_import_boundaries.py` fails the build if the trusted
  computing base imports network, subprocess, provider, MCP, or web-framework code.
- `tests/adversarial/test_containment_matrix.py` pins every `enforced: False` dimension
  listed above and proves every `enforced: True` one against a REAL worker manifest, so the
  containment claims and the code cannot drift apart in either direction.
- `tests/adversarial/test_workspace_bind_mount.py` proves the bind by writing a file from
  inside the jail and finding it on the host, and proves the refusals by making the bind
  impossible and asserting a fail-closed `IsolationError` rather than a degraded run.
- Property and adversarial suites (Epic 3 / Epic 5) assert capability attenuation,
  revocation, replay-safety, and worker-escape resistance.
- `tests/nona/test_assert_authorization.py` reproduces the R1 attack above and each of the
  forgeries N7 refuses, at all three layers, with the specific denial code asserted at each
  site — plus the positive controls (the Reckoner still mints, promotes and runs a real
  organ; a delegated grant whose every hop wrote its own still authorizes). It also pins the
  guarded-type merge-class rule in both directions: a hostile `TYPE_DEF` changes nothing about
  a `capability`, and an ordinary type's declared merge class is still honoured.
- `tests/kernel/test_denial_codes.py` reproduces the grantee-less grant and the unfloored
  Morta-gated grant, asserts the exact denial code for each, and pairs both with the positive
  control on the same fixture (the same grant carrying its grantee / its floor authorizes).
- `tests/nona/test_canary.py` reproduces the finding-suppression attacks end to end on a real
  promoted organ — a stranger's WITHDRAW, REDACT and TERMINATE, a stranger's severity downgrade,
  a stranger re-typing the `finding` as a `note`, and a stranger's `found_in` edge on a finding
  the auditor never edged — each failing to disarm the auto-revoke, against the control that an
  anchored auditor withdrawing its own finding still stands the monitor down. The type-flip test
  also asserts the kernel's own `high_findings` falls to 0 on the same fixture, so the monitor's
  1 is provably an independent re-derivation and not the kernel's number read twice.

## What an agent must never do to pass a test

Per handoff §17.6, an implementation must **stop and report** rather than: bypass
authorization, disable signature checks, grant broad filesystem access, expose secrets to
a model, execute handlers in the kernel process, treat approval absence as approval,
replace durable state with in-memory state, or weaken containment without documentation.

## Secrets

Secrets are applied by provider/secret brokers, never placed in model context, logs,
fixtures, or diagnostic exports. No test or fixture may contain a real secret.

## Key custody

Signing keys are held by a *custodian* (`decima.kernel.keystore.KeyStore`): the raw
private key never leaves it — a caller receives only a public key or a signature.

- **`DirectoryKeyStore` — the DEFAULT for every real run (split custody).**
  Per-principal 32-byte seeds minted with `os.urandom` and persisted `0600`, one file per
  principal, inside a `0700` directory; keys live outside the `Keyring`, survive a restart
  (warm start — the Weft a prior run signed still verifies), and a principal with no
  provisioned key **fails closed**. Compromising one principal's key does not yield any
  other principal's key. Every real path goes through
  `decima.services.custody.install_keyring`: the API daemon
  (`decima.services.api.server.build_application`), first-run provisioning
  (`decima.services.provision.first_run`), and the operations CLI (`decima.cli.main`,
  which verifies doctor/backup/restore against the same custody). Keys are custodied at
  `<base>/keys/principals/` — inside the secrets partition, excluded from every backup and
  support bundle (`data_layout.EXCLUDED_FROM_BACKUP`) — or at `<db>.keys.d` for an ad-hoc
  database path. A backup therefore carries **no signing key**: on restore the operator
  re-places the master seed *and* the per-principal custody directory from their own
  custody (the clean-install rehearsal exercises exactly that).
- **`DerivedKeyStore` — DEV-ONLY, and only the bare `Keyring()` library/test default.**
  Every principal's Ed25519 key is derived from **one master seed**
  (`blake2b(master + pid)`). This is convenient and reproducible for the heartbeat profile
  and tests, but it fuses all identities under a single secret: whoever holds the master
  seed can sign as **every** principal. That collapses split custody and, with it, the
  ocap + Morta trust model — a leaked master seed forges authority, approvals, and
  receipts for all principals at once. It emits a `UserWarning` at construction. **No real
  run may use it**: the daemon, provisioning, and CLI paths pass an explicit
  `DirectoryKeyStore` via `Keyring(custodian=...)`, and building a served instance emits
  no such warning (asserted in `tests/api/test_key_custody.py`).
- **Migration of a derived-custody install.** A pid has exactly one key, so an existing
  author cannot be handed a fresh key without making its recorded events unverifiable.
  `custody.adopt_legacy_authors` imports the derived seed into per-principal custody for
  exactly those existing authors whose **recorded signature verifies** under that derived
  key — proven by a signature check, never assumed — and leaves every other principal
  without a key (fail closed). Adopted keys are per-principal files that were originally
  derived from the master seed; rotate them (`decima.kernel.rotation`) to reach full split
  custody. Newly minted principals always get fresh random keys.
- The master seed remains a secret but is no longer a signing key: it seeds only the
  loopback pairing secret and the self-certifying `mint_keyed` derivation.
