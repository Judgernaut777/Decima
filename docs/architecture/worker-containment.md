# Worker containment matrix

_Post-0.3.0 evolution — no redesign. This documents the **enforced subset** of worker
isolation exactly as `decima/workers/execution.py` applies it, and is kept honest against
the code by the pure `containment_report()` function and the adversarial tests that assert
each row. Where a layer is **not** enforced it says so plainly; do not read a claim into a
gap._

## Scope and threat model

A worker runs one **digest-bound, already-authorized** effect implementation as untrusted
DATA in a fresh child process that inherits **none** of the parent's authority (invariant 7).
The worker mints no authority, holds no signing keys, no home, no parent secrets, and no
Weft db handle. Containment's job is to bound what that untrusted code can *reach* and
*consume*, and to report **honestly** which layers actually engaged (the in-child manifest is
built from read-backs, never assumed).

The matrix below is emitted as data by `decima.workers.containment_report(profile, limits)`
(pure, no side effects). Each **enforced** row that is verified in-child carries a
`manifest_proof` `{key: engaged_value}`; the containment-matrix tests
(`tests/adversarial/test_containment_matrix.py`) run a real worker and assert the live
manifest satisfies every such proof, and that every honestly-not-enforced row is absent from
the enforced set — so this document and the code cannot drift.

## Platform requirements (host ground truth)

- **Requires** unprivileged Linux **user + mount + network** namespaces. Verified on this
  aarch64 host (`unprivileged_userns_clone` enabled).
- For the `PURE` and `WORKSPACE` profiles the namespace layers are **mandatory**: if they
  cannot engage, the spawn **fails closed** (`IsolationError`) and nothing runs. There is no
  silent downgrade to a weaker guarantee — honest degradation is decided at profile-definition
  time, never at runtime.
- **Not guaranteed on a stock host**: PID-namespace isolation, seccomp syscall filtering, and
  cgroup resource accounting are **not** applied by this code (see the gaps section). A host
  that lacks user namespaces cannot run a `PURE`/`WORKSPACE` worker at all (by design).

## Profiles

| Profile | Network | Filesystem jail | Namespaces mandatory | Status |
|---|---|---|---|---|
| `PURE` | denied (netns) | chroot into empty scratch jail | yes | fully wired |
| `WORKSPACE` | denied (netns) | chroot into scratch jail (repo materialized into it by the capability layer) | yes | wired as PURE; bind-mount seam **not** wired |
| `PROVIDER` | **permitted** | chroot into scratch jail | yes | egress mediation **not** wired — do not route real traffic |

`WORKSPACE` is the profile behind the isolated coding workspace (Path C). It currently runs
with the same empty-jail chroot as `PURE`; its repository files are materialized into the
scratch jail by the capability layer, not bind-mounted by the worker. `PROVIDER` permits
network but has **no** egress mediation in this phase.

## Enforced dimensions

Every row below is `enforced=True` in `containment_report()` for `PURE`/`WORKSPACE`. The
`Proof (manifest)` column is the key the live in-child manifest must show engaged; the
`Fail behavior` column is what happens to the confined code when it hits the boundary.

| Dimension | Mechanism | Fail behavior | Enforcing code | Proof (manifest) | Adversarial test |
|---|---|---|---|---|---|
| Environment scrub | minimal allow-listed env (`PATH,HOME,TMPDIR,LANG,LC_ALL`); child aborts if any un-allowed key leaked | fail closed (`IsolationError`) | `_minimal_env` / `_BOOTSTRAP` env check | `env_keys` = the 5 allowed keys | `test_worker_cannot_read_a_parent_env_secret`; `test_environment_secret_unreachable_from_worker` |
| Working-directory jail | cwd is a fresh per-run tmp scratch dir, verified `realpath(getcwd)` | fail closed | `_spawn` (`mkdtemp`) / `_BOOTSTRAP` cwd check | `cwd_jail` present | driven via `containment_report` proofs |
| FD closure | `close_fds` + explicit `pass_fds`; child asserts only stdio + 2 worker pipes open | fail closed | `_spawn(close_fds)` / `_BOOTSTRAP` fd check | `open_fds` present | driven via `containment_report` proofs |
| Session isolation | `start_new_session`; the whole session is SIGKILLed on timeout | fail closed | `_spawn(start_new_session)` / `_kill_group` | `new_session=True` | `test_worker_cpu_and_wallclock_are_bounded` |
| No-new-privs | `prctl(PR_SET_NO_NEW_PRIVS,1)` read back via `PR_GET_NO_NEW_PRIVS` | fail closed | `_BOOTSTRAP` | `no_new_privs=True` | driven via `containment_report` proofs |
| **Non-dumpable** (added) | `prctl(PR_SET_DUMPABLE,0)` read back via `PR_GET_DUMPABLE` — no ptrace-attach by a peer, no core dump | fail closed | `_BOOTSTRAP` | `non_dumpable=True` | `test_worker_is_non_dumpable` |
| Resource limits | `RLIMIT_CPU/AS/NOFILE/NPROC/FSIZE` set then `getrlimit` read-back; `RLIMIT_CORE=0` | CPU→SIGXCPU then SIGKILL (**UNKNOWN**); AS→`MemoryError` (FAILED); FSIZE→SIGXFSZ/OSError; NOFILE/NPROC→errno | `DEFAULT_LIMITS` / `_BOOTSTRAP` setrlimit | `rlimits` present | `test_worker_memory_is_bounded`; `test_worker_cannot_fork_a_grandchild_beyond_nproc`; `test_worker_fsize_is_bounded` |
| Filesystem isolation | user+mount namespace, make-rprivate, `chroot` into the scratch jail | fail closed (mandatory) | `_BOOTSTRAP` `apply_namespaces` (chroot) | `namespaces.fs_jail=True` | `test_worker_cannot_read_dot_ssh`; `test_worker_cannot_read_etc_passwd_by_absolute_path`; `test_weft_db_access_attempt_from_worker_fails` |
| User namespace | `CLONE_NEWUSER`, `setgroups=deny`, single-entry uid/gid map | fail closed (mandatory) | `_BOOTSTRAP` `apply_namespaces` (unshare) | `namespaces.user_ns=True` | driven via `containment_report` proofs |
| Mount namespace | `CLONE_NEWNS` so the chroot/rprivate cannot affect the host | fail closed (mandatory) | `_BOOTSTRAP` `apply_namespaces` | `namespaces.fs_jail=True` | filesystem tests above |
| Network isolation | `CLONE_NEWNET` ⇒ no interfaces, no route out (network-denied profiles) | fail closed (mandatory) | `_BOOTSTRAP` `apply_namespaces` (`CLONE_NEWNET`) | `namespaces.net_isolated=True` | `test_worker_cannot_reach_the_network`; `test_network_access_attempt_from_worker_fails` |
| Wall-clock timeout | parent `select()` deadline; an over-budget worker's session is SIGKILLed | killed mid-effect ⇒ **UNKNOWN** (outcome unobservable, never faked) | `_read_to_eof` / `run_worker` | (parent-side; no manifest key) | `test_worker_cpu_and_wallclock_are_bounded` |

