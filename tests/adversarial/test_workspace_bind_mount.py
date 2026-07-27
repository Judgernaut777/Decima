"""The WORKSPACE bind-mount seam, attacked.

A profile that claims a bind-mounted subtree and does not have one is worse than a profile
that claims nothing: the receipt says "workspace" and the operator believes a containment
boundary exists where there is only a name. So every test here is written to fail if the
mount stops being real, and each refusal is paired with a POSITIVE CONTROL on the same
fixture — a refusal suite that would also pass against a seam that simply never runs is not
evidence of anything.

The four questions, and where each is answered:

  IS THE MOUNT REAL?          A file written from inside the jail is found on the HOST
                              afterwards, and a byte planted on the host is read from
                              inside. A copy-in model passes neither.
  IS IT BOUNDED?              The jail sees the declared subtree and nothing else: not the
                              sibling directory next to it, not the parent that contains it,
                              not `/etc`. A symlink planted INSIDE the subtree pointing out
                              is followed to nowhere, because the chroot resolves it.
  DOES IT FAIL CLOSED?        A WORKSPACE dispatch with no subtree, with a subtree that
                              vanished, and a subtree handed to a profile that binds none
                              are all `IsolationError` — never a quiet PURE-equivalent run.
  CAN IT BE SWAPPED?          The source path is replaced with a different directory in the
                              exact window between the parent pinning it and the child
                              mounting it. The child compares the mounted inode against the
                              pinned fd and refuses. The same shim WITHOUT the swap is the
                              positive control, so the refusal is the detector firing and
                              not the shim breaking the run.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from decima.workers import (
    PURE,
    WORKSPACE,
    IsolationError,
    MountRefused,
    WorkerRequest,
    WorkspaceMount,
    compute_digest,
    declare_workspace,
    resolve_bind_source,
    run_worker,
)
from decima.workers.protocol import SUCCEEDED

SECURITY_MD = pathlib.Path(__file__).resolve().parents[2] / "SECURITY.md"


def _lease() -> dict:
    return {
        "step_id": "s-bind",
        "worker": "w-bind",
        "capability_ids": [],
        "issued_frontier": 0,
        "expiry": 100,
        "attempt": 1,
        "idempotency_key": "idem-bind",
    }


def _run(src: str, *, profile=WORKSPACE, workspace=None, args=None):
    req = WorkerRequest(
        invocation_id="inv-bind",
        job_id="job-bind",
        effect="workspace_effect",
        implementation_digest=compute_digest(src),
        arguments=args or {},
        lease=_lease(),
        capability_proof={"grant_id": "g-bind"},
    )
    return run_worker(req, src, "go", now=0, profile=profile, workspace=workspace)


# The organ under test: it writes, it looks around, and it tries to leave.
_PROBE = """
def go(probe):
    import os
    with open("written-inside.txt", "w") as handle:
        handle.write("the worker was here")
    try:
        with open("seed.txt") as handle:
            seen = handle.read()
    except OSError as exc:
        seen = "unreadable: %s" % exc
    escaped = []
    for path in probe:
        try:
            with open(path) as handle:
                handle.read(1)
            escaped.append(path)
        except OSError:
            pass
    return {"cwd": os.getcwd(), "listing": sorted(os.listdir(".")),
            "seed": seen, "escaped": escaped}
"""


@pytest.fixture
def subtree(tmp_path):
    """A declared subtree, a SIBLING beside it, and a secret in the parent.

    The siblings exist so "the jail sees only the subtree" is a claim with something to be
    false about: a bind of the parent, or no bind at all with a chroot on the parent, would
    make one of them visible."""
    root = tmp_path / "declared"
    root.mkdir()
    (root / "seed.txt").write_text("planted-on-the-host", encoding="utf-8")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "not-yours.txt").write_text("must never be visible", encoding="utf-8")
    (tmp_path / "parent-secret.txt").write_text("must never be visible", encoding="utf-8")
    return root


# ── IS THE MOUNT REAL? ────────────────────────────────────────────────────────
def test_a_write_inside_the_jail_lands_on_the_host_subtree(subtree):
    """The load-bearing property. A copy-in model cannot pass this: it has no path by which
    a byte written in the child reaches the parent's filesystem."""
    resp = _run(_PROBE, workspace=declare_workspace(str(subtree)), args={"probe": []})
    assert resp.status == SUCCEEDED, resp.diagnostics

    landed = subtree / "written-inside.txt"
    assert landed.exists(), (
        "the worker's write did not reach the host — the 'bind mount' is a copy, "
        "or the jail cwd is not the bound subtree"
    )
    assert landed.read_text(encoding="utf-8") == "the worker was here"

    out = resp.receipt_data["output"]
    # ...and the reverse direction: a byte planted on the host BEFORE the run was read
    # inside it. Both directions have to hold or it is not one filesystem.
    assert out["seed"] == "planted-on-the-host"
    assert out["cwd"] == "/workspace"


