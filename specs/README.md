# Decima Kernel Specifications

Read in this order:

1. [`../KERNEL.md`](../KERNEL.md) — constitutional vision and fixed vocabulary.
2. [`WEFT_PROTOCOL.md`](WEFT_PROTOCOL.md) — canonical event envelope, authorization proof, and four verb bodies.
3. [`FOLD_AND_LIFECYCLE.md`](FOLD_AND_LIFECYCLE.md) — deterministic materialization, snapshots, merge, scratch graduation, sync, and GC.
4. [`MORTA_CAPABILITIES.md`](MORTA_CAPABILITIES.md) — selector/caveat language, attenuation proof, approvals, powerbox, and revocation.
5. [`NONA_RECKONER.md`](NONA_RECKONER.md) — extension quarantine, evaluation, canary, promotion, rollback, and bootstrap test.
6. [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md) — memory Cell taxonomy, recall routing, consolidation, provenance, and governance.
7. [`BROWSER_WORKER.md`](BROWSER_WORKER.md) — visual browser execution, untrusted-page boundaries, credential injection, and effect classes.
8. [`DONOR_MATRIX.md`](DONOR_MATRIX.md) — repository disposition, licensing posture, subsystem destination, and risk.

## Compatibility rule

The Python Heartbeat is a prototype profile. It may use simpler representations, but semantic differences should be recorded explicitly before durable data is created. In particular:

- Prototype canonical JSON/BLAKE2b-128 is not the durable wire format.
- Capability IDs are not bearer credentials.
- `INVOKE` records authorized intent; executor receipts record observed outcomes.
- Projection replay never repeats external effects.
- Concurrent Cell heads are preserved unless a declared type reducer or attested adjudication resolves them.
