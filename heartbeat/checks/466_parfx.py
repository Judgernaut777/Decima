"""REAL PARALLEL EFFECTS — worker effects actually OVERLAP; only the Weft commit serializes
(Batch B · correctness debts, PARFX1).

Cycle 55's concurrency lane was honest that its parallelism was NOMINAL: worker threads only
moved job ids between queues while every EFFECT ran serially inside the single Weft-owner
thread. `decima/concurrency.py` now splits each job into its two true halves — the effect-
HANDLER execution runs CONCURRENTLY in worker threads, while ONLY the Weft commit (the INVOKE
append + receipt + DONE/FAILED status transition) serializes through the owner under the
append lock — without giving up either law: exactly-once (the single-use lease, re-consulted
inside the serialized commit) and a clean fold (no seq/parents/lamport corruption).

This check proves, offline + deterministically (the proof of overlap is a RENDEZVOUS, never a
timing measurement):

  (a) EFFECTS REALLY OVERLAP (load-bearing): K independent due jobs whose effects rendezvous
      on ONE shared threading.Barrier(parties=K) ALL complete. The barrier releases NOBODY
      until K handlers are simultaneously in-flight — a serial runner (one effect at a time)
      can never assemble K parties, so it dies LOUD on the barrier's bounded timeout. All K
      jobs DONE + every handler crossed the barrier + max concurrent in-flight == K is the
      deterministic witness of real concurrency.
  (b) EXACTLY-ONCE UNDER CONTENTION: W workers released by a barrier RACE ONE single-use
      job — exactly 1 fires (lease_uses == 1 after), every other worker is denied BY THE
      EXHAUSTED LEASE, and the job ends DONE (a loser never overwrites the winner).
  (c) CLEAN FOLD + SAME FIRED-SET: after the parallel runs, a fresh Kernel(db, fresh=False)
      re-verifies + folds both logs with no WeftError (stable state_root), and the SET of
      fired effects EQUALS what a serial `reactor.tick` fires over an identically-built
      kernel; a re-run over the same frontier is a quiet no-op.
  (d) INTS / NO WALL-CLOCK: no float (and no wall-clock/thread-id field) anywhere in any
      recorded job Cell or probe receipt; the runner's summary counts are ints. (The
      rendezvous barrier/counters are check-local RUNTIME state, never recorded content.)

Mutation-resistance (the load-bearing line): force effects back to serial — in
`concurrency._work_one` drop `pre = _run_effect(verdict[1])` and let the commit run the real
handler inside the serialized critical section (the pre-PARFX shape) — and (a) goes RED: the
first handler blocks ALONE on the barrier inside the single owner thread, no second party can
ever arrive, and the bounded timeout fails the check loud (BrokenBarrierError) instead of
hanging. Existing checks/436_concurrency.py must stay green alongside.

Contract: run(k, line). Fail loud (assert). Registers its OWN hermetic `parfx_probe` effect
(never reuses 'echo'); fresh Kernels over private dbs; logical int ticks; no clock in
anything recorded.
"""
import os
import tempfile
import threading

from decima.kernel import Kernel
from decima import concurrency, jobs, reactor, executor

# A check-local, deterministic effect so the probe is HERMETIC — independent of whatever a
# prior check left in the module-global executor registry (register overwrites).
_PROBE_EFFECT = "parfx_probe"

# Generous ceiling for the rendezvous: correctness never depends on it (the barrier trips
# the instant all K parties are in-flight); only a BROKEN (serial) runner ever waits this
# long — and then fails LOUD instead of hanging the suite. Runtime-only, never recorded.
_RENDEZVOUS_TIMEOUT = 30


def _plain_handler(impl, args):
    # Output derives ONLY from the capability's impl tag — pure, deterministic, no clock.
    return {"out": "parfx:" + str((impl or {}).get("tag", ""))}


class _Rendezvous:
    """The deterministic witness of overlap: a handler that BLOCKS on a shared
    Barrier(parties=K) until K effects are in-flight AT ONCE. Every field here (barrier,
    counters) is check-local RUNTIME state that never reaches recorded content — the
    recorded output is the same pure impl-tag string as `_plain_handler`'s."""

    def __init__(self, parties):
        self.barrier = threading.Barrier(parties)
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0
        self.crossed = 0

    def handler(self, impl, args):
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # Releases ONLY when all K parties are simultaneously inside their effect; a
            # serial runner never assembles them → BrokenBarrierError on the bounded
            # timeout, propagated loud by the runner (never a fabricated SUCCEEDED).
            self.barrier.wait(timeout=_RENDEZVOUS_TIMEOUT)
            with self._lock:
                self.crossed += 1
        finally:
            with self._lock:
                self.in_flight -= 1
        return _plain_handler(impl, args)


