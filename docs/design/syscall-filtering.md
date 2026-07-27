# Design: Syscall Filtering — honestly scoped

**Status:** scoping only. Nothing here is implemented by this document. It exists so the
next containment wave starts from what the code actually does rather than from what the
containment matrix says it does.

**Risk of record:** R7, `docs/assessments/2026-07-24-ultraplan-assessment.md` —
*"seccomp absent on x86_64, denylist-only on aarch64. Off-aarch64 the syscall filter is
gone entirely; even on aarch64 it is default-allow over ~30 entries (no `socket`/`open`/
`execve` deny). Honestly disclosed, but 'seccomp on aarch64' overstates the cross-arch
posture."*

**Reading order:** `decima/workers/execution.py` (`install_seccomp` in `_BOOTSTRAP`,
`_seccomp_arch_supported`, `containment_report`), `decima/workers/profiles.py`,
`docs/architecture/worker-containment.md`, SECURITY.md *"What the worker jail does NOT
contain"*.

---

## 1. One-paragraph summary

The syscall filter is a best-effort seccomp-bpf **denylist of 32 syscall numbers**, arch-
guarded to aarch64, installed as the last step before the untrusted implementation runs.
On any other architecture it is skipped and the manifest says so. Scoping it honestly
turned up something larger than R7 described: **R7 understated the problem.** The filter is
not the marginal layer on top of a solid namespace floor — the namespace floor it is
supposed to supplement has two verified escapes of its own, and the denylist as written
would not have stopped either of them even on aarch64 (§4.3, §4.4). So the real question is
not "should we port the denylist to x86_64" but "what is the syscall filter *for*, given
that the chroot it defends is escapable and a default-deny allowlist is both cheap and
achievable here". §5 measures the allowlist: **13 distinct syscalls** for a busy pure
effect, because the filter installs after CPython has already started and after the chroot
has already reduced the worker's reachable Python to its pre-imported modules. That is a
small enough surface that default-deny is the right target, and it is the only version of
this layer that would have mattered.

## 2. Why this is a decision, not a patch

Three things make this an owner call rather than a follow-up commit.

1. **A default-deny allowlist can break a worker that a denylist cannot.** A missing entry
   in a denylist costs containment; a missing entry in an allowlist costs availability —
   the effect dies at a syscall nobody predicted. Nona *generates* the code that runs in
   these workers (`docs/design/nona-self-extension.md` §5.4), so the allowlist has to cover
   any pure-Python program a model might author, not just the ones in the suite today.
   Choosing default-deny is choosing to take availability risk to buy containment.
2. **"Refuse or run" is a product decision with no safe default.** `profiles.py` already
   made the analogous call once, in the opposite direction: `namespaces_mandatory=True`
   means a `PURE` worker on a host without user namespaces **refuses to run**. The syscall
   filter has no equivalent field, so the same class of missing layer produces a refusal in
   one case and a silent best-effort skip in the other. §6 argues that inconsistency is
   currently *accidental* rather than deliberate, and that fixing it is a policy choice
   about who is allowed to deploy Decima on x86_64.
3. **The two escapes in §4 have to be sequenced against this work.** Porting the denylist
   to x86_64 would close R7 as written and leave both escapes wide open, while producing a
   containment matrix that looks *better*. That is the worst available outcome: it buys a
   green row and spends the reviewer attention that the real holes need.

---

## 3. What exists today, read from the code

### 3.1 What engages, per architecture

`install_seccomp` (`decima/workers/execution.py`, in `_BOOTSTRAP`) carries a literal arch
check, and `_seccomp_arch_supported()` carries the same check parent-side so
`containment_report` cannot claim a layer the child skips:

```python
_SECCOMP_ARCH = "aarch64"

def _seccomp_arch_supported() -> bool:
    return os.uname().machine == _SECCOMP_ARCH
```

| Host | Filter installed? | Manifest | `containment_report` row |
|---|---|---|---|
| **aarch64** | yes — `PR_SET_SECCOMP` + `PR_GET_SECCOMP` read-back | `seccomp.engaged=True`, `denied_syscalls=32`, `action="ERRNO(EPERM)"` | `enforced=True`, `posture="best_effort"` |
| **x86_64** | **no** — skipped before any BPF is built | `seccomp.engaged=False`, `detail="skipped: filter table is aarch64-only, host is x86_64 …"` | `enforced=False` + a `gap` string |
| any other | no, same path | same | same |
| aarch64, kernel refuses | no | `engaged=False`, `detail="PR_SET_SECCOMP refused (errno N)"` | `enforced=True` — **the report is arch-derived, not outcome-derived** |

That last row is a real (small) honesty gap in the *report*, distinct from R7: on aarch64
`containment_report()` says `enforced=True` because the host CPU supports the filter, not
because the filter installed. Only the per-run manifest knows whether it actually engaged.
The matrix test papers over this because on a healthy aarch64 box both are true.

