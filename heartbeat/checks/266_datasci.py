"""DATASCI1 — read-only data-science / analytics over the Weave.

`metrics.py` gives single-number aggregates; `datasci.py` is the richer shape:
a tabular FRAME (rows = cells, cols = fields), GROUP-BY, integer summary STATS,
a PIVOT, and a Vega-Lite-style CHART SPEC (structured — never a rendering). This
check proves:
  - frame() builds a tabular view over live cells of a type (rows = cells, in
    sorted-id order; cols = the requested fields; missing field reads as None);
  - group_by() partitions rows by a column and aggregates correctly — integer
    sums/counts/mean (mean is the integer floor, never a float);
  - describe() gives correct INTEGER summary stats (count/min/max/sum/mean) and
    counts missing fields rather than coercing them;
  - chart_spec() emits a STRUCTURED Vega-Lite-style spec (data + encoding) with
    provenance — no rendering, just the spec;
  - it is DETERMINISTIC: a fresh recompute is byte-identical;
  - NO EVAL: a field value is read, never evaluated — even a "derived" column is a
    named safe op, and an injected formula string is data, never executed.

Read-only: builds cells via the public assert path, then only READS via datasci
(which composes metrics over `k.weave().of_type`). Fail loud.

Contract: run(k, line).
"""
from decima import datasci
from decima.model import assert_content
from decima.hashing import content_id