def test_the_manifest_reports_the_bind_it_actually_performed(subtree):
    resp = _run(_PROBE, workspace=declare_workspace(str(subtree)), args={"probe": []})
    bind = resp.diagnostics["isolation"]["workspace_bind"]
    assert bind["engaged"] is True
    assert bind["requested"] is True
    # The inode check ran and agreed — not merely "mount() returned 0".
    assert bind["inode_verified"] is True
    # nosuid/nodev/noexec are not options; they are read back from statvfs every time.
    assert bind["posture"] == {"nosuid": True, "nodev": True, "noexec": True, "rdonly": False}


# ── IS IT BOUNDED? ────────────────────────────────────────────────────────────
def test_the_jail_sees_the_declared_subtree_and_nothing_else(subtree):
    parent = subtree.parent
    probe = [
        "/etc/passwd",
        "/etc/shadow",
        str(parent / "parent-secret.txt"),
        str(parent / "sibling" / "not-yours.txt"),
        str(subtree / "seed.txt"),  # the real host path of a file that IS in the subtree
        "../sibling/not-yours.txt",
        "../../etc/passwd",
    ]
    resp = _run(_PROBE, workspace=declare_workspace(str(subtree)), args={"probe": probe})
    assert resp.status == SUCCEEDED
    out = resp.receipt_data["output"]

    assert out["escaped"] == [], f"the worker read outside its subtree: {out['escaped']}"
    # POSITIVE CONTROL: the probe list is not simply unreadable-by-construction — the
    # subtree's own file IS readable from inside, by its in-jail name.
    assert out["seed"] == "planted-on-the-host"
    assert "seed.txt" in out["listing"]
    assert "not-yours.txt" not in out["listing"]


def test_a_symlink_inside_the_subtree_cannot_walk_out_of_the_chroot(subtree):
    """Symlinks inside the bound tree are deliberately NOT scrubbed at mount time. They do
    not need to be: the worker resolves them from inside a chroot, where an absolute target
    is relative to the jail root and a relative walk dead-ends at `/`."""
    (subtree / "escape-abs").symlink_to("/etc/passwd")
    (subtree / "escape-rel").symlink_to("../sibling/not-yours.txt")
    (subtree / "inner").mkdir()
    (subtree / "inner" / "real.txt").write_text("inside", encoding="utf-8")
    (subtree / "friendly").symlink_to("inner/real.txt")

    src = """
def go():
    out = {}
    for name in ("escape-abs", "escape-rel", "friendly"):
        try:
            with open(name) as handle:
                out[name] = handle.read()
        except OSError as exc:
            out[name] = "refused:%s" % type(exc).__name__
    return out
"""
    resp = _run(src, workspace=declare_workspace(str(subtree)))
    assert resp.status == SUCCEEDED
    out = resp.receipt_data["output"]
    assert out["escape-abs"].startswith("refused:")
    assert out["escape-rel"].startswith("refused:")
    # POSITIVE CONTROL: symlinks are not broken in general — one pointing INSIDE resolves.
    assert out["friendly"] == "inside"


