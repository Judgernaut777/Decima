"""BACKUP / RESTORE — a portable, verifiable, tamper-evident backup of the whole Weft.

`decima/backup.py` composes ONLY public APIs (`Weft.events`/`.ingest`, the exact
wire-row shape `sync.py` already serializes, `hashing.content_id`) into three
functions: `backup(k)` exports the ENTIRE causal event log as a portable manifest
with a hash-chain integrity `root`; `verify(blob)` is a PURE, offline check that the
manifest's bytes still say what they claim; `restore(blob, db_path, keyring=...)`
replays every event into a FRESH database through `Weft.ingest` — the WEFT §2
acceptance gate — so a restored world re-earns EVERY event's admission (id
recompute, signature, parents-present, honest lamport) exactly as a sync peer's
foreign feed would.

This check proves, offline + deterministically (fresh Kernels, temp dbs, no clock):

  (a) ROUND-TRIP — back up a kernel carrying REAL state (invokes + receipts, plus
      asserted content Cells authored through the kernel's own principal), restore
      into a FRESH db, and prove the restored Weave EQUALS the original: same set
      of cell ids, same `state_root`, same `of_type` counts for every type present;
  (b) TAMPER-EVIDENT (the load-bearing guarantee) — corrupt one event's content in
      the blob: `verify(blob)` returns `False`, and `restore(blob, ...)` FAILS
      CLOSED (raises) rather than admitting a mutated world. A second, sneakier
      tamper — one that ALSO recomputes a matching id AND a matching hash-chain
      root (so `verify()`'s two pure checks are both fooled) — still cannot forge
      the author's signature, so `restore` still refuses it at the `Weft.ingest`
      gate;
  (c) DETERMINISM — `backup(k)` called twice on an unchanged log yields an
      IDENTICAL blob: same `root`, same event order, same count;
  (d) INTS preserved through the backup/restore round-trip, and restore adds NO NEW
      AUTHORITY — the restored log has exactly the same capability/grant cells as
      the original, never more.

Mutation-resistance (the load-bearing line): in `backup.restore`, replace the
`Weft.ingest` replay with a raw `INSERT INTO events` (or otherwise skip WEFT §2
acceptance) and (b) goes red — the tampered row is admitted into the restored
database instead of being refused, and a mutated-but-accepted world is produced.

Contract: run(k, line). Fail loud (assert). Owns fresh Kernels over fresh temp dbs.
"""
import copy
import json
import os
import tempfile

from decima.kernel import Kernel
from decima.weave import Weave
from decima import model, backup as bk


def _kernel(db=None):
    db = db or os.path.join(tempfile.mkdtemp(), "weft.db")
    return Kernel(db, fresh=True)


def _seed_state(k):
    """A kernel carrying REAL state: a couple of invokes + receipts (through the
    live agent loop), plus a couple of content Cells asserted through the kernel's
    own orchestrator principal (Law 1/3: state is Cells on the Log, authored
    through an existing principal — never fabricated out of band)."""
    for ln in k.say("echo hello, fates"):
        pass
    for ln in k.say("echo backup me"):
        pass
    model.assert_content(k.weft, k.decima.id, "note-1", "note", {"text": "alpha", "n": 1})
    model.assert_content(k.weft, k.decima.id, "note-2", "note", {"text": "beta", "n": 2})


def _cap_grant_signature(w: Weave) -> set:
    """A signature of every capability/grant cell present — used to prove restore
    confers NO NEW authority (Law 2: zero ambient authority)."""
    sig = set()
    for t in ("capability", "grant"):
        for c in w.of_type(t):
            sig.add((t, c.id))
    return sig


