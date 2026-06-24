"""M2 — remaining merge classes on M1's substrate: Sequence CRDT, Map CRDT,
Counter, Append-log, and the semantic-adjudication path (an ATTEST collapses
preserved heads). Each is proven to CONVERGE under a concurrent fork — folded in
two different arrival orders, the state_root is identical.

Sequence/Map/Counter/Append-log + adjudication land here; State-machine (the
capability/task lifecycle) stays imperative in the kernel for now and a declarative
merge-class version is the next slice (noted at the end). Contract: run(k, line).
"""
from decima import model
from decima.weave import (Weave, MERGE_SEQUENCE, MERGE_MAP, MERGE_COUNTER,
                          MERGE_APPEND, MERGE_MV, MERGE_ORSET, MERGE_LWW)
from decima.weft import ASSERT, ATTEST
from decima.hashing import content_id


def run(k, line):
    line("\n== MERGE CLASSES II (sequence · map · counter · append-log · adjudication) ==")
    root, wf = k.root.id, k.weft

    model.define_type(wf, root, "doc", merge_class=MERGE_SEQUENCE)
    model.define_type(wf, root, "worker", merge_class=MERGE_MAP,
                      field_classes={"envelope": MERGE_ORSET, "budget": MERGE_COUNTER,
                                     "brain": MERGE_LWW})
    model.define_type(wf, root, "tally", merge_class=MERGE_COUNTER)
    model.define_type(wf, root, "log", merge_class=MERGE_APPEND)
    model.define_type(wf, root, "belief", merge_class=MERGE_MV)

    def at(parents, cell, typ, content):
        return wf.append(root, ASSERT,
                         {"cell": cell, "type": typ, "kind": "CONTENT", "content": content},
                         parents=parents)

    base = wf.head        # every branch below forks from this shared parent

    # ---- Sequence CRDT: "Loom" then a CONCURRENT insert after it on each side ---
    doc = content_id({"doc": "headline"})
    e1 = at([base], doc, "doc", {"op": "insert", "elem_id": "w1", "value": "Loom", "after": None})
    # two concurrent inserts AFTER w1 (neither observed the other) — converge by
    # (lamport, eid) desc; plus a concurrent insert at head.
    at([e1.id], doc, "doc", {"op": "insert", "elem_id": "w2", "value": "ships", "after": "w1"})
    at([e1.id], doc, "doc", {"op": "insert", "elem_id": "w3", "value": "weaves", "after": "w1"})
    at([e1.id], doc, "doc", {"op": "insert", "elem_id": "w0", "value": "The", "after": None})
    eD = at([e1.id], doc, "doc", {"op": "insert", "elem_id": "wx", "value": "DRAFT", "after": "w1"})
    at([eD.id], doc, "doc", {"op": "delete", "elem_id": "wx"})       # tombstone it

    # ---- Map CRDT: per-key independent merge (OR-set ∥ counter ∥ LWW) -----------
    wkr = content_id({"worker": "scout"})
    ea = at([base], wkr, "worker", {"key": "envelope", "op": "add", "element": "echo"})
    eg = at([ea.id], wkr, "worker", {"key": "envelope", "op": "add", "element": "shell"})
    at([eg.id], wkr, "worker", {"key": "envelope", "op": "remove", "element": "echo"})  # observes echo
    at([base], wkr, "worker", {"key": "budget", "delta": 10})        # concurrent counter
    at([base], wkr, "worker", {"key": "budget", "delta": 5})         # +5, commute → 15
    at([base], wkr, "worker", {"key": "brain", "value": "rules"})    # LWW field, independent

    # ---- Counter (whole-cell): concurrent increments sum ------------------------
    cnt = content_id({"tally": "denials"})
    at([base], cnt, "tally", {"delta": 2})
    at([base], cnt, "tally", {"delta": 3})                           # concurrent → 5

    # ---- Append-log: concurrent observations accrete, never conflict ------------
    lg = content_id({"log": "stream"})
    at([base], lg, "log", {"line": "a"})
    at([base], lg, "log", {"line": "b"})                            # concurrent, both kept

    # ---- MV belief to be ADJUDICATED later -------------------------------------
    bel = content_id({"belief": "owner"})
    pa = at([base], bel, "belief", {"text": "Alice owns it"})
    pb = at([base], bel, "belief", {"text": "Bob owns it"})         # concurrent head

    # ===== convergence: fold the GENUINELY-forked Weft in two arrival orders =====
    evs = list(wf.events())
    asc = sorted(evs, key=lambda e: (e.lamport, e.id))
    desc = sorted(sorted(evs, key=lambda e: e.id, reverse=True), key=lambda e: e.lamport)
    assert [e.id for e in asc] != [e.id for e in desc], "no concurrent events forked"

    def root_of(order):
        w = Weave()
        for ev in order:
            w._apply(ev)
        return w.state_root()
    r_asc, r_desc, r_canon = root_of(asc), root_of(desc), k.weave().state_root()
    assert r_asc == r_desc == r_canon, (r_asc[:8], r_desc[:8], r_canon[:8])
    line(f"  all classes converge: ONE state_root in both arrival orders: {r_asc[:12]} ✓")

    w = k.weave()

    # Sequence: causal order is fixed ("The" before "Loom"); the two CONCURRENT
    # inserts after "Loom" tiebreak by (lamport, event_id) — a deterministic order
    # WITHIN a fold (so both arrival orders agree, asserted above via state_root),
    # but the event ids depend on the run's keys, so we assert the set, not which of
    # the two concurrent siblings wins the tiebreak. The tombstone must be gone.
    dc = w.get(doc)
    els = dc.content["elements"]
    assert els[:2] == ["The", "Loom"], els
    assert set(els[2:]) == {"weaves", "ships"} and len(els) == 4, els
    assert "DRAFT" not in els, "tombstone leaked"
    line(f"  Sequence: concurrent inserts converge, delete tombstoned → {els}")

    # Map: each key merged by its own class, independently.
    mc = w.get(wkr).content
    assert mc["envelope"] == ["shell"] and mc["budget"] == 15 and mc["brain"] == "rules", mc
    line(f"  Map: envelope(or-set)={mc['envelope']} ∥ budget(counter)={mc['budget']} "
         f"∥ brain(lww)={mc['brain']!r} — keys merge independently")

    # Counter + Append-log.
    assert w.get(cnt).content["value"] == 5
    assert sorted(e["line"] for e in w.get(lg).content["entries"]) == ["a", "b"]
    line(f"  Counter: 2+3 concurrent → {w.get(cnt).content['value']}; "
         f"Append-log: both observations kept → {[e['line'] for e in w.get(lg).content['entries']]}")

    # ===== adjudication: an ATTEST collapses the MV heads to one resolved value ==
    bc = w.get(bel)
    assert bc.in_conflict and len(bc.content_heads) == 2, bc.content_heads
    # a trusted principal (root) SELECTs Bob's head as the resolution, naming both
    # heads as evidence (binds only what it observed, §4.3).
    wf.append(root, ATTEST, {"target_cell": bel, "predicate": "adjudicates",
                             "resolution": "select", "winner": pb.id,
                             "evidence": [pa.id, pb.id], "claim": "owner resolved"})
    w2 = k.weave()
    rc = w2.get(bel)
    assert not rc.in_conflict and rc.content_heads == [{"text": "Bob owns it"}], rc.content_heads
    assert rc.content["text"] == "Bob owns it"
    # the superseded branch is gone from heads but still in history (provenance).
    assert pa.id in rc.provenance and pb.id in rc.provenance
    line(f"  Adjudication: MV heads [Alice|Bob] → ATTEST(select Bob) collapses to "
         f"{rc.content['text']!r}; conflict={rc.in_conflict}, loser kept in history")

    # adjudication is still arrival-order independent (the post-ATTEST fold converges).
    evs2 = list(wf.events())
    assert (root_of(sorted(evs2, key=lambda e: (e.lamport, e.id)))
            == root_of(sorted(sorted(evs2, key=lambda e: e.id, reverse=True),
                              key=lambda e: e.lamport)) == w2.state_root())
    line("  → §11 #2 holds across all merge classes incl. adjudication. "
         "State-machine (cap/task lifecycle) stays kernel-imperative; declarative class next.")
