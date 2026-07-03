"""CONC1 — safe concurrency: parallel job execution that can never double-fire or corrupt the log.

REACTOR1 runs due jobs in ONE serial pass. Real always-on wants INDEPENDENT due jobs to run
across worker threads — but parallelism must not (1) double-fire a single job's effect when two
workers pick up the SAME job, nor (2) corrupt the append-only Weft (seq/parents/head) under
concurrent appends. This module is the concurrent RUNNER that preserves both, composing ONLY
public APIs (`jobs.due`/`jobs.run`, `kernel.lease_uses`, the Weave fold) — no edit to
reactor/jobs/kernel/weft.

The design (and why):

  - EXACTLY-ONCE IS THE LEASE'S LAW, NOT A NEW AUTHORITY. Each job's authority is its
    pre-fixed single-use lease (JOBS1); the INVOKE event is the durable use-record
    `kernel.lease_uses` folds. This runner invents NO second authority: `attempt` consults
    that SAME fold inside the serialized critical section, so a worker that races an
    already-fired job is DENIED by the exhausted lease before `jobs.run` ever re-invokes —
    the second worker's denial cites the lease, the ground truth of exactly-once.

  - THE WEFT MUTATION IS SERIALIZED. `weft.append` is the serialization point of the whole
    system (it reads `head`, assigns `parents`/`lamport`, inserts, moves `head`); interleaved
    appends would fork/corrupt that chain. Two belts keep it linear:
      (1) STRUCTURAL — the Weft's SQLite connection is thread-bound (`check_same_thread`),
          so worker threads never touch it: they CLAIM work and do pre-effect preparation
          concurrently, then submit a commit request to a queue drained by the SINGLE
          Weft-owner thread (the caller). Effects overlap where they can; commits are
          serialized through one owner — the spec'd fallback for a thread-bound store.
      (2) EXPLICIT — `attempt` (the invoke/append + status transition, the smallest correct
          critical section) additionally holds `_APPEND_LOCK`, so even a runtime whose store
          admitted cross-thread writes could never interleave two attempts' appends.

  - THE RECORD IS DETERMINISTIC. This module appends NOTHING of its own — every Cell/event
    is the one `jobs.run`/`kernel.invoke` would write in a serial pass. No wall-clock, no
    thread id, no float ever reaches recorded content; `now` is a logical int the caller
    supplies. Wall-clock scheduling may reorder WHICH commit lands first, but the fired SET
    is invariant across every interleaving (each lease admits exactly one INVOKE), so a
    concurrent run fires exactly the set a serial `reactor.tick` would fire.

  - FAIL CLOSED. A racing attempt is a clean denial, never a second effect; a job already
    transitioned (e.g. crash-recovered by RESUME) is refused without invoking. Compose
    `resume.recover(k, now)` BEFORE this runner exactly as `reactor.tick` does — a
    crash-fired-but-ENQUEUED job is denied here (its lease is spent), left for recovery.

Public APIs only. Threading/queue are stdlib; nothing recorded depends on them.
"""
from __future__ import annotations

import queue
import threading

from decima import jobs

# A worker's report-status for an attempt the exhausted lease (or a prior transition)
# refused — returned to the caller, never recorded on the Weft (the Weft records only
# what jobs.run itself asserts).
DENIED = "denied"

# The explicit serialization of the Weft-mutation critical section (belt (2) above).
# One lock process-wide: the reference runs one process, and over-serializing across
# kernels is safe; under-serializing one kernel is not.
_APPEND_LOCK = threading.Lock()

_WORKER_DONE = object()          # commit-queue sentinel: one per worker, ends the drain


