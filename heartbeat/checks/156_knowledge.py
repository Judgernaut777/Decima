"""KNOW1: knowledge-graph queries over the folded Weave.

Builds a small linked graph of cells (assert_content + assert_edge) and proves
the four read-only traversals: `neighbors` (adjacent cells, rel-filtered),
`path` (shortest edge path; None when unreachable within depth), `subgraph`
(the reachable set), and `related` (ranked by shared structure). Determinism is
asserted by folding twice and comparing. Pure read-only — asserts nothing during
the queries themselves.
"""
from decima import knowledge, model
from decima.hashing import content_id


def run(k, line):
    line("\n== KNOWLEDGE GRAPH (neighbors · path · subgraph · related) ==")
    author = k.human.id
    weft = k.weft

    # Build a deterministic little topology of 'topic' cells:
    #   a → b → c → d   (chain, rel='leads_to')
    #   a → e           (branch, rel='cites')
    #   x  (isolated; unreachable from a)
    # Two entity-shared topics b and f both 'about' entity ent (no direct edge).
    ids = {n: content_id({"know1_topic": n}) for n in ("a", "b", "c", "d", "e", "x", "f")}
    ent = content_id({"know1_entity": "shared"})
    for n, cid in ids.items():
        model.assert_content(weft, author, cid, "topic", {"name": n})
    model.assert_content(weft, author, ent, "entity", {"name": "shared"})

    model.assert_edge(weft, author, ids["a"], "leads_to", ids["b"])
    model.assert_edge(weft, author, ids["b"], "leads_to", ids["c"])
    model.assert_edge(weft, author, ids["c"], "leads_to", ids["d"])
    model.assert_edge(weft, author, ids["a"], "cites", ids["e"])
    model.assert_edge(weft, author, ids["b"], "about", ent)
    model.assert_edge(weft, author, ids["f"], "about", ent)

    # -- neighbors: adjacency in/out, plus rel-filtering -----------------------
    nbrs = {c.id for c in knowledge.neighbors(k, ids["a"])}
    assert nbrs == {ids["b"], ids["e"]}, nbrs                 # out-edges of a
    back = {c.id for c in knowledge.neighbors(k, ids["b"])}
    assert back == {ids["a"], ids["c"], ent}, back            # in (a) + out (c, ent)
    filt = {c.id for c in knowledge.neighbors(k, ids["a"], rel="cites")}
    assert filt == {ids["e"]}, filt                          # rel filter drops b
    assert knowledge.neighbors(k, ids["x"]) == [], "isolated cell has no neighbors"
    line(f"  neighbors(a)={len(nbrs)} (b,e); rel='cites'→1 (e); "
         f"neighbors(b) sees a (in) + c,ent (out)")

    # -- path: shortest edge path, and unreachable → None ----------------------
    p = knowledge.path(k, ids["a"], ids["d"], max_depth=5)
    assert p is not None and len(p) == 3, p                  # a→b→c→d is 3 hops
    assert [h["dst"] for h in p] == [ids["b"], ids["c"], ids["d"]], p
    assert all(h["rel"] == "leads_to" for h in p), p
    assert knowledge.path(k, ids["a"], ids["a"], max_depth=3) == [], "self path is empty"
    too_shallow = knowledge.path(k, ids["a"], ids["d"], max_depth=2)
    assert too_shallow is None, too_shallow                  # 3 hops > depth 2
    assert knowledge.path(k, ids["a"], ids["x"], max_depth=9) is None, "x is unreachable"
    line(f"  path(a→d)={len(p)} hops (leads_to×3); depth=2→None; a→x→None (disconnected)")

    # -- subgraph: the reachable frontier within depth -------------------------
    sg2 = knowledge.subgraph(k, ids["a"], depth=2)
    assert set(sg2["cells"]) == {ids["a"], ids["b"], ids["e"], ids["c"], ent}, sg2["cells"]
    assert sg2["depths"][ids["a"]] == 0 and sg2["depths"][ids["c"]] == 2, sg2["depths"]
    sg_full = knowledge.subgraph(k, ids["a"], depth=9)
    assert ids["d"] in sg_full["cells"] and ids["x"] not in sg_full["cells"], sg_full["cells"]
    assert ids["f"] in sg_full["cells"], "f reachable via shared entity ent"
    line(f"  subgraph(a,depth=2)={len(sg2['cells'])} cells, {len(sg2['edges'])} edges; "
         f"full reach={len(sg_full['cells'])} (x excluded)")

    # -- related: ranked by shared structure -----------------------------------
    rel = knowledge.related(k, ids["b"])
    rel_ids = {r["cell"].id for r in rel}
    assert ids["a"] in rel_ids and ids["c"] in rel_ids, rel_ids   # direct neighbors
    assert ids["f"] in rel_ids, "f shares entity ent with b"      # shared hub, no direct edge
    by_id = {r["cell"].id: r for r in rel}
    assert by_id[ids["a"]]["direct"] and not by_id[ids["f"]]["direct"], by_id
    assert by_id[ids["f"]]["shared"] >= 1, by_id[ids["f"]]
    assert all(isinstance(r["score"], int) for r in rel), "scores are ints, not floats"
    line(f"  related(b)={len(rel)} cells; top={rel[0]['cell'].content['name']} "
         f"score={rel[0]['score']} (direct neighbors + entity-shared f)")

    # -- determinism: a second independent fold yields identical answers -------
    p2 = knowledge.path(k, ids["a"], ids["d"], max_depth=5)
    sg2b = knowledge.subgraph(k, ids["a"], depth=2)
    assert p == p2, (p, p2)
    assert sg2 == sg2b, (sg2, sg2b)
    rel2 = [(r["cell"].id, r["score"]) for r in knowledge.related(k, ids["b"])]
    assert rel2 == [(r["cell"].id, r["score"]) for r in rel], "related is deterministic"
    line("  deterministic: re-folded path/subgraph/related are byte-identical")
