"""Weft.ingest — WEFT §2 ACCEPTANCE VALIDATION + networked sync over the wire.

Cross-peer sync used to insert a foreign event after checking only its hash + signature
(sync.py); there was no `Weft.ingest`, so an event with DANGLING PARENTS or a FORGED
LAMPORT could enter the log. This hardens the acceptance gate: `Weft.ingest(row)` runs
full §2 validation before a foreign event may join the append-only DAG, and `sync`'s
transport now routes through it (out-of-order feeds converge by defer-and-retry). A
networked wire path (`sync_over_wire`) exchanges have-sets + SERIALIZED feeds instead of
reading a peer's DB.

This check proves (on its own fresh, keyring-sharing Wefts):
  - a VALID foreign event is ingested; a duplicate is a no-op;
  - every §2 violation is REJECTED and never inserted — bad verb, non-canonical parents,
    tampered payload (id≠bytes), bad signature, forged lamport;
  - an event whose parent is absent is an ORPHAN (not inserted) — and an OUT-OF-ORDER
    batch still unions a CLOSED DAG via defer-and-retry;
  - networked wire sync converges two divergent Wefts to ONE state_root, and a forged
    event injected into a wire feed is rejected while the genuine one syncs.

Fail closed throughout: possession of an id buys nothing; only a self-proving event enters.

Contract: run(k, line). Fail loud.
"""
import json
import os
import tempfile

from decima.kernel import Kernel
from decima.weft import Weft
from decima.weave import Weave
from decima.hashing import content_id
from decima import sync


def _row(keyring, *, author, verb="ASSERT", body, parents, lamport, authorized=None):
    """Craft a signed wire record (id, payload_text, author, sig) — a foreign event as a
    peer feed would deliver it. `id` and `sig` are computed to be internally consistent
    unless a test deliberately corrupts them afterwards."""
    payload = {"parents": parents, "author": author, "authorized": authorized,
               "verb": verb, "body": body, "lamport": lamport}
    eid = content_id(payload, kind="event")
    return [eid, json.dumps(payload, sort_keys=True), author, keyring.sign(author, eid)]


