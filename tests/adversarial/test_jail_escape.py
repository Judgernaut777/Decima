"""Wave S0: the two escapes that were real, as permanent regression tests.

Every other test in this directory asks whether a worker can reach THROUGH the jail — read an
absolute host path, resolve a symlink out, open the Weft. All of them passed while the jail was
escapable, because none of them tried to LEAVE. That gap is the lesson this file exists to
keep: a containment test that only probes forbidden paths measures the path list, not the
boundary.

ESCAPE A — the classic double-`chroot`. `chroot()` does not move the caller's cwd, and a task
that is root in its own user namespace holds `CAP_SYS_CHROOT` unconditionally. So
`mkdir e; chroot e; chdir ../../..; chroot .` left the cwd above the new root and walked out.
Measured before the fix, from a PURE worker, effect reporting SUCCEEDED:

    listdir('/')        ['bin','boot','dev','etc','home','lib', ...]
    open('/etc/passwd') 'root:x:0:0:root:/root:/bin/bash...'

The fix is NOT a prohibition on the second chroot — it is `pivot_root` plus
`umount2(MNT_DETACH)`, after which the host tree is not present in the worker's mount
namespace at all. So the hostile sequence still RUNS, and every path it can name resolves
inside the jail. That distinction is the point of `test_the_escape_sequence_still_runs_and_
lands_nowhere`: a test asserting the syscall fails would pin the wrong mechanism and would go
green for the wrong reason if someone swapped the fix for a seccomp deny (which is absent on
x86_64 anyway).

ESCAPE B — the inherited `O_PATH` pin fd. The parent opens an `O_PATH|O_DIRECTORY` fd on the
declared subtree so the child can prove the mounted inode is the one the parent verified
(`stat` vs `fstat`). It was then left open across the effect, and its number is readable from
`sys.argv[4]`. It cannot be `fchdir`'d, but it is a valid `openat` dirfd against the ORIGINAL
mount — so it defeated a read-only workspace and reached above the containment root. No
syscall filter can fix that: seccomp cannot tell where a dirfd points. The fd is now closed
before any untrusted byte runs.
"""

from __future__ import annotations

import os
import pathlib

from tests.adversarial.test_worker_isolation import SUCCEEDED, _run

# ── escape A ─────────────────────────────────────────────────────────────────
_DOUBLE_CHROOT = (
    "def go(x):\n"
    "    import os\n"
    "    out = {}\n"
    "    try:\n"
    "        os.mkdir('esc')\n"
    "        os.chroot('esc')\n"
    "    except OSError as e:\n"
    "        out['chroot1'] = type(e).__name__\n"
    "        return out\n"
    "    out['chroot1'] = 'ok'\n"
    "    try:\n"
    "        for _ in range(60):\n"
    "            os.chdir('..')\n"
    "        os.chroot('.')\n"
    "        out['chroot2'] = 'ok'\n"
    "    except OSError as e:\n"
    "        out['chroot2'] = type(e).__name__\n"
    "    try:\n"
    "        with open('/etc/passwd') as f:\n"
    "            out['READ_ETC_PASSWD'] = f.read(24)\n"
    "    except OSError as e:\n"
    "        out['passwd'] = type(e).__name__\n"
    "    try:\n"
    "        out['root_listing'] = sorted(os.listdir('/'))\n"
    "    except OSError as e:\n"
    "        out['listdir'] = type(e).__name__\n"
    "    return out\n"
)


def _out(source: str, **kw) -> dict:
    resp = _run(source, args={"x": 1}, **kw)
    assert resp.status == SUCCEEDED, f"worker did not complete: {resp.receipt_data}"
    return resp.receipt_data["output"]


def test_the_double_chroot_escape_reaches_no_host_file() -> None:
    """The headline property. Before S0 this returned the first 24 bytes of the host
    `/etc/passwd`."""
    out = _out(_DOUBLE_CHROOT)

    assert "READ_ETC_PASSWD" not in out, f"ESCAPED — read a host file: {out}"
    assert out.get("passwd") == "FileNotFoundError"