### Authority-seam gates (before any child spawns)

These are not OS-isolation layers but the fail-closed capability gates in `run_worker` that
decide whether an effect runs at all:

- **Capability proof** required — an effect with no authority is refused (invariant 3).
  Test: `test_worker_refuses_an_effect_with_no_capability_proof`.
- **Lease** must validate at `now` and not be replayed/expired.
  Tests: `test_worker_refuses_a_replayed_lease`, `test_worker_refuses_an_expired_lease`,
  `test_expired_lease_replay_never_runs`.
- **Digest binding** — `compute_digest(source)` must equal the request's
  `implementation_digest`; an undigested/swapped body never runs.
  Tests: `test_worker_refuses_an_undigested_implementation`,
  `test_worker_refuses_a_swapped_implementation_under_a_valid_digest`.

## Gaps — honestly NOT enforced

These are listed by `containment_report()` with `enforced=False` and a `gap` note. They are
documented so no one mistakes an absent layer for an enforced one.

| Dimension | Why it is a gap | Compensating factor (if any) |
|---|---|---|
| **PID namespace** | No `CLONE_NEWPID` is unshared; the worker does not get its own PID 1. | The chroot removes `/proc` from the jail, so the worker cannot enumerate host processes — but process-table isolation itself is not guaranteed. |
| **Seccomp syscall filter** | No seccomp-bpf filter is installed; the syscall surface is not reduced. | `no_new_privs` + user/mount/net namespaces + rlimits are the confinement. A seccomp binding is not part of this phase. |
| **cgroup resource control** | Bounds are POSIX rlimits (per-process), not cgroup accounting. | Aggregate limits across a descendant set are not enforced; the worker does not fork (nproc is tight). |
| **Egress mediation (PROVIDER)** | The `PROVIDER` profile permits network but wires no redaction/mediation seam. | Not applicable to `PURE`/`WORKSPACE` (network-denied). Do not route real provider traffic through a network-permitted worker until the seam lands. |
| **Workspace bind-mount (WORKSPACE)** | The declared-subtree bind-mount seam is not wired. | `WORKSPACE` runs the same empty-jail chroot as `PURE`; the repo is materialized into the scratch jail by the capability layer. |

## Canonical dimension identifiers

Every row above is keyed by a stable machine id emitted by `containment_report()`. The
drift test (`test_doc_documents_every_dimension`) fails if any id here is missing from this
document, so this list is the authoritative index of covered dimensions:

```
environment_scrub        working_directory_jail   fd_closure
session_isolation        no_new_privs             non_dumpable
resource_limits          filesystem_isolation     user_namespace
mount_namespace          network_isolation        wallclock_timeout
pid_namespace            syscall_filter           cgroup_resource_control
egress_mediation         workspace_bind_mount
```

The first twelve are enforced for `PURE`/`WORKSPACE` (for `PROVIDER`, `network_isolation`
moves to the gap set because that profile permits network); the last five are the documented
gaps — never claimed as enforced isolation.

## Change discipline

- The matrix is **data**: change `containment_report()` and this table together. The
  containment-matrix tests assert every enforced row's `manifest_proof` against a live worker
  and assert the gaps stay gaps, so a drift between doc, `containment_report()`, and the
  actual manifest turns a test red.
- Additive hardening only. Do not weaken or remove an enforced row without changing the
  profile's honest declaration and the tests. The non-dumpable row was added post-0.3.0 as
  pure additive hardening (a `prctl` that cannot affect the worker's own execution).
