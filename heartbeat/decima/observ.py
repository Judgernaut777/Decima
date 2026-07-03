"""OBSERVABILITY — operational metrics folded from the Weft (Law 5: a lens, no storage).

VISION "operational metrics / live spend metering": the operator (and Decima's own doctor
routines) need ONE deterministic, int-only report of what actually happened — how many
events, how many invokes were denied, how many jobs are stuck, how much has been spent —
without adding a single new Cell to record it. Every other lane already keeps its own
ground truth on the Weft (jobs.py's `job` Cells, spend.py's `spend_charge` Cells, redact.py's
`redaction` Cells, the kernel's `result` receipts); this module adds NOTHING new to sign. It
only READS, composing the SAME public projections `weave.canary_health` already uses
(`of_type`, `.invocations`, `receipts_for_idempotency`'s sibling `of_type("result")`).

Law 5 kept literally: `metrics(k)` is a PURE fold — calling it twice, or calling it and then
diffing `len(weave.cells)`/event count, must be byte-identical / zero-growth. It asserts
NOTHING, mints NO capability, holds no state of its own between calls — every call re-derives
from scratch, exactly like `workspace.py`'s lenses and `weave.canary_health`'s fold.

Ints-not-floats (Law/DECIMA house rule): every value `metrics()` returns is a plain `int`
(never a float, never a bool masquerading as an int) — enforced by `_ct` at the door of the
report builder, so a caller can trust every number here is signed-content-grade.

Public APIs only: `kernel.weave()`, `Weave.of_type`, `Weave.invocations`, `weft.events()`,
`jobs.JOB`/status constants, `spend.CHARGE`, `redact.REDACTION`/`SECRET_SENSITIVE`. No core
edit; no new Cell type; no new capability.
"""
from __future__ import annotations

from decima.weft import ASSERT, RETRACT, INVOKE, ATTEST, VERBS
from decima import jobs as _jobs
from decima import spend as _spend
from decima import redact as _redact


def _ct(x) -> int:
    """Ints-not-floats at the door: every number this lens reports must be a plain int
    (never a float, never a bool-as-int) — a report the operator/doctor can trust is
    signed-content-grade even though nothing here is actually signed."""
    if not isinstance(x, int) or isinstance(x, bool):
        raise TypeError(f"observ metric must be an int, got {type(x).__name__}: {x!r}")
    return x


def _is_failure_receipt(c) -> bool:
    """The SAME failure predicate `weave.canary_health` uses (WEFT §8): a receipt is a
    denial/failure when its status is FAILED or it carried `ok: False`. Reused here, not
    reimplemented, so 'denial' means the same thing everywhere in Decima."""
    return c.content.get("status") == "FAILED" or c.content.get("ok") is False


def _per_capability(weave) -> list:
    """Reuse the canary-health idiom (weave.canary_health): fold per-capability
    invocations/failures from `.invocations` + `result` receipts — but here across EVERY
    capability that was ever invoked, not just one CANARY. Sorted by cap id for a
    deterministic report order (DETERMINISM: no dict-iteration-order leakage)."""
    caps = sorted({i.cap for i in weave.invocations})
    out = []
    for cap_id in caps:
        cap_cell = weave.get(cap_id)
        name = (cap_cell.content.get("name") if cap_cell is not None else None) or cap_id[:8]
        inv_events = {i.event for i in weave.invocations if i.cap == cap_id}
        receipts = [c for c in weave.of_type("result") if c.content.get("of") in inv_events]
        failures = [r for r in receipts if _is_failure_receipt(r)]
        out.append({
            "cap": cap_id,
            "name": name,
            "invocations": _ct(len(inv_events)),
            "failures": _ct(len(failures)),
        })
    return out


