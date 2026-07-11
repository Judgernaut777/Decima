## Operations layer (Phases 11-13): backup / restore / doctor / first-run

A local Decima install lives under one **base data directory**, partitioned by what each kind of data *is* with respect to Law 5:

```
<base>/
  weft/          the Weft -- the SOLE canonical store (weft.db)
  artifacts/     content-addressed blobs; filename == digest
  checkpoints/   signed integrity commitments over fold frontiers
  config/        PUBLIC config only (budgets, identity fingerprint) -- NO secrets
  projections/   DISPOSABLE read-models -- rebuildable from the fold; never canonical
  logs/          operational logs -- disposable; only redacted tails leave the box
  keys/          SECRETS (master seed, 0600) -- never in a backup or support bundle
```

### Backup / restore (`decima.services.backup`)
A backup is **the event log itself made portable** (not a snapshot of state -- that is a disposable projection). It captures `{weft, artifacts, checkpoints, config}`; it excludes projections (rebuildable) and keys (secret).

- `backup_create(base, dest, *, keyring)` -- verified read of the source log, records its authoritative fold `state_root`, copies artifacts with per-file digests, and writes a `MANIFEST.json` carrying a hash-chain `root` over the ordered event ids plus a `backup_root` binding the log root, the fold root, and every file digest.
- `backup_verify(path)` -- **pure**, fail-closed (no keyring/db): recomputes each event's content id, the hash-chain, every file digest, and `backup_root`. Rejects a tampered backup offline.
- `restore_apply(dest, base, *, keyring)` -- verify -> preserve a **rollback** copy of any existing base -> replay every event through `Weft.ingest` (the kernel's WEFT §2 acceptance gate) -> restore artifacts (re-checking digests) -> rebuild nothing canonical -> confirm the folded `state_root` equals the certified one. Any failure raises; no partial world.

### Diagnostics (`decima.services.diagnostics`)
- `doctor(base, *, keyring=None)` -- structured report over: package/python version, Weft integrity (a full fold must verify), checkpoint consistency (missing -> warn, stale -> warn, root-divergent -> fail), artifact digests, disk space, unresolved effects. Overall status is the worst check. `decima-doctor --json`.
- `diagnostic_export(base, *, keyring=None)` -- a **scrubbed** support bundle: versions, per-check status/code/numeric fields, and REDACTED log tails. Never reads `keys/`, never emits raw Weft payloads or artifact bytes. `decima-doctor --export`.

### First-run (`decima.services.provision.first_run`)
Stands up a usable **fully local** install (no network): creates the layout, mints the box root identity (master seed custodied 0600), initializes an empty Weft, writes public default budgets (ints). Mints no authority. Runnable as `python3 -m decima.services.provision <base>`. Refuses to clobber an existing identity.

### CLI + deploy
`decima-doctor / -backup / -restore / -rebuild` are wired to the real implementations (`decima/cli/main.py`); `-restore` takes `--identity <base>` to locate the seed (excluded from backups by design). `deploy/decima.service` is a hardened systemd **user** unit (NoNewPrivileges, PrivateTmp, ProtectSystem=strict, single ReadWritePaths, MemoryMax/TasksMax/CPUQuota, Restart=on-failure); `deploy/install.sh` sketches the local install -> first-run -> optional unit-enable flow.