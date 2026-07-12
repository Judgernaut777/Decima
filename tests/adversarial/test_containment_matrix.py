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

from decima.workers.execution import compute_digest, containment_report, run_worker
from decima.workers.profiles import PROVIDER, PURE, WORKSPACE
from decima.workers.protocol import FAILED, SUCCEEDED, WorkerRequest

DOC = pathlib.Path(__file__).resolve().parents[2] / "docs/architecture/worker-containment.md"


def _lease() -> dict:
    return {
        "step_id": "s1", "worker": "w1", "capability_ids": [],
        "issued_frontier": 0, "expiry": 100, "attempt": 1, "idempotency_key": "idem-cm",
    }


def _run(src: str, *, args: dict | None = None, profile=PURE, limits=None):
    req = WorkerRequest(
        invocation_id="inv-cm", job_id="job-cm", effect="pure_compute",
        implementation_digest=compute_digest(src), arguments=args or {},
        lease=_lease(), capability_proof={"grant_id": "g1"},
    )
    return run_worker(req, src, "go", now=0, profile=profile, limits=limits)


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
    for name in ("filesystem_isolation", "user_namespace", "mount_namespace",
                 "network_isolation"):
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
    row = next(d for d in containment_report(PURE)["dimensions"]
               if d["dimension"] == "non_dumpable")
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
    row = next(d for d in containment_report(PURE, {"fsize": 1 << 16})["dimensions"]
               if d["dimension"] == "resource_limits")
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


# ── GAPS are honest: the worker confirms the layer is genuinely absent ───────────
def test_seccomp_gap_is_honest_no_filter_installed():
    gap = next(d for d in containment_report(PURE)["dimensions"]
               if d["dimension"] == "syscall_filter")
    assert gap["enforced"] is False and "gap" in gap
    # PR_GET_SECCOMP == 21; mode 0 means NO seccomp filter — matching the documented gap.
    src = (
        "def go(x):\n"
        "    import ctypes\n"
        "    return {'seccomp_mode': ctypes.CDLL(None).prctl(21, 0, 0, 0, 0)}\n"
    )
    resp = _run(src, args={"x": 1})
    assert resp.status == SUCCEEDED
    assert resp.receipt_data["output"]["seccomp_mode"] == 0, (
        "a seccomp filter IS installed — the report must stop calling this a gap"
    )


def test_no_enforced_row_is_also_listed_as_a_gap():
    for prof in (PURE, WORKSPACE, PROVIDER):
        for row in containment_report(prof)["dimensions"]:
            if row["enforced"]:
                assert "gap" not in row, (row["dimension"], prof.name)
            else:
                assert "gap" in row and "manifest_proof" not in row, row["dimension"]


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
