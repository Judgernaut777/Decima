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

## Automated guardrails

- `tests/architecture/test_import_boundaries.py` fails the build if the trusted
  computing base imports network, subprocess, provider, MCP, or web-framework code.
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
