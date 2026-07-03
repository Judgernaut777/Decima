"""SAFE CONCURRENCY — parallel job execution that can never double-fire or corrupt the log
(Phase 4 · always-on substrate, CONC1).

REACTOR1 runs due jobs in one SERIAL pass. `decima/concurrency.py` is the concurrent runner:
independent due jobs run across real worker threads, with the two catastrophes parallelism
invites made impossible by shape — (1) a job's effect can never DOUBLE-FIRE when two workers
race the same job, because the job's pre-fixed SINGLE-USE LEASE is the ground truth of
exactly-once (the INVOKE event is the durable use-record `kernel.lease_uses` folds; the
runner consults that same fold inside its serialized critical section and denies the loser);
and (2) the append-only Weft can never interleave-corrupt (seq/parents/head), because every
Weft mutation is serialized — structurally through the single Weft-owner thread (the SQLite
connection is thread-bound, so workers claim/prepare concurrently and commits drain through
one owner) and explicitly under the runner's append lock.

This check proves, offline, with REAL stdlib threads but assertions that hold for EVERY
interleaving (the invariant — exactly-once, clean fold, same fired-set — never a timing):

  (a) EXACTLY-ONCE UNDER CONTENTION (load-bearing): one due job on a single-use lease,
      K >= 8 workers released by a barrier to RACE that SAME job, over many rounds. Every
      round: the effect fired EXACTLY ONCE (lease_uses == 1 after), exactly one worker
      reports done, every other worker is denied BY THE EXHAUSTED LEASE, and the job ends
      DONE — a loser can never overwrite the winner's outcome;
  (b) PARALLEL INDEPENDENT JOBS: M independent due jobs (each its own lease+effect) across
      W workers all reach DONE, and the SET of fired effects EQUALS what a serial
      `reactor.tick` fires over an identically-built kernel (same outcome, different
      unrecorded wall-clock order); a re-run is a quiet no-op (nothing due);
  (c) LOG INTEGRITY: after the concurrent runs, a fresh Kernel(db, fresh=False) folds the
      whole log with no WeftError — reconstruction + full verified fold + stable state_root
      (the serialized append kept seq/parents/lamport honest);
  (d) INTS / NO WALL-CLOCK: no float (and no wall-clock/thread-id field) anywhere in any
      recorded job Cell or probe receipt; the runner's summary counts are ints.

Mutation-resistance (the load-bearing line): revert the lease-exhaustion guard in
`concurrency.attempt` — `if k.lease_uses(w, lease) >= max_uses: return {... denied ...}` —
and (a) goes RED: the racing losers reach `jobs.run` against the already-run job, which
either fails loud or, in the crash window, re-invokes and LWW-overwrites the winner's DONE
with a lying FAILED. Neutering the serialization (commits off the single owner / no lock)
lets appends interleave and (c)'s verified fold goes red.

Contract: run(k, line). Fail loud (assert). Registers its OWN hermetic `conc_probe` effect
(never reuses 'echo'); fresh Kernels over private dbs; logical int ticks; no clock.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import concurrency, jobs, reactor, executor

# A check-local, deterministic effect so the probe is HERMETIC — independent of whatever a
# prior check left in the module-global executor registry (register overwrites).
_PROBE_EFFECT = "conc_probe"


def _probe_handler(impl, args):
    # Output derives ONLY from the capability's impl tag — pure, deterministic, no clock.
    return {"out": "conc:" + str((impl or {}).get("tag", ""))}


def _probe_cap(k, tag):
    """Register the hermetic probe effect and mint+grant a per-job capability (distinct
    impl tag → distinct fired output, so fired-SETS are comparable across kernels)."""
    executor.register(_PROBE_EFFECT, _probe_handler)
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
            and c.content["out"].startswith("conc:")
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
    line("\n== SAFE CONCURRENCY — parallel jobs: never a double-fire, never a corrupt log ==")

    # ── (a) EXACTLY-ONCE UNDER CONTENTION — K workers race ONE single-use job, many rounds.
    db_race = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Kernel(db_race, fresh=True)
    ROUNDS, K = 5, 10
    for rnd in range(ROUNDS):
        jid, lease = _enqueue(kr, f"race-{rnd}")
        res = concurrency.race(kr, jid, 0, workers=K)
        assert len(res["ran"]) == K, f"every contender must report: {res}"
        dones = [r for r in res["ran"] if r["status"] == jobs.DONE]
        losers = [r for r in res["ran"] if r["status"] != jobs.DONE]
        assert res["fired"] == 1 and len(dones) == 1, \
            f"round {rnd}: the effect must fire EXACTLY once, got {res}"
        assert res["denied"] == K - 1 and len(losers) == K - 1, \
            f"round {rnd}: every other worker must be denied, got {res}"
        assert all("denied" in r and "lease exhausted" in r["denied"] for r in losers), \
            f"round {rnd}: the losers must be denied BY THE EXHAUSTED LEASE: {losers}"
        assert kr.lease_uses(kr.weave(), lease) == 1, \
            f"round {rnd}: the single-use lease must record exactly ONE INVOKE use"
        assert kr.weave().get(jid).content["status"] == jobs.DONE, \
            f"round {rnd}: the job must end DONE — a loser may never overwrite the winner"
    line(f"  exactly-once under contention: {ROUNDS} rounds × {K} barrier-released workers "
         f"racing ONE single-use job — every round exactly 1 fired (lease_uses == 1), "
         f"{K - 1} denied by the exhausted lease, job DONE (no interleaving double-fires "
         f"or overwrites the winner) ✓")

    # ── (b) PARALLEL INDEPENDENT JOBS — all DONE; fired-set EQUALS a serial reactor.tick's.
    M, W = 6, 4
    db_par = os.path.join(tempfile.mkdtemp(), "weft.db")
    kp = Kernel(db_par, fresh=True)
    par_jobs = [(_enqueue(kp, f"fleet-{i}")[0]) for i in range(M)]
    summary = concurrency.run_concurrent(kp, 0, workers=W)
    assert summary["fired"] == M and summary["denied"] == 0, \
        f"all {M} independent due jobs must fire across {W} workers: {summary}"
    assert {r["job"] for r in summary["ran"]} == set(par_jobs)
    assert all(r["status"] == jobs.DONE for r in summary["ran"]), summary
    assert all(kp.weave().get(j).content["status"] == jobs.DONE for j in par_jobs), \
        "every independent job must reach DONE on the Weft"
    # the SAME fleet, built identically on a fresh kernel, run by the SERIAL reactor:
    db_ser = os.path.join(tempfile.mkdtemp(), "weft.db")
    ks = Kernel(db_ser, fresh=True)
    ser_jobs = [(_enqueue(ks, f"fleet-{i}")[0]) for i in range(M)]
    tick = reactor.tick(ks, 0)
    assert all(j["status"] == jobs.DONE for j in tick["jobs"]) and len(tick["jobs"]) == M
    expected = {f"conc:fleet-{i}" for i in range(M)}
    assert _fired_set(kp) == _fired_set(ks) == expected, \
        (f"the concurrent fired-SET must EQUAL the serial reactor's (only unrecorded "
         f"wall-clock order may differ): {_fired_set(kp)} vs {_fired_set(ks)}")
    # deterministic no-op on re-run: nothing is due anymore, nothing re-fires.
    again = concurrency.run_concurrent(kp, 0, workers=W)
    assert again == {"ran": [], "fired": 0, "denied": 0}, \
        f"a re-run over the same frontier must be a quiet no-op: {again}"
    line(f"  parallel independent jobs: {M} jobs × {W} workers all DONE; the fired-SET "
         f"equals a serial reactor.tick's over an identically-built kernel ({sorted(expected)}); "
         f"a re-run is a quiet no-op ✓")

    # ── (c) LOG INTEGRITY — a fresh Kernel folds the whole concurrent log, no WeftError. ──
    for name, db, jids in (("race", db_race, None), ("parallel", db_par, par_jobs)):
        k2 = Kernel(db, fresh=False)          # reconstruction re-verifies every event
        w2 = k2.weave()                       # full verified fold: id recompute + signature
        assert w2.state_root() == k2.weave().state_root(), \
            "the folded state must be stable across folds (deterministic record)"
        if jids:
            assert all(w2.get(j).content["status"] == jobs.DONE for j in jids), \
                "the reconstructed fold must agree: every job DONE"
    line("  log integrity: fresh Kernel(db, fresh=False) over both concurrent logs folds "
         "cleanly (every event id + signature re-verified, state_root stable) — the "
         "serialized append kept seq/parents/lamport honest ✓")

    # ── (d) INTS / NO WALL-CLOCK — nothing scheduling-dependent reached any recorded Cell. ─
    for kk in (kr, kp):
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
            if str(cell.content.get("out", "")).startswith("conc:"):
                _assert_no_float(cell.content, f"result:{cell.id[:8]}")
    for key in ("fired", "denied"):
        assert isinstance(summary[key], int) and not isinstance(summary[key], bool)
    line("  ints / no wall-clock: every recorded job Cell + probe receipt is float-free with "
         "int logical ticks, and carries no wall-clock/thread field; summary counts are ints ✓")

    line("  → concurrency is now SAFE: independent due jobs run across real worker threads, "
         "the single-use lease (folded from the Log inside the serialized commit section) "
         "keeps every effect exactly-once under genuine contention, the single-owner + "
         "locked append path keeps the append-only Weft fold-clean, and the record is "
         "deterministic — the fired-set of a concurrent run IS the serial run's.")