def run(k, line):
    line("\n== BACKUP / RESTORE — a portable, verifiable, tamper-evident backup of the whole Weft ==")

    # ── (a) ROUND-TRIP — backup a kernel with real state, restore fresh, prove equal. ──
    k1 = _kernel()
    _seed_state(k1)
    w1 = k1.weave()
    blob = bk.backup(k1)
    assert isinstance(blob, dict) and {"events", "root", "count"} <= blob.keys()
    assert blob["count"] == k1.weft.count() == len(blob["events"]), \
        "the manifest must carry every event in the source log, no more no less"

    db2 = os.path.join(tempfile.mkdtemp(), "weft.db")
    restored_weft = bk.restore(blob, db2, keyring=k1.keyring)
    assert restored_weft.count() == k1.weft.count(), "restore must admit every backed-up event"
    w2 = Weave.fold(restored_weft)

    assert set(w2.cells.keys()) == set(w1.cells.keys()), \
        "the restored Weave must hold exactly the same set of cells as the original"
    assert w2.state_root() == w1.state_root(), \
        "the restored Weave must fold to the IDENTICAL state_root as the original"
    for t in set(w1.types) | {"agent", "capability", "note"}:
        assert len(w2.of_type(t)) == len(w1.of_type(t)), \
            f"of_type({t!r}) count must match after restore"
    line(f"  round-trip: {blob['count']} events backed up and restored into a FRESH db — "
         f"identical cell set, identical state_root ({w1.state_root()[:12]}...) ✓")

    # ── (b) TAMPER-EVIDENT — the load-bearing guarantee. ────────────────────────────────
    # Tamper 1: mutate a payload's content WITHOUT fixing its claimed id. `verify()`
    # (a pure, offline check) must catch the id/content mismatch on its own.
    tampered = copy.deepcopy(blob)
    victim = tampered["events"][-1]                 # id, payload_text, author, sig
    payload = json.loads(victim[1])
    payload["body"] = dict(payload["body"])
    payload["body"]["text"] = "TAMPERED-BY-ADVERSARY"
    victim[1] = json.dumps(payload, sort_keys=True)  # id (victim[0]) left stale on purpose
    ok, reason = bk.verify(tampered)
    assert ok is False and "mismatch" in reason, \
        f"a payload tampered without fixing its id must fail verify(): {ok!r} {reason!r}"

    db3 = os.path.join(tempfile.mkdtemp(), "weft.db")
    try:
        bk.restore(tampered, db3, keyring=k1.keyring)
        raise AssertionError("restore accepted a backup that verify() already rejected")
    except bk.BackupError:
        pass
    line("  tamper (id-stale): corrupting one event's content without fixing its id — "
         "verify(blob) returns False, restore refuses to even open the database ✓")

    # Tamper 2: a MAXIMALLY SNEAKY adversary who recomputes BOTH the tampered event's
    # content id AND the manifest's hash-chain root (so `verify()`'s two pure checks —
    # id-match and root — are both fooled) STILL cannot forge the author's SIGNATURE.
    # restore's replay through Weft.ingest (WEFT §2) must be the backstop that refuses it.
    from decima.hashing import content_id
    tampered2 = copy.deepcopy(blob)
    victim2 = tampered2["events"][-1]
    payload2 = json.loads(victim2[1])
    payload2["body"] = dict(payload2["body"])
    payload2["body"]["text"] = "TAMPERED-WITH-RECOMPUTED-ID"
    new_id = content_id(payload2, kind="event")       # the id now matches the payload...
    victim2[1] = json.dumps(payload2, sort_keys=True)
    victim2[0] = new_id                               # ...but the signature (victim2[3]) is stale
    ids2 = [r[0] for r in tampered2["events"]]
    tampered2["root"] = bk._chain_root(ids2)          # the root is patched to match too
    ok2, reason2 = bk.verify(tampered2)
    assert ok2 is True, \
        f"an id- AND root-consistent (but unsigned) tamper must PASS the pure checks: {reason2}"
    db4 = os.path.join(tempfile.mkdtemp(), "weft.db")
    try:
        bk.restore(tampered2, db4, keyring=k1.keyring)
        raise AssertionError("restore accepted an event with a forged/stale signature")
    except bk.BackupError as e:
        assert "bad-signature" in str(e) or "orphan" in str(e) or "rejected" in str(e), \
            f"restore must refuse a signature-invalid row at the ingest gate: {e}"
    line("  tamper (id+root recomputed): an adversary who fixes BOTH the content id and "
         "the manifest's root still cannot forge the signature — verify() is fully fooled, "
         "but restore's replay through Weft.ingest (WEFT §2) still refuses the row and "
         "raises — no mutated world is ever produced ✓")

    # ── (c) DETERMINISM — backup(k) twice on an unchanged log is byte-identical. ────────
    blob_again = bk.backup(k1)
    assert blob_again["root"] == blob["root"], "the root must be identical across two backups"
    assert blob_again["events"] == blob["events"], "the event order/content must be identical"
    assert blob_again["count"] == blob["count"]
    line(f"  determinism: backup(k) called twice on an unchanged log yields an IDENTICAL "
         f"blob (root {blob['root'][:12]}...) ✓")

    # ── (d) INTS preserved + NO NEW AUTHORITY conferred by restore. ─────────────────────
    note1 = w2.get("note-1")
    assert isinstance(note1.content["n"], int) and not isinstance(note1.content["n"], bool), \
        "an int recorded before backup must still be an int after restore"
    assert note1.content["n"] == 1 and w2.get("note-2").content["n"] == 2

    before = _cap_grant_signature(w1)
    after = _cap_grant_signature(w2)
    assert after == before, \
        "restore must confer NO NEW authority — identical capability/grant cells, no more"
    line("  ints preserved + no new authority: restored numeric content stays int "
         "(n=1, n=2), and the restored log's capability/grant cells are EXACTLY the "
         "original set — restore fabricates no authority ✓")

    line("  → backup/restore is now a portable, verifiable, tamper-evident copy of the "
         "WHOLE Weft: backup exports the causal log with a hash-chain root, verify() "
         "cheaply distrusts a corrupted manifest offline, and restore replays every "
         "event back through the real WEFT §2 acceptance gate — a tampered backup is "
         "REJECTED, never silently woven into a fresh world.")