**The host this branch was scoped on is `x86_64`.** So on this box, and in CI on this box,
the syscall filter is absent on every single worker spawn. Several docstrings still describe
the tree as running on "this aarch64 box" (§7).

### 3.2 What the denylist actually contains

32 arm64 (asm-generic) numbers, all of them things a pure-compute worker never calls:

| Group | Syscalls (arm64 `nr`) |
|---|---|
| debug / cross-process memory | `ptrace` 117, `process_vm_readv` 270, `process_vm_writev` 271, `kcmp` 272 |
| namespace manipulation | `setns` 268, `unshare` 97 |
| mount, old + new API | `mount` 40, `umount2` 39, `pivot_root` 41, `mount_setattr` 442, `open_tree` 428, `fsopen` 430 |
| kernel code loading | `init_module` 105, `finit_module` 273, `delete_module` 106 |
| machine control | `reboot` 142, `kexec_load` 104, `kexec_file_load` 294, `swapon` 224, `swapoff` 225 |
| kernel attack surface | `bpf` 280, `perf_event_open` 241 |
| keyrings | `add_key` 217, `request_key` 218, `keyctl` 219 |
| host identity / accounting | `acct` 89, `sethostname` 161, `setdomainname` 162, `quotactl` 60 |
| clock | `clock_settime` 112, `clock_adjtime` 266, `adjtimex` 171 |

The BPF program itself is correct and worth keeping: load `seccomp_data.arch`, `KILL` on any
arch that is not `AUDIT_ARCH_AARCH64`, load `nr`, a linear chain of 32 `JEQ`s, fall through
to `ALLOW`, matches jump to `ERRNO(EPERM)`. The arch guard is the right shape — it is what
makes the "numbers are per-arch" problem fail loudly instead of silently mis-denying.

### 3.3 What the denylist does not contain

Everything else, by construction — the program's default action is `ALLOW`. Concretely, a
worker on aarch64 today may still call:

- `openat` (56), `read`, `write`, `unlinkat`, `mkdirat`, `renameat` — **all filesystem I/O**;
- `socket` (198), `connect`, `bind`, `sendto` — **all socket calls** (the network namespace,
  not the filter, is what makes them useless on a network-denied profile);
- `execve` (221), `clone` (220), `clone3` (435) — **process creation**;
- **`chroot` (51)** — and this one is load-bearing; see §4.3;
- `seccomp` (277) — the worker can install *further* filters on itself, which is harmless,
  but it also means the filter is not "the last word" in any structural sense;
- `prctl` (167) — including `PR_SET_DUMPABLE(1)`, undoing the non-dumpable hardening for
  itself.

R7's phrasing ("no `socket`/`open`/`execve` deny") is accurate. `open` specifically does not
exist on arm64 — asm-generic has only `openat` — which is itself a small illustration of why
a hand-maintained per-arch number table is a liability.

---

## 4. Question 1 — the real containment posture, per architecture

### 4.1 The layers that are actually doing the work

For a `PURE` or `WORKSPACE` worker, the mandatory floor is what contains the effect, and it
is genuinely strong. All of it is verified in-child and fails closed:

- a scrubbed 5-key environment, with the child aborting on any leaked key;
- a fresh `mkdtemp` scratch cwd, verified against `realpath(getcwd())`;
- `close_fds` + an in-child assertion that only stdio and the worker pipes are open;
- `RLIMIT_CPU/AS/NOFILE/NPROC/FSIZE` + `CORE=0`, each set then `getrlimit`-read-back;
- `PR_SET_NO_NEW_PRIVS`, `PR_SET_DUMPABLE(0)`, both read back;
- `CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWNET | CLONE_NEWPID`, `setgroups=deny`, a
  single-entry uid/gid map, `mount --make-rprivate /`, then `chroot(scratch)`;
- a reaper fork so the effect runs as PID 1 of the new PID namespace;
- a parent-side `select()` deadline that SIGKILLs the whole session.

Measured on this x86_64 host (no filter at all), a `PURE` worker's *reachable world* is
already very small:

```
uid/euid            0        (inside the user namespace)
pid                 1        (own PID namespace)
os.listdir('/')     []       (empty chroot — no /proc, /dev, /sys, no binaries)
import socket       ModuleNotFoundError
import hashlib      ModuleNotFoundError
sys.modules         83 modules — only what the bootstrap imported before the chroot
unshare(CLONE_NEWUSER)  EPERM (the kernel refuses a nested userns to a chrooted task)
```

The `ModuleNotFoundError`s matter more than they look. Because the chroot lands on an empty
directory, **no C extension in `lib-dynload` can be loaded after it** — the file is not
there. A worker cannot import `socket`, `hashlib`, `sqlite3`, `select`, or `_ssl`. It has
builtins, `ctypes` (already loaded, so `libc` is reachable), `os`, `json`, `fcntl`,
`resource`, `stat`, and whatever else the bootstrap dragged in. That is not a syscall
filter, but it removes most of the ways Python *expresses* a dangerous syscall.

