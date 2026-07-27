"""Containment-matrix adversarial tests — the doc, `containment_report()`, and the REAL
in-child isolation manifest are held in lockstep so none can drift from the others.

The pure `containment_report(profile, limits)` enumerates the enforced confinement subset
as data. Every row it marks `enforced=True` with a `manifest_proof` is asserted here against
a manifest produced by a REAL worker run on this aarch64 Linux box — so a claim the code
stops enforcing (or a manifest key that stops engaging) turns a row red instead of silently
passing. Every row it honestly marks `enforced=False` is asserted to be a genuine gap (the
worker itself confirms the layer is absent) and to be documented as a gap in the matrix doc.

These are adversarial: the non-dumpable and fsize rows are proven by the confined worker
attempting the thing the row bounds and being denied; the network/filesystem rows reuse the
real escape attempts in test_worker_isolation.py, cross-referenced from the doc.
"""

from __future__ import annotations

import pathlib

from decima.workers.execution import (
    _seccomp_arch_supported,
    compute_digest,
    containment_report,
    run_worker,
)
from decima.workers.mount import declare_workspace
from decima.workers.profiles import PROVIDER, PURE, WORKSPACE
from decima.workers.protocol import FAILED, SUCCEEDED, WorkerRequest

DOC = pathlib.Path(__file__).resolve().parents[2] / "docs/architecture/worker-containment.md"


def _lease() -> dict:
    return {
        "step_id": "s1",
        "worker": "w1",
        "capability_ids": [],
        "issued_frontier": 0,
        "expiry": 100,
        "attempt": 1,
        "idempotency_key": "idem-cm",
    }


def _run(src: str, *, args: dict | None = None, profile=PURE, limits=None, workspace=None):
    req = WorkerRequest(
        invocation_id="inv-cm",
        job_id="job-cm",
        effect="pure_compute",
        implementation_digest=compute_digest(src),
        arguments=args or {},
        lease=_lease(),
        capability_proof={"grant_id": "g1"},
    )
    return run_worker(req, src, "go", now=0, profile=profile, limits=limits, workspace=workspace)


def _resolve(manifest: dict, dotted: str):
    node = manifest
    for part in dotted.split("."):
        assert isinstance(node, dict) and part in node, f"missing manifest key {dotted!r}"
        node = node[part]
    return node


# ── report shape / purity ──────────────────────────────────────────────────────
def test_containment_report_is_pure_and_stable():
    a = containment_report(PURE)
    b = containment_report(PURE)
    assert a == b  # deterministic, no side effects
    assert a["profile"] == "pure"
    assert a["namespaces_mandatory"] is True
    dims = [d["dimension"] for d in a["dimensions"]]
    assert len(dims) == len(set(dims)), "duplicate dimension rows"


def test_report_reflects_profile_network_posture():
    # PURE / WORKSPACE deny network (netns enforced); PROVIDER permits it (a documented gap).
    for prof in (PURE, WORKSPACE):
        rep = containment_report(prof)
        net = next(d for d in rep["dimensions"] if d["dimension"] == "network_isolation")
        assert net["enforced"] is True
        assert rep["network_permitted"] is False
    prov = containment_report(PROVIDER)
    net = next(d for d in prov["dimensions"] if d["dimension"] == "network_isolation")
    assert net["enforced"] is False and "gap" in net
    assert prov["network_permitted"] is True


def test_mandatory_rows_declare_fail_closed():
    rep = containment_report(PURE)
    for name in ("filesystem_isolation", "user_namespace", "mount_namespace", "network_isolation"):
        row = next(d for d in rep["dimensions"] if d["dimension"] == name)
        assert row["enforced"] is True
        assert row["fail_mode"] == "fail_closed_isolation_error", name


# ── every ENFORCED row with a manifest_proof holds against a REAL worker ─────────
def test_every_enforced_manifest_proof_holds_live():
    resp = _run("def go(x):\n    return {'ok': True}\n", args={"x": 1})
    assert resp.status == SUCCEEDED
    manifest = resp.diagnostics["isolation"]

    checked = 0
    for row in containment_report(PURE)["dimensions"]:
        proof = row.get("manifest_proof")
        if not row["enforced"] or not proof:
            continue
        for dotted, expected in proof.items():
            got = _resolve(manifest, dotted)
            if isinstance(expected, bool):
                # boolean rows must engage exactly; True means the layer is on
                assert got is expected, f"{row['dimension']}: {dotted}={got!r} != {expected!r}"
            elif isinstance(expected, list):
                assert sorted(got) == sorted(expected), row["dimension"]
            else:
                # sentinel True-only presence proofs (dict/list payloads) — key must exist
                assert got is not None, row["dimension"]
            checked += 1
    assert checked >= 8, f"too few live proofs verified ({checked})"


