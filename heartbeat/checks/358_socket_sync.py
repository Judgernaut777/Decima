"""SY-socket — sync over a REAL socket (the last networked-sync seam, offline).

`sync_over_wire` (check 80/94) proved the union is transport-decoupled: a JSON
string is the whole wire. This check carries that SAME serialized `feed` across an
ACTUAL stream socket (`socket.socketpair()` — a kernel-connected pair, no ports/
firewall), so two `Weft`s in SEPARATE threads converge. It asserts the socket path
enjoys the exact same guarantees as the in-process wire:

  - two peers, a shared keyring, each with a distinct appended event, converge over
    a REAL socket to EQUAL have-sets and ONE identical `state_root` (SYNC §10);
  - a forged/tampered event injected INTO the socket feed is REJECTED (fails
    `Weft.ingest` §2 acceptance) — possession of the wire buys nothing — while the
    genuine event syncs cleanly right after;
  - a TCP/loopback variant converges too (same protocol, different byte channel).

No thread or socket leaks even on assertion failure (helpers clean up in `finally`;
the inline malicious-server test joins its thread before asserting).

Contract: run(k, line). Fail loud.
"""
import os
import socket
import tempfile
import threading

from decima import model, sync
from decima.weft import Weft, ASSERT
from decima.weave import Weave, MERGE_ORSET
from decima.hashing import content_id


def _add(weft, author, cell, element):
    weft.append(author, ASSERT, {"cell": cell, "type": "members", "kind": "CONTENT",
                                 "content": {"op": "add", "element": element}})


def run(k, line):
    line("\n== SOCKET SYNC (two Wefts · REAL socketpair · verify · converge) ==")
    kr, author = k.keyring, k.root.id          # both peers share the keyring
    d = tempfile.mkdtemp()
    A = Weft(os.path.join(d, "sockA.db"), kr)
    B = Weft(os.path.join(d, "sockB.db"), kr)

    # ---- shared base on A, transferred to B, then a UNIQUE event on each peer ----
    model.define_type(A, author, "members", merge_class=MERGE_ORSET)
    pset = content_id({"members": "sock-roster"})
    _add(A, author, pset, "root-seed")
    assert sync.pull(A, B)["ingested"] == A.count()          # B learns the base
    _add(A, author, pset, "alpha")                           # A-only
    _add(B, author, pset, "beta")                            # B-only

    rA0, rB0 = Weave.fold(A).state_root(), Weave.fold(B).state_root()
    assert rA0 != rB0, "peers must diverge before syncing"
    assert sync.event_ids(A) != sync.event_ids(B)
    line(f"  pre-sync: roots differ ({rA0[:8]} != {rB0[:8]}); have-sets differ")

    # ---- converge over a REAL socket pair (B serves in a thread) ---------------
    rep = sync.sync_over_socket(A, B)
    assert rep["converged"] and rep["state_root"], rep
    assert rep["to_a"]["ingested"] == 1 and rep["to_b"]["ingested"] == 1, rep
    assert sync.event_ids(A) == sync.event_ids(B), "have-sets must be EQUAL after socket sync"
    assert Weave.fold(A).state_root() == Weave.fold(B).state_root() == rep["state_root"]
    members = sorted(Weave.fold(A).get(pset).content["elements"])
    assert members == ["alpha", "beta", "root-seed"], members
    line(f"  socket sync: A<->B each ingested 1 -> ONE state_root {rep['state_root'][:12]}; "
         f"have-sets equal; union drops nothing {members} ✓")

    # ---- re-sync over the socket is idempotent (0 ingested each way) ------------
    again = sync.sync_over_socket(A, B)
    assert again["converged"]
    assert again["to_a"]["ingested"] == 0 and again["to_b"]["ingested"] == 0, again
    line("  re-sync over the socket is idempotent (0 ingested each way) ✓")

    # ---- a FORGED event injected into the socket feed is REJECTED ---------------
    # A appends `gamma`; a malicious server sends B a feed with `gamma`'s bytes edited
    # (same id, tampered payload). B's client applies it through `Weft.ingest` (§2) and
    # REJECTS it — the socket path has the same acceptance gate as the in-process wire.
    g = A.append(author, ASSERT, {"cell": pset, "type": "members", "kind": "CONTENT",
                                  "content": {"op": "add", "element": "gamma"}})
    b_have = sorted(sync.event_ids(B))
    genuine_wire = sync.feed(A, b_have)                       # the [gamma] row B lacks
    forged_wire = genuine_wire.replace("gamma", "HACKED")     # same id, edited bytes
    assert forged_wire != genuine_wire

    a_have = sorted(sync.event_ids(A))                       # snapshot in THIS thread
    c_sock, s_sock = socket.socketpair()
    err = []

    def _malicious_server():
        try:
            sync._recv_json(s_sock)                           # B announces its have-set
            sync._send_json(s_sock, {"feed": forged_wire, "have": a_have})
            sync._recv_json(s_sock)                           # B's feed reply (ignored)
        except Exception as exc:
            err.append(exc)
        finally:
            s_sock.close()

    t = threading.Thread(target=_malicious_server, name="malicious-peer")
    t.start()
    try:
        res = sync.sync_socket(B, c_sock)                     # B pulls the forged feed
    finally:
        c_sock.close()
        t.join(timeout=10)
    assert not err, err
    assert res["ingested"] == 0 and res["rejected"] == 1, res
    assert g.id not in sync.event_ids(B), "a forged event must NOT enter the union"
    line("  forged event injected into the socket feed is REJECTED (id != bytes) ✓")

    # ---- the GENUINE event then syncs cleanly over the socket ------------------
    good = sync.sync_over_socket(A, B)
    assert good["converged"] and good["to_b"]["ingested"] == 1, good
    assert g.id in sync.event_ids(B) and sync.event_ids(A) == sync.event_ids(B)
    assert Weave.fold(A).state_root() == Weave.fold(B).state_root()
    line("  the genuine event syncs cleanly right after (same acceptance guarantee) ✓")

    # ---- TCP/loopback variant converges too (same protocol, different channel) --
    _add(A, author, pset, "delta")                           # A-only
    _add(B, author, pset, "epsilon")                         # B-only
    assert Weave.fold(A).state_root() != Weave.fold(B).state_root()
    tcp = sync.sync_over_tcp(A, B)
    assert tcp["converged"] and tcp["state_root"], tcp
    assert sync.event_ids(A) == sync.event_ids(B), "TCP sync must equalize have-sets"
    assert Weave.fold(A).state_root() == Weave.fold(B).state_root() == tcp["state_root"]
    members = sorted(Weave.fold(A).get(pset).content["elements"])
    assert members == ["alpha", "beta", "delta", "epsilon", "gamma", "root-seed"], members
    line(f"  TCP/loopback variant converges too -> {tcp['state_root'][:12]}; "
         f"full OR-set {members} ✓")
