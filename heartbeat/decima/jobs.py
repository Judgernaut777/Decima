"""JOBS1 — durable job queue: a scheduled job is a FUTURE AUTHORITY GRANT.

CAPABILITY_MAP B2 (task queues / durable execution): "A scheduled job is a *future
authority grant* — fix its capability set at enqueue, Morta-review." This module makes
that literal. Enqueuing a job does not just record "run X later"; it FIXES the authority
the job will ever wield, at enqueue time, by minting a LEASE (LEASE1) — a capability
attenuated to exactly the job's needs, time-locked to its run window (`expires_at` near
`run_at`) and use-bounded (`max_uses`). That lease IS the future authority grant: it is a
`capability` Cell on the Weft, Morta-reviewable/revocable like any other grant, and the
ONLY authority the job may ever use.

The laws this lane keeps (every one composed from existing PUBLIC kernel APIs):

  - AUTHORITY FIXED AT ENQUEUE. `enqueue` calls `k.spawn` to mint a downhill, signed,
    attenuated grant (the lease) to a fresh job-runner principal — its capability set is
    frozen the moment the job is enqueued. Running the job later can use ONLY that grant.

  - NO ESCALATION / NO AMBIENT AUTHORITY. `run` invokes through `kernel.invoke`, whose
    `authorize` gate (envelope + grantee + downhill delegation + caveats) is the SAME ocap
    check as everything else. An attempt to use authority beyond the lease — a different
    capability, a cost over the lease budget — fails CLOSED at that gate. The runner holds
    NOTHING but the one lease.

  - TIME-LOCK FAILS CLOSED. The lease carries `expires_at` (a LEASE caveat). `run` past the
    expiry frontier is denied by `lease_status` exactly like a revoked grant — a job that
    missed its window cannot fire late with stale authority.

  - DETERMINISM. "now" is the LOGICAL frontier (lamport / an int tick the caller supplies),
    never wall-clock. `run_at`, `expires_at`, `max_uses` are ints — no float reaches signed
    content. `due(k, now)` is a pure projection over the fold.

  - EVERYTHING ON THE WEFT. The job is a `job` Cell; its state transitions
    (enqueued → done/failed) are LWW re-asserts on that Cell; the lease is a `capability`
    Cell; the schedule is a `scheduled_event` Cell. The whole queue is a fold over the Log.

Public APIs only — scheduling.schedule/due, kernel.spawn/invoke/weave, capability leases,
model.assert_content/assert_edge. No core edit.
"""
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc
from decima import scheduling as sched

JOB = "job"

# Lifecycle states a job Cell moves through (LWW on the same id).
ENQUEUED = "enqueued"
DONE = "done"
FAILED = "failed"