# ── the added NON-DUMPABLE hardening, proven from INSIDE the confined worker ─────
def test_worker_is_non_dumpable():
    # The manifest claims non_dumpable; the worker itself reads PR_GET_DUMPABLE and must
    # see 0 — it cannot be ptrace-attached by a peer and produces no core dump.
    row = next(
        d for d in containment_report(PURE)["dimensions"] if d["dimension"] == "non_dumpable"
    )
    assert row["enforced"] is True and row["manifest_proof"] == {"non_dumpable": True}

    src = (
        "def go(x):\n"
        "    import ctypes\n"
        "    libc = ctypes.CDLL(None)\n"
        "    return {'dumpable': libc.prctl(3, 0, 0, 0, 0)}\n"  # PR_GET_DUMPABLE == 3
    )
    resp = _run(src, args={"x": 1})
    assert resp.status == SUCCEEDED
    assert resp.receipt_data["output"]["dumpable"] == 0, "worker is dumpable — hardening lost"
    assert resp.diagnostics["isolation"]["non_dumpable"] is True


# ── FSIZE rlimit genuinely bounds the file a worker may write ────────────────────
def test_worker_fsize_is_bounded():
    row = next(
        d
        for d in containment_report(PURE, {"fsize": 1 << 16})["dimensions"]
        if d["dimension"] == "resource_limits"
    )
    assert row["detail"]["fsize"] == (1 << 16)

    src = (
        "def go(x):\n"
        "    try:\n"
        "        with open('big.bin', 'wb') as f:\n"
        "            f.write(b'A' * (50 * 1024 * 1024))\n"
        "        import os\n"
        "        return {'wrote': os.path.getsize('big.bin')}\n"
        "    except OSError as e:\n"
        "        return {'blocked': type(e).__name__}\n"
    )
    resp = _run(src, args={"x": 1}, limits={"fsize": 1 << 16})
    # Exceeding RLIMIT_FSIZE denies the write (EFBIG) — the 50 MiB never lands.
    assert resp.status in (SUCCEEDED, FAILED)
    out = resp.receipt_data.get("output") or {}
    assert "wrote" not in out, "worker wrote past its fsize bound — escape!"


# ── the added best-effort SECCOMP filter, proven from INSIDE the confined worker ──
def test_seccomp_filter_is_installed_and_denies_a_syscall():
    # seccomp is aarch64-only (arch-guarded BPF + asm-generic syscall numbers). The report
    # and the live worker must agree on THIS host: on aarch64 the filter engages and a denied
    # syscall returns EPERM; on any other arch both honestly report it skipped (a gap), never
    # a filter that silently never installed.
    row = next(
        d for d in containment_report(PURE)["dimensions"] if d["dimension"] == "syscall_filter"
    )
    assert row["posture"] == "best_effort"

    src = (
        "def go(x):\n"
        "    import ctypes\n"
        "    libc = ctypes.CDLL(None, use_errno=True)\n"
        "    mode = libc.prctl(21, 0, 0, 0, 0)\n"  # PR_GET_SECCOMP == 21
        "    ctypes.set_errno(0)\n"
        "    rc = libc.unshare(0x10000000)\n"  # CLONE_NEWUSER — a denied syscall
        "    return {'seccomp_mode': mode, 'unshare_rc': rc, 'errno': ctypes.get_errno()}\n"
    )
    resp = _run(src, args={"x": 1})
    assert resp.status == SUCCEEDED
    out = resp.receipt_data["output"]

    if _seccomp_arch_supported():
        assert row["enforced"] is True and "gap" not in row
        assert row["manifest_proof"] == {"seccomp.engaged": True}
        assert out["seccomp_mode"] == 2, "seccomp filter not installed — report overclaims"
        # unshare is on the deny-list: it must fail with EPERM (1), never succeed.
        assert out["unshare_rc"] == -1 and out["errno"] == 1, "a denied syscall was NOT blocked"
        assert resp.diagnostics["isolation"]["seccomp"]["engaged"] is True
    else:
        # non-aarch64: the filter is genuinely absent, and the report must say so (gap) —
        # the mandatory namespace/rlimit floors still hold (asserted by the other tests).
        assert row["enforced"] is False and "gap" in row
        assert "manifest_proof" not in row
        assert out["seccomp_mode"] != 2  # no filter installed on this arch
        assert resp.diagnostics["isolation"]["seccomp"]["engaged"] is False


def test_no_enforced_row_is_also_listed_as_a_gap():
    for prof in (PURE, WORKSPACE, PROVIDER):
        for row in containment_report(prof)["dimensions"]:
            if row["enforced"]:
                assert "gap" not in row, (row["dimension"], prof.name)
            else:
                assert "gap" in row and "manifest_proof" not in row, row["dimension"]


# ── the honestly-NOT-enforced rows are PINNED, so the claim cannot drift silently ──
# Before this existed, `enforced` on a gap row was a value no test read: flipping one to True
# without wiring the mechanism left the whole suite green while the report started telling
# operators a layer was on. Each entry below is pinned to the value the CODE can currently
# justify. Wiring a mechanism means inverting its entry IN THE SAME COMMIT — which is exactly
# what `workspace_bind_mount` did, and why it now appears in the enforced table instead.
_STILL_A_GAP: tuple[str, ...] = ("cgroup_resource_control", "egress_mediation")

