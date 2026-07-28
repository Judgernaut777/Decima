"""Worker profiles — the containment shape a given class of effect runs under.

A profile is DATA (it mints no authority): it only declares which confinement layers a
worker of that class must run behind. `execution.run_worker` reads a profile and applies
the layers; the honest in-child manifest reports which actually engaged.

PURE is the floor: no network, a chroot filesystem jail rooted at the scratch dir (no
home, no host filesystem, no secrets), and — because this box supports Linux
user/mount/network namespaces — those namespace layers are MANDATORY, so a PURE worker
that cannot engage them fails closed rather than running degraded.

WORKSPACE adds exactly one thing on top of that floor, and it is REAL: `workspace_bind`
declares that the profile REQUIRES a caller-declared host subtree to be `MS_BIND`-mounted
inside the mount namespace before the chroot, so the worker reads and writes that subtree
and nothing else. The field is what makes WORKSPACE structurally different from PURE
rather than a rename of it — and because it is REQUIRED, a WORKSPACE dispatch that is
handed no subtree is REFUSED (`IsolationError`) instead of quietly running as PURE. A
profile whose extra seam is optional is a profile that silently is not there.

PROVIDER is still STRUCTURE: its extra seam (mediated, redacted network egress) is
deliberately not wired, `run_worker` refuses every network-permitted profile at the
primitive, and the profile says so honestly instead of implying a containment it lacks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerProfile:
    """The confinement contract for a class of worker.

    - `network`             — may the worker reach the network. False ⇒ a network
      namespace is requested so the child has no route out.
    - `filesystem_jail`     — chroot the child into its scratch dir (no host filesystem,
      no home). Requires a user + mount namespace.
    - `namespaces_mandatory`— if the requested namespace layers cannot engage on the host,
      fail closed (True) instead of running with a weaker guarantee (False). Honest
      degradation is chosen at profile-definition time, never silently at runtime.
    - `syscall_filter_mandatory` — if the seccomp allowlist cannot install on this host,
      fail closed (True) instead of running behind the namespace floor alone (S4). True for
      every profile that runs untrusted code. The operator override
      `DECIMA_ALLOW_UNFILTERED_WORKER=1` is the only way through and is recorded on the
      manifest, so a receipt always says whether a worker ran filtered.
    - `workspace_bind`      — the worker REQUIRES a caller-declared host subtree bind-
      mounted at `/workspace` inside the jail (see `decima.workers.mount`). Required, not
      permitted: a dispatch under such a profile with no declared subtree is refused, so
      the profile can never decay into the one below it.
    """

    name: str
    network: bool
    filesystem_jail: bool
    namespaces_mandatory: bool
    syscall_filter_mandatory: bool = True
    workspace_bind: bool = False
    note: str = ""


PURE = WorkerProfile(
    name="pure",
    network=False,
    filesystem_jail=True,
    namespaces_mandatory=True,
    note=(
        "No network, no home, no secrets: a user+mount namespace chroots the worker into "
        "its scratch jail and a network namespace removes every route out. Mandatory on "
        "this box; on a host without user namespaces a PURE worker refuses to run."
    ),
)

WORKSPACE = WorkerProfile(
    name="workspace",
    network=False,
    filesystem_jail=True,
    namespaces_mandatory=True,
    workspace_bind=True,
    note=(
        "Everything PURE enforces, plus ONE declared host subtree MS_BIND-mounted at "
        "/workspace inside the mount namespace before the chroot (nosuid, nodev, noexec "
        "always; read-only when the tier warrants). The bind is the worker's cwd, so a "
        "write it makes is a write on the host — inside that subtree and nowhere else. "
        "The child re-verifies the mounted inode against the fd the parent pinned and "
        "fails closed on a mismatch, so the path cannot be swapped under the mount. A "
        "WORKSPACE dispatch with no declared subtree is REFUSED, never downgraded to PURE."
    ),
)

PROVIDER = WorkerProfile(
    name="provider",
    network=True,
    filesystem_jail=True,
    namespaces_mandatory=True,
    note=(
        "STRUCTURE (not yet wired): a worker permitted to make ONE outbound provider call. "
        "Network is allowed (no network namespace); egress must still pass a separate "
        "mediation/redaction seam that this phase does not implement. Filesystem stays "
        "jailed. Do not route real provider traffic through this until egress is wired."
    ),
)

PROFILES: dict[str, WorkerProfile] = {p.name: p for p in (PURE, WORKSPACE, PROVIDER)}