def _probe_cap(k, tag):
    """Mint+grant a per-job probe capability (distinct impl tag → distinct fired output,
    so fired-SETS are comparable across kernels). The handler is registered per section —
    the rendezvous one for the overlap proof, the plain one everywhere else."""
    cap_id = k._assert_cap(f"{_PROBE_EFFECT}:{tag}", _PROBE_EFFECT, impl={"tag": tag})
    k.grant(cap_id, k.decima_agent_id)
    return cap_id


def _enqueue(k, tag, *, run_at=0, window=100_000):
    """One due job on its OWN single-use lease + probe effect. Returns (job_id, lease_id)."""
    cap_id = _probe_cap(k, tag)
    jid = jobs.enqueue(k, tag, capability=cap_id, run_at=run_at, max_uses=1, window=window)
    return jid, jobs.status(k, jid)["lease"]


def _fired_set(k):
    """The SET of fired probe effects, folded from the Log (SUCCEEDED receipts' outputs) —
    the deterministic record a serial and a concurrent run must agree on."""
    return {c.content.get("out") for c in k.weave().of_type("result")
            if isinstance(c.content.get("out"), str)
            and c.content["out"].startswith("parfx:")
            and c.content.get("status") == "SUCCEEDED"}


def _assert_no_float(x, path):
    """Recursively reject any float in recorded content (ints-not-floats, by shape)."""
    assert not isinstance(x, float), f"float in recorded content at {path}: {x!r}"
    if isinstance(x, dict):
        for kk, vv in x.items():
            _assert_no_float(kk, f"{path}.<key>")
            _assert_no_float(vv, f"{path}.{kk}")
    elif isinstance(x, (list, tuple)):
        for i, vv in enumerate(x):
            _assert_no_float(vv, f"{path}[{i}]")


