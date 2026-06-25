"""HEALTH1 — private, sensitive health tracking that is DATA, never instruction.

Proves `decima.health` honors the recall-vs-instruct law and `scope` as an
authorization boundary: several metric points are recorded (every one
instruction_eligible=False, in a private scope); `history` returns them in order;
`trend` folds a correct INT summary (min/max/latest/delta); and a general /
out-of-scope recall does NOT surface the private health data.

Composes only PUBLIC APIs (health/memory/model). Contract: run(k, line). Fail loud.
"""
from decima import health, memory


def run(k, line):
    line("\n== HEALTH1 (private health · DATA not instruction · scope-isolated · int trend) ==")

    # ---- (1) record several points — all private, none instruction-eligible ----
    ids = [
        health.record(k, "weight", 82_000, unit="g"),     # int minor units (grams)
        health.record(k, "weight", 81_500, unit="g"),
        health.record(k, "weight", 80_750, unit="g"),
        health.record(k, "resting_hr", 58, unit="bpm"),
        health.record(k, "resting_hr", 55, unit="bpm"),
    ]
    w = k.weave()
    cells = [w.get(i) for i in ids]
    assert all(c is not None and c.type == health.HEALTH for c in cells)
    assert all(c.content["instruction_eligible"] is False for c in cells), \
        "health is sensitive DATA — never instruction-eligible"
    assert all(c.content["recallable"] is False for c in cells), \
        "health must not surface in general recall"
    assert all(c.content["scope"].startswith(health.SCOPE_PREFIX) for c in cells), \
        "every point lives in a private health scope"
    assert all(isinstance(c.content["value"], int) for c in cells), "values are ints"
    # provenance on the Weft (Law 4): each point grounded by a supported_by edge.
    assert all(w.edges_from(c.id, "supported_by") for c in cells), "points carry evidence"
    line(f"  recorded {len(ids)} points (weight x3, resting_hr x2) — all "
         f"instruction_eligible=False, recallable=False, private scope, int-valued ✓")

    # a float value is refused outright — ints only (WEFT §4/§7).
    try:
        health.record(k, "weight", 80.5, unit="kg")
        raised = False
    except TypeError:
        raised = True
    assert raised, "a float health value must be refused"
    line("  float value refused → ints in minor units only ✓")

    # ---- (2) history returns the recorded points, scope-filtered, in order -----
    hw = health.history(k, "weight")
    assert [p["value"] for p in hw] == [82_000, 81_500, 80_750], hw
    assert all(p["unit"] == "g" for p in hw)
    hr = health.history(k, "resting_hr")
    assert [p["value"] for p in hr] == [58, 55], hr
    # scope isolation: weight's history holds no resting_hr point and vice-versa.
    assert all(p["scope"] == health.health_scope("weight") for p in hw)
    assert all(p["scope"] == health.health_scope("resting_hr") for p in hr)
    line(f"  history(weight)={[p['value'] for p in hw]} g · "
         f"history(resting_hr)={[p['value'] for p in hr]} bpm — scope-filtered, in order ✓")

    # ---- (3) trend computes a correct INT summary (min/max/latest/delta) -------
    tw = health.trend(k, "weight")
    assert tw["min"] == 80_750 and tw["max"] == 82_000, tw
    assert tw["latest"] == 80_750 and tw["first"] == 82_000, tw
    assert tw["delta"] == -1_250 and tw["count"] == 3, tw   # 80750 - 82000
    assert all(isinstance(tw[kk], int) for kk in ("min", "max", "latest", "first", "delta"))
    thr = health.trend(k, "resting_hr")
    assert thr["latest"] == 55 and thr["delta"] == -3, thr   # 55 - 58
    assert health.trend(k, "blood_pressure") is None, "no points → no trend"
    line(f"  trend(weight): min={tw['min']} max={tw['max']} latest={tw['latest']} "
         f"delta={tw['delta']:+d} g (int) · trend(resting_hr).delta={thr['delta']:+d} bpm ✓")

    # ---- (4) general / out-of-scope recall does NOT leak the private data ------
    # general recall over the memory taxonomy can't even consider a `health` Cell,
    # and a scoped recall in the default realm finds nothing either.
    leak_general = memory.recall(w, "weight")                       # claims — empty
    leak_typed = memory.recall(w, "weight", memory_types=memory.MEMORY_TYPES)
    leak_realm = memory.recall(w, "weight", scope="realm:default",
                               memory_types=memory.MEMORY_TYPES)
    # even naming the private health scope through the general recall path yields
    # nothing: the points are recallable=False, so the retriever skips them.
    leak_scoped = memory.recall(w, "weight", scope=health.health_scope("weight"),
                                memory_types=memory.MEMORY_TYPES)
    assert leak_general == [] and leak_typed == [], (leak_general, leak_typed)
    assert leak_realm == [] and leak_scoped == [], (leak_realm, leak_scoped)
    # the data IS reachable through the private health API (scope-authorized).
    assert len(health.history(k, "weight")) == 3
    line(f"  general recall('weight')={len(leak_general)} · taxonomy-wide={len(leak_typed)} · "
         f"realm-scoped={len(leak_realm)} · health-scoped={len(leak_scoped)} — zero leak; "
         f"private API still returns {len(hw)} points ✓")

    line("  → health is sensitive DATA in a private scope: never an instruction, "
         "never surfaced by general recall, ints throughout, provenance on the Weft.")
