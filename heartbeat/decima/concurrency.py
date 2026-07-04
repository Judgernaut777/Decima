"""CONC1/PARFX1 — real parallel effects: worker EFFECTS overlap; only the Weft commit serializes.

REACTOR1 runs due jobs in ONE serial pass. The first cut of this lane was honest that its
parallelism was NOMINAL: worker threads only moved job ids between queues while every EFFECT
still ran serially inside the single Weft-owner thread. This cycle makes the overlap REAL by
splitting each job into its two true halves:

  (1) THE EFFECT-HANDLER EXECUTION — the pure-ish work at the executor seam — runs in the
      WORKER thread, so two workers' effects are genuinely in-flight AT THE SAME TIME; and
  (2) THE WEFT COMMIT — the INVOKE append + receipt + DONE/FAILED status transition, the
      smallest correct critical section — stays serialized through the single Weft-owner
      thread under `_APPEND_LOCK`, exactly as before.

The design (and why it keeps both laws):

  - EXACTLY-ONCE IS STILL THE LEASE'S LAW, NOT A NEW AUTHORITY. Two guards, ONE authority.
    The authority of RECORD is the job's pre-fixed single-use lease (JOBS1): `attempt`
    re-consults `kernel.lease_uses` INSIDE the serialized commit, so a racing commit is
    denied by the exhausted lease — never a double record, never a loser LWW-overwriting a
    winner's DONE. The RUNTIME guard for the overlapping half is the owner's RESERVATION:
    before a worker may run a job's effect it asks the owner (`prefetch`), who folds the
    Weave — lease unspent? still ENQUEUED? not already reserved by an in-flight worker? —
    and hands out either the effect spec or a denial. Each guard lives where it can be
    trusted: the reservation in the ONE owner thread (no interleaving to reason about),
    the lease on the Log (the durable ground truth a fold re-derives).

  - THE WEFT MUTATION IS SERIALIZED. `weft.append` is the serialization point of the whole
    system (it reads `head`, assigns `parents`/`lamport`, inserts, moves `head`); interleaved
    appends would fork/corrupt that chain. The same two belts as ever keep it linear:
      (1) STRUCTURAL — the Weft's SQLite connection is thread-bound (`check_same_thread`),
          so worker threads never touch it: the fold-reads (gate) and every commit execute
          in the SINGLE Weft-owner thread (the caller), which drains an inbox of prefetch/
          commit requests. Only the effect handlers overlap; commits drain one at a time.
      (2) EXPLICIT — `attempt` (the invoke/append + status transition) additionally holds
          `_APPEND_LOCK`, so even a runtime whose store admitted cross-thread writes could
          never interleave two attempts' appends.

  - THE RECORD IS DETERMINISTIC. A worker's pre-executed outcome is REPLAYED through the
    very same `jobs.run → kernel.invoke → executor.execute` path at commit time (a
    commit-scoped handler interposition, held only by the owner thread), so every recorded
    Cell is exactly the one a serial pass would write: no wall-clock, no thread id, no
    float ever reaches recorded content; `now` is a logical int the caller supplies. The
    thread id steers only in-memory dispatch during the replay window — never content.
    Wall-clock scheduling may reorder WHICH commit lands first, but the fired SET is
    invariant across every interleaving (each lease admits exactly one INVOKE), so a
    concurrent run fires exactly the set a serial `reactor.tick` would fire.

  - FAIL CLOSED, AND THE CRASH WINDOW IS HONEST. A racing worker is a clean denial, never
    a second effect; a job already transitioned is refused without invoking. Splitting the
    effect from its commit INVERTS jobs.run's crash window: a crash after a worker ran the
    effect but BEFORE its commit leaves NO record — the job is still ENQUEUED on an unspent
    lease and will re-run (at-least-once for the pure-ish effect at this seam; exactly-once
    on the RECORD, as ever). Nothing is fabricated: the fold never lies about an effect it
    recorded. Compose `resume.recover(k, now)` BEFORE this runner exactly as `reactor.tick`
    does — a crash-fired-but-ENQUEUED job is denied here (its lease is spent), left for
    recovery.

Public APIs only (`jobs.due`/`jobs.run`, `kernel.lease_uses`, the Weave fold, the executor
registry seam) — no edit to reactor/jobs/kernel/executor/weft. Threading/queue are stdlib;
nothing recorded depends on them.
"""
from __future__ import annotations

