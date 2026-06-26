"""DATASCI1 — a read-only data-science / analytics layer over the folded Weave.

`metrics.py` (METRICS1) gives single-number aggregates over a cell type — a
histogram, a sum, a named metric with provenance. This module is the *richer*
shape that data science needs: a tabular FRAME (rows = cells, cols = fields),
GROUP-BY aggregation, integer summary STATS (describe), and a Vega-Lite-style
CHART SPEC — the structured description of a plot, never a rendering.

Everything here is a FOLD (Law 5): a pure projection over `k.weave().of_type`,
never stored state. Two evaluations of the same Weft yield byte-identical frames,
groups, stats, and specs (and the same provenance), so a chart spec IS a
reproducible report — not a cached side-channel.

LAWS honored here:
  - READ-ONLY over the Weave: nothing appends to the Weft; we only call public
    read APIs (`k.weave()` → `of_type` / `Cell.content` / `Cell.id`) and compose
    `metrics`.
  - DETERMINISTIC: cells are read in sorted-id order, columns in caller order,
    group keys in a total type-then-value order — all arrival-order independent.
  - INTEGERS, NOT FLOATS (WEFT §4/§7): every aggregate (sum/min/max/mean) is an
    int; a mean is the integer floor `sum // n`. We never produce a float.
  - TRUST BOUNDARY / NO EVAL: a cell's `content` is untrusted DATA. A field value
    is READ, never evaluated — there is no `eval`/`exec`/formula interpreter here.
    A "formula" column (`derive`) is expressed as a small set of NAMED, built-in
    safe ops over already-read int fields; an unknown op is rejected, never run.
    A non-numeric / missing field is skipped (counted absent), never coerced.
  - PROVENANCE: a frame carries the sorted cell ids it folded over, so any view
    derived from it is auditable back to exactly the cells that built it.

Public surface:
  frame(k, *, cell_type, fields)             → tabular view {cell_type, fields, rows, ids}
  group_by(k, frame, by, *, agg, field=None) → grouped aggregation {by, agg, field, groups, order}
  describe(k, frame, field)                  → int summary stats {count,min,max,sum,mean,...}
  pivot(k, frame, index, column, *, agg, field=None) → 2-D grouped table
  chart_spec(k, frame, *, kind, x, y)        → a Vega-Lite-style data+encoding spec
"""

from decima import metrics

# Group/stat aggregations. mean is an INTEGER floor (sum // n), never a float.
AGGS = ("count", "sum", "min", "max", "mean")
# Vega-Lite-style chart kinds we emit a spec for (we describe, never render).
CHART_KINDS = ("bar", "point", "line")
# Named, safe row-derivation ops (the NON-eval "formula" boundary): each is a
# pure int function of already-read int fields. There is no expression parser.
_DERIVE_OPS = ("add", "sub", "mul")