def _int_tick(name: str, v) -> int:
    """Reject floats/bools — "now" is a LOGICAL int tick (DETERMINISM); no wall-clock
    anywhere in the pass, and no float may steer recorded content."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise TypeError(f"{name} must be an int logical tick, got {type(v).__name__}")
    return int(v)


def _int_workers(v) -> int:
    if not isinstance(v, int) or isinstance(v, bool):
        raise TypeError(f"workers must be an int, got {type(v).__name__}")
    if v < 1:
        raise ValueError("workers must be a positive worker count")
    return int(v)


def attempt(k, job_id: str, now: int) -> dict:
    """ONE guarded attempt to run ONE due job — the serialized critical section.

    Must execute in the thread that owns the Weft (its SQLite connection is thread-bound);
    the lock additionally guarantees no two attempts ever interleave their appends (the
    invoke/append + DONE/FAILED status transition happen as one unbroken section).

    The exactly-once gate is the job's OWN single-use lease: `kernel.lease_uses` folds the
    INVOKE use-records from the Log, and an attempt against a spent lease is DENIED here —
    before `jobs.run` — so a racing worker can neither re-fire the effect nor LWW-overwrite
    a winner's DONE with a losing FAILED. No second authority is consulted or minted.

    Returns {"job": id, "status": done|failed|denied} (+ "denied": reason when refused)."""
    now = _int_tick("now", now)
    with _APPEND_LOCK:                       # serialize the invoke/append + status transition
        w = k.weave()
        cell = w.get(job_id)
        if cell is None or cell.type != jobs.JOB:
            raise ValueError(f"no job {job_id!r}")
        lease = cell.content["lease"]
        max_uses = int(cell.content.get("max_uses", 1))
        if k.lease_uses(w, lease) >= max_uses:   # the exhausted lease denies the racer (exactly-once)
            return {"job": job_id, "status": DENIED,
                    "denied": f"lease exhausted ({max_uses}/{max_uses} uses spent) — "
                              "another worker already fired this job"}
        if cell.content.get("status") != jobs.ENQUEUED:
            # Transitioned without a spent lease (e.g. cancelled/failed upstream) — a run
            # would be jobs.run's own loud error; refuse cleanly, fire nothing.
            return {"job": job_id, "status": DENIED,
                    "denied": f"job already {cell.content.get('status')!r} (not enqueued)"}
        runner = w.get(cell.content["runner"])
        res = jobs.run(k, runner, job_id, now)   # ONLY the job's pre-fixed lease, same gate as ever
        out = {"job": res["job"], "status": res["status"]}
        if "denied" in res:
            out["denied"] = res["denied"]
        return out


def _drain_commits(k, now: int, commits: "queue.Queue", n_workers: int) -> list:
    """The single-owner commit loop: the CALLING thread — the only thread the Weft's
    SQLite connection admits — drains the commit queue and executes each `attempt`
    serially, until every worker has submitted its done-sentinel. Arrival order is
    wall-clock (thread scheduling) and deliberately NOT recorded; only the invariant
    outcome (each lease fires once) reaches the Log."""
    ran, finished = [], 0
    while finished < n_workers:
        item = commits.get()
        if item is _WORKER_DONE:
            finished += 1
            continue
        ran.append(attempt(k, item, now))
    return ran


def _summarize(ran: list) -> dict:
    """Int-only summary, deterministically ordered — the report never depends on which
    interleaving the scheduler happened to pick."""
    ran = sorted(ran, key=lambda r: (r["job"], r["status"], r.get("denied", "")))
    return {
        "ran": ran,
        "fired": sum(1 for r in ran if r["status"] == jobs.DONE),
        "denied": sum(1 for r in ran if "denied" in r),
    }


def run_concurrent(k, now: int, *, workers: int) -> dict:
    """Run every job `jobs.due(k, now)` across up to `workers` claiming threads.

    Each due job is claimed EXACTLY ONCE off a shared work queue (the explicit one-job →
    one-worker hand-off); claiming and any pre-effect preparation overlap across threads,
    while every Weft mutation is committed serially through the single owner (`attempt`,
    under the append lock). Even if the hand-off were defeated and two workers submitted
    the same job, the lease — not the queue — is the authority: the second commit is
    denied by the exhausted lease, so the effect fires AT MOST ONCE per job and the final
    Weave folds cleanly.

    Deterministic record: the fired SET equals a serial `reactor.tick`'s (same jobs, same
    leases, same receipts); only unrecorded wall-clock ordering differs. Returns the
    int-only summary {"ran": [{"job", "status"}...], "fired": int, "denied": int}."""
    now = _int_tick("now", now)
    workers = _int_workers(workers)

    # Snapshot the due set in the lane's own deterministic (run_at, id) order, in the
    # owner thread, BEFORE any worker starts — a pure projection over the fold.
    todo = [c.id for c in jobs.due(k, now)]

    work: "queue.Queue" = queue.Queue()
    for jid in todo:
        work.put(jid)
    commits: "queue.Queue" = queue.Queue()

    def claim_loop():
        try:
            while True:
                try:
                    jid = work.get_nowait()      # the one-job → one-worker CLAIM
                except queue.Empty:
                    break
                # Pre-effect work overlaps HERE, concurrently, off the Weft. The
                # mutation itself is submitted to the single Weft owner.
                commits.put(jid)
        finally:
            commits.put(_WORKER_DONE)

    threads = [threading.Thread(target=claim_loop, daemon=True)
               for _ in range(workers)]
    for t in threads:
        t.start()
    ran = _drain_commits(k, now, commits, len(threads))
    for t in threads:
        t.join()
    return _summarize(ran)


def race(k, job_id: str, now: int, *, workers: int) -> dict:
    """Adversarial contention probe: `workers` (>= 2) threads RACE to run the SAME job.

    A barrier releases every worker at once to maximize real contention; each submits one
    attempt for `job_id`, and the serialized commit path + the job's single-use lease
    guarantee the invariant FOR EVERY INTERLEAVING: the effect fires exactly once (one
    worker reports done; every other is denied by the exhausted lease), and the job ends
    DONE — never a double-fire, never a loser overwriting the winner's outcome.

    Returns the same int-only summary shape as `run_concurrent`."""
    now = _int_tick("now", now)
    workers = _int_workers(workers)
    if workers < 2:
        raise ValueError("a race needs at least 2 contending workers")

    commits: "queue.Queue" = queue.Queue()
    barrier = threading.Barrier(workers)

    def one_attempt():
        try:
            barrier.wait()                       # all contenders released together
            commits.put(job_id)
        finally:
            commits.put(_WORKER_DONE)

    threads = [threading.Thread(target=one_attempt, daemon=True)
               for _ in range(workers)]
    for t in threads:
        t.start()
    ran = _drain_commits(k, now, commits, len(threads))
    for t in threads:
        t.join()
    return _summarize(ran)