import contextlib
import queue
import threading

from decima import executor, jobs

# A worker's report-status for an attempt the exhausted lease (or a prior transition)
# refused — returned to the caller, never recorded on the Weft (the Weft records only
# what jobs.run itself asserts).
DENIED = "denied"

# The explicit serialization of the Weft-mutation critical section (belt (2) above).
# One lock process-wide: the reference runs one process, and over-serializing across
# kernels is safe; under-serializing one kernel is not.
_APPEND_LOCK = threading.Lock()

_WORKER_DONE = object()          # inbox sentinel: one per worker, ends the drain


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

    Called directly, this runs the effect handler inline (the serial path); the concurrent
    runner instead REPLAYS a worker's pre-executed outcome through this same section (see
    `_commit`), so the critical section holds only the commit, never the effect's latency.

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


# ── the overlapping half: gate, pre-execution in the worker, replay at commit ────────


def _effect_spec(w, job_cell) -> tuple:
    """Resolve the (effect, impl, args, sandbox) a job's lease will invoke — a pure read
    of the fold, computed in the OWNER thread and handed to a worker as plain data
    (workers never touch the thread-bound Weft)."""
    cap = w.get(job_cell.content["lease"])
    return (cap.content["effect"], cap.content.get("impl"), {},
            cap.content.get("caveats", {}).get("sandbox"))


def _gate(k, job_id: str) -> tuple:
    """The owner-thread pre-flight for ONE job: may a worker run its effect NOW?

    Mirrors `attempt`'s guards over the current fold — the lease must be unspent (the
    INVOKE use-records are the ground truth of exactly-once) and the job still ENQUEUED.
    Evaluated ONLY in the single owner thread, with a reservation held from `go` until
    the commit lands (see `_serve`), so no interleaving can hand the same job's effect
    to two workers while its lease is unspent.

    Returns ("go", spec) or ("denied", result-dict)."""
    w = k.weave()
    cell = w.get(job_id)
    if cell is None or cell.type != jobs.JOB:
        raise ValueError(f"no job {job_id!r}")
    lease = cell.content["lease"]
    max_uses = int(cell.content.get("max_uses", 1))
    if k.lease_uses(w, lease) >= max_uses:   # the exhausted lease denies the racer (exactly-once)
        return ("denied", {"job": job_id, "status": DENIED,
                           "denied": f"lease exhausted ({max_uses}/{max_uses} uses spent) — "
                                     "another worker already fired this job"})
    if cell.content.get("status") != jobs.ENQUEUED:
        return ("denied", {"job": job_id, "status": DENIED,
                           "denied": f"job already {cell.content.get('status')!r} (not enqueued)"})
    return ("go", _effect_spec(w, cell))


def _run_effect(spec: tuple) -> tuple:
    """Run the effect handler in the CALLING WORKER THREAD, outside the commit lock —
    this is where two workers' effects genuinely overlap. Same contract as the kernel's
    executor boundary (sandbox enforced pre-dispatch; a definite refusal is captured, not
    raised); the outcome travels back as plain data for the serialized replay. Anything
    else a handler raises propagates — the caller surrenders it to the owner, loud."""
    effect, impl, args, sandbox = spec
    try:
        return ("receipt", executor.execute(effect, impl, args, sandbox=sandbox))
    except executor.SandboxViolation as e:
        return ("sandbox", str(e))
    except executor.ExecError as e:
        return ("exec", str(e))