def metrics(k) -> dict:
    """Fold ONE deterministic, int-only operational report from the Weft — a pure
    projection (Law 5): reads only, asserts NOTHING, mints NO capability, adds NO Cell.

    Shape (every leaf an int, except `per_capability`'s cap/name strings)::

        {"events_total": int,
         "by_verb": {"ASSERT": int, "RETRACT": int, "INVOKE": int, "ATTEST": int},
         "invocations": int,
         "denials": int,                         # INVOKEs whose receipt is FAILED/denied
         "receipts_by_status": {"SUCCEEDED": int, "FAILED": int, "UNKNOWN": int},
         "by_effect_class": {class_name: int, ...},
         "jobs": {"enqueued": int, "done": int, "failed": int, "recovered": int},
         "spend_microcents": int,                # sum of spend_charge cells, 0 if none
         "redactions": {"total": int, "secret_sensitive": int},
         "per_capability": [{"cap", "name", "invocations", "failures"}, ...]}

    Deterministic: two calls on the same fold return an identical dict (no wall-clock, no
    randomness, no arrival-order dependence — the same discipline `weave.canary_health` and
    `spend.SpendMeter` already keep)."""
    weave = k.weave()

    # -- events / by_verb: a pure tally over the Log (WEFT verb set) -----------------
    by_verb = {v: 0 for v in VERBS}
    events_total = 0
    for ev in k.weft.events():
        events_total += 1
        by_verb[ev.verb] = by_verb.get(ev.verb, 0) + 1

    # -- invocations / denials / receipts_by_status / by_effect_class ---------------
    invocations = len(weave.invocations)
    receipts = weave.of_type("result")
    receipts_by_status = {"SUCCEEDED": 0, "FAILED": 0, "UNKNOWN": 0}
    by_effect_class: dict = {}
    denials = 0
    for r in receipts:
        st = r.content.get("status", "UNKNOWN")
        receipts_by_status[st] = receipts_by_status.get(st, 0) + 1
        ec = r.content.get("effect_class", "READ")
        by_effect_class[ec] = by_effect_class.get(ec, 0) + 1
        if _is_failure_receipt(r):
            denials += 1

    # -- jobs: folded straight from jobs.JOB Cells' `status` (JOBS1/RESUME) ---------
    job_cells = weave.of_type(_jobs.JOB)
    jobs_report = {"enqueued": 0, "done": 0, "failed": 0, "recovered": 0}
    for j in job_cells:
        st = j.content.get("status")
        if st == _jobs.ENQUEUED:
            jobs_report["enqueued"] += 1
        elif st == _jobs.DONE:
            jobs_report["done"] += 1
        elif st == _jobs.FAILED:
            jobs_report["failed"] += 1
        if j.content.get("recovered") is True:
            jobs_report["recovered"] += 1

    # -- spend: sum of spend_charge cells (0 if the meter was never configured) -----
    spend_microcents = sum(int(c.content.get("microcents", 0))
                           for c in weave.of_type(_spend.CHARGE))

    # -- redactions: folded from redact.py's `redaction` provenance Cells -----------
    redaction_cells = weave.of_type(_redact.REDACTION)
    redactions = {
        "total": len(redaction_cells),
        "secret_sensitive": sum(1 for c in redaction_cells
                                if c.content.get("classification") == _redact.SECRET_SENSITIVE),
    }

    report = {
        "events_total": _ct(events_total),
        "by_verb": {v: _ct(n) for v, n in by_verb.items()},
        "invocations": _ct(invocations),
        "denials": _ct(denials),
        "receipts_by_status": {s: _ct(n) for s, n in receipts_by_status.items()},
        "by_effect_class": {ec: _ct(n) for ec, n in by_effect_class.items()},
        "jobs": {s: _ct(n) for s, n in jobs_report.items()},
        "spend_microcents": _ct(spend_microcents),
        "redactions": {k2: _ct(v2) for k2, v2 in redactions.items()},
        "per_capability": _per_capability(weave),
    }
    return report


def dashboard_lines(k) -> list:
    """Human-readable one-line-per-metric display lines (like `workspace.notes`/`board`) —
    a lens for a shell 'metrics'/'observe' view. Pure: derived entirely from `metrics(k)`."""
    m = metrics(k)
    lines = []
    lines.append(f"events        total={m['events_total']}  "
                 + "  ".join(f"{v}={n}" for v, n in m["by_verb"].items()))
    lines.append(f"invocations   total={m['invocations']}  denials={m['denials']}")
    lines.append("receipts      " + "  ".join(f"{s}={n}" for s, n in
                                               m["receipts_by_status"].items()))
    if m["by_effect_class"]:
        lines.append("effect_class  " + "  ".join(f"{ec}={n}" for ec, n in
                                                    sorted(m["by_effect_class"].items())))
    j = m["jobs"]
    lines.append(f"jobs          enqueued={j['enqueued']}  done={j['done']}  "
                 f"failed={j['failed']}  recovered={j['recovered']}")
    lines.append(f"spend         microcents={m['spend_microcents']}")
    r = m["redactions"]
    lines.append(f"redactions    total={r['total']}  secret_sensitive={r['secret_sensitive']}")
    for pc in m["per_capability"]:
        lines.append(f"  cap {pc['cap'][:8]}  {pc['name']:<20} "
                     f"invocations={pc['invocations']}  failures={pc['failures']}")
    return lines