def run(k, line):
    line("\n== Weft.ingest — WEFT §2 ACCEPTANCE VALIDATION + wire sync ==")
    src = Kernel(os.path.join(tempfile.mkdtemp(), "src.db"), fresh=True)   # a real signer
    kr = src.weft.keyring
    who = src.human.id

    # 1. VALID foreign event → ingested; duplicate → no-op. ────────────────────────────
    B = Weft(os.path.join(tempfile.mkdtemp(), "b.db"), kr)
    g = _row(kr, author=who, body={"cell": "n0", "type": "note", "content": {"t": "hi"}},
             parents=[], lamport=1)                       # genesis: 1 + max([]) = 1
    assert B.ingest(g) == "ingested", "a valid foreign event must be accepted"
    assert B.ingest(g) == "duplicate", "a duplicate must be an idempotent no-op"
    assert B.count() == 1
    line("  valid foreign event ingested; duplicate is a no-op ✓")

    # 2. §2 VIOLATIONS each rejected, never inserted (fail closed). ─────────────────────
    before = B.count()
    bad_verb = _row(kr, author=who, verb="NOPE", body={}, parents=[], lamport=1)
    assert B.ingest(bad_verb) == "rejected:bad-verb", B.ingest(bad_verb)
    noncanon = _row(kr, author=who, body={"x": 1}, parents=["zzz", "aaa"], lamport=2)
    assert B.ingest(noncanon) == "rejected:parents-not-canonical"
    # tampered payload: a FRESH event whose bytes are edited but id kept → id≠bytes.
    fresh = _row(kr, author=who, body={"cell": "n2", "type": "note", "content": {"t": "ok"}},
                 parents=[], lamport=1)
    p = json.loads(fresh[1]); p["body"]["t"] = "EVIL"; fresh[1] = json.dumps(p, sort_keys=True)
    assert B.ingest(fresh) == "rejected:id-mismatch", B.ingest(fresh)
    # bad signature: consistent id/bytes, but the signature is garbage.
    badsig = _row(kr, author=who, body={"cell": "n9", "type": "note", "content": {}},
                  parents=[], lamport=1); badsig[3] = "00" * 16
    assert B.ingest(badsig) == "rejected:bad-signature", B.ingest(badsig)
    # forged lamport: real present parent, but lamport != 1 + parent.lamport.
    forged_lam = _row(kr, author=who, body={"cell": "n1", "type": "note", "content": {}},
                      parents=[g[0]], lamport=99)
    assert B.ingest(forged_lam) == "rejected:bad-lamport", B.ingest(forged_lam)
    assert B.count() == before, "no rejected event may enter the log"
    line("  rejected (never inserted): bad verb · non-canonical parents · tampered bytes "
         "· bad signature · forged lamport ✓")

    # 3. ORPHAN (absent parent) + OUT-OF-ORDER batch → closed DAG via defer-retry. ──────
    orphan = _row(kr, author=who, body={"cell": "x", "type": "note", "content": {}},
                  parents=["f" * 32], lamport=2)          # parent not present
    assert B.ingest(orphan) == "orphan", "an event with an absent parent is an orphan"
    assert B.count() == before, "an orphan is not inserted"
    # A valid causal chain g0→g1→g2, delivered OUT OF ORDER, still unions completely.
    C = Weft(os.path.join(tempfile.mkdtemp(), "c.db"), kr)
    g0 = _row(kr, author=who, body={"cell": "c0", "type": "note", "content": {"t": "0"}},
              parents=[], lamport=1)
    g1 = _row(kr, author=who, body={"cell": "c1", "type": "note", "content": {"t": "1"}},
              parents=[g0[0]], lamport=2)
    g2 = _row(kr, author=who, body={"cell": "c2", "type": "note", "content": {"t": "2"}},
              parents=[g1[0]], lamport=3)
    res = sync.ingest(C, [g2, g0, g1])                    # child first — must defer/retry
    assert res["ingested"] == 3 and res["rejected"] == 0, res
    assert C.count() == 3, "the whole chain unions despite arrival order"
    line("  absent parent → orphan (not inserted); out-of-order chain unions a closed DAG ✓")

    # 4. NETWORKED WIRE SYNC — two divergent Wefts converge over serialized feeds. ──────
    P = Kernel(os.path.join(tempfile.mkdtemp(), "p.db"), fresh=True)
    Q = Weft(os.path.join(tempfile.mkdtemp(), "q.db"), P.weft.keyring)
    sync.ingest(Q, sync._rows(P.weft))                    # seed Q with P's history
    P.weft.append(P.human.id, "ASSERT", {"cell": "pnote", "type": "note", "content": {"t": "P"}})
    Q.append(P.human.id, "ASSERT", {"cell": "qnote", "type": "note", "content": {"t": "Q"}})
    assert Weave.fold(P.weft).state_root() != Weave.fold(Q).state_root(), "peers must diverge first"
    rep = sync.sync_over_wire(P.weft, Q)
    assert rep["converged"] and rep["state_root"], rep
    assert sync.event_ids(P.weft) == sync.event_ids(Q), "have-sets must match after wire sync"
    # A forged event injected into a wire feed is rejected; a genuine one rides through.
    head, lam = P.weft.head, P.weft.lamport
    genuine = _row(P.weft.keyring, author=P.human.id,
                   body={"cell": "extra", "type": "note", "content": {"t": "real"}},
                   parents=[head], lamport=lam + 1)
    forged = _row(P.weft.keyring, author=P.human.id,
                  body={"cell": "extra2", "type": "note", "content": {"t": "real"}},
                  parents=[head], lamport=lam + 1)
    pp = json.loads(forged[1]); pp["body"]["content"]["t"] = "TAMPERED"
    forged[1] = json.dumps(pp, sort_keys=True)            # bytes changed, id now stale
    wire = json.dumps([genuine, forged])
    got = sync.apply_feed(Q, wire)
    assert got["ingested"] == 1 and got["rejected"] == 1, got
    line(f"  wire sync: divergent P/Q → ONE state_root {rep['state_root'][:12]}; forged "
         "feed event rejected, genuine ingested ✓")

    line("  → Weft.ingest is the §2 acceptance gate: a foreign event joins the DAG only "
         "if it proves itself (integrity + signature + parents-present + honest clock); "
         "sync unions out-of-order feeds into a closed DAG and converges over the wire.")
