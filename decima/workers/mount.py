"""The declared workspace subtree a WORKSPACE worker may see — and the containment
rule that decides whether it may be bound at all.

A `WorkerProfile` is DATA about a CLASS of worker; it cannot name a host path, because
the path is a fact about one invocation. This module holds the per-invocation half: a
`WorkspaceMount` names exactly one host subtree, the tier's read/write posture, and the
single fixed point inside the jail where it appears. `execution._spawn` binds it; nothing
else in the tree may hand a path to the mount syscall.

WHY A CONTAINMENT ROOT, AND WHY THE CALLER MUST SUPPLY IT. The subtree is chosen by a
CAVEAT — data on a capability Cell, written by whoever built the grant. If a caveat could
name an absolute path, the grant would name its own blast radius and `"/"` would be a
legal workspace. So `resolve_bind_source` takes two arguments with very different trust:
a `containment_root` the OPERATOR declares out of band (a deployment fact, never on the
Log) and a `relative` subpath the caveat may choose freely. The caveat can only ever pick
a point BENEATH what the operator already conceded. An absolute path, a `..` component,
or a symlink that leaves the root are all refused, and the refusal names which rule fired.

WHAT THE SYMLINK RULES ACTUALLY BUY. `os.path.realpath` resolves EVERY component, so a
symlinked root, a symlinked middle component, and a symlinked leaf are all collapsed
before the containment comparison — the check is against where the path really lands, not
where it claims to. `"a..b"` is a legal filename and stays legal: the `..` rule is applied
COMPONENT-wise, never as a substring, because a substring rule that refuses `a..b` teaches
callers to route around it. A symlink INSIDE the bound subtree is deliberately NOT refused
here: it cannot escape, because the worker reads it from inside a chroot where an absolute
target resolves against the jail root and a relative `../..` walk dead-ends at `/`. That
containment is the chroot's, and it is asserted as a test rather than assumed.

WHAT THIS MODULE REFUSES TO DO: decide authority. It mints nothing, reads no Weft, and
answers exactly one question — "is this a path the operator's declared root contains?"
Whether the CALLER may have a workspace at all is settled upstream by the capability
spine, long before a mount is described.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The one point inside the jail where a bound workspace appears. Fixed, not caller-chosen:
# a caller-chosen target is a second path to validate for no benefit, and an organ that
# must be told where its workspace is cannot be written against a stable contract.
JAIL_TARGET = "workspace"


class MountRefused(Exception):
    """A declared workspace subtree was refused before any mount was attempted.

    Always a statement about the PATH (absolute, traversing, escaping, missing, not a
    directory) — never about the caller's authority, which is decided elsewhere."""


@dataclass(frozen=True)
class WorkspaceMount:
    """One host subtree, bound into one worker, for the life of one invocation.

    - `host_root`  — the ALREADY-RESOLVED host directory to bind. Build it with
      :func:`resolve_bind_source`; constructing one by hand bypasses the containment rule
      and is the only way to get a path here that no operator conceded.
    - `read_only`  — bind the subtree read-only (`MS_RDONLY`). The child verifies the
      posture with `statvfs` after the remount and fails closed if it does not hold, so
      "read-only" is a read-back, never an intention.
    - `target`     — the path inside the jail. Fixed at `/workspace`; see `JAIL_TARGET`.

    `MS_NOSUID | MS_NODEV | MS_NOEXEC` are NOT options: they are applied to every bind
    regardless of tier, and verified. There is no workspace for which a setuid bit, a
    device node, or an executable mapping is part of the job.
    """

    host_root: str
    read_only: bool = False
    target: str = JAIL_TARGET

    def __post_init__(self) -> None:
        if not isinstance(self.host_root, str) or not self.host_root:
            raise MountRefused("a workspace mount needs a non-empty host_root")
        if not os.path.isabs(self.host_root):
            raise MountRefused(f"host_root must be an absolute resolved path: {self.host_root!r}")
        if self.target != JAIL_TARGET:
            # The target is fixed so there is exactly one path shape to reason about.
            raise MountRefused(
                f"the jail target is fixed at {JAIL_TARGET!r}; {self.target!r} is not offered"
            )
        if not isinstance(self.read_only, bool):
            raise MountRefused("read_only must be a bool")

    @property
    def jail_path(self) -> str:
        """Where the subtree appears to the confined worker (its cwd, after the chroot)."""
        return "/" + self.target


def resolve_bind_source(containment_root: str, relative: str = ".") -> str:
    """Resolve `relative` beneath `containment_root`, or refuse.

    The ONLY sanctioned way to turn a caveat's subtree name into a path a bind may use.
    Refuses, in this order and with a distinct message each: a non-string or empty name, an
    absolute name, a `..` COMPONENT, a resolved location outside the root, a missing path,
    and a path that is not a directory. Returns the fully resolved absolute directory.

    Note what the ordering buys: the absolute/`..` refusals fire on the LITERAL name, so a
    caller gets told which rule they broke, and the realpath containment check then fires on
    the RESOLVED location, so a name that broke no literal rule but still escapes through a
    symlink is caught anyway. Neither check is sufficient alone.
    """
    if not isinstance(containment_root, str) or not containment_root:
        raise MountRefused("a containment root must be a non-empty string")
    if not isinstance(relative, str) or not relative:
        raise MountRefused("a workspace subtree name must be a non-empty string")
    if os.path.isabs(relative):
        raise MountRefused(
            f"a workspace subtree may not be an absolute path: {relative!r} — the operator's "
            "containment root, not the caveat, decides where a workspace can live"
        )
    parts = relative.replace("\\", "/").split("/")
    if ".." in parts:
        raise MountRefused(f"path traversal is not allowed in a workspace subtree: {relative!r}")

    root = os.path.realpath(containment_root)
    if not os.path.isdir(root):
        raise MountRefused(f"containment root is not an existing directory: {containment_root!r}")
    full = os.path.realpath(os.path.join(root, relative))
    if full != root and not full.startswith(root + os.sep):
        raise MountRefused(
            f"workspace subtree {relative!r} resolves to {full!r}, which escapes the declared "
            f"containment root {root!r} (a symlinked root, middle component, or leaf will land "
            "here — the check is on where the path REALLY goes)"
        )
    if not os.path.exists(full):
        raise MountRefused(f"workspace subtree does not exist: {relative!r} under {root!r}")
    if not os.path.isdir(full):
        raise MountRefused(f"a workspace subtree must be a directory, not a file: {relative!r}")
    return full


def declare_workspace(
    containment_root: str,
    relative: str = ".",
    *,
    read_only: bool = False,
) -> WorkspaceMount:
    """`resolve_bind_source` + `WorkspaceMount` — the one-call path callers should use."""
    return WorkspaceMount(
        host_root=resolve_bind_source(containment_root, relative), read_only=read_only
    )
