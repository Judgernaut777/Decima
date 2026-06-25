"""METRICS1 — read-only "Starmind" analytics over the Weave.

Generalizes wager.calibration into a generic dashboards layer: deterministic,
integer-only aggregations over cells of a type. This check proves:
  - count_by groups cells of a type by a content field (a histogram by severity);
  - total sums a numeric field across a type, as an int (never a float);
  - a NAMED metric reports a value WITH provenance (exactly which cells folded in);
  - a dashboard evaluates several metrics at once into a structured report;
  - it is DETERMINISTIC: recompute yields byte-identical numbers and provenance.

Read-only: builds cells via the public assert path, then only READS via metrics
(which compose `k.weave().of_type`). Fail loud.

Contract: run(k, line).
"""
from decima import metrics
from decima.model import assert_content
from decima.hashing import content_id


def run(k, line):
    line("\n== METRICS / DASHBOARDS (read-only analytics over the Weave) — METRICS1 ==")

    author = k.decima_agent_id

    # Build a few "finding" cells: a severity field (grouped) + a score field (summed).
    findings = [
        ("high",   30),
        ("low",    10),
        ("high",   20),
        ("medium", 15),
        ("low",     5),
    ]
    for i, (sev, score) in enumerate(findings):
        fid = content_id({"metrics_finding": i})
        assert_content(k.weft, author, fid, "m_finding",
                       {"severity": sev, "score": score})
    # One more with NO score — proves missing fields are skipped, not coerced.
    no_score = content_id({"metrics_finding": "no_score"})
    assert_content(k.weft, author, no_score, "m_finding", {"severity": "high"})

    # 1. count_by — a histogram of findings by severity (deterministic key order).
    hist = metrics.count_by(k, "m_finding", "severity")
    assert hist == {"high": 3, "low": 2, "medium": 1}, hist
    line(f"  count_by(m_finding, severity) → {hist}")

    # 2. total — integer sum of the numeric score field (the field-less cell adds 0).
    tot = metrics.total(k, "m_finding", "score")
    assert tot == 80 and isinstance(tot, int), tot
    line(f"  total(m_finding, score) → {tot} (int; field-less cell contributes 0)")

    # 3. A named metric with provenance: avg score over cells that HAVE a score.
    avg = metrics.metric(k, "mean_severity_score",
                         cell_type="m_finding", agg="avg", field="score")
    assert avg["value"] == 80 // 5 and avg["n"] == 5, avg            # floor(80/5) = 16
    assert no_score not in avg["cells"], avg                         # excluded: no score
    assert len(avg["cells"]) == 5 and avg["cells"] == sorted(avg["cells"]), avg
    line(f"  metric '{avg['name']}' (avg score) = {avg['value']} over n={avg['n']} cells; "
         f"provenance: {len(avg['cells'])} ids (field-less cell excluded ✓)")

    # 4. A dashboard — several metrics at once into one structured report.
    report = metrics.dashboard(k, [
        {"name": "n_findings", "cell_type": "m_finding", "agg": "count"},
        {"name": "scored",     "cell_type": "m_finding", "agg": "count", "field": "score"},
        {"name": "score_sum",  "cell_type": "m_finding", "agg": "sum",   "field": "score"},
        {"name": "score_avg",  "cell_type": "m_finding", "agg": "avg",   "field": "score"},
    ])
    assert report["order"] == ["n_findings", "scored", "score_sum", "score_avg"], report
    m = report["metrics"]
    assert m["n_findings"]["value"] == 6, m["n_findings"]            # all six cells
    assert m["scored"]["value"] == 5, m["scored"]                    # five have a score
    assert m["score_sum"]["value"] == 80, m["score_sum"]
    assert m["score_avg"]["value"] == 16, m["score_avg"]
    line(f"  dashboard → " + ", ".join(
        f"{name}={m[name]['value']}" for name in report["order"]))

    # 5. Determinism — a fresh recompute matches numbers AND provenance exactly.
    again = metrics.dashboard(k, [
        {"name": "n_findings", "cell_type": "m_finding", "agg": "count"},
        {"name": "score_avg",  "cell_type": "m_finding", "agg": "avg", "field": "score"},
    ])
    assert again["metrics"]["score_avg"] == m["score_avg"], (again, m)
    assert metrics.count_by(k, "m_finding", "severity") == hist
    line("  determinism: recompute matches values + provenance ✓")

    line("  → read-only, integer-only aggregations with provenance: a metric is a "
         "fold, so a dashboard is a reproducible report — not cached state.")