def test_the_escape_sequence_still_runs_and_lands_nowhere() -> None:
    """Pins the RIGHT mechanism. `pivot_root` + detach does not forbid the syscalls; it makes
    them point nowhere. Both chroots still succeed and the walk still happens — the worker
    simply arrives back inside its own jail, because the host mounts are gone rather than
    stepped over.

    If someone replaced this fix with a seccomp deny on `chroot`, this test would fail — which
    is the intent. A capability check can be regained and the filter is absent on x86_64;
    "the host tree is not in this mount namespace" cannot be undone from inside."""
    out = _out(_DOUBLE_CHROOT)

    assert out["chroot1"] == "ok", "the fix must not depend on forbidding chroot"
    assert out["chroot2"] == "ok"
    listing = out.get("root_listing")
    assert listing is not None
    # What it sees after re-rooting is its OWN jail — the directory it just made.
    assert listing == ["esc"], f"root listing is not the jail: {listing}"


def test_a_host_path_is_not_writable_after_the_escape_attempt() -> None:
    """The escape's worst outcome was a WRITE. The Weft, the keystore and `~/.ssh` all live on
    the filesystem the worker was reaching, so the write path gets its own assertion rather
    than being inferred from the read one."""
    target = str(pathlib.Path.home() / ".decima-escape-canary")
    src = (
        "def go(target):\n"
        "    import os\n"
        "    try:\n"
        "        os.mkdir('esc'); os.chroot('esc')\n"
        "        for _ in range(60):\n"
        "            os.chdir('..')\n"
        "        os.chroot('.')\n"
        "    except OSError as e:\n"
        "        return {'setup': type(e).__name__}\n"
        "    try:\n"
        "        with open(target, 'w') as f:\n"
        "            f.write('escaped')\n"
        "        return {'WROTE': target}\n"
        "    except OSError as e:\n"
        "        return {'blocked': type(e).__name__}\n"
    )
    resp = _run(src, args={"target": target})

    assert resp.status == SUCCEEDED
    out = resp.receipt_data["output"]
    assert "WROTE" not in out, f"ESCAPED — wrote a host file: {out}"
    assert not os.path.exists(target), "a host file appeared — escape!"


def test_the_manifest_records_the_pivot_rather_than_claiming_a_chroot() -> None:
    """The containment claim has to be derived from what happened, not from what the profile
    asked for — the same discipline as every other read-back in the bootstrap."""
    src = "def go(x):\n    return {'ok': True}\n"
    resp = _run(src, args={"x": 1})

    iso = resp.diagnostics["isolation"]["namespaces"]
    assert iso["engaged"] is True
    assert iso["fs_jail"] is True
    assert iso.get("pivoted") is True, "the jail must report the pivot it actually performed"


def test_an_unknown_architecture_refuses_rather_than_falling_back_to_chroot() -> None:
    """The arch table is the one place this fix could silently degrade. `pivot_root` goes
    through `syscall(2)` with a per-arch number, and a host we have no number for must REFUSE
    — falling back to `chroot` would restore the escape while every containment claim in the
    manifest still read as engaged.

    Asserted against the source rather than by faking an arch, because the branch is inside
    the bootstrap string that runs in the child: what matters is that the fallback does not
    exist to be reached."""
    from decima.workers import execution

    src = execution._BOOTSTRAP
    assert "_PIVOT_ROOT_NR" in src
    assert "no pivot_root syscall number known for arch" in src
    # There is exactly one chroot call left in the bootstrap — inside the hostile-sequence
    # comment is fine, but no `libc.chroot(` may remain as the jail mechanism.
    assert "libc.chroot(" not in src, "chroot must not remain as a jail mechanism"


# ── escape B ─────────────────────────────────────────────────────────────────
def test_the_workspace_pin_fd_is_closed_before_untrusted_code_runs() -> None:
    """Escape B, asserted where it is observable without a workspace: the fd is closed and the
    closure is read back with `fstat`, so a descriptor we believe is gone but is not fails the
    worker closed instead of silently handing the effect a dirfd on the original mount."""
    from decima.workers import execution

    src = execution._BOOTSTRAP
    assert "os.close(ws_fd)" in src
    assert "workspace pin fd still open after close" in src, "the closure must be read back"
    assert "allowed_fds.discard(ws_fd)" in src
