"""Health / wellness tracking — PRIVATE, sensitive memory as DATA, never instruction.

Health is the sharpest test of the recall-vs-instruct law and of `scope` as an
authorization boundary (Codex MEMORY_ARCHITECTURE §5; CAPABILITY_MAP Part B): a
person's metrics are sensitive DATA that must (a) never be treated as an
instruction and (b) never leak into a general recall. We get this STRUCTURALLY,
three ways at once:

  - **own Cell type.** Each point is a `health` Cell — a type that is NOT in
    memory's recall taxonomy (CLAIM/EPISODIC/.../GOVERNANCE), so a general
    `memory.recall(...)` over those types cannot even consider it.
  - **private scope.** Every point carries a per-subject private `scope`
    (`health:private:<metric>`); `history` filters by it, so out-of-scope reads
    return nothing.
  - **non-recallable + not instruction-eligible.** `recallable=False` and
    `instruction_eligible=False` are stamped on the Cell, the same boundaries the
    browser receipt obeys — even a retriever that did look at `health` Cells would
    skip them, and the brain may never act on one as a command.

Provenance lives on the Weft: each point asserts a `supported_by` evidence edge to
the utterance/receipt that grounded it (WEFT §4/Law 4). Values are INTS in minor
units (WEFT §4/§7: never a float) — e.g. weight in grams, a heart rate in bpm,
steps as a count. `trend` folds the recorded points into a deterministic int
summary (min/max/latest/delta) — a read-only fold, not a new authority.

This module OWNS only heartbeat/decima/health.py and composes the PUBLIC model API
(`model.assert_content`/`assert_edge`/`define_type`); it adds no kernel code.
"""
from __future__ import annotations

from decima import model
from decima.hashing import content_id, nfc

HEALTH = "health"
# A private scope keyed to the metric. General recall is scoped to a realm
# (e.g. "realm:default"); a health scope never collides with it, so even a
# scope-blind query for general memory cannot name this scope by accident.
SCOPE_PREFIX = "health:private"


def health_scope(metric: str) -> str:
    """The private scope a metric's points live in — never a general realm scope."""
    return f"{SCOPE_PREFIX}:{nfc(metric)}"


def _point_id(metric: str, value: int, seq: int) -> str:
    return content_id({"health": nfc(metric), "value": int(value), "seq": int(seq)})


def record(k, metric: str, value: int, *, unit: str | None = None,
           author: str | None = None, evidence_src: str | None = None) -> str:
    """Record one private health point and return its Cell id.

    `value` is an INT in minor units (grams, bpm, steps, ...). The point is stamped
    `instruction_eligible=False` and `recallable=False` in a private `scope`, so it
    is DATA that general recall cannot surface and the brain can never obey. A
    `supported_by` edge grounds it on the Weft (provenance); `evidence_src` defaults
    to the agent's own utterance/identity when no external receipt is given.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("health value must be an int in minor units (never a float)")
    metric = nfc(metric)
    author = author or k.decima_agent_id
    scope = health_scope(metric)
    seq = k.weft.count() + 1                    # deterministic, log-positioned id
    cid = _point_id(metric, value, seq)
    content = {
        "metric": metric,
        "value": int(value),
        "unit": nfc(unit) if unit is not None else None,
        "scope": scope,
        "seq": seq,
        # the four permissions (Codex §5): DATA only — never an instruction,
        # never surfaced by general recall.
        "instruction_eligible": False,
        "recallable": False,
        "citable": False,
    }
    model.assert_content(k.weft, author, cid, HEALTH, content)
    # provenance on the Weft: ground the point in evidence (Law 4).
    model.assert_edge(k.weft, author, cid, "supported_by",
                      evidence_src or author)
    return cid


def _points(k, metric: str) -> list:
    """All health Cells for `metric`, scope-filtered, in record (seq) order."""
    scope = health_scope(metric)
    metric = nfc(metric)
    out = [c for c in k.weave().of_type(HEALTH)
           if c.content.get("metric") == metric
           and c.content.get("scope") == scope]      # authorization-first filter
    return sorted(out, key=lambda c: int(c.content.get("seq", 0)))


def history(k, metric: str) -> list:
    """The recorded points for `metric` as DATA dicts (scope-filtered, in order)."""
    return [{
        "id": c.id,
        "metric": c.content["metric"],
        "value": int(c.content["value"]),
        "unit": c.content.get("unit"),
        "scope": c.content["scope"],
    } for c in _points(k, metric)]


def trend(k, metric: str) -> dict | None:
    """A deterministic INT summary of a metric: min/max/latest/delta over points.

    `delta` is latest − first (the net movement). Returns None when nothing is on
    record. All figures are ints in the metric's minor units — a read-only fold.
    """
    pts = _points(k, metric)
    if not pts:
        return None
    values = [int(c.content["value"]) for c in pts]
    return {
        "metric": nfc(metric),
        "unit": pts[-1].content.get("unit"),
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "latest": values[-1],
        "first": values[0],
        "delta": values[-1] - values[0],
    }
