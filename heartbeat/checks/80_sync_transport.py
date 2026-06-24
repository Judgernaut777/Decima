"""SY2 — sync transport between two REAL Weft instances (offline, shared keyring).

SY1 simulated peers as forks in one Weft; this drives the actual protocol from
decima.sync between two separate `Weft`s that share the kernel keyring (so each can
verify the other's signatures, per the HMAC profile):

  - two peers each holding UNIQUE events sync bidirectionally to ONE state_root
    (DAG union → convergence, SYNC §10 / FOLD §11 #2);
  - the union drops nothing — both peers' concurrent OR-set adds survive (no overwrite);
  - a tampered foreign event is REJECTED on ingest, and — the underlying guarantee —
    a tampered event already in a log is rejected on FOLD (read-time verification).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import model, sync
from decima.weft import Weft, WeftError, ASSERT
from decima.weave import Weave, MERGE_ORSET
from decima.hashing import content_id


def run(k, line):
    line("\n== SYNC TRANSPORT (two Wefts · verify · DAG union · converge) ==")
    kr, author = k.keyring, k.root.id          # both peers share the keyring
    d = tempfile.mkdtemp()
    A = Weft(os.path.join(d, "peerA.db"), kr)
    B = Weft(os.path.join(d, "peerB.db"), kr)

    # ---- shared base built on A, then transferred to B (so both agree on type) --
    model.define_type(A, author, "members", merge_class=MERGE_ORSET)
    pset = content_id({"members": "roster"})
    A.append(author, ASSERT, {"cell": pset, "type": "members", "kind": "CONTENT",
                              "content": {"op": "add", "element": "root-seed"}})
    seeded = sync.pull(A, B)                    # one direction: B learns the base
    assert seeded["ingested"] == A.count() and sync.event_ids(A) == sync.event_ids(B)
    line(f"  base: A→B transferred {seeded['ingested']} events; have-sets equal ✓")

    # ---- each peer now makes UNIQUE, concurrent events (both fork the shared head) --
    A.append(author, ASSERT, {"cell": pset, "type": "members", "kind": "CONTENT",
                              "content": {"op": "add", "element": "alpha"}})   # A-only
    B.append(author, ASSERT, {"cell": pset, "type": "members", "kind": "CONTENT",
                              "content": {"op": "add", "element": "beta"}})    # B-only

    rA0, rB0 = Weave.fold(A).state_root(), Weave.fold(B).state_root()
    assert rA0 != rB0, "peers should diverge before syncing"
    line(f"  pre-sync: A frontier={len(sync.frontier(A))} B frontier={len(sync.frontier(B))}; "
         f"roots differ ({rA0[:8]} ≠ {rB0[:8]})")

    # ---- bidirectional sync → convergence -----------------------------------
    rep = sync.sync(A, B)
    assert rep["converged"] and rep["state_root"], rep
    assert rep["a_to_b"]["ingested"] == 1 and rep["b_to_a"]["ingested"] == 1, rep
    assert sync.event_ids(A) == sync.event_ids(B), "have-sets must match after union"
    line(f"  sync: A→B ingested {rep['a_to_b']['ingested']}, B→A ingested "
         f"{rep['b_to_a']['ingested']} → ONE state_root {rep['state_root'][:12]} ✓")

    # ---- no overwrite: both peers' concurrent adds survive the union --------
    members = sorted(Weave.fold(A).get(pset).content["elements"])
    assert members == ["alpha", "beta", "root-seed"], members
    line(f"  union drops nothing — OR-set holds every peer's add: {members} ✓")

    # ---- re-sync is a no-op (idempotent union) ------------------------------
    again = sync.sync(A, B)
    assert again["a_to_b"]["ingested"] == 0 and again["b_to_a"]["ingested"] == 0
    # and re-ingesting events the target already holds is harmless (deduped by id).
    redelivered = sync.ingest(B, sync._rows(A))
    assert redelivered["ingested"] == 0 and redelivered["duplicate"] == A.count()
    line(f"  idempotent: re-sync ingests 0; re-delivering all {A.count()} events → "
         f"{redelivered['duplicate']} duplicates, 0 inserted ✓")

    # ---- tampered foreign event REJECTED on ingest --------------------------
    g = A.append(author, ASSERT, {"cell": pset, "type": "members", "kind": "CONTENT",
                                  "content": {"op": "add", "element": "gamma"}})
    row = sync.missing_for(A, B)[0]            # the gamma row, headed for B
    forged = (row[0], row[1].replace("gamma", "HACKED"), row[2], row[3])  # same id, edited bytes
    res = sync.ingest(B, [forged])
    assert res["rejected"] == 1 and res["ingested"] == 0, res
    assert g.id not in sync.event_ids(B), "a forged event must not enter the union"
    # the genuine event still syncs fine
    assert sync.pull(A, B)["ingested"] == 1 and Weave.fold(A).state_root() == Weave.fold(B).state_root()
    line("  tampered foreign event rejected on ingest (id≠bytes); genuine one syncs ✓")

    # ---- backstop: a tamper already in a log is caught on FOLD --------------
    C = Weft(os.path.join(d, "peerC.db"), kr)
    sync.pull(A, C)                            # C = a faithful copy of A
    C.db.execute("UPDATE events SET payload = REPLACE(payload, 'alpha', 'XXXXX') "
                 "WHERE payload LIKE '%alpha%'")
    C.db.commit()
    raised = False
    try:
        Weave.fold(C)
    except WeftError:
        raised = True
    assert raised, "fold must reject a tampered event (read-time verification)"
    line("  backstop: a tampered event is rejected on FOLD too — the union is "
         "tamper-evident end to end ✓")
