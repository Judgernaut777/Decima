"""B3: memory maturation — decay, consolidation, heat/promotion."""
from decima import memory, model, retrieval
from decima.hashing import content_id


def _last_result(k):
    return k.weave().of_type("result")[-1].id


def run(k, line):
    line("\n== MEMORY MATURATION (decay · consolidation · heat) ==")
    author = k.human.id

    # Two equally relevant claims: recency/decay should rank the fresh one above
    # the stale one before heat changes the picture.
    src = _last_result(k)
    stale = memory.remember_semantic(
        k.weft, author, "Alice owns the antique archive budget", src,
        confidence=800_000, event_time=10)
    fresh = memory.remember_semantic(
        k.weft, author, "Bob owns the archive budget", src,
        confidence=800_000, event_time=100)
    ranker = retrieval.LexicalRetriever()
    first = memory.recall(k.weave(), "archive budget owns",
                          memory_types=(memory.SEMANTIC,), retriever=ranker)
    assert first[0].id == fresh, [c.content["text"] for c in first[:3]]
    line(f"  decay/recency: stale={memory.recency(k.weave(), k.weave().get(stale))} "
         f"fresh={memory.recency(k.weave(), k.weave().get(fresh))} → fresh ranks first")

    # Recall a query only the stale claim matches. That appends access-signal
    # Cells; no mutable in-memory counter is involved.
    for _ in range(4):
        memory.recall_with_heat(k.weft, author, k.weave(), "antique",
                                memory_types=(memory.SEMANTIC,), retriever=ranker)
    heated = k.weave()
    assert memory.heat(heated, stale) == 4, memory.heat(heated, stale)
    promoted_ranker = retrieval.LexicalRetriever(heat_weight=50, recency_weight=1)
    promoted = memory.recall(heated, "archive budget owns",
                             memory_types=(memory.SEMANTIC,), retriever=promoted_ranker)
    assert promoted[0].id == stale, [c.content["text"] for c in promoted[:3]]
    line(f"  heat: repeated recall wrote {memory.heat(heated, stale)} access signal(s); "
         "heated stale memory now outranks the fresher peer")

    # Build two near-duplicates with distinct evidence, then consolidate them.
    src1 = content_id({"b3_evidence": "one"})
    model.assert_content(k.weft, author, src1, "result",
                         {"out": "b3 evidence one", "status": "SUCCEEDED"})
    dup1 = memory.remember_semantic(
        k.weft, author, "Release plan uses blue path", src1,
        confidence=700_000, event_time=200)
    src2 = content_id({"b3_evidence": "two"})
    model.assert_content(k.weft, author, src2, "result",
                         {"out": "b3 evidence two", "status": "SUCCEEDED"})
    dup2 = memory.remember_semantic(
        k.weft, author, "Plan release uses blue path", src2,
        confidence=710_000, event_time=201)

    consolidated = memory.consolidate(
        k.weft, author, k.weave(), "release plan blue",
        memory_type=memory.SEMANTIC)
    assert consolidated, "duplicate memories did not consolidate"
    w = k.weave()
    c = w.get(consolidated)
    assert set(c.content["supersedes"]) == {dup1, dup2}, c.content
    why = memory.why(w, k.weft, consolidated)
    assert {src1, src2}.issubset(set(why["supported_by"])), why
    assert {dup1, dup2}.issubset(set(why["derived_from"])), why
    current = memory.recall(w, "release plan blue",
                            memory_types=(memory.SEMANTIC,), retriever=ranker)
    assert current[0].id == consolidated, [x.id for x in current[:3]]
    assert dup1 not in {x.id for x in current} and dup2 not in {x.id for x in current}
    line(f"  consolidation: {dup1[:8]} + {dup2[:8]} → {consolidated[:8]} "
         f"with evidence={len(why['supported_by'])} derived_from={len(why['derived_from'])}")