@contextlib.contextmanager
def _replayed(effect: str, pre: tuple):
    """Commit-scoped interposition at the executor's registry seam: while held (only ever
    by the single owner thread, one commit at a time), `executor.execute` inside
    `kernel.invoke` receives the worker's PRE-EXECUTED outcome instead of re-running the
    handler — the effect fires once, in the worker; the record is written once, in the
    commit, byte-identical to the serial path's (execute() wraps `{"status": SUCCEEDED,
    **out}` and the replayed outcome's own status wins). Any OTHER thread resolving this
    effect mid-window (a worker overlapping a DIFFERENT job on the same effect name) is
    delegated to the real handler untouched. The thread id steers only this in-memory
    dispatch and never reaches recorded content."""
    real = executor._REGISTRY.get(effect)     # the registry seam; restored below, always
    owner = threading.get_ident()

    def memo(impl, args):
        if threading.get_ident() != owner:
            if real is None:
                raise executor.ExecError(f"unknown effect {effect!r}")
            return real(impl, args)           # another worker's overlapping effect — untouched
        kind, val = pre
        if kind == "sandbox":
            raise executor.SandboxViolation(val)
        if kind == "exec":
            raise executor.ExecError(val)
        return dict(val)                      # the pre-executed receipt, replayed verbatim

    executor.register(effect, memo)
    try:
        yield
    finally:
        if real is None:
            executor._REGISTRY.pop(effect, None)
        else:
            executor.register(effect, real)


def _commit(k, job_id: str, now: int, pre: tuple) -> dict:
    """The serialized Weft commit of a pre-executed effect: replay the worker's outcome
    through the SAME `attempt` critical section (INVOKE append + receipt + status
    transition under `_APPEND_LOCK`), so every guard — the exhausted-lease denial above
    all — still runs inside the lock, and the recorded Cells are the serial path's."""
    w = k.weave()
    effect = w.get(w.get(job_id).content["lease"]).content["effect"]
    with _replayed(effect, pre):
        return attempt(k, job_id, now)


def _release(k, job_id: str, reserved: set, deferred: dict, ran: list) -> None:
    """A reservation ended (its commit landed / its worker errored): answer the deferred
    prefetches for this job against the NEW fold — typically each is now denied by the
    exhausted lease; a multi-use lease (or an errored winner) may hand `go` to the next
    waiter, re-deferring the rest behind the fresh reservation."""
    waiters = deferred.pop(job_id, [])
    for i, reply in enumerate(waiters):
        verdict = _gate(k, job_id)
        if verdict[0] == "go":
            reserved.add(job_id)
            reply.put(verdict)
            if waiters[i + 1:]:
                deferred[job_id] = waiters[i + 1:]
            return
        ran.append(verdict[1])
        reply.put(verdict)


def _serve(k, now: int, inbox: "queue.Queue", n_workers: int) -> list:
    """The single-owner service loop: the CALLING thread — the only thread the Weft's
    SQLite connection admits — drains an inbox of worker messages until every worker has
    submitted its done-sentinel. Messages:

      ("prefetch", job_id, reply_q) — gate + reserve in the owner; reply ("go", spec) or
                                      ("denied", result); a prefetch against a RESERVED
                                      job is deferred and answered on release;
      ("commit",  job_id, pre)      — the serialized commit of a pre-executed effect;
      ("error",   job_id, exc)      — a worker's effect raised: release the reservation,
                                      finish the drain, then re-raise LOUD (fail closed —
                                      never a fabricated outcome for an unobserved effect).

    Arrival order is wall-clock (thread scheduling) and deliberately NOT recorded; only
    the invariant outcome (each lease fires once) reaches the Log."""
    ran, errors = [], []
    reserved, deferred = set(), {}
    finished = 0
    while finished < n_workers:
        msg = inbox.get()
        if msg is _WORKER_DONE:
            finished += 1
            continue
        kind, job_id, payload = msg
        if kind == "prefetch":
            if job_id in reserved:
                deferred.setdefault(job_id, []).append(payload)   # answered on release
                continue
            verdict = _gate(k, job_id)
            if verdict[0] == "go":
                reserved.add(job_id)
            else:
                ran.append(verdict[1])
            payload.put(verdict)
        elif kind == "commit":
            ran.append(_commit(k, job_id, now, payload))
            reserved.discard(job_id)
            _release(k, job_id, reserved, deferred, ran)
        else:                                                     # "error"
            errors.append(payload)
            reserved.discard(job_id)
            _release(k, job_id, reserved, deferred, ran)
    if errors:
        raise errors[0]                       # fail LOUD, after every worker has drained
    return ran


