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

- **`DirectoryKeyStore` — the intended production posture (split custody).**
  Per-principal 32-byte seeds persisted `0600`, one file per principal, provisioned
  explicitly; keys live outside the `Keyring` and a principal with no provisioned key
  **fails closed**. Compromising one principal's key does not yield any other
  principal's key.
- **`DerivedKeyStore` — DEV-ONLY (the current default).** Every principal's Ed25519
  key is derived from **one master seed** (`blake2b(master + pid)`). This is convenient
  and reproducible for the heartbeat profile and tests, but it fuses all identities
  under a single secret: whoever holds the master seed can sign as **every** principal.
  That collapses split custody and, with it, the ocap + Morta trust model — a leaked
  master seed forges authority, approvals, and receipts for all principals at once. It
  emits a `UserWarning` at construction. **Do not use it in production**; pass an
  explicit `DirectoryKeyStore` (or another split-custody custodian) via
  `Keyring(custodian=...)`.