So the honest answer to "how much does seccomp add here, given the other layers?" is:
**against an accidental or opportunistic effect, very little** — the interesting denied
syscalls were already unreachable or useless (`mount` needs `CAP_SYS_ADMIN` over a mount
namespace it cannot re-create; `unshare` is already EPERM under the chroot; `ptrace` has no
target in an empty PID namespace; `bpf`/`perf_event_open`/keyrings/`kexec` need host
privilege the userns map does not confer). **Against a deliberate escape, it adds nothing at
all today**, because of the next two subsections.

### 4.2 Two verified escapes

Both were reproduced against the real `run_worker` on this branch, on this x86_64 host, with
`PURE`/`WORKSPACE` and the standard profiles. Neither needs the syscall filter to be absent
— see §4.4 for why the aarch64 denylist does not stop either one.

### 4.3 Escape A — the classic double-`chroot`, all profiles, all architectures

`chroot()` does not move the caller's cwd. So a task that is root *in its user namespace*
(which every `PURE` worker is, uid 0 via `uid_map`) and holds `CAP_SYS_CHROOT` over that
namespace (which `unshare(CLONE_NEWUSER)` grants unconditionally) can `chroot()` into a
subdirectory, leaving its cwd *outside* the new root, and then walk `..` upward — the
kernel's "`..` at the root is a no-op" clamp only applies when resolution is at the current
root, and it no longer is.

```python
os.mkdir('esc'); os.chroot('esc')          # cwd is now above the new root
os.chdir('../' * 40); os.chroot('.')       # re-root at the real filesystem root
os.chdir('/')
```

Observed result from inside a `PURE` worker, effect reported `SUCCEEDED`:

```
listdir('/')        ['bin','boot','dev','etc','home','lib','opt','proc','root','run', …]
open('/etc/passwd') 'root:x:0:0:root:/root:/bin/bash\ndaemon:x…'
listdir(HOME)       ['.aws','.cargo','.claude','.config','.gnupg','.ssh', …]
open(HOST_PATH,'w') SUCCEEDED — the file exists on the host afterwards
listdir('/proc')    host PIDs
```

The PID namespace still holds for *signalling* (a host PID is not in the worker's namespace,
so `kill()` is `ESRCH`) and the network namespace still holds, so this is a filesystem
escape, not a full host takeover. But the filesystem is where Decima keeps everything that
matters: the Weft SQLite database, the keystore, `~/.ssh`.

**Blast radius depends on the parent's uid, and only on that.** The escape mechanism itself
does not: `chroot()` checks `ns_capable(current_user_ns(), CAP_SYS_CHROOT)`, which is true
for any unprivileged parent. What changes is what the escaped worker may then *do* to files:
the single-entry `uid_map` maps namespace-uid 0 to the parent's real uid, so the worker acts
on the host filesystem as the Decima user. On this box the parent runs as uid 0, so the
escaped worker had real root and wrote a file owned by root. Under an unprivileged service
account it would instead have exactly the Decima user's authority — which still includes the
Weft, the keystore, and `~/.ssh`. **For Decima's threat model there is no configuration in
which this is contained.** The `uid_map` is not a mitigation; it only decides whose files.

Three adversarial tests assert precisely what this defeats, and pass today only because no
test attempts a second `chroot`:

- `test_worker_cannot_read_dot_ssh`
- `test_worker_cannot_read_etc_passwd_by_absolute_path`
- `test_weft_db_access_attempt_from_worker_fails` (`tests/adversarial/test_workspace_containment.py`)

They are not vacuous — they test a real refusal on a real path — but the property they claim
("the worker cannot reach host files") is false, and the positive control they lack is a
hostile effect that tries to *leave* rather than to *reach through*.

### 4.4 Escape B — the inherited `O_PATH` workspace fd, `WORKSPACE` only

`_spawn` opens an `O_PATH | O_DIRECTORY` fd on the declared host subtree and holds it open
for the child's whole lifetime, deliberately: pinning the inode is what makes the child's
`stat`-vs-`fstat` swap detector a real identity check. The fd number is passed as `argv[4]`
and added to `allowed_fds`. It is never closed before the untrusted implementation runs, and
`sys.argv` is visible to that implementation.

An `O_PATH` fd cannot be `fchdir`'d, but it *is* a valid `dirfd` for `openat`. So it is a
handle on a directory that lives outside the chroot **and outside the hardened bind mount**:

```python
ws_fd = int(sys.argv[4])
os.open('file.txt', os.O_WRONLY | os.O_TRUNC, dir_fd=ws_fd)   # writes
os.open('../..',    os.O_RDONLY | os.O_DIRECTORY, dir_fd=ws_fd)  # walks above the root
```

Observed against a **read-only** `WORKSPACE` mount whose posture read back correctly as
`{'nosuid': True, 'nodev': True, 'noexec': True, 'rdonly': True}`:

```
write via /workspace   OSError            ← MS_RDONLY works, exactly as documented
write via ws_fd        SUCCEEDED          ← host file content replaced
walk up via ws_fd      ['bin','boot','dev','etc','home', …]   ← above the containment root
```

This is narrower than escape A but sharper, because it defeats two claims that are stated as
proven rather than best-effort:

- the **read-only tier**. `MS_RDONLY` is applied and read back honestly; the fd simply
  references the original mount, where no such flag was ever set. `SECURITY.md`'s
  *"read-only when the tier warrants"* and `WorkspaceMount.read_only`'s *"the child verifies
  the posture with `statvfs` … so 'read-only' is a read-back, never an intention"* are both
  true statements about the mount and false statements about the worker.
- the **containment root**. `mount.py`'s whole argument is that a caveat may only name a
  subtree beneath what the operator conceded. `openat(ws_fd, "../..")` leaves it.

`nosuid`/`nodev`/`noexec` are defeated the same way and for the same reason.

**Seccomp cannot fix this one at all.** A filter sees syscall numbers and register values; it
cannot tell that a `dirfd` argument points outside a chroot. The fix is three lines in
`_spawn`/`_BOOTSTRAP` — close `ws_fd` after `bind_workspace` returns and before the
implementation is exec'd, and drop it from `allowed_fds` at that point — and it belongs in a
containment wave, not this one. It is listed here because it changes what the syscall filter
is *for*.

### 4.5 Would the aarch64 filter have stopped either escape?

No. Checked against the actual `DENY` table:

| syscall the escapes need | arm64 `nr` | in the denylist? |
|---|---|---|
| `chroot` | 51 | **no** |
| `chdir` | 49 | no |
| `mkdirat` | 34 | no |
| `openat` | 56 | no |
| `fchdir` | 50 | no |

So R7's "denylist-only on aarch64" is right about the shape and generous about the
consequence. On aarch64 today the filter denies a well-chosen set of syscalls that a
contained worker could not usefully call anyway, and allows the five that let it leave.

### 4.6 Posture summary an operator can act on

| | aarch64 | x86_64 (incl. this box and CI) |
|---|---|---|
| Env scrub, cwd jail, fd closure, rlimits, no-new-privs, non-dumpable | enforced, read back | same |
| user + mount + net + PID namespaces, chroot | enforced, fail closed | same |
| Workspace bind posture (`nosuid`/`nodev`/`noexec`/`MS_RDONLY`) | enforced, read back | same |
| Syscall filter | 32-entry denylist, default-allow | **absent** |
| Reaches host files by escaping the chroot (§4.3) | **yes** | **yes** |
| Writes a read-only workspace / above the containment root (§4.4) | **yes**, `WORKSPACE` | **yes**, `WORKSPACE` |
| Reaches the network on a network-denied profile | no | no |
| Signals or enumerates host processes via `kill` | no | no |

The operator-facing sentence: **the difference between architectures is real but second
order.** On both, a worker that tries to leave, leaves. Deploying on aarch64 rather than
x86_64 buys defense-in-depth against a worker that misbehaves without trying to escape; it
does not buy containment against one that does.

---

## 5. Question 2 — is default-DENY achievable, and what does it cost?

**Yes, and more cheaply than it would be in a general-purpose sandbox** — because of where
the filter sits in the bootstrap. `install_seccomp` runs *last*: after CPython has fully
started, after the namespaces, after the chroot, after the reaper fork, after `apply_rlimits`
and the `prctl`s, and immediately before the manifest handoff and
`exec(compile(cfg["implementation"]))`. The allowlist therefore has to cover only the
*steady-state* syscalls of an already-running interpreter executing pure Python — not
interpreter startup, not dynamic linking, not module loading from disk (impossible anyway,
§4.1).

### 5.1 Measured footprint

`strace -ff` against real `run_worker` spawns on this host, counting only the effect-runner
child (PID 1 of the new namespace) and only the phase after the manifest write, i.e. exactly
where a filter installed at `install_seccomp` would apply:

| Effect | distinct syscalls after the handoff | total calls |
|---|---|---|
| arithmetic loop + `str.upper` | **4** — `close`, `exit_group`, `mmap`, `write` | 8 |
| the same plus a failed `import hashlib` | **7** — adds `brk`, `newfstatat`, `openat` | 15 |
| list comprehension over 120k ints, `json.dumps`, write + read back an 850 KB file in the jail, `os.stat`, a caught `ZeroDivisionError`, 8 attempted imports, a sort | **13** — `brk`, `close`, `exit_group`, `fstat`, `ioctl`, `lseek`, `mmap`, `mremap`, `munmap`, `newfstatat`, `openat`, `read`, `write` | 116 |

For comparison, the whole bootstrap process including CPython startup uses 44 distinct
syscalls across 1033 calls — that is the number a filter installed *early* would have to
cover, and it is why installing last is the right call.

