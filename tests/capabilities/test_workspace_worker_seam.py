"""Workspace lane capability glue — the prepare/execute worker seam + changed_files.

``Workspace.prepare_worker_run`` (Weft-touching: durable lease + digest-bound request)
and ``execute_prepared_run`` (pure dispatch into the isolated worker) must compose to
exactly the behavior of ``run_in_worker`` — the split exists so the API service can
keep every canonical-store access on its single serving thread while the pure worker
execution runs elsewhere.
"""

from __future__ import annotations

import pytest

from decima.capabilities.workspace import (
    create_workspace,
    durable_guard_and_consume,
    execute_prepared_run,
)
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.workers.lease import LeaseError, LeaseGuard
from decima.workers.protocol import SUCCEEDED, WorkerResponse

REPO = {
    "app.py": "def add(a, b):\n    return a + b\n",
    "README.md": "# Demo\n",
}

CHECK = (
    "def check(files):\n"
    "    ns = {}\n"
    "    exec(files['app.py'], ns)\n"
    "    ok = ns['add'](2, 3) == 5\n"
    "    return {'passed': 1 if ok else 0, 'failed': 0 if ok else 1}\n"
)


def test_prepare_then_execute_matches_run_in_worker(weft, author):
    ws = create_workspace(weft, author, name="seam")
    ws.mount_repo(REPO)
    request, now = ws.prepare_worker_run(effect="unit", check_source=CHECK)
    # The durable lease is on the Weft and the request is digest-bound.
    assert request.lease["idempotency_key"].startswith(ws.id)
    assert request.capability_proof == {"workspace": ws.id}

    resp = execute_prepared_run(request, now=now, workspace_root=ws.root)
    assert isinstance(resp, WorkerResponse)
    assert resp.status == SUCCEEDED
    assert resp.receipt_data["output"]["passed"] == 1
    assert resp.receipt_data["output"]["failed"] == 0


def test_prepared_lease_is_single_use_per_guard(weft, author):
    """Replaying the SAME prepared request against one guard fails closed."""
    ws = create_workspace(weft, author, name="replay")
    ws.mount_repo(REPO)
    request, now = ws.prepare_worker_run(effect="unit", check_source=CHECK)
    guard = LeaseGuard()
    first = execute_prepared_run(request, now=now, workspace_root=ws.root, lease_guard=guard)
    assert isinstance(first, WorkerResponse)
    assert first.status == SUCCEEDED
    with pytest.raises(LeaseError):
        execute_prepared_run(request, now=now, workspace_root=ws.root, lease_guard=guard)


def test_expired_lease_never_runs(weft, author):
    ws = create_workspace(weft, author, name="expired")
    ws.mount_repo(REPO)
    request, now = ws.prepare_worker_run(effect="unit", check_source=CHECK)
    expired_at = int(request.lease["expiry"]) + 1
    with pytest.raises(LeaseError):
        execute_prepared_run(request, now=expired_at, workspace_root=ws.root)


def test_changed_files_lists_edits_additions_and_removals(weft, author):
    ws = create_workspace(weft, author, name="delta")
    ws.mount_repo(REPO)
    assert ws.changed_files() == []
    ws.edit_file("app.py", "def add(a, b):\n    return a + b + 0\n")
    ws.edit_file("new.py", "x = 1\n")
    assert ws.changed_files() == ["app.py", "new.py"]


def test_durable_marker_refuses_replay_across_a_fresh_guard(weft_env):
    """Durable single-use: a lease consumed once via the store-holding boundary cannot be
    replayed even by a BRAND-NEW guard on a re-opened store. The restart forgets the
    in-memory consumption; the folded consumed-lease marker does not (P2.2)."""
    weft, author, db, kr = weft_env
    ws = create_workspace(weft, author, name="durable-replay")
    ws.mount_repo(REPO)
    request, now = ws.prepare_worker_run(effect="unit", check_source=CHECK)

    # First dispatch through the boundary: seeds a guard AND durably records the lease.
    guard1 = durable_guard_and_consume(weft, author, request.lease)
    first = execute_prepared_run(request, now=now, workspace_root=ws.root, lease_guard=guard1)
    assert isinstance(first, WorkerResponse)
    assert first.status == SUCCEEDED

    # The consumption is on the durable fold, keyed by (idempotency_key, attempt).
    key = (request.lease["idempotency_key"], int(request.lease["attempt"]))
    assert key in cells.consumed_lease_keys(Weave.fold(weft))

    # A plain guard with no durable seeding is BLIND to the cross-process replay — the
    # exact hole the durable marker closes.
    blind = LeaseGuard()
    blind.consume(request.lease, now=now)  # succeeds: an in-memory-only guard forgot it

    # A "restart": reopen the SAME store and build a fresh guard through the same boundary.
    # It is seeded from the durable marker, so the identical prepared request now fails
    # closed as a replay.
    reopened = Weft(db, kr)
    guard2 = durable_guard_and_consume(reopened, author, request.lease)
    with pytest.raises(LeaseError):
        execute_prepared_run(request, now=now, workspace_root=ws.root, lease_guard=guard2)