def run(k, line):
    line("\n== DATA SCIENCE / ANALYTICS (frame · group_by · describe · chart spec) — DATASCI1 ==")

    author = k.decima_agent_id

    # Build "sample" cells: a region (group key), a units field (numeric), and ONE
    # cell whose units carries an INJECTED FORMULA STRING — to prove it is treated as
    # untrusted DATA (skipped as non-numeric), never parsed or evaluated.
    samples = [
        ("west",  10),
        ("east",  30),
        ("west",  20),
        ("east",  40),
        ("west",  30),
    ]
    for i, (region, units) in enumerate(samples):
        sid = content_id({"ds_sample": i})
        assert_content(k.weft, author, sid, "ds_sample",
                       {"region": region, "units": units})
    # A cell with a malicious "formula" as the units value — must be read as data,
    # never evaluated. If datasci ever eval'd a field, this would blow up loudly.
    evil = content_id({"ds_sample": "evil"})
    assert_content(k.weft, author, evil, "ds_sample",
                   {"region": "west", "units": "__import__('os').system('echo PWNED')"})

    # 1. frame — a tabular view (rows = cells in sorted-id order, cols = fields).
    fr = datasci.frame(k, cell_type="ds_sample", fields=["region", "units"])
    assert fr["fields"] == ("region", "units"), fr
    assert len(fr["rows"]) == 6 and len(fr["ids"]) == 6, fr           # 5 numeric + 1 evil
    assert fr["ids"] == sorted(fr["ids"]), fr                         # deterministic id order
    assert all(set(r) == {"region", "units"} for r in fr["rows"]), fr  # only requested cols
    # The evil cell's units is present as the RAW STRING — data, not executed.
    raw_strs = [r["units"] for r in fr["rows"] if isinstance(r["units"], str)]
    assert raw_strs == ["__import__('os').system('echo PWNED')"], raw_strs
    line(f"  frame(ds_sample, [region, units]) → {len(fr['rows'])} rows × {len(fr['fields'])} cols; "
         f"injected formula held as raw data (never eval'd) ✓")

    # 2. group_by — count per region, and integer SUM of units per region.
    g_count = datasci.group_by(k, fr, "region", agg="count")
    assert g_count["order"] == ["east", "west"], g_count
    assert g_count["groups"]["west"]["value"] == 4, g_count          # 3 numeric + 1 evil
    assert g_count["groups"]["east"]["value"] == 2, g_count
    g_sum = datasci.group_by(k, fr, "region", agg="sum", field="units")
    # west numeric: 10+20+30 = 60 (evil string skipped); east: 30+40 = 70.
    assert g_sum["groups"]["west"]["value"] == 60, g_sum
    assert g_sum["groups"]["west"]["n"] == 3, g_sum                  # evil skipped, not coerced
    assert g_sum["groups"]["east"]["value"] == 70, g_sum
    assert isinstance(g_sum["groups"]["east"]["value"], int), g_sum
    g_mean = datasci.group_by(k, fr, "region", agg="mean", field="units")
    assert g_mean["groups"]["west"]["value"] == 60 // 3 == 20, g_mean  # integer floor
    assert g_mean["groups"]["east"]["value"] == 70 // 2 == 35, g_mean
    line(f"  group_by(region) → counts {{west:4, east:2}}; sum units {{west:60, east:70}} "
         f"(evil string skipped); mean(int) {{west:20, east:35}} ✓")

    # 3. describe — integer summary stats over the units field of the whole frame.
    d = datasci.describe(k, fr, "units")
    # numeric units: 10,30,20,40,30 → count 5, missing 1 (evil), min 10, max 40,
    # sum 130, mean floor(130/5)=26.
    assert d["count"] == 5 and d["n_missing"] == 1, d
    assert d["min"] == 10 and d["max"] == 40, d
    assert d["sum"] == 130 and d["mean"] == 130 // 5 == 26, d
    assert all(isinstance(d[s], int) for s in ("count", "n_missing", "min", "max", "sum", "mean")), d
    line(f"  describe(units) → count={d['count']} missing={d['n_missing']} "
         f"min={d['min']} max={d['max']} sum={d['sum']} mean={d['mean']} (all ints) ✓")

    # 3b. derive — a SAFE named-op column (NO eval): units doubled via mul, not a string.
    fr2 = datasci.derive(k, fr, "double_units", op="mul", left="units", right="units")
    # The evil row's units is non-numeric → derived cell is None (skipped, not run).
    doubled = datasci.describe(k, fr2, "double_units")
    assert doubled["count"] == 5 and doubled["n_missing"] == 1, doubled
    assert doubled["max"] == 40 * 40, doubled                       # 1600
    line(f"  derive(double_units = units·units) via NAMED safe op (no eval); "
         f"max={doubled['max']} ✓")

    # 4. pivot — region × units-bucket count (a 2-D grouped table, deterministic).
    pv = datasci.pivot(k, fr, "region", "units", agg="count")
    assert pv["index_order"] == ["east", "west"], pv
    # east has units 30 and 40 → two buckets, one row each.
    assert pv["cells"]["east"][30]["value"] == 1, pv
    assert pv["cells"]["east"][40]["value"] == 1, pv
    line(f"  pivot(region × units, count) → index {pv['index_order']}, "
         f"{len(pv['column_order'])} unit columns ✓")

    # 5. chart_spec — a structured Vega-Lite-style spec (data + encoding), NO render.
    grouped_rows = [{"region": r, "units": g_sum["groups"][r]["value"]}
                    for r in g_sum["order"]]
    chart_frame = {"cell_type": "ds_sample", "fields": ("region", "units"),
                   "rows": grouped_rows, "ids": list(fr["ids"])}
    spec = datasci.chart_spec(k, chart_frame, kind="bar", x="region", y="units")
    assert spec["mark"] == "bar", spec
    assert spec["encoding"]["x"] == {"field": "region", "type": "nominal"}, spec
    assert spec["encoding"]["y"] == {"field": "units", "type": "quantitative"}, spec
    assert spec["data"]["values"] == grouped_rows, spec             # self-contained data
    assert spec["provenance"]["n"] == 6 and "$schema" in spec, spec
    # It is a SPEC, not a rendering: plain JSON-serializable structure, no image bytes.
    import json
    json.dumps(spec)                                                 # must serialize cleanly
    line(f"  chart_spec(bar, x=region, y=units) → Vega-Lite spec, {len(spec['data']['values'])} "
         f"data values + encoding (structured, no rendering) ✓")

    # 6. determinism — a fresh recompute is byte-identical (a fold, not cached state).
    fr_again = datasci.frame(k, cell_type="ds_sample", fields=["region", "units"])
    assert fr_again == fr, "frame recompute drifted"
    assert datasci.group_by(k, fr_again, "region", agg="sum", field="units") == g_sum
    assert datasci.describe(k, fr_again, "units") == d
    assert datasci.chart_spec(k, chart_frame, kind="bar", x="region", y="units") == spec
    line("  determinism: frame + group_by + describe + chart_spec recompute byte-identical ✓")

    line("  → read-only, integer-only frames/group-by/stats + a structured chart spec: "
         "every view is a fold over the Weave, and a field value is data — never eval'd.")
