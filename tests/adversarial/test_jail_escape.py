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


# ── the syscall filter (S2/S3): default-deny, and on this arch at all ─────────
_PROBE = (
    "def go(x):\n"
    "    import ctypes\n"
    "    libc = ctypes.CDLL(None, use_errno=True)\n"
    "    out = {}\n"
    "    for name, nr in x['calls']:\n"
    "        ctypes.set_errno(0)\n"
    "        rc = libc.syscall(nr, 0, 0, 0, 0, 0, 0)\n"
    "        out[name] = [rc, ctypes.get_errno()]\n"
    "    return out\n"
)

# (name, x86_64 nr, aarch64 nr) for syscalls no compute effect needs and every escape wants.
_FORBIDDEN = (
    ("socket", 41, 198),
    ("execve", 59, 221),
    ("clone", 56, 220),
    ("ptrace", 101, 117),
    ("mount", 165, 40),
    ("setns", 308, 268),
    ("bpf", 321, 280),
    ("keyctl", 250, 219),
)


def _probe(names_and_numbers) -> dict:
    """Probe each syscall BY NUMBER for this arch. The index is worth a comment because the
    first version of this helper got it wrong: after `name, x86, arm = row` the numbers are
    0-indexed, so an off-by-one probed aarch64's `setns` (268) on x86_64, where 268 is
    `fchmodat` — an ALLOWED call that returned EFAULT and read as "not refused". Every other
    row passed by coincidence. Hence indexing a named tuple-of-two rather than slicing."""
    import os as _os

    x86 = _os.uname().machine == "x86_64"
    calls = [[name, (nr_x86 if x86 else nr_arm)] for name, nr_x86, nr_arm in names_and_numbers]
    resp = _run(_PROBE, args={"x": {"calls": calls}})
    assert resp.status == SUCCEEDED, resp.receipt_data
    return resp.receipt_data["output"]


def test_the_filter_is_default_deny_and_engages_on_this_architecture() -> None:
    """S2/S3. The old filter was a 32-entry DENYLIST that existed only on aarch64 — so on
    x86_64, which is this box and CI, there was no syscall filtering at all, and the
    containment matrix said so honestly rather than pretending otherwise.

    Porting that denylist across would have turned the matrix row green while changing what a
    hostile effect can do by nothing at all: `chroot`, `chdir`, `openat` and `clone` were never
    on it. So the table was inverted instead of extended."""
    resp = _run("def go(x):\n    return {'ok': True}\n", args={"x": 1})
    sec = resp.diagnostics["isolation"]["seccomp"]

    assert sec["engaged"] is True
    assert sec["policy"] == "default-deny"
    assert sec["action"] == "ERRNO(EPERM)", "a miss must be diagnosable, not a SIGSYS death"
    assert sec["allowed_syscalls"] > 0


def test_the_syscalls_every_escape_needs_are_refused() -> None:
    """The teeth. Each of these is EPERM now and was permitted before — on x86_64 by the
    filter's total absence, on aarch64 by the denylist not naming it."""
    out = _probe(_FORBIDDEN)

    for name, (rc, errno) in out.items():
        assert rc == -1 and errno == 1, f"{name} was NOT refused: rc={rc} errno={errno}"


def test_an_ordinary_effect_still_runs_under_the_filter() -> None:
    """The positive control, and the reason the action is EPERM rather than KILL: an
    allowlist that strands real work is an availability cliff, not a containment layer. This
    exercises file I/O, allocation and serialisation — the shapes a real organ uses."""
    src = (
        "def go(x):\n"
        "    import json, os\n"
        "    os.mkdir('d')\n"
        "    with open('d/f.json', 'w') as f:\n"
        "        json.dump({'n': sum(range(50000))}, f)\n"
        "    with open('d/f.json') as f:\n"
        "        back = json.load(f)\n"
        "    return {'n': back['n'], 'listing': sorted(os.listdir('d'))}\n"
    )
    resp = _run(src, args={"x": 1})

    assert resp.status == SUCCEEDED, resp.receipt_data
    assert resp.receipt_data["output"] == {"n": sum(range(50000)), "listing": ["f.json"]}