### 5.2 What a `PURE` allowlist needs

The measured 13, plus the ones that are absent from a short deterministic run but that any
longer-lived or unluckier Python will reach. A defensible starting allowlist, ~25 entries:

- **memory:** `mmap`, `munmap`, `mremap`, `brk`, `madvise`, `mprotect`
- **file I/O in the jail:** `openat`, `read`, `write`, `close`, `lseek`, `fstat`,
  `newfstatat`, `getdents64`, `ftruncate`, `unlinkat`, `mkdirat`, `fcntl`, `ioctl`
  (CPython probes `TCGETS` on stdio), `dup`/`dup3`
- **signals and threading primitives CPython touches:** `rt_sigaction`, `rt_sigprocmask`,
  `rt_sigreturn`, `sigaltstack`, `futex`, `sched_yield`, `clock_gettime`
  (usually vDSO, sometimes not), `getrandom` (hash seed / `os.urandom`)
- **exit:** `exit_group`, `exit`
- Explicitly **not** on it: `socket`, `connect`, `execve`, `clone`, `clone3`, `fork`,
  `ptrace`, `chroot`, `pivot_root`, `mount`, `unshare`, `setns`, `prctl`, `seccomp`,
  `kill`, `openat2` — every one of which is either an escape primitive or unnecessary.

Note what that list does: `chroot` and `clone` being *absent* closes escape A structurally
rather than by enumeration, which is the entire argument for allowlists over denylists.
Escape B stays open (§4.4) — no filter closes it.

### 5.3 The costs, stated plainly

1. **A miss is an outage, not a leak.** With `SECCOMP_RET_ERRNO(EPERM)` an unlisted syscall
   returns `EPERM` and the effect probably raises — a `FAILED` receipt for a correct organ.
   With `SECCOMP_RET_KILL_*` it is a signal death, which `run_worker` maps to `UNKNOWN`. Both
   are worse failure modes than a denylist's, and both are *nondeterministic across hosts*:
   a kernel or libc that routes an operation differently changes the footprint.
2. **Per-arch tables, doubled.** Today one table exists for one arch. Default-deny needs a
   correct allowlist per supported arch — `AUDIT_ARCH_X86_64 = 0xC000003E` plus the x86_64
   numbers (`chroot` 161, `unshare` 272, `ptrace` 101, `mount` 165, `bpf` 321 … — see
   `/usr/include/x86_64-linux-gnu/asm/unistd_64.h`), and on x86_64 also the legacy
   duplicates (`open`, `stat`, `fork`, `dup2`, `select`) and the x32 ABI (`nr | 0x40000000`),
   which the arm64 asm-generic table simply does not have. A generated table checked into the
   repo, derived from the kernel headers, is the only maintainable form.
3. **A linear `JEQ` chain gets slower.** 32 entries is fine; ~25 allow-entries plus a
   deny-tail is also fine. If the table grows past ~64, a binary search over `nr` (the
   standard libseccomp shape) becomes worth writing. Not a concern at this size.
4. **Determinism and replay.** The filter is already excluded from
   `reckoner.environment_digest` and `executor`'s recorded environment for exactly the right
   reason (`decima/services/nona/executor.py` ~500: recording it would make a byte-identical
   replay differ between an aarch64 and an x86_64 host). An allowlist must stay excluded on
   the same grounds — and this is what makes "refuse on hosts without it" (§6) attractive:
   if the layer is mandatory everywhere, it is no longer a host-varying fact at all.
5. **Deriving the list needs a log mode, and `prctl` cannot give one.** `PR_SET_SECCOMP`
   takes no flags: no `SECCOMP_FILTER_FLAG_LOG`, no `SECCOMP_RET_LOG`, no `TSYNC`, no
   `SECCOMP_RET_KILL_PROCESS`. Moving to the `seccomp(2)` syscall (also via `ctypes`, no new
   dependency) is a precondition for the shadow-mode phase in §8 — which is the only
   responsible way to discover the true allowlist across the organs Nona generates.

---

## 6. Question 3 — refuse, warn, or run?

### 6.1 What the code does now, and what it does elsewhere

`profiles.py` carries `namespaces_mandatory: bool` with a docstring that states the
principle outright:

> *"if the requested namespace layers cannot engage on the host, fail closed (True) instead
> of running with a weaker guarantee (False). Honest degradation is chosen at
> profile-definition time, never silently at runtime."*

`PURE`, `WORKSPACE`, and `PROVIDER` all set it `True`, and the in-child gate is real:
`if cfg["namespaces_mandatory"] and not iso.get("engaged"): fatal(...)`. Separately,
`run_worker` refuses *every* network-permitted profile at the primitive because the egress
mediation seam is unwired — a whole profile made unreachable rather than shipped
half-contained.

