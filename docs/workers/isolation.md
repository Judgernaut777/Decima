# Phase 5 — Worker Isolation (`decima/workers/`)

Effect execution never inherits the parent process authority (invariant 7 / handoff §5). A bounded effect runs in a fresh child process behind layered confinement, and only after three fail-closed gates pass.

## Public surface
- `protocol.py` — versioned local IPC. `WorkerRequest{protocol_version, invocation_id, job_id, effect, implementation_digest, arguments, lease, capability_proof}` -> `WorkerResponse{invocation_id, status(SUCCEEDED|FAILED|UNKNOWN), output_refs, receipt_data, diagnostics}`. Encoders/decoders refuse to serialize raw private-key material (keys matching `private_key`/`signing_key`/`seed…` at any depth) and reject an unknown `protocol_version` — a raw signing key never crosses the boundary.
- `lease.py` — `validate_lease` / `LeaseGuard.consume` over the `decima.runtime.cells` lease shape. Expired (`now > expiry`), not-yet-valid, mis-bound, float-clock, or replayed leases fail closed. Logical time only (ints), no wall clock.
- `execution.py` — `run_worker(request, implementation, entrypoint, *, now, profile, …)`. Gates in order: (1) a `capability_proof` must be present (no ambient authority); (2) the lease validates and is not replayed; (3) `compute_digest(implementation) == request.implementation_digest` (digest binding) else `DigestMismatch`. Then the child applies and verifies in-child: scrubbed minimal env, tmp-cwd jail, rlimits (CPU/AS/NOFILE/NPROC/FSIZE, read back), closed fds, `no_new_privs`, new session, and an honest layer manifest.
- `profiles.py` — `PURE` (floor: no network, no home, no secrets), plus `WORKSPACE` / `PROVIDER` as noted structure (their extra seams are deliberately not wired).

## Real OS isolation on this box (honesty, handoff §16)
This aarch64 Linux host supports unprivileged user + mount + network namespaces (Landlock is unavailable, ABI -1). So `PURE` uses, as mandatory layers that fail closed if they cannot engage: a user+mount namespace + `chroot` into the scratch jail (the worker cannot open `~/.ssh`, `/etc/passwd`, or any host path — the host filesystem is simply not present), and a network namespace (no route out).

Honest limitation, documented not hidden: after the chroot into an empty jail, only already-imported stdlib works. Preimported modules (`os`, `sys`, `json`) are fine; a lazily-loaded compiled extension like `socket` cannot be imported — itself a stronger network denial. On a host without user namespaces a `PURE` worker refuses to run (fail closed) rather than silently downgrading.

Outcome mapping stays honest: completed -> SUCCEEDED; a raising effect -> FAILED (no fabricated pass); a worker killed by the CPU/wall backstop (empty result + signal exit) -> UNKNOWN (unobservable, never invented). The in-child manifest rides back in `diagnostics.isolation` as Weft-receipt provenance.

## Adversarial proof (runs for real here)
`tests/adversarial/test_worker_isolation.py` proves the worker CANNOT: read `~/.ssh` or `/etc/passwd`, see a parent env secret, run an undigested or swapped implementation, run without a capability proof, reuse a replayed or expired lease, or reach the network — and that RLIMIT_AS bounds a memory bomb and CPU/wall bounds an infinite loop.