def test_a_read_only_mount_refuses_the_write_a_writable_one_allows(subtree):
    """Same fixture, same source, one flag apart — so the refusal is the flag and not the
    worker being unable to write at all."""
    src = """
def go():
    try:
        with open("attempt.txt", "w") as handle:
            handle.write("x")
        return {"wrote": True}
    except OSError as exc:
        return {"wrote": False, "error": type(exc).__name__}
"""
    ro = _run(src, workspace=declare_workspace(str(subtree), read_only=True))
    assert ro.status == SUCCEEDED
    assert ro.receipt_data["output"]["wrote"] is False
    assert not (subtree / "attempt.txt").exists()
    assert ro.diagnostics["isolation"]["workspace_bind"]["posture"]["rdonly"] is True

    rw = _run(src, workspace=declare_workspace(str(subtree), read_only=False))
    assert rw.status == SUCCEEDED
    assert rw.receipt_data["output"]["wrote"] is True, "positive control failed"
    assert (subtree / "attempt.txt").read_text(encoding="utf-8") == "x"


# ── DOES IT FAIL CLOSED? ──────────────────────────────────────────────────────
def test_a_workspace_profile_with_no_subtree_is_refused_not_downgraded(subtree):
    """The finding that made this wave necessary: WORKSPACE must not be PURE in a hat."""
    with pytest.raises(IsolationError, match="requires a declared workspace subtree"):
        _run(_PROBE, workspace=None, args={"probe": []})
    # POSITIVE CONTROL: the same call WITH a subtree runs, so the refusal is the missing
    # mount and not a broken profile.
    assert _run(_PROBE, workspace=declare_workspace(str(subtree)), args={"probe": []}).status == (
        SUCCEEDED
    )


def test_a_subtree_handed_to_a_non_binding_profile_is_refused_not_dropped(subtree):
    """The mirror-image failure: silently discarding a mount a caller asked for would give
    them an empty jail and no error."""
    with pytest.raises(IsolationError, match="binds none"):
        _run(_PROBE, profile=PURE, workspace=declare_workspace(str(subtree)), args={"probe": []})


def test_a_bind_that_cannot_engage_refuses_rather_than_running_degraded(tmp_path):
    """Make the mount IMPOSSIBLE and assert nothing runs.

    The subtree is resolved (so the declaration is legal) and then deleted, so the failure
    happens at the syscall rather than in validation — the path a host problem would take."""
    doomed = tmp_path / "doomed"
    doomed.mkdir()
    mount = declare_workspace(str(doomed))
    doomed.rmdir()

    with pytest.raises(IsolationError) as caught:
        _run(_PROBE, workspace=mount, args={"probe": []})
    # Fail CLOSED and fail LOUD: the refusal names the bind, not some downstream symptom.
    assert "workspace" in str(caught.value).lower()

    # POSITIVE CONTROL: recreate the directory and the identical mount succeeds.
    doomed.mkdir()
    assert _run(_PROBE, workspace=mount, args={"probe": []}).status == SUCCEEDED


# ── CAN IT BE SWAPPED? (TOCTOU) ───────────────────────────────────────────────
def _swapping_popen(monkeypatch, *, swap):
    """Replace `subprocess.Popen` inside the execution module with a shim that runs `swap()`
    at the exact moment the parent has pinned the subtree and the child has not yet mounted
    it — the only window an attacker has.

    `swap=None` installs the SAME shim with no swap, which is the positive control: it
    proves the instrumentation itself does not break the run."""
    import decima.workers.execution as ex

    real = subprocess.Popen

    def shim(*args, **kwargs):
        if swap is not None:
            swap()
        return real(*args, **kwargs)

    monkeypatch.setattr(ex.subprocess, "Popen", shim)


def test_swapping_the_source_between_the_pin_and_the_mount_is_caught(tmp_path, monkeypatch):
    honest = tmp_path / "honest"
    honest.mkdir()
    (honest / "seed.txt").write_text("honest", encoding="utf-8")
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "loot.txt").write_text("swapped in", encoding="utf-8")

    mount = declare_workspace(str(honest))

    def swap() -> None:
        # The declared path now names a DIFFERENT inode. The parent's O_PATH fd still
        # refers to the original, which is precisely what the child compares against.
        os.rename(str(honest), str(tmp_path / "moved-away"))
        os.rename(str(attacker), str(honest))

    _swapping_popen(monkeypatch, swap=swap)
    with pytest.raises(IsolationError) as caught:
        _run(_PROBE, workspace=mount, args={"probe": []})
    assert "swapped" in str(caught.value) or "pinned" in str(caught.value), caught.value
    # The swapped-in tree was never exposed: the attacker's file was not written to.
    assert not (honest / "written-inside.txt").exists()


