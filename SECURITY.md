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

## Automated guardrails

- `tests/architecture/test_import_boundaries.py` fails the build if the trusted
  computing base imports network, subprocess, provider, MCP, or web-framework code.
- Property and adversarial suites (Epic 3 / Epic 5) assert capability attenuation,
  revocation, replay-safety, and worker-escape resistance.

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
