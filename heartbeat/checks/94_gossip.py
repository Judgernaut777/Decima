"""GX1 — networked sync at scale: Merkle-DAG diff + N-peer gossip/anti-entropy.

Proves, over real `Weft` instances sharing the kernel keyring (offline):
  - 4 peers with divergent events gossip to ONE shared state_root (and Merkle root);
  - the Merkle diff localizes divergence — a reconcile moves only the divergent
    events and visits far fewer nodes than the log holds;
  - a grant revoked on one peer stays revoked across the merged union;
  - no overwrite — every peer's concurrent OR-set add survives.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import model, sync, merkle, gossip
from decima.weft import Weft, ASSERT, RETRACT
from decima.weave import Weave, MERGE_ORSET
from decima.hashing import content_id


def run(k, line):
    line("\n== GOSSIP @ SCALE (Merkle diff · N-peer anti-entropy · convergence) ==")
    kr, author = k.keyring, k.root.id
    d = tempfile.mkdtemp()
    peers = [Weft(os.path.join(d, f"p{i}.db"), kr) for i in range(4)]

    # ---- shared base built on peer0, then propagated to all -----------------
    model.define_type(peers[0], author, "roster", merge_class=MERGE_ORSET)
    grant = content_id({"grant": "alpha-cap"})
    model.assert_content(peers[0], author, grant, "capability",
                         {"name": "alpha-cap", "active": True})
    roster = content_id({"roster": "members"})
    peers[0].append(author, ASSERT, {"cell": roster, "type": "roster", "kind": "CONTENT",
                                     "content": {"op": "add", "element": "seed"}})
    # filler so the shared log is large — then a small divergence is genuinely
    # LOCALIZED by the Merkle diff (the win only shows when |divergence| ≪ |log|).
    for i in range(64):
        nid = content_id({"filler": i})
        model.assert_content(peers[0], author, nid, "note", {"text": f"f{i}"})
    for p in peers[1:]:
        sync.pull(peers[0], p)
    assert gossip.converged(peers), "base should be identical on all peers after seeding"
    base_n = peers[0].count()
    line(f"  seeded {len(peers)} peers with a shared base ({base_n} events); roots equal ✓")

    # A late-joiner that holds ONLY the base — set aside now, reconciled after gossip
    # to show the Merkle diff moves only the events it is actually missing.
    fresh = Weft(os.path.join(d, "fresh.db"), kr)
    sync.pull(peers[0], fresh)
    assert fresh.count() == base_n

    # ---- each peer makes UNIQUE, concurrent events; peer2 REVOKES the grant ---
    for i, p in enumerate(peers):
        p.append(author, ASSERT, {"cell": roster, "type": "roster", "kind": "CONTENT",
                                  "content": {"op": "add", "element": f"node{i}"}})
    peers[2].append(author, RETRACT, {"cell": grant})        # revoked on ONE peer only

    assert not gossip.converged(peers), "peers should diverge before gossip"
    roots_before = len({merkle.of_weft(p).root_hash for p in peers})
    line(f"  divergence: {roots_before} distinct Merkle roots across {len(peers)} peers; "
         f"peer2 revoked the grant")

    # ---- gossip to convergence ----------------------------------------------
    rep = gossip.gossip(peers)
    assert rep["converged"], rep
    # convergence is real: identical state_root from every peer's independent fold
    state_roots = {Weave.fold(p).state_root() for p in peers}
    assert len(state_roots) == 1, state_roots
    assert len({merkle.of_weft(p).root_hash for p in peers}) == 1
    line(f"  gossip: {rep['peers']} peers → ONE state_root {list(state_roots)[0][:12]} "
         f"in {rep['rounds']} rounds (moved {rep['moved_total']} event-transfers) ✓")

    # ---- no overwrite: every peer's concurrent add survived the union -------
    members = sorted(Weave.fold(peers[0]).get(roster).content["elements"])
    assert members == ["node0", "node1", "node2", "node3", "seed"], members
    line(f"  union drops nothing — OR-set holds every peer's add: {members} ✓")

    # ---- revoked grant stays revoked across the whole population ------------
    assert all(Weave.fold(p).get(grant).retracted for p in peers), \
        "a revoke must hold on every peer after convergence"
    line("  revoked grant stays revoked on ALL peers post-merge (sync can't un-revoke) ✓")

    # ---- Merkle moves ONLY the divergent set (not the whole log) -----------
    # `fresh` (set aside above) shares the base but lacks everything gossip added:
    # one reconcile transfers exactly the events it's missing, visiting ≪ |log| nodes.
    full_n = peers[0].count()
    missing = full_n - base_n
    r = gossip.reconcile(fresh, peers[0])
    assert r["a_pulled"] == missing and r["b_pulled"] == 0, (r, missing)
    assert r["moved"] == missing
    assert r["visited"] < full_n, (r["visited"], full_n)     # localized, not a full scan
    assert merkle.of_weft(fresh).root_hash == merkle.of_weft(peers[0]).root_hash
    line(f"  Merkle diff moved only the {missing} divergent events (of {full_n} total), "
         f"visiting {r['visited']} nodes — O(log n) localization, not a full scan ✓")