The syscall filter has **no profile field at all**. It is best-effort by omission: there is
no `syscall_filter_mandatory` to set, so no profile can express "I require this", and the
in-child code has no gate to consult. The result is that the same category of event — a
declared containment layer that cannot engage on this host — produces a refusal for
namespaces and a logged shrug for seccomp.

### 6.2 Is the inconsistency deliberate?

**Partly, and the deliberate part is smaller than it looks.** The genuinely deliberate
decision is the one CHANGELOG records under *"seccomp filter is architecture-aware"*: the
filter used to hard-code aarch64 constants and would have killed every worker at its first
syscall on x86_64, and the fix was to skip it there. Given a filter that only exists for one
arch, skipping beats killing — that reasoning is sound and the honest reporting around it
(the `gap` row, the `warnings` entry, the manifest `detail`) is genuinely good work.

What was *not* decided is the question that fix left behind: **should a host on which the
filter cannot engage be allowed to run workers at all?** That was never put to an owner. It
was inherited from a bug fix, and the layer's "best-effort" label has been carrying it ever
since. The tell is that the label is attached to the *mechanism* ("seccomp is
defense-in-depth") rather than derived from a *profile declaration*, which is the pattern
every other layer here follows.

### 6.3 Recommendation

**Refuse — but only once the filter is worth requiring, and only per profile.**

Sequencing matters, because refusing today would be theatre. Today the filter denies 32
syscalls that a contained worker cannot usefully call and allows the five it needs to escape
(§4.5); making that mandatory would strand every x86_64 operator in exchange for no real
containment. So:

- **Now (with the escapes still open):** keep best-effort, and fix the *claims* (§7). An
  operator's decision today should be driven by §4.6, not by which arch they are on.
- **After a default-deny allowlist exists on both arches:** add
  `syscall_filter_mandatory: bool` to `WorkerProfile`, set it `True` on `PURE` and
  `WORKSPACE`, and gate it in `_BOOTSTRAP` next to the namespace gate, with a distinct fatal
  message so a refusal names the real cause. At that point the layer is load-bearing and the
  argument that carried `namespaces_mandatory` applies unchanged.
- **`warn` is the wrong middle.** `containment_report()`'s `warnings` list already exists and
  is already emitted for the `PROVIDER`-on-unfiltered-arch case, and nothing in the product
  reads it before dispatching. A warning that no code path consults is documentation with a
  worse type signature.
- **Keep an operator escape hatch, explicitly.** An unsupported-arch bring-up needs a way in.
  It should be an operator-supplied deployment fact in the same shape as
  `workspace_root` — never a profile default, never inferred from `os.uname()` — and the
  manifest must record that it was used, so a receipt from a degraded host is
  distinguishable forever.

---

## 7. Question 4 — what currently overstates this, and the corrected wording

Corrections marked **[fixed here]** are made in the same commit as this document, because
they are claims about security posture rather than design preferences. Corrections marked
**[needs a code wave]** cannot be fixed by rewording — the claim is false because the code
is wrong, and honest text is the interim measure.

### 7.1 `SECURITY.md` — the seccomp residual **[fixed here]**

> **The seccomp syscall filter is aarch64-only** (`syscall_filter`, enforced only on
> aarch64). It is best-effort defense-in-depth: on any other architecture it is skipped and
> the worker still runs.

Two overstatements. It names the arch caveat as *the* caveat, and "the seccomp syscall
filter" invites an operator to read the aarch64 case as *contained*. It is a 32-entry
denylist over a default-allow program; an operator who knows only this line will
over-estimate what choosing aarch64 buys. Corrected wording: state the shape (default-allow
denylist, 32 entries), name what is *not* denied, and say plainly that the arch difference
is second order.

### 7.2 `SECURITY.md` — the containment residual list is missing the two escapes **[fixed here / needs a code wave]**

The section is introduced as *"what the jail an authorized effect runs inside actually
enforces"* and enumerates three residuals. §4.3 and §4.4 are both larger than any of them
and neither is listed, so the list currently reads as exhaustive and is not. Two new named
residuals are added, each pointing at §4.3/§4.4 here. The underlying defects need a
containment wave.

### 7.3 `docs/architecture/worker-containment.md` — false host claims **[fixed here]**

> - **Requires** unprivileged Linux **user + mount + network** namespaces. Verified on this
>   aarch64 host (`unprivileged_userns_clone` enabled).

and

> On this host it engages (verified); `containment_report()` reports it as a `gap` elsewhere

Both are false on this x86_64 box — the box CI runs on. The second is the more damaging: a
reader is told the filter is verified engaged here when every spawn on this host skips it.
Corrected to name the arch as a property of *a* host rather than *this* one, and to point at
the manifest as the only authority on whether the filter engaged.

### 7.4 `docs/architecture/worker-containment.md` — the filesystem-isolation row **[fixed here / needs a code wave]**

The `Filesystem isolation` row lists `test_worker_cannot_read_dot_ssh` and
`test_worker_cannot_read_etc_passwd_by_absolute_path` as its adversarial proof, and the
`Workspace bind-mount` row claims a posture read-back as proof of the read-only tier. Both
are true about what the tests assert and false about the property a reader will infer. Both
rows get an explicit residual pointer.

### 7.5 `decima/workers/__init__.py`, `execution.py`, `mount.py` docstrings **[fixed here]**

- `__init__.py`: *"and — on this **aarch64 Linux box** — real Linux namespace isolation"*
- `execution.py`: *"STRONGEST-AVAILABLE OS isolation, per the profile (this **aarch64 box**
  supports it …)"*
