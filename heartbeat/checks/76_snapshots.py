"""SN1 — snapshots, first increment: a verifiable cache of state at a frontier.

Proves the SNAPSHOTS.md contract on the live Weft:
  - snapshot a frontier → restore + verify (chunk hashes + state_root);
  - fold-from-snapshot == genesis fold to that frontier (FOLD §11.1) — the whole
    point: a snapshot is only ever a cache, provably equal to recomputing;
  - a corrupted chunk is rejected (tamper-evidence, SNAPSHOTS §13);
  - a reducer-set mismatch is refused, never folded (SNAPSHOTS §9);
  - it captures merge-layer state too (M1's plural heads ride in the leaves).

Incremental fold-from-base (skip genesis — the perf win) is deferred to a core
cycle. Contract: run(k, line). Fail loud.
"""
from decima import snapshot
from decima.weave import Weave


def run(k, line):
    line("\n== SNAPSHOTS (verifiable cache of state at a frontier) ==")
    wf = k.weft
    head = wf.count()
    mid = max(1, head // 2)

    # A snapshot at the current frontier, signed by the executor principal.
    manifest, store = snapshot.snapshot(wf, head, created_by=k.executor.id,
                                        keyring=k.keyring)
    line(f"  snapshot @e{head}: {len(manifest['chunks'])} chunk(s), "
         f"event_count={manifest['event_count']}, root={manifest['state_root'][:12]}")

    # Restore + verify (chunk hashes, signature, recomputed root), then prove the
    # restored root EQUALS a fresh genesis fold to the same frontier.
    genesis_root = Weave.fold(wf, head).state_root()
    restored = snapshot.restore(manifest, store, keyring=k.keyring)
    assert manifest["state_root"] == genesis_root, "manifest root != genesis fold"
    assert restored.state_root() == genesis_root, "restored root != genesis fold"
    line(f"  restored + verified; fold-from-snapshot == genesis fold "
         f"({restored.state_root()[:12]}) ✓  (FOLD §11.1)")

    # A mid-history frontier is equally a verifiable cache of THAT point.
    m_mid, s_mid = snapshot.snapshot(wf, mid, created_by=k.executor.id, keyring=k.keyring)
    assert snapshot.restore(m_mid, s_mid, keyring=k.keyring).state_root() \
        == Weave.fold(wf, mid).state_root()
    line(f"  snapshot @e{mid} (mid-history) also restores to its genesis fold ✓")

    # Tamper-evidence: corrupt a stored chunk's bytes → hash check rejects it.
    victim = manifest["chunks"][0]["blob_id"]
    store.blobs[victim] = store.blobs[victim].replace("id", "ID", 1)
    try:
        snapshot.restore(manifest, store, keyring=k.keyring)
        assert False, "corrupted chunk was NOT rejected"
    except snapshot.SnapshotError as e:
        line(f"  corrupted chunk → rejected: {e}")

    # Reducer-version coupling: a snapshot is refused (not folded) under a different
    # reducer set — a reducer upgrade invalidates old snapshots (SNAPSHOTS §9).
    m_ok, s_ok = snapshot.snapshot(wf, head, created_by=k.executor.id, keyring=k.keyring)
    try:
        snapshot.restore(m_ok, s_ok, reducer_set=[{"name": "heartbeat-weave", "version": 2}])
        assert False, "reducer mismatch was NOT refused"
    except snapshot.SnapshotError as e:
        line(f"  reducer-set mismatch → refused: {e}")

    line("  → a snapshot is a cache, provably equal to the fold or rejected. "
         "Incremental fold-from-base (skip genesis): deferred to a core cycle.")
