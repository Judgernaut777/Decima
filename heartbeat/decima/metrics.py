"""METRICS1 — a generic "Starmind" analytics layer over the folded Weave.

Receipts say what happened; the Wager/Verdict loop (`wager.py`) learns which
*decisions* work. This module generalizes that idea into read-only **dashboards**:
deterministic, integer-only aggregations over cells of a type — a histogram of
findings by severity, the total spend across a batch of orders, an average score
reported with the exact cells it summarized.

Law 5: a metric is a fold — a pure projection over `weave.of_type`, never stored
state. Two evaluations of the same Weft yield the same numbers (and the same
provenance), so a dashboard IS a reproducible report, not a cached side-channel.

LAWS honored here:
  - READ-ONLY: nothing appends to the Weft; we only call public read APIs
    (`k.weave()` → `of_type` / `Cell.content` / `Cell.id`).
  - DETERMINISTIC: cells are processed in sorted-id order, so sums, group order,
    and provenance are arrival-order independent.
  - INTEGERS, NOT FLOATS (WEFT §4/§7): rates/averages are ints (an average is the
    integer floor sum//n; rates would be millionths like `wager.calibration`).
  - TRUST BOUNDARY: a cell's `content` is untrusted DATA. A missing/non-numeric
    field is skipped (counted as absent), never coerced or executed.

Public surface:
  count_by(k, cell_type, field)          → histogram {value: count}
  total(k, cell_type, field)             → int sum of a numeric field
  metric(k, name, *, cell_type, agg)     → a named metric + provenance
  dashboard(k, specs)                    → several metrics → one structured report
"""

AGGS = ("count", "sum", "avg")


def _cells(k, cell_type):
    """Live cells of a type in deterministic (sorted-id) order. The sort is what
    makes sums, group ordering, and provenance independent of fold/arrival order."""
    return sorted(k.weave().of_type(cell_type), key=lambda c: c.id)


def _field(cell, field):
    """A cell's content field, or None when absent. `content` is untrusted data:
    a non-dict content (defensive) yields None rather than raising."""
    c = cell.content
    return c.get(field) if isinstance(c, dict) else None


def _as_int(v):
    """Numeric coercion under the trust boundary: an int (but NOT a bool — bool is
    an int subclass in Python, and True/1 must not conflate) counts; anything else
    (str, None, float-in-content, …) is treated as absent. We never float."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return None


def count_by(k, cell_type, field):
    """Histogram of live cells of `cell_type` grouped by a content `field` — e.g.
    findings by severity, orders by status. Returns a dict {field_value: count}.

    Cells missing the field group under the sentinel key None. Keys are emitted in
    deterministic order: first by a stable type tag, then by value (so a heterogeneous
    field — ints beside strs — still orders the same every fold)."""
    counts = {}
    for cell in _cells(k, cell_type):
        key = _field(cell, field)
        counts[key] = counts.get(key, 0) + 1
    return {key: counts[key] for key in sorted(counts, key=_sort_key)}


def total(k, cell_type, field):
    """Integer sum of a numeric `field` across every live cell of `cell_type`.
    Non-numeric / missing fields contribute nothing (never coerced). Result is an
    int (WEFT §7: no float in an aggregate over signed content)."""
    s = 0
    for cell in _cells(k, cell_type):
        v = _as_int(_field(cell, field))
        if v is not None:
            s += v
    return s


def metric(k, name, *, cell_type, agg, field=None):
    """A NAMED metric with provenance — the cells it summarized.

    `agg` is one of:
      - "count": how many live cells of `cell_type` (field optional: if given,
                 counts only those whose field is present);
      - "sum":   integer sum of `field` over the type (field required);
      - "avg":   integer average (floor) of `field` over the cells that HAVE a
                 numeric field — sum // n, or None for n == 0 (field required).

    Returns {name, agg, cell_type, field, value, n, cells} where `cells` is the
    sorted list of contributing cell ids (the provenance: exactly which cells the
    number folds over, so the report is auditable and reproducible)."""
    if agg not in AGGS:
        raise ValueError(f"unknown agg {agg!r}; expected one of {AGGS}")
    if agg in ("sum", "avg") and field is None:
        raise ValueError(f"agg {agg!r} requires a field")

    contributing = []      # cell ids that actually fed the number (provenance)
    s = 0
    n = 0
    for cell in _cells(k, cell_type):
        if agg == "count":
            if field is not None and _field(cell, field) is None:
                continue
            contributing.append(cell.id)
            n += 1
        else:  # sum / avg over the numeric field
            v = _as_int(_field(cell, field))
            if v is None:
                continue
            contributing.append(cell.id)
            s += v
            n += 1

    if agg == "count":
        value = n
    elif agg == "sum":
        value = s
    else:  # avg — integer floor, None when nothing contributed
        value = (s // n) if n else None

    return {
        "name": name, "agg": agg, "cell_type": cell_type, "field": field,
        "value": value, "n": n, "cells": contributing,
    }


def dashboard(k, specs):
    """Evaluate several named metrics at once into one structured report.

    `specs` is an ordered iterable of metric specs; each is either a dict of
    kwargs for `metric` (must carry `name`, `cell_type`, `agg`, optional `field`)
    or a (name, kwargs) pair. Returns
        {"metrics": {name: <metric report>}, "order": [name, …]}
    The `order` list preserves spec order so a renderer is deterministic; `metrics`
    keys each metric by name. Duplicate names are rejected (a dashboard is a record,
    and a name must address exactly one metric)."""
    out = {}
    order = []
    for spec in specs:
        if isinstance(spec, tuple):
            name, kwargs = spec
            kwargs = {"name": name, **kwargs}
        else:
            kwargs = dict(spec)
        name = kwargs["name"]
        if name in out:
            raise ValueError(f"duplicate metric name in dashboard: {name!r}")
        m = metric(k, kwargs.pop("name"), cell_type=kwargs.pop("cell_type"),
                   agg=kwargs.pop("agg"), field=kwargs.pop("field", None))
        if kwargs:
            raise ValueError(f"unexpected metric spec keys for {name!r}: {sorted(kwargs)}")
        out[name] = m
        order.append(name)
    return {"metrics": out, "order": order}


def _sort_key(v):
    """Deterministic ordering for heterogeneous group keys (None, ints, strs, …).
    Sort first by a stable type tag, then by a string form of the value, so the
    histogram order is total and arrival-order independent regardless of the field's
    runtime types."""
    if v is None:
        return (0, "")
    if isinstance(v, bool):
        return (1, str(int(v)))
    if isinstance(v, int):
        return (2, f"{v:020d}" if v >= 0 else f"-{-v:020d}")
    return (3, str(v))