def _int_tick(name: str, v) -> int:
    """Reject floats/bools — only an int logical tick may reach signed content (§1)."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise TypeError(f"{name} must be an int logical tick, got {type(v).__name__}")
    return int(v)


def _job_id(name: str, run_at: int, lamport: int) -> str:
    """Content-address the job by (name, run_at, enqueue frontier) so two enqueues of
    the same name at the same run_at are distinct durable jobs (distinct frontiers)."""
    return content_id({"job": nfc(name), "run_at": int(run_at), "n": int(lamport)})


def enqueue(k, name: str, *, capability: str, run_at: int, max_uses: int = 1,
            budget: int | None = None, window: int = 16) -> str:
    """Enqueue a durable job that FIXES its capability set at enqueue by minting a LEASE.

    `capability` is the id of a capability the orchestrator HOLDS; the job's authority is
    a downhill attenuation of it — a lease scoped to exactly this job, granted to a fresh
    single-purpose runner principal. The lease is time-locked to the job's RUN WINDOW
    `[run_at, run_at + window)` (`expires_at = run_at + window`, default headroom 16 ticks)
    so it is live from `run_at` and lapses once the window closes — a job that missed its
    slot cannot fire late with stale authority. The lease is also use-bounded (`max_uses`,
    default a single fire); optionally clamp its `budget` too.

    Returns the `job` Cell id. The job is scheduled via SCHED1 at `run_at` and is
    Morta-reviewable: the lease is an ordinary `capability` Cell on the Weft, revocable.

    "now"/`run_at`/`window` are LOGICAL ticks (ints); a float is rejected before signing.
    """
    run_at = _int_tick("run_at", run_at)
    max_uses = _int_tick("max_uses", max_uses)
    window = _int_tick("window", window)
    if max_uses <= 0:
        raise ValueError("max_uses must be a positive number of uses")
    if window <= 0:
        raise ValueError("window must be a positive number of ticks")
    if budget is not None:
        budget = _int_tick("budget", budget)

    name = nfc(name)
    decima = k.weave().get(k.decima_agent_id)
    expires_at = run_at + window

    # Fix the authority AT ENQUEUE: mint a lease (downhill attenuated grant) to a fresh
    # job-runner principal. expires_at closes the run window — live from run_at, fails
    # closed once the window passes (a job that missed its slot cannot fire late).
    stricter = {"expires_at": expires_at, "max_uses": max_uses}
    if budget is not None:
        stricter["budget"] = budget
    runner_id, lease_id, _runner = k.spawn(
        decima, f"job-runner:{name}", capability, stricter,
        objective=f"run durable job {name!r} at tick {run_at} with pre-fixed authority")

    # The job is a Cell on the Weft. Its authority is the lease id; its runner is the
    # principal that holds it. status tracked as data, transitioned LWW.
    job_id = _job_id(name, run_at, k.weft.lamport)
    assert_content(k.weft, k.decima.id, job_id, JOB, {
        "name": name,
        "run_at": run_at,
        "lease": lease_id,         # THE future authority grant — fixed here, forever
        "runner": runner_id,       # the principal that will use only that lease
        "max_uses": max_uses,
        "expires_at": expires_at,
        "status": ENQUEUED,
        "result": None,
    })

    # Schedule it (SCHED1) and bind the schedule to the job + the job to its lease, so the
    # whole future-authority-grant is one connected provenance subgraph on the Weft.
    event_id = sched.schedule(k, f"job:{job_id}", run_at, author=k.decima.id)
    assert_edge(k.weft, k.decima.id, job_id, "scheduled_as", event_id)
    assert_edge(k.weft, k.decima.id, job_id, "authority", lease_id)
    return job_id


def due(k, now: int) -> list:
    """The enqueued jobs whose `run_at <= now` and not yet run, in (run_at, id) order.

    A pure projection over the fold — `now` is the LOGICAL frontier the caller supplies
    (no wall-clock). Future jobs (run_at > now) and already-run jobs are excluded."""
    now = _int_tick("now", now)
    out = [c for c in k.weave().of_type(JOB)
           if c.content.get("status") == ENQUEUED and int(c.content["run_at"]) <= now]
    out.sort(key=lambda c: (int(c.content["run_at"]), c.id))
    return out


def run(k, agent, job, now: int, *, args: dict | None = None) -> dict:
    """Execute a due job using ONLY its pre-fixed lease, at logical frontier `now`.

    `agent` is the runner agent Cell that holds the lease (the job's `runner`). `job` is
    a job Cell id (or Cell). The job runs by invoking its lease through the kernel's ocap
    path — so an attempt to use authority beyond the lease, OR to run past the lease's
    expiry (`expires_at`), FAILS CLOSED at the same gate as any other invoke. On success
    the job is marked done (LWW); a closed-fail marks it failed and records the denial.

    `args` are the invocation args for the lease's effect (e.g. {"text": ...}); a `cost`
    over the lease budget is itself a fail-closed over-reach. Returns the kernel invoke
    result augmented with {"job": id, "status": new_status}."""
    now = _int_tick("now", now)
    job_id = job if isinstance(job, str) else job.id
    cell = k.weave().get(job_id)
    if cell is None or cell.type != JOB:
        raise ValueError(f"no job {job_id!r}")
    if cell.content.get("status") != ENQUEUED:
        raise ValueError(f"job {job_id!r} already {cell.content.get('status')} "
                         "(durable: a job runs at most once to completion)")
    if int(cell.content["run_at"]) > now:
        raise ValueError(f"job {job_id!r} not due (run_at {cell.content['run_at']} > now {now})")

    lease = cell.content["lease"]

    # Run using ONLY the pre-fixed lease. kernel.invoke runs the SAME authorize gate as
    # everything else: envelope (the runner holds nothing but this lease), grantee match,
    # downhill delegation, and the LEASE caveats — time-locked (expires_at, evaluated at
    # the logical frontier `now == weft.lamport`) + use-bounded (max_uses). An attempt to
    # exceed the lease — wrong cap, cost over budget, OR a frontier past expiry — is denied
    # here. No ambient authority, no escalation: the job's blast radius is exactly its lease.
    res = k.invoke(agent, lease, args or {})

    base = k.weave().get(job_id).content
    if "denied" in res:
        new = {**base, "status": FAILED, "result": res["denied"]}
        assert_content(k.weft, k.decima.id, job_id, JOB, new)
        return {**res, "job": job_id, "status": FAILED}

    new = {**base, "status": DONE, "result": res.get("result_cell")}
    assert_content(k.weft, k.decima.id, job_id, JOB, new)
    return {**res, "job": job_id, "status": DONE}


def status(k, job) -> dict:
    """Project a job's current state from the fold: its status, the pre-fixed lease (its
    future authority grant), runner, run_at/expires_at, and result. Deterministic — a
    pure read of the Weave."""
    job_id = job if isinstance(job, str) else job.id
    cell = k.weave().get(job_id)
    if cell is None or cell.type != JOB:
        raise ValueError(f"no job {job_id!r}")
    c = cell.content
    return {
        "job": job_id,
        "name": c["name"],
        "status": c["status"],
        "lease": c["lease"],
        "runner": c["runner"],
        "run_at": int(c["run_at"]),
        "expires_at": int(c["expires_at"]),
        "max_uses": int(c["max_uses"]),
        "result": c.get("result"),
    }
