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

One consequence worth stating: **retraction of a non-guarded Cell remains unauthenticated.**
Any key-holder may retract an ordinary Cell — a plan step, a receipt, a note. That is a
denial-of-service surface, not an escalation, and binding it would break right-to-be-forgotten
(`lifecycle.redact`) and the ordinary status writes the runtime makes.

### What N7 did NOT close — the residual, stated plainly

- **A grantee-less grant is usable by anyone who can name it.** `authorize_detail` refuses a
  mismatched grantee only when `grantee is not None`, and `capability_content` defaults it
  to `None`. A grant on the log that names no grantee can be placed in any agent envelope
  and used. This is a *content* defect, not an authorship one, and no authorship rule
  closes it. Do not mint a capability without a grantee.
- **An ordinary `agent` Cell is not authorship-bound.** Only the `sandbox` flag is. The
  powerbox is why: a broker issuing a grant must append it to the *requesting* agent's
  envelope, which another principal created. The escalation this leaves is bounded by the
  capability rule (a self-written envelope can only name grants that already trace to root)
  and by the grantee check — but combined with the bullet above, an envelope write is still
  the sharpest remaining edge.
- **Morta floors are applied when a grant is minted, not re-derived when it is read.**
  `MORTA_FLOORS` is merged in by the issuing code path; `authorize_detail` reads the caveats
  the Cell carries. A principal entitled to mint a grant (root, or a root-anchored promoter)
  can therefore mint one without the floor for its effect class. Compromise of a
  root-anchored promoter is compromise of the realm's minting authority.
- **A forgery can still ENTER the log.** The acceptance gate judges at the event's causal
  frontier, so a parentless forged event — whose frontier contains no root at all — is
  accepted and then refused by every read. The log accumulates inert junk; the boundary
  holds. This is deliberate (judging against mutable current state would be
  non-deterministic under merge) and is asserted by
  `tests/nona/test_assert_authorization.py`.
- **Any principal can redeclare a guarded type's MERGE CLASS, and that is a denial of
  service.** A `TYPE_DEF` assertion is not itself one of the guarded types, so anyone may
  declare `capability` (or `agent`, `promoter`, `promotion`) to be an OR-set, map, counter,
  sequence or append-log. Such a Cell has no single asserting head, so `cell_asserted_by`
  answers None and **every** authority read fails closed: no grant on the realm authorizes
  anything until the declaration is retracted. It fails closed, not open — before the fix
  above it was an *escalation*, because an OR-set materialization replaces a capability's
  content with `{'elements': []}`, dropping `quarantined`, `caveats.sandbox_only`,
  `requires_approval` and `grantee` while a per-cell authorship map still credited ROOT with
  asserting it. Binding `TYPE_DEF` authorship would refuse the ordinary type declarations the
  runtime makes on every boot, so the honest statement is: the realm's type declarations are
  a shared, unauthenticated namespace, and the blast radius is availability.
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
  organ; a delegated grant whose every hop wrote its own still authorizes).

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