def test_the_swap_detector_does_not_fire_on_an_unswapped_run(tmp_path, monkeypatch):
    """Positive control for the test above: the identical shim, no swap, must SUCCEED.

    Without this, a bug that made every bind refuse would leave the TOCTOU test green."""
    honest = tmp_path / "honest"
    honest.mkdir()
    (honest / "seed.txt").write_text("honest", encoding="utf-8")

    _swapping_popen(monkeypatch, swap=None)
    resp = _run(_PROBE, workspace=declare_workspace(str(honest)), args={"probe": []})
    assert resp.status == SUCCEEDED
    assert resp.diagnostics["isolation"]["workspace_bind"]["inode_verified"] is True
    assert (honest / "written-inside.txt").exists()


# ── the containment rule on the DECLARATION, before any mount ─────────────────
def test_a_caveat_cannot_name_a_subtree_outside_the_conceded_root(tmp_path):
    root = tmp_path / "conceded"
    (root / "inner" / "deep").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link-out").symlink_to(outside)
    (root / "inner" / "mid").symlink_to(outside)

    refused = [
        "/",  # the caveat that names the whole filesystem
        "/etc",
        "../outside",
        "inner/../../outside",
        "link-out",  # a symlinked LEAF that leaves the root
        "link-out/anything",  # a symlinked MIDDLE component
        "inner/mid",
        "missing",  # not there at all
    ]
    for name in refused:
        with pytest.raises(MountRefused):
            resolve_bind_source(str(root), name)

    # A file is not a subtree.
    (root / "a-file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(MountRefused, match="must be a directory"):
        resolve_bind_source(str(root), "a-file.txt")

    # POSITIVE CONTROLS on the same root: legal names resolve, so the refusals above are
    # the containment rule firing rather than the function refusing everything.
    assert resolve_bind_source(str(root), ".") == os.path.realpath(str(root))
    assert resolve_bind_source(str(root), "inner") == os.path.join(os.path.realpath(root), "inner")
    assert resolve_bind_source(str(root), "inner/deep").endswith("deep")


def test_a_dotdot_rule_applied_to_substrings_would_ban_legal_names(tmp_path):
    """`a..b` is a legal directory name. The traversal rule is COMPONENT-wise for a reason:
    a substring rule refuses honest paths and teaches callers to route around it."""
    root = tmp_path / "conceded"
    (root / "a..b").mkdir(parents=True)
    assert resolve_bind_source(str(root), "a..b").endswith("a..b")
    with pytest.raises(MountRefused, match="traversal"):
        resolve_bind_source(str(root), "a/../../b")


def test_a_symlinked_containment_root_is_resolved_not_trusted(tmp_path):
    """The root itself may be a symlink; containment is compared after resolution, so a
    subtree beneath it is admitted and one beside the REAL location is not."""
    real = tmp_path / "real"
    (real / "inner").mkdir(parents=True)
    link = tmp_path / "via-link"
    link.symlink_to(real)
    assert resolve_bind_source(str(link), "inner") == os.path.join(os.path.realpath(real), "inner")
    with pytest.raises(MountRefused):
        resolve_bind_source(str(link), "../real/../outside")


def test_a_hand_built_mount_still_refuses_an_incoherent_target(tmp_path):
    """`WorkspaceMount` is the last line, not the first: even constructed directly it
    refuses a relative source and a caller-chosen jail target."""
    with pytest.raises(MountRefused, match="absolute"):
        WorkspaceMount(host_root="relative/path")
    with pytest.raises(MountRefused, match="fixed"):
        WorkspaceMount(host_root=str(tmp_path), target="elsewhere")
    assert WorkspaceMount(host_root=str(tmp_path)).jail_path == "/workspace"


# ── the operator-facing record says what the code does ────────────────────────
def test_security_md_records_what_the_bind_does_and_does_not_close():
    text = SECURITY_MD.read_text(encoding="utf-8")
    assert "workspace_bind_mount" in text, "the residual record must name the dimension"
    assert "MS_BIND" in text
    # The two residuals that are still open must stay named in the operator's document.
    for still_open in ("cgroup_resource_control", "egress_mediation"):
        assert still_open in text, f"SECURITY.md dropped the {still_open} residual"