- `mount.py`: *"A symlink INSIDE the bound subtree is deliberately NOT refused here: **it
  cannot escape**, because the worker reads it from inside a chroot where … a relative
  `../..` walk dead-ends at `/`."*

The first two are false on this host. The third is true about symlink resolution and
misleading as a containment claim, since the chroot it relies on is escapable by other means
— corrected to attribute the guarantee to the chroot *and* to name the residual.

### 7.6 Test-module docstrings **[fixed here]**

`tests/adversarial/test_worker_isolation.py`, `test_workspace_containment.py`, and
`test_containment_matrix.py` all open with *"These run for real on this aarch64 Linux box."*
They run on x86_64 here, where `test_seccomp_filter_is_installed_and_denies_a_syscall` takes
its non-aarch64 branch. Corrected to describe the host as Linux-with-namespaces and to note
the arch-conditional branch.

### 7.7 Checked and **not** overstating

- **`README.md`** makes no seccomp or arch claim. Its strongest statement is *"runs ONLY
  inside a jailed, networkless `decima.workers` child (no push, no credential …)"* — the
  networkless part is true and enforced; "jailed" is weakened by §4.3 but the README is not
  where that residual belongs. Left alone. (Its test-count line is also owned by the
  reconciling agent and was not touched.)
- **`containment_report()`'s `warnings` entry** for `PROVIDER`-on-unfiltered-arch is accurate
  and correctly scoped. Left alone.
- **`docs/design/rust-port.md`** §2 and its risk table already describe the filter as
  *"aarch64-only … records `seccomp ABSENT` elsewhere rather than failing closed"*, with a
  P7 exit criterion that the caveat is gone. Accurate. Left alone.
- **`docs/design/nona-self-extension.md`** §4.1 and §5.7 say *"a best-effort aarch64 seccomp
  filter"* and *"the isolation manifest is host-dependent — the seccomp layer is
  aarch64-only"*. Accurate as far as they go. Left alone.

---

## 8. Phased plan

Each phase is independently landable and independently reviewable. **S0 is not part of this
lane** but is sequenced first because the rest is not worth doing before it.

| Phase | Scope | Exit criterion |
|---|---|---|
| **S0 — close the escapes** *(prerequisite, separate wave)* | Replace `chroot` with `pivot_root` into the scratch mount, or drop `CAP_SYS_CHROOT` after the jail is built. Close `ws_fd` before the implementation is exec'd and remove it from `allowed_fds` at that point. | A hostile effect that attempts §4.3 and §4.4 fails, asserted by new adversarial tests **that go red when the fix is reverted**. The three tests in §4.3 keep passing, now for the right reason. |
| **S1 — shadow mode** | Move from `PR_SET_SECCOMP` to `seccomp(2)` via `ctypes`. Add a log-only filter (`SECCOMP_RET_LOG` / `SECCOMP_FILTER_FLAG_LOG`) that denies nothing and records the syscalls a worker actually makes. Run it over the whole adversarial + Nona suite and over generated organs. | An empirically derived allowlist per arch, checked in as generated data, with the derivation script and the corpus it was derived from. Zero behavior change to any worker. |
| **S2 — x86_64 tables** | Generate per-arch tables from kernel headers (`AUDIT_ARCH_X86_64`, x86_64 numbers, legacy duplicates, x32 handling). Extend the BPF arch guard to accept a *set* of arches, keeping `KILL` for anything unlisted. | The existing 32-entry denylist engages on x86_64 with `seccomp.engaged=True`; `containment_report` claims the row on both arches; the matrix test asserts the live manifest on whichever arch it runs. |
| **S3 — flip to default-deny** | Replace the deny chain with the S1 allowlist plus a deny-tail. Keep `ERRNO(EPERM)` initially, not `KILL`, so a miss is diagnosable. Report `denied_default: True` and the table digest in the manifest. | The full suite is green on both arches with default-deny; a deliberately removed allowlist entry makes a specific test go red; a hostile effect calling `chroot`/`clone`/`socket` gets `EPERM`. |
| **S4 — make it mandatory** | Add `syscall_filter_mandatory` to `WorkerProfile`, `True` for `PURE`/`WORKSPACE`, gated in `_BOOTSTRAP` beside the namespace gate with its own fatal message. Add the operator override of §6.3, recorded in the manifest. | A host where the filter cannot install refuses the spawn with a message naming the syscall filter; the override is the only way through and is visible in the receipt; `containment_report`'s row becomes outcome-derived rather than arch-derived (fixing §3.1's last row). |

