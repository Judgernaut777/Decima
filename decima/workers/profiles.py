"""Worker profiles — the containment shape a given class of effect runs under.

A profile is DATA (it mints no authority): it only declares which confinement layers a
worker of that class must run behind. `execution.run_worker` reads a profile and applies
the layers; the honest in-child manifest reports which actually engaged.

PURE is the floor: no network, a chroot filesystem jail rooted at the scratch dir (no
home, no host filesystem, no secrets), and — because this aarch64 box supports Linux
user/mount/network namespaces — those namespace layers are MANDATORY, so a PURE worker
that cannot engage them fails closed rather than running degraded. WORKSPACE and PROVIDER
are noted here as STRUCTURE: their extra seams (a bind-mounted workspace subtree, mediated
network egress) are deliberately not wired in this phase, and the profiles say so honestly.
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
    """

    name: str
    network: bool
    filesystem_jail: bool
    namespaces_mandatory: bool
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
    note=(
        "STRUCTURE (not yet wired): like PURE, but a declared workspace subtree would be "
        "bind-mounted beneath the jail before the chroot so the worker reads/writes only "
        "that subtree. Until the bind-mount seam lands, WORKSPACE behaves as PURE."
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