def _as_int(v):
    """Numeric coercion under the trust boundary, matching metrics._as_int: an int
    (but NOT a bool — bool is an int subclass and True/1 must not conflate) counts;
    anything else (str, None, float-in-content, …) is absent. We never float."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return None


def frame(k, *, cell_type, fields):
    """A tabular view over live cells of `cell_type`: rows are cells (in sorted-id
    order), columns are the named `fields` read straight from each cell's content.

    Returns a dict {cell_type, fields, rows, ids} where:
      - `fields` is the requested column order (a tuple — the schema of the frame);
      - `rows`  is a list of {field: value} dicts, one per cell, in id order, each
                holding ONLY the requested fields (a missing field reads as None —
                untrusted content is read, never coerced or executed);
      - `ids`   is the parallel list of contributing cell ids (the provenance).

    Pure read: composes `metrics._cells` (sorted-id fold) + `metrics._field`. A
    frame is a projection — recomputing it over the same Weft is byte-identical."""
    fields = tuple(fields)
    if not fields:
        raise ValueError("frame requires at least one field")
    rows, ids = [], []
    for cell in metrics._cells(k, cell_type):
        rows.append({f: metrics._field(cell, f) for f in fields})
        ids.append(cell.id)
    return {"cell_type": cell_type, "fields": fields, "rows": rows, "ids": ids}


def derive(k, frame, name, *, op, left, right):
    """Add a safe DERIVED column to a frame WITHOUT eval (the injection boundary).

    A "formula" here is NOT a string parsed/eval'd from untrusted content — it is a
    NAMED built-in op (`add`/`sub`/`mul`) applied to two already-read int columns.
    An unknown op is REJECTED, never executed. A row where either operand is absent
    /non-numeric yields None for the derived cell (skipped, never coerced). Returns
    a NEW frame (the input is not mutated) — frames are values, like every fold."""
    if op not in _DERIVE_OPS:
        raise ValueError(f"unknown derive op {op!r}; expected one of {_DERIVE_OPS}")
    new_rows = []
    for row in frame["rows"]:
        a, b = _as_int(row.get(left)), _as_int(row.get(right))
        if a is None or b is None:
            val = None
        elif op == "add":
            val = a + b
        elif op == "sub":
            val = a - b
        else:  # mul
            val = a * b
        new_rows.append({**row, name: val})
    return {
        "cell_type": frame["cell_type"],
        "fields": frame["fields"] + (name,),
        "rows": new_rows,
        "ids": list(frame["ids"]),
    }


def _agg(agg, values):
    """Apply an integer aggregation over a list of ints. `mean` is the integer floor
    sum // n (never a float); empty input yields None for min/max/mean, 0 for sum,
    and (for count) the caller passes the membership list so count is just len."""
    if agg not in AGGS:
        raise ValueError(f"unknown agg {agg!r}; expected one of {AGGS}")
    if agg == "count":
        return len(values)
    if not values:
        return 0 if agg == "sum" else None
    if agg == "sum":
        return sum(values)
    if agg == "min":
        return min(values)
    if agg == "max":
        return max(values)
    return sum(values) // len(values)   # mean — integer floor


def group_by(k, frame, by, *, agg, field=None):
    """Grouped aggregation over a frame: partition rows by their `by` column value,
    then aggregate each group.

      - agg "count": how many rows per group (field ignored);
      - agg in (sum/min/max/mean): the integer aggregate of `field` over the rows in
        the group that HAVE a numeric field (field required; rows whose field is
        absent/non-numeric are skipped, never coerced).

    Returns {by, agg, field, groups, order} where `groups` maps each group key to
    {value, n} (n = rows that fed the number) and `order` lists the keys in a total,
    arrival-order-independent order (metrics._sort_key: None < bools < ints < strs).
    A `mean` is the integer floor. Pure: recompute is byte-identical."""
    if agg not in AGGS:
        raise ValueError(f"unknown agg {agg!r}; expected one of {AGGS}")
    if agg != "count" and field is None:
        raise ValueError(f"agg {agg!r} requires a field")

    buckets = {}          # group key -> list of contributing int values
    counts = {}           # group key -> total rows in the group
    for row in frame["rows"]:
        key = row.get(by)
        counts[key] = counts.get(key, 0) + 1
        buckets.setdefault(key, [])
        if agg != "count":
            v = _as_int(row.get(field))
            if v is not None:
                buckets[key].append(v)

    order = sorted(buckets, key=metrics._sort_key)
    groups = {}
    for key in order:
        if agg == "count":
            groups[key] = {"value": counts[key], "n": counts[key]}
        else:
            vals = buckets[key]
            groups[key] = {"value": _agg(agg, vals), "n": len(vals)}
    return {"by": by, "agg": agg, "field": field, "groups": groups, "order": order}


def describe(k, frame, field):
    """Deterministic INTEGER summary stats for a numeric `field` of a frame.

    Returns {field, count, n_missing, min, max, sum, mean} where:
      - `count`     = rows with a numeric value for the field (fed the stats);
      - `n_missing` = rows where the field was absent / non-numeric (skipped);
      - min/max/sum are ints; `mean` is the integer floor sum // count;
      - for count == 0: min/max/mean are None and sum is 0.

    No float ever appears (WEFT §7). Pure read over the frame's already-read values
    — recompute is byte-identical."""
    vals = []
    n_missing = 0
    for row in frame["rows"]:
        v = _as_int(row.get(field))
        if v is None:
            n_missing += 1
        else:
            vals.append(v)
    return {
        "field": field,
        "count": len(vals),
        "n_missing": n_missing,
        "min": _agg("min", vals),
        "max": _agg("max", vals),
        "sum": _agg("sum", vals),
        "mean": _agg("mean", vals),
    }


def pivot(k, frame, index, column, *, agg, field=None):
    """A 2-D grouped table (a pivot): rows keyed by the `index` column, sub-columns
    keyed by the `column` column, each cell the integer `agg` of `field` over the
    rows in that (index, column) bucket.

    Returns {index, column, agg, field, index_order, column_order, cells} where
    `cells` maps index_key -> {column_key: {value, n}} and the two *_order lists give
    the deterministic total order of row and column keys. `count` ignores `field`;
    other aggs require it (rows with absent/non-numeric field are skipped). Pure."""
    if agg not in AGGS:
        raise ValueError(f"unknown agg {agg!r}; expected one of {AGGS}")
    if agg != "count" and field is None:
        raise ValueError(f"agg {agg!r} requires a field")

    buckets = {}          # (idx, col) -> list of int values
    counts = {}           # (idx, col) -> total rows
    idx_keys, col_keys = set(), set()
    for row in frame["rows"]:
        ik, ck = row.get(index), row.get(column)
        idx_keys.add(ik)
        col_keys.add(ck)
        bk = (ik, ck)
        counts[bk] = counts.get(bk, 0) + 1
        buckets.setdefault(bk, [])
        if agg != "count":
            v = _as_int(row.get(field))
            if v is not None:
                buckets[bk].append(v)

    index_order = sorted(idx_keys, key=metrics._sort_key)
    column_order = sorted(col_keys, key=metrics._sort_key)
    cells = {}
    for ik in index_order:
        rowmap = {}
        for ck in column_order:
            bk = (ik, ck)
            if bk not in counts:
                continue
            if agg == "count":
                rowmap[ck] = {"value": counts[bk], "n": counts[bk]}
            else:
                vals = buckets[bk]
                rowmap[ck] = {"value": _agg(agg, vals), "n": len(vals)}
        cells[ik] = rowmap
    return {
        "index": index, "column": column, "agg": agg, "field": field,
        "index_order": index_order, "column_order": column_order, "cells": cells,
    }


def chart_spec(k, frame, *, kind, x, y):
    """A Vega-Lite-style chart SPEC — a structured data + encoding description, NOT a
    rendering. We emit the spec a renderer would consume; the Heartbeat never draws.

    `kind` is one of CHART_KINDS (bar/point/line). `x` and `y` are field names that
    MUST be columns of the frame (rejected otherwise — a spec only encodes data it
    actually has). The returned spec is a plain, JSON-serializable dict:

        {"$schema", "mark", "data": {"values": [...]}, "encoding": {"x": {...},
         "y": {...}}, "provenance": {"cell_type", "n", "ids"}}

    `data.values` is the frame's rows (the read values — never executed), so the
    spec is self-contained and DETERMINISTIC: same Weft → byte-identical spec. It is
    intended to be stored as a structured Cell (the caller asserts it); building it
    here appends nothing to the Weft (read-only)."""
    if kind not in CHART_KINDS:
        raise ValueError(f"unknown chart kind {kind!r}; expected one of {CHART_KINDS}")
    fields = frame["fields"]
    for axis, name in (("x", x), ("y", y)):
        if name not in fields:
            raise ValueError(f"chart {axis} field {name!r} not in frame fields {fields}")
    # x is nominal unless every present value is an int; y is the measure (quantitative).
    x_vals = [row.get(x) for row in frame["rows"]]
    x_type = "quantitative" if all(_as_int(v) is not None for v in x_vals if v is not None) and any(
        _as_int(v) is not None for v in x_vals) else "nominal"
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": kind,
        "data": {"values": [dict(row) for row in frame["rows"]]},
        "encoding": {
            "x": {"field": x, "type": x_type},
            "y": {"field": y, "type": "quantitative"},
        },
        "provenance": {
            "cell_type": frame["cell_type"],
            "n": len(frame["ids"]),
            "ids": list(frame["ids"]),
        },
    }
