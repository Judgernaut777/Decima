"""REACTOR1 — the reactive tick: the live heartbeat.

WATCH1, SCHED1 and JOBS1 each give Decima a *kind* of reactivity — a condition over the
Weave, a due reminder, a durable job — but each is fired by its own caller. REACTOR1 is the
single deterministic PASS that fires all three at one logical instant: given a frontier `now`,
`tick(k, now)` evaluates every armed watcher, fires every scheduled event due at `now`, and
runs every durable job due at `now` within its pre-fixed lease — in one ordered pass — and
returns a structured summary of everything that fired. `run_until(k, ticks)` drives a sequence
of ticks: a stub run-loop, the heartbeat beating, for demonstration.

This module is PURE COMPOSITION of the three lanes' PUBLIC APIs (watch.check_watchers,
scheduling.due/fire, jobs.due/run) plus disposition (transitively, through those). It does not
reimplement reactivity and it does not edit any of them.

The laws REACTOR1 keeps — every one inherited from the lanes it composes:

  - DETERMINISM. "now" is a LOGICAL tick (an int the caller supplies), never wall-clock.
    The pass is ordered (watchers → events → jobs; within each, the lane's own deterministic
    order) so two ticks at the same `now` over the same Weave fire the same set in the same
    order. ints not floats — a float `now` is rejected before it reaches any lane.

  - IDEMPOTENCE. Re-ticking the SAME `now` fires nothing already fired: a watcher trigger is
    content-addressed over (watcher, match) and skipped if live (WATCH1); a fired event is no
    longer `due` (SCHED1); a run job is no longer `due` (JOBS1). So `tick(k, now)` then
    `tick(k, now)` again is the second one a NO-OP. A tick with nothing due is a no-op too.

  - NO ESCALATION. A watcher / reminder / job firing does NOT bypass the gates. Watcher and
    event actions route through `disposition.dispose` (trusted-automation, still Morta-gated
    on an irreversible effect); a job runs through `kernel.invoke` on ONLY its pre-fixed lease,
    failing CLOSED past its window or over its budget. An untrusted-triggered action can never
    escalate itself — that law lives in DISP1/JOBS1 and REACTOR1 inherits it by composition.

  - EVERYTHING ON THE WEFT. Each fired action leaves its own audited Cells/edges (the trigger,
    the disposition, the fired event, the job transition). The tick adds NO new signed state of
    its own — it is a projection-and-fire over the lanes; the audit trail is theirs.

Public APIs only (watch / scheduling / jobs) — no core edit, no edit to those modules.
"""
from __future__ import annotations

from decima import watch
from decima import scheduling as sched
from decima import jobs
from decima import resume


