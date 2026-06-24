"""M1 — merge layer, first increment: concurrent events fold deterministically.

The Weft is linear in normal operation; this check FORKS it (two events sharing a
parent — concurrent, neither causally before the other) and proves the four
properties of the first increment (specs/MERGE_SEMANTICS.md):

  - the forked Weft folds to the SAME state_root() in either arrival order —
    FOLD §11 #2 made GENUINELY concurrent, not trivially-linear;
  - LWW register resolves to the deterministic (lamport, event_id) winner;
  - OR-set merges add/remove by observed event identity, add-wins on concurrency;
  - an MV register PRESERVES both concurrent heads (FOLD §3), in conflict until
    adjudicated.

Sequence CRDT, Map CRDT, and semantic adjudication are deferred to later
increments (MERGE_SEMANTICS §6). Contract: run(k, line). Fail loud.
"""
from decima import model
from decima.weave import Weave, MERGE_LWW, MERGE_MV, MERGE_ORSET
from decima.weft import ASSERT
from decima.hashing import content_id


def run(k, line):
    line("\n== MERGE LAYER (concurrent events fold deterministically) ==")
    root, wf = k.root.id, k.weft

    # Declare the types and HOW they merge (the merge class rides on the TYPE_DEF).
    model.define_type(wf, root, "setting", merge_class=MERGE_LWW)
    model.define_type(wf, root, "headline", merge_class=MERGE_MV)
    model.define_type(wf, root, "tagset", merge_class=MERGE_ORSET)

    def at(parents, cell, typ, content):
        return wf.append(root, ASSERT,
                         {"cell": cell, "type": typ, "kind": "CONTENT", "content": content},
                         parents=parents)

    base = wf.head        # the shared parent every branch below forks from

    # LWW register: two CONCURRENT values for one setting cell (both parent=base).
    sett = content_id({"setting": "theme"})
    a = at([base], sett, "setting", {"value": "dark"})
    b = at([base], sett, "setting", {"value": "light"})       # concurrent with `a`
    lww_winner = "dark" if a.id > b.id else "light"           # max (lamport, eid); equal lamport

    # MV register: two concurrent headlines — both heads must survive.
    head = content_id({"headline": "lead"})
    at([base], head, "headline", {"text": "Loom ships"})
    at([base], head, "headline", {"text": "Loom delays"})     # concurrent

    # OR-set: add red, blue (sequential); then CONCURRENTLY remove red & add green;
    # then an add-wins probe — concurrently remove blue and re-add blue (a NEW add
    # the remove cannot have observed).
    tags = content_id({"tagset": "doc"})
    ar = at([base], tags, "tagset", {"op": "add", "element": "red"})
    ab = at([ar.id], tags, "tagset", {"op": "add", "element": "blue"})
    at([ab.id], tags, "tagset", {"op": "remove", "element": "red"})   # observes the red add
    ag = at([ab.id], tags, "tagset", {"op": "add", "element": "green"})  # concurrent w/ the remove
    at([ag.id], tags, "tagset", {"op": "remove", "element": "blue"})  # observes the blue add
    at([ag.id], tags, "tagset", {"op": "add", "element": "blue"})     # NEW add, concurrent → add-wins

    # ---- arrival-order independence over the GENUINELY-forked Weft ----------
    # Two valid topological orders that differ only in the order of concurrent
    # (equal-lamport) events: ids ascending vs descending within each lamport.
    evs = list(wf.events())
    order_asc = sorted(evs, key=lambda e: (e.lamport, e.id))
    order_desc = sorted(sorted(evs, key=lambda e: e.id, reverse=True),
                        key=lambda e: e.lamport)

    # the two orders must genuinely differ (a real fork exists) — else the test is vacuous
    assert [e.id for e in order_asc] != [e.id for e in order_desc], "no concurrent events forked"

    def root_of(order):
        w = Weave()
        for ev in order:
            w._apply(ev)
        return w.state_root()

    r_asc, r_desc, r_canon = root_of(order_asc), root_of(order_desc), k.weave().state_root()
    assert r_asc == r_desc == r_canon, (r_asc[:8], r_desc[:8], r_canon[:8])
    line(f"  forked Weft → ONE state_root in both arrival orders: {r_asc[:12]} ✓")

    # ---- per-class resolution on the canonical fold ------------------------
    w = k.weave()

    sc = w.get(sett)
    assert sc.content["value"] == lww_winner, (sc.content, lww_winner)
    assert len(sc.content_heads) == 1 and not sc.in_conflict, sc.content_heads
    line(f"  LWW: two concurrent writes → resolved to '{sc.content['value']}' "
         f"(max (lamport,id)); 1 head, conflict={sc.in_conflict}")

    hc = w.get(head)
    texts = sorted(h["text"] for h in hc.content_heads)
    assert hc.in_conflict and len(hc.content_heads) == 2, hc.content_heads
    assert texts == ["Loom delays", "Loom ships"], texts
    line(f"  MV: concurrent heads PRESERVED → {texts}; conflict={hc.in_conflict}")

    tc = w.get(tags)
    assert tc.content["elements"] == ["blue", "green"], tc.content["elements"]
    line(f"  OR-set: red removed (observed), green added concurrently, blue re-added "
         f"beats a concurrent remove → {tc.content['elements']}")

    line("  → arrival-order independence is now GENUINELY concurrent (FOLD §11 #2), "
         "not trivially linear. Sequence/Map CRDT + adjudication: deferred (§6).")
