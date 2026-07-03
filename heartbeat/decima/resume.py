"""RESUME — crash-resumable durable execution across a restart (Phase 4 · always-on).

Everything durable already lives on the Weft, so a fresh Kernel over the same log rebuilds
the whole job queue by folding — durability across a restart is STRUCTURAL, not a feature we
add. The subtle gap is a CRASH WINDOW inside `jobs.run`: it appends the INVOKE (the effect +
its `result` receipt) and THEN, as a SEPARATE append, re-asserts the job Cell as DONE. A
crash between those two appends leaves the job `ENQUEUED` with its effect ALREADY fired.

The single-use lease already makes the effect EXACTLY-ONCE: the INVOKE event is the durable
use-record, folded by `kernel.lease_uses`, so a re-run after restart is denied by the
exhausted lease — the effect cannot fire twice. But a naive restart then RE-RUNS the still-
enqueued job, the denied re-invoke marks it `FAILED`, and the job's recorded outcome LIES
(it says failed when the effect SUCCEEDED). That is the gap this module closes.

`recover(k, now)` is the WAL-style repair pass to run BEFORE the normal due-lane: for each
still-`ENQUEUED` job whose lease has ALREADY fired, it reads the job's own `result` receipt
and reconciles the job to the TRUE outcome (DONE / FAILED) WITHOUT re-invoking — so no second
effect, no false failure. A job that never fired is left untouched for the normal lane to run
(recover repairs; it does not run fresh work). An ambiguous (UNKNOWN) receipt is NOT
fabricated into a definite outcome (FOLD §11 #8) — the job is reported `unresolved` and left
for the kernel's own multi-attempt reconciliation. Idempotent: a second `recover` finds the
repaired jobs no longer `ENQUEUED` and does nothing.

Public APIs only (`jobs`, `kernel.weave`, `model.assert_content`, the `result`/INVOKE fold).
No core edit; every number is a logical int; recovery ADDS no authority and fires no effect.
"""
from __future__ import annotations

from decima.model import assert_content
from decima import jobs

RESULT = "result"


def _int_tick(name: str, v) -> int:
    if not isinstance(v, int) or isinstance(v, bool):
        raise TypeError(f"{name} must be an int logical tick, got {type(v).__name__}")
    return int(v)


def _lease_invoke_events(weave, lease_id) -> set:
    """The INVOKE event ids this lease has authorized — the durable use-records folded
    from the Log (the same signal `kernel.lease_uses` counts)."""
    return {i.event for i in weave.invocations if i.cap == lease_id}


def _receipt_for(weave, lease_id):
    """The latest `result` (EffectReceipt) descending from an INVOKE this lease authorized,
    or None. Deterministic: the receipt's `of` names its INVOKE event (WEFT §8)."""
    inv_events = _lease_invoke_events(weave, lease_id)
    receipts = [c for c in weave.of_type(RESULT) if c.content.get("of") in inv_events]
    receipts.sort(key=lambda c: c.id)
    return receipts[-1] if receipts else None


def fired(weave, job_cell) -> bool:
    """True iff the job's pre-fixed lease has already authorized at least one INVOKE — i.e.
    the effect fired (possibly in a pre-crash run whose DONE transition never landed)."""
    return bool(_lease_invoke_events(weave, job_cell.content["lease"]))


def _reconcile(k, job_cell) -> str | None:
    """Reconcile a fired-but-enqueued job to the TRUTH carried by its receipt, WITHOUT
    re-invoking. Returns the new job status (`jobs.DONE`/`jobs.FAILED`), or `"unresolved"`
    for an UNKNOWN/absent receipt (left ENQUEUED — never fabricated into a definite outcome)."""
    weave = k.weave()
    receipt = _receipt_for(weave, job_cell.content["lease"])
    if receipt is None:
        return "unresolved"                      # fired but no receipt folded yet — leave it
    rstatus = receipt.content.get("status")
    if rstatus == "SUCCEEDED":
        new_status = jobs.DONE
    elif rstatus == "FAILED":
        new_status = jobs.FAILED
    else:                                        # UNKNOWN — FOLD §11 #8: do not fabricate
        return "unresolved"
    base = weave.get(job_cell.id).content
    assert_content(k.weft, k.decima.id, job_cell.id, jobs.JOB, {
        **base, "status": new_status, "result": receipt.id, "recovered": True,
    })
    return new_status


def recover(k, now: int, *, author: str | None = None) -> dict:
    """Crash-recovery pass — reconcile every job whose effect ALREADY fired but whose
    DONE/FAILED transition was lost to a crash, using each job's receipt (no re-invoke).

    Run this BEFORE the normal due-lane (`jobs.due`/`reactor.tick`): a reconciled job is no
    longer `ENQUEUED`, so the naive lane never re-runs a fired job into a false FAILED. A job
    that never fired is left for that lane to run; recover repairs, it does not start fresh work.

    Returns::

        {"now": now,
         "reconciled": [{"job", "status"}, ...],   # fired jobs repaired to their true outcome
         "unresolved": [job_id, ...],              # fired but receipt UNKNOWN/absent (left as-is)
         "recovered":  <count of reconciled>}

    Idempotent: a second call finds the reconciled jobs no longer `ENQUEUED` and is a no-op.
    Deterministic (logical `now`); ADDS no authority and fires NO effect."""
    now = _int_tick("now", now)
    weave = k.weave()
    enqueued = [c for c in weave.of_type(jobs.JOB)
                if c.content.get("status") == jobs.ENQUEUED]
    reconciled, unresolved = [], []
    for job in sorted(enqueued, key=lambda c: (int(c.content["run_at"]), c.id)):
        if not fired(weave, job):
            continue                             # never fired → the normal lane runs it
        outcome = _reconcile(k, job)
        if outcome in (jobs.DONE, jobs.FAILED):
            reconciled.append({"job": job.id, "status": outcome})
        else:
            unresolved.append(job.id)
    return {
        "now": now,
        "reconciled": reconciled,
        "unresolved": unresolved,
        "recovered": len(reconciled),
    }