def _work_one(job_id: str, inbox: "queue.Queue", reply: "queue.Queue") -> None:
    """One worker's handling of ONE job: ask the owner for clearance (prefetch), run the
    effect CONCURRENTLY on `go`, then submit the outcome for the serialized commit. A
    denial was already recorded by the owner; an unexpected handler error is surrendered
    to the owner (reservation released, re-raised loud after the drain)."""
    inbox.put(("prefetch", job_id, reply))
    verdict = reply.get()
    if verdict[0] != "go":
        return
    try:
        pre = _run_effect(verdict[1])     # THE OVERLAP — the handler runs HERE, in the worker
    except BaseException as e:            # surrendered to the owner, re-raised loud
        inbox.put(("error", job_id, e))
        return
    inbox.put(("commit", job_id, pre))


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
    """Run every job `jobs.due(k, now)` across up to `workers` claiming threads — with the
    effect HANDLERS actually overlapping.

    Each due job is claimed EXACTLY ONCE off a shared work queue (the explicit one-job →
    one-worker hand-off); the claiming worker asks the owner for clearance, runs the
    effect handler IN ITS OWN THREAD — so independent jobs' effects are genuinely
    in-flight simultaneously — and submits the outcome for the serialized commit (the
    INVOKE append + status transition, through the single owner, under the append lock).
    Even if the hand-off were defeated and two workers pursued the same job, the owner's
    reservation admits one effect at a time per unspent lease, and the lease — not the
    queue — remains the authority of record: a racing commit is denied by the exhausted
    lease, so the effect fires AT MOST ONCE per job and the final Weave folds cleanly.

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
    inbox: "queue.Queue" = queue.Queue()

    def claim_loop():
        reply: "queue.Queue" = queue.Queue()
        try:
            while True:
                try:
                    jid = work.get_nowait()      # the one-job → one-worker CLAIM
                except queue.Empty:
                    break
                _work_one(jid, inbox, reply)
        finally:
            inbox.put(_WORKER_DONE)

    threads = [threading.Thread(target=claim_loop, daemon=True)
               for _ in range(workers)]
    for t in threads:
        t.start()
    ran = _serve(k, now, inbox, len(threads))
    for t in threads:
        t.join()
    return _summarize(ran)


def race(k, job_id: str, now: int, *, workers: int) -> dict:
    """Adversarial contention probe: `workers` (>= 2) threads RACE to run the SAME job.

    A barrier releases every worker at once to maximize real contention; each asks the
    owner for clearance on `job_id`. The owner's reservation admits ONE worker's effect
    per unspent lease; every other contender is answered from the fold AFTER the winner's
    commit — denied by the exhausted lease, the ground truth of exactly-once. The
    invariant holds FOR EVERY INTERLEAVING: the effect fires exactly once (one worker
    reports done; every other is denied), and the job ends DONE — never a double-fire,
    never a loser overwriting the winner's outcome.

    Returns the same int-only summary shape as `run_concurrent`."""
    now = _int_tick("now", now)
    workers = _int_workers(workers)
    if workers < 2:
        raise ValueError("a race needs at least 2 contending workers")
    cell = k.weave().get(job_id)
    if cell is None or cell.type != jobs.JOB:
        raise ValueError(f"no job {job_id!r}")

    inbox: "queue.Queue" = queue.Queue()
    barrier = threading.Barrier(workers)

    def one_attempt():
        reply: "queue.Queue" = queue.Queue()
        try:
            barrier.wait()                       # all contenders released together
            _work_one(job_id, inbox, reply)
        finally:
            inbox.put(_WORKER_DONE)

    threads = [threading.Thread(target=one_attempt, daemon=True)
               for _ in range(workers)]
    for t in threads:
        t.start()
    ran = _serve(k, now, inbox, len(threads))
    for t in threads:
        t.join()
    return _summarize(ran)