Landing S2 without S3 is explicitly *allowed but discouraged*: it closes R7 as literally
written while changing containment by approximately nothing (§4.5). If it ships alone, the
`syscall_filter` row must not lose its `posture="best_effort"` marker.

---

## 9. Non-goals

- **Landlock.** A real answer to filesystem containment and a much better fit for escape A
  than seccomp, but a different mechanism with a different availability story
  (`ENOSYS` on many kernels — the heartbeat reference already found this). Its own design doc.
- **cgroup v2.** Already an honest, separately-tracked residual (`cgroup_resource_control`).
- **WASM / gVisor / Firecracker.** `specs/CAPABILITY_MAP.md` and `docs/design/rust-port.md`
  own the "real isolation substrate" question. Nothing here forecloses them; a default-deny
  allowlist is the cheap interim, not a competitor.
- **Filtering syscall *arguments*.** Seccomp can inspect scalar registers but cannot deref
  pointers, so it cannot filter by path — and §4.4 shows it cannot filter by `dirfd` location
  either. Any design that needs "`openat` but only under `/workspace`" needs Landlock or
  `openat2(RESOLVE_BENEATH)`, not seccomp.
- **Porting the filter to non-Linux.** The whole worker floor is Linux-namespace-shaped.

---

## 10. Decisions required (owner sign-off)

1. **Does S0 gate this lane?** Recommendation: yes. Escapes A and B are larger than R7, and
   S2 in isolation improves the matrix without improving containment. *Owner may
   reasonably override* if closing R7-as-written unblocks something external — in which case
   S2 must ship with §7's corrections and without dropping `best_effort`.
2. **Default-deny, or a bigger denylist?** Recommendation: default-deny (S3). The measured
   footprint is 13 syscalls (§5.1) and a ~25-entry allowlist closes escape A *structurally*.
   A denylist can only ever enumerate; the previous enumeration missed `chroot`.
3. **`EPERM` or `KILL` on a denied syscall?** Recommendation: `ERRNO(EPERM)` through S3, then
   revisit. `EPERM` gives a `FAILED` receipt with a diagnosable error;
   `KILL` gives `UNKNOWN`, which is correct but tells an operator nothing. Note `KILL_PROCESS`
   requires the move to `seccomp(2)` in S1 regardless.
4. **Refuse on a host where the filter cannot engage?** Recommendation: yes, at S4 and not
   before (§6.3), via a per-profile `syscall_filter_mandatory` rather than a global switch —
   matching how `namespaces_mandatory` is expressed. **This is the R7 question proper, and it
   has never been put to an owner**; the current best-effort posture is inherited from a bug
   fix, not chosen.
5. **Is an operator override acceptable at S4, and in what shape?** Recommendation: yes, as a
   caller-supplied deployment fact (the `workspace_root` pattern), defaulting to
   "concede nothing", and recorded in the manifest so a degraded receipt stays identifiable.
   *This is the decision most likely to be wrong in the permissive direction* — an override
   that is easy to set is an override that ends up in a default config.
6. **Which arches does Decima claim to support?** S2's cost is per-arch tables forever.
   Committing to {aarch64, x86_64} and refusing everything else is cheaper and more honest
   than an open-ended promise. Recommendation: name the two, refuse the rest at S4.

---

## 11. Risks

- **The allowlist is derived from a corpus, and the corpus is not the future.** Nona
  generates organs; S1's corpus cannot contain organs that do not exist yet. Mitigation:
  keep shadow mode available behind the operator override, and treat an `EPERM` on an
  unlisted syscall as a first-class diagnostic in the receipt rather than a generic `FAILED`.
- **CPython, libc, and kernel version drift move the footprint.** A glibc that starts routing
  an operation through a different syscall turns a green suite red on upgrade. Mitigation:
  generate the tables, pin the derivation, and make the drift a loud test failure rather than
  a worker failure — the containment-matrix lane is the right place.
- **S2 alone is a posture regression disguised as progress.** Covered in §8; the mitigation
  is the sign-off in decision 1.
- **Fixing escape A may cost a real capability.** `pivot_root` needs a mount point rather
  than a plain directory, and dropping `CAP_SYS_CHROOT` post-jail interacts with the
  `WORKSPACE` bind ordering. There is a live chance S0 is harder than it reads here. It is
  still the right first phase.
- **Writing this down raises exploitability before the fix lands.** §4.3 and §4.4 are
  reproducible from this document. The counter-argument is the repo's own standard: an
  unreplayable or overstated containment claim is a defect, and SECURITY.md's residual list
  exists precisely so an operator can decide with real information. The mitigation is
  sequencing S0 first, not withholding §4.