SECURITY_MD = pathlib.Path(__file__).resolve().parents[2] / "SECURITY.md"


def _row(profile, dimension: str) -> dict:
    return next(d for d in containment_report(profile)["dimensions"] if d["dimension"] == dimension)


def test_the_unwired_layers_are_pinned_as_gaps_for_every_profile():
    for prof in (PURE, WORKSPACE, PROVIDER):
        for dimension in _STILL_A_GAP:
            row = _row(prof, dimension)
            assert row["enforced"] is False, (
                f"{dimension} is reported ENFORCED for {prof.name}. If the mechanism was "
                "really wired, invert this pin in the same commit and prove it live; if it "
                "was not, the report is now lying to operators about containment."
            )
            assert "gap" in row and "manifest_proof" not in row


def test_the_workspace_bind_pin_is_inverted_because_the_seam_is_real():
    """The other half of the rule above: a layer that IS wired must be pinned enforced, and
    must be pinned enforced only where the profile actually requires it."""
    assert _row(WORKSPACE, "workspace_bind_mount")["enforced"] is True
    assert _row(WORKSPACE, "workspace_bind_mount")["manifest_proof"] == {
        "workspace_bind.engaged": True
    }
    # PURE and PROVIDER declare no subtree: an honest gap, not an overclaim in either direction.
    for prof in (PURE, PROVIDER):
        assert _row(prof, "workspace_bind_mount")["enforced"] is False
    assert WORKSPACE.workspace_bind is True and PURE.workspace_bind is False


def test_the_enforced_workspace_row_holds_against_a_real_bound_worker(tmp_path):
    """A pinned-True row has to be redeemable against a live manifest, or the pin is just a
    second place to write the same false claim."""
    host = tmp_path / "bound"
    host.mkdir()
    resp = _run(
        "def go():\n    return {'ok': True}\n",
        profile=WORKSPACE,
        workspace=declare_workspace(str(host)),
    )
    assert resp.status == SUCCEEDED
    proof = _row(WORKSPACE, "workspace_bind_mount")["manifest_proof"]
    for dotted, expected in proof.items():
        assert _resolve(resp.diagnostics["isolation"], dotted) is expected


def test_security_md_documents_every_dimension_the_report_calls_a_gap():
    """The operator's document is the one that decides whether a lane gets turned on. A gap
    that lives only in a runtime data structure is a gap nobody reading SECURITY.md sees."""
    text = SECURITY_MD.read_text(encoding="utf-8")
    gaps = set()
    for prof in (PURE, WORKSPACE, PROVIDER):
        for row in containment_report(prof)["dimensions"]:
            if not row["enforced"]:
                gaps.add(row["dimension"])
    # `workspace_bind_mount` is a gap only for the profiles that declare no subtree; it is
    # documented in SECURITY.md as the residual that CLOSED, which the text below asserts.
    missing = sorted(d for d in gaps if d not in text)
    assert not missing, f"containment gaps absent from SECURITY.md: {missing}"
    assert "MS_BIND" in text, "SECURITY.md must say what closed the workspace residual"


# ── doc ↔ code drift guard: every report dimension is documented in the matrix ───
def test_doc_documents_every_dimension():
    assert DOC.exists(), f"missing containment matrix doc at {DOC}"
    text = DOC.read_text(encoding="utf-8")
    seen = set()
    for prof in (PURE, WORKSPACE, PROVIDER):
        for row in containment_report(prof)["dimensions"]:
            seen.add(row["dimension"])
    missing = sorted(d for d in seen if d not in text)
    assert not missing, f"dimensions absent from the matrix doc: {missing}"


def test_doc_names_the_enforcing_module():
    text = DOC.read_text(encoding="utf-8")
    assert "decima/workers/execution.py" in text
    assert "containment_report" in text


# ── network-permitted profile on an unfiltered arch is loudly warned ─────────────
def test_network_permitted_profile_warns_on_unfiltered_arch(monkeypatch):
    # The seccomp syscall filter is aarch64-only. On an arch WITHOUT it a network-permitted
    # profile (PROVIDER) has NEITHER the best-effort syscall floor NOR an egress-mediation
    # seam — the worst case. `containment_report` must surface a loud top-level warning for
    # exactly that combination, and stay silent for a network-denied profile (netns holds).
    import decima.workers.execution as ex

    monkeypatch.setattr(ex, "_seccomp_arch_supported", lambda: False)
    prov = ex.containment_report(PROVIDER)
    assert prov["warnings"], "network-permitted + unfiltered arch must warn loudly"
    assert any("provider" in w.lower() and "network" in w.lower() for w in prov["warnings"])
    # A network-denied profile on the same unfiltered arch does NOT warn: the seccomp gap is
    # already an honest per-row gap and there is no permitted network to compound it with.
    assert ex.containment_report(PURE)["warnings"] == []

    # Where the filter engages (aarch64), even PROVIDER carries no seccomp-arch warning.
    monkeypatch.setattr(ex, "_seccomp_arch_supported", lambda: True)
    assert ex.containment_report(PROVIDER)["warnings"] == []