def _int_tick(name: str, v) -> int:
    """Reject floats/bools — "now" is a LOGICAL int tick (DETERMINISM); the caller owns the
    clock and there is no wall-clock anywhere in the pass."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise TypeError(f"{name} must be an int logical tick, got {type(v).__name__}")
    return int(v)


def tick(k, now: int, *, author: str | None = None) -> dict:
    """Fire every reactive source due at logical frontier `now`, in ONE deterministic pass.

    The pass, in order:
      1. WATCHERS — evaluate every armed watcher over the current Weave and fire each match
         (watch.check_watchers). Each match records a `trigger` Cell + routes its action
         through disposition (trusted automation, still gated). Idempotent: a (watcher, match)
         pair fires at most once.
      2. SCHEDULED EVENTS — fire every event `due` at `now` (scheduling.due/fire), in the
         lane's (at, id) order. Each routes its action through disposition and marks fired
         (LWW); a repeating event reschedules itself. A fired event is not due again.
      3. JOBS — run every durable job `due` at `now` (jobs.due/run), in the lane's
         (run_at, id) order, using ONLY each job's pre-fixed lease. A job that over-reaches or
         missed its window fails CLOSED at the ocap gate; a run job is not due again.

    Returns a structured summary::

        {"now": now,
         "watchers": [trigger_id, ...],            # WATCH1 trigger Cell ids
         "events":   [{"event", "disposition", "rescheduled"}, ...],   # SCHED1 fires
         "jobs":     [{"job", "status", "denied"?}, ...],              # JOBS1 runs
         "fired":    <total count across all three>,
         "quiet":    <bool: True iff nothing fired — a no-op tick>}

    DETERMINISM: ordered pass, logical `now` (int). IDEMPOTENCE: re-ticking the same `now`
    over the same Weave returns an all-empty summary (`quiet=True`) — everything already fired
    is skipped by the lanes themselves. NO new signed state is added by the tick; the audit
    trail is the lanes' own Cells/edges on the Weft.
    """
    now = _int_tick("now", now)
    author = author or k.decima_agent_id

    # ── 1. WATCHERS ──────────────────────────────────────────────────────────
    # Fold the live Weave, fire each armed watcher's matches. Already-fired (watcher, match)
    # pairs are skipped by WATCH1 (content-addressed trigger), so this is idempotent.
    watcher_triggers = list(watch.check_watchers(k, author=author))

    # ── 2. SCHEDULED EVENTS due at `now` ─────────────────────────────────────
    # due(now) is a pure projection (at <= now, not yet fired) in (at, id) order. Snapshot the
    # ids first, then fire — firing re-asserts the Cell (marks fired LWW), and a repeating
    # event reschedules to at + interval (a fresh, future, not-yet-due Cell), so it can't
    # re-fire within this same pass. A fired event is not in due() again → idempotent.
    event_fires = []
    for event in sched.due(k, now):
        res = sched.fire(k, event.id, now, author=author)
        event_fires.append({
            "event": res["event"],
            "disposition": res["disposition"]["disposition"],
            "action": res["disposition"]["action"],
            "rescheduled": res["rescheduled"],
        })

    # ── 3a. CRASH RECOVERY (always-on / crash-resumable) ─────────────────────
    # BEFORE running due jobs, repair any job whose effect ALREADY fired pre-restart but
    # whose DONE/FAILED transition was lost to a crash in jobs.run's window. recover()
    # reads each such job's own receipt and marks its TRUE outcome WITHOUT re-invoking — so
    # the naive due-lane below never re-runs a fired job into a false FAILED (the exhausted
    # single-use lease would deny it). Fires no effect, adds no authority; idempotent (a
    # tick with nothing to recover is a no-op here).
    recovery = resume.recover(k, now, author=author)

    # ── 3b. JOBS due at `now` ────────────────────────────────────────────────
    # due(now) is a pure projection (run_at <= now, status enqueued) in (run_at, id) order.
    # Each job runs through ONLY its pre-fixed lease (jobs.run → kernel.invoke); an over-reach
    # or a missed window fails CLOSED there and marks the job failed. A run/failed job is not
    # in due() again → idempotent. The runner principal holds nothing but the lease.
    job_runs = []
    for job in jobs.due(k, now):
        runner = k.weave().get(job.content["runner"])
        res = jobs.run(k, runner, job.id, now)
        entry = {"job": res["job"], "status": res["status"]}
        if "denied" in res:
            entry["denied"] = res["denied"]
        job_runs.append(entry)

    fired = len(watcher_triggers) + len(event_fires) + len(job_runs)
    return {
        "now": now,
        "watchers": watcher_triggers,
        "events": event_fires,
        "jobs": job_runs,
        "recovered": recovery["reconciled"],   # crash-fired jobs repaired to their true outcome
        "fired": fired,
        "quiet": fired == 0,
    }


def run_until(k, ticks, *, start: int | None = None, author: str | None = None) -> list:
    """Drive a sequence of ticks — a stub run-loop, the heartbeat beating — for demonstration.

    `ticks` is either an iterable of explicit logical frontiers to tick at, OR an int count N,
    in which case we tick at `start, start+1, …, start+N-1` (default `start = k.weft.lamport`).
    Each frontier is ticked once via `tick(k, now)`; the per-tick summaries are returned in
    order. DETERMINISM: the frontiers are an explicit, caller-owned sequence of ints — no
    wall-clock drives the loop. The loop fires only what each `now` makes due; quiet ticks are
    genuine no-ops, so this is safe to run over a sparse schedule.
    """
    if isinstance(ticks, int) and not isinstance(ticks, bool):
        if ticks < 0:
            raise ValueError("a tick count must be non-negative")
        base = _int_tick("start", start) if start is not None else int(k.weft.lamport)
        frontiers = [base + i for i in range(ticks)]
    else:
        frontiers = [_int_tick("tick", t) for t in ticks]
    return [tick(k, now, author=author) for now in frontiers]