def run(k, line):
    line("\n== REAL PARALLEL EFFECTS — handlers overlap across workers; only the commit serializes ==")

    # ── (a) EFFECTS REALLY OVERLAP — K jobs rendezvous on ONE Barrier(parties=K). ──────
    K = 4
    db_par = os.path.join(tempfile.mkdtemp(), "weft.db")
    ka = Kernel(db_par, fresh=True)
    rv = _Rendezvous(parties=K)
    executor.register(_PROBE_EFFECT, rv.handler)
    par_jobs = [_enqueue(ka, f"olap-{i}")[0] for i in range(K)]
    summary = concurrency.run_concurrent(ka, 0, workers=K)
    assert summary["fired"] == K and summary["denied"] == 0, \
        f"all {K} rendezvous jobs must fire across {K} workers: {summary}"
    assert all(r["status"] == jobs.DONE for r in summary["ran"]), summary
    assert all(ka.weave().get(j).content["status"] == jobs.DONE for j in par_jobs), \
        "every rendezvous job must reach DONE on the Weft"
    assert rv.crossed == K, \
        (f"every effect must CROSS the shared barrier — possible only if all {K} handlers "
         f"were in-flight AT ONCE (a serial runner never assembles them): {rv.crossed}/{K}")
    assert rv.max_in_flight == K, \
        f"the witness: max concurrent in-flight handlers must be {K}, got {rv.max_in_flight}"
    line(f"  effects really overlap: {K} jobs × {K} workers rendezvoused on ONE "
         f"Barrier(parties={K}) inside their effect handlers — all {K} DONE, every handler "
         f"crossed, max in-flight == {K} (a serial runner would have died loud on the "
         f"bounded timeout) ✓")

    # ── (b) EXACTLY-ONCE UNDER CONTENTION — W workers race ONE single-use job. ─────────
    W = 8
    db_race = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Kernel(db_race, fresh=True)
    executor.register(_PROBE_EFFECT, _plain_handler)      # no rendezvous: one winner runs
    jid, lease = _enqueue(kr, "solo")
    res = concurrency.race(kr, jid, 0, workers=W)
    assert len(res["ran"]) == W, f"every contender must report: {res}"
    dones = [r for r in res["ran"] if r["status"] == jobs.DONE]
    losers = [r for r in res["ran"] if r["status"] != jobs.DONE]
    assert res["fired"] == 1 and len(dones) == 1, \
        f"the effect must fire EXACTLY once under contention, got {res}"
    assert res["denied"] == W - 1 and len(losers) == W - 1, \
        f"every other worker must be denied, got {res}"
    assert all("denied" in r and "lease exhausted" in r["denied"] for r in losers), \
        f"the losers must be denied BY THE EXHAUSTED LEASE: {losers}"
    assert kr.lease_uses(kr.weave(), lease) == 1, \
        "the single-use lease must record exactly ONE INVOKE use"
    assert kr.weave().get(jid).content["status"] == jobs.DONE, \
        "the job must end DONE — a loser may never overwrite the winner"
    line(f"  exactly-once under contention: {W} barrier-released workers raced ONE "
         f"single-use job — exactly 1 fired (lease_uses == 1), {W - 1} denied by the "
         f"exhausted lease, job DONE ✓")

    # ── (c) CLEAN FOLD + SAME FIRED-SET — the parallel record IS the serial record. ────
    db_ser = os.path.join(tempfile.mkdtemp(), "weft.db")
    ks = Kernel(db_ser, fresh=True)
    for i in range(K):
        _enqueue(ks, f"olap-{i}")                         # the SAME fleet, built identically
    tick = reactor.tick(ks, 0)                            # the SERIAL reactor
    assert len(tick["jobs"]) == K and all(j["status"] == jobs.DONE for j in tick["jobs"])
    expected = {f"parfx:olap-{i}" for i in range(K)}
    assert _fired_set(ka) == _fired_set(ks) == expected, \
        (f"the parallel fired-SET must EQUAL the serial reactor's (only unrecorded "
         f"wall-clock order may differ): {_fired_set(ka)} vs {_fired_set(ks)}")
    for db in (db_par, db_race):
        k2 = Kernel(db, fresh=False)          # reconstruction re-verifies every event
        w2 = k2.weave()                       # full verified fold: id recompute + signature
        assert w2.state_root() == k2.weave().state_root(), \
            "the folded state must be stable across folds (deterministic record)"
    again = concurrency.run_concurrent(ka, 0, workers=K)
    assert again == {"ran": [], "fired": 0, "denied": 0}, \
        f"a re-run over the same frontier must be a quiet no-op: {again}"
    line("  clean fold + same fired-set: fresh Kernel(db, fresh=False) folds both parallel "
         "logs with no WeftError (state_root stable), the fired-SET equals a serial "
         f"reactor.tick's ({sorted(expected)}), and a re-run is a quiet no-op ✓")

    # ── (d) INTS / NO WALL-CLOCK — nothing scheduling-dependent reached the record. ────
    for kk in (ka, kr):
        w = kk.weave()
        for cell in w.of_type(jobs.JOB):
            _assert_no_float(cell.content, f"job:{cell.id[:8]}")
            for key in ("run_at", "expires_at", "max_uses"):
                v = cell.content[key]
                assert isinstance(v, int) and not isinstance(v, bool), \
                    f"job.{key} must be an int logical tick, got {v!r}"
            assert not any(bad in kk2 for kk2 in cell.content for bad in
                           ("time", "clock", "thread")), \
                f"no wall-clock/thread field may reach a recorded job Cell: {sorted(cell.content)}"
        for cell in w.of_type("result"):
            if str(cell.content.get("out", "")).startswith("parfx:"):
                _assert_no_float(cell.content, f"result:{cell.id[:8]}")
                assert not any(bad in kk2 for kk2 in cell.content for bad in
                               ("time", "clock", "thread")), \
                    f"no wall-clock/thread field may reach a probe receipt: {sorted(cell.content)}"
    for key in ("fired", "denied"):
        assert isinstance(summary[key], int) and not isinstance(summary[key], bool)
    line("  ints / no wall-clock: every recorded job Cell + probe receipt is float-free with "
         "int logical ticks and carries no wall-clock/thread field; summary counts are ints ✓")

    line("  → parallelism is now REAL, not nominal: effect handlers genuinely overlap across "
         "worker threads (proven by a shared rendezvous no serial runner could satisfy), "
         "while ONLY the Weft commit serializes through the single owner under the append "
         "lock — exactly-once still the lease's law, the fold still clean, the fired-set "
         "still the serial run's.")
