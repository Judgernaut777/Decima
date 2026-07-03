"""CRASH-RESUMABLE DURABLE EXECUTION across a restart (Phase 4 · always-on substrate).

Durable work lives on the Weft, so a fresh Kernel over the same log rebuilds the job queue
by folding — durability across a restart is STRUCTURAL. The subtle gap is the CRASH WINDOW
inside `jobs.run`: it appends the INVOKE (effect + `result` receipt) and THEN, as a SEPARATE
append, marks the job DONE. A crash between the two leaves the job ENQUEUED with its effect
already fired. The single-use lease keeps the EFFECT exactly-once (the INVOKE event is the
durable use-record, so a re-run is denied by the exhausted lease) — but a naive restart then
re-runs the still-enqueued job, the denied re-invoke marks it FAILED, and the recorded
outcome LIES (failed, when the effect SUCCEEDED). `resume.recover` closes that.

This check proves, offline + deterministically (fresh Kernels reconstructed over the SAME
db, logical int ticks, no clock):

  (a) DURABLE — an enqueued job survives a Kernel reconstruction: a NEW Kernel over the same
      weft.db folds the job back, still due (durability is a fold, not a feature);
  (b) THE BUG IS REAL — reproduce the crash window (fire the effect via the lease, do NOT
      mark DONE) and show the NAIVE restart path (`jobs.run`) records the job FAILED though
      the effect SUCCEEDED — and, crucially, does NOT double-fire (lease stays exhausted);
  (c) RECOVER IS TRUTHFUL + EXACTLY-ONCE — on a freshly reconstructed Kernel, `recover`
      reconciles that same crash-fired job to DONE from its own receipt WITHOUT re-invoking:
      the lease use-count is UNCHANGED (no second effect) and the job's result points at the
      real receipt;
  (d) WIRED INTO THE REACTOR — a plain `reactor.tick` after a restart RECOVERS the crash-
      fired job (it appears under `recovered`, resolves to done) instead of false-failing it,
      and does not re-fire the effect;
  (e) IDEMPOTENT — a second `recover` (or re-tick) is a no-op: nothing to reconcile;
  (f) REPAIR, NOT RE-RUN — a job that NEVER fired is left for the normal due-lane, which runs
      it to done; recover reconciles only already-fired jobs and starts no fresh effect.

Mutation-resistance (the load-bearing line): drop `resume.recover(...)` from `reactor.tick`
and (d) goes red — the crash-fired job reaches `jobs.run`, is denied by the exhausted lease,
and is recorded FAILED (the lying outcome returns).

Contract: run(k, line). Fail loud (assert). Owns fresh Kernels reconstructed over one db.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import jobs, resume, reactor, executor

# A check-local, deterministic effect so the probe is HERMETIC — independent of whatever a
# prior check may have left in the module-global executor registry (register overwrites).
_PROBE_EFFECT = "resume_probe"


def _kernel(db):
    return Kernel(db, fresh=True)


def _probe_cap(k):
    """Register a pure, always-SUCCEEDED probe effect and mint+grant a capability for it to
    the decima orchestrator (so `jobs.enqueue` can attenuate it into a job lease)."""
    executor.register(_PROBE_EFFECT, lambda impl, args: {"out": "ran:" + str(args.get("text", ""))})
    cap_id = k._assert_cap(_PROBE_EFFECT, _PROBE_EFFECT)
    k.grant(cap_id, k.decima_agent_id)
    return cap_id


def _enqueue(k, name, *, run_at=0, window=100_000):
    """Enqueue a durable job on a live, generously-windowed lease and return
    (job_id, lease_id, runner_id)."""
    cap_id = _probe_cap(k)
    jid = jobs.enqueue(k, name, capability=cap_id, run_at=run_at, max_uses=1, window=window)
    st = jobs.status(k, jid)
    return jid, st["lease"], st["runner"]


def _crash_fire(k, lease, runner_id):
    """Reproduce the crash window: fire the effect through the lease (the INVOKE + receipt
    land on the Weft) but DO NOT mark the job DONE — as if the process died in jobs.run's
    gap between the invoke and the status re-assert."""
    res = k.invoke(k.weave().get(runner_id), lease, {"text": "nightly-report"})
    assert res.get("status") == "SUCCEEDED", f"the pre-crash effect must succeed: {res}"
    return res


def run(k, line):
    line("\n== CRASH-RESUMABLE DURABLE EXECUTION — recover an interrupted job, exactly once ==")

    # ── (a) DURABLE — an enqueued job survives a Kernel reconstruction. ───────────────
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    k0 = _kernel(db)
    jid, lease, runner = _enqueue(k0, "nightly")
    assert k0.weave().get(jid).content["status"] == jobs.ENQUEUED
    k0b = Kernel(db, fresh=False)                     # a fresh process over the SAME log
    assert jid in {c.id for c in jobs.due(k0b, 0)}, \
        "an enqueued job must fold back, still due, on a reconstructed Kernel (durable across restart)"
    line("  durable: an enqueued job folds back on a NEW Kernel over the same weft.db — still "
         "due after a restart (durability is a fold, not a feature) ✓")

    # ── (b) THE BUG IS REAL — the crash window + a naive restart lies (but never double-fires).
    db2 = os.path.join(tempfile.mkdtemp(), "weft.db")
    kc = _kernel(db2)
    jid2, lease2, runner2 = _enqueue(kc, "invoice-run")
    _crash_fire(kc, lease2, runner2)
    assert kc.weave().get(jid2).content["status"] == jobs.ENQUEUED, \
        "the crash window must leave the job ENQUEUED with its effect already fired"
    naive = Kernel(db2, fresh=False)
    uses_pre = naive.lease_uses(naive.weave(), lease2)
    assert uses_pre == 1, "the effect fired once pre-crash — the lease records exactly one use"
    naive_res = jobs.run(naive, naive.weave().get(runner2), jid2, 0)   # the NAIVE restart path
    assert naive_res["status"] == jobs.FAILED and "exhausted" in (naive_res.get("denied") or ""), \
        f"the naive restart must be denied by the exhausted lease (the lying FAILED): {naive_res}"
    assert naive.lease_uses(naive.weave(), lease2) == 1, \
        "exactly-once holds: the denied re-run did NOT fire a second effect"
    line("  bug is real: after the crash window a NAIVE restart re-runs the job, is denied by "
         "the exhausted lease, and records it FAILED — though the effect SUCCEEDED (no double "
         "fire, but a lying outcome) ✓")

    # ── (c) RECOVER IS TRUTHFUL + EXACTLY-ONCE (on a clean reconstruction of the crash). ──
    db3 = os.path.join(tempfile.mkdtemp(), "weft.db")
    kc3 = _kernel(db3)
    jid3, lease3, runner3 = _enqueue(kc3, "settlement")
    fired_res = _crash_fire(kc3, lease3, runner3)
    rk = Kernel(db3, fresh=False)                     # restart
    uses_before = rk.lease_uses(rk.weave(), lease3)
    report = resume.recover(rk, 0)
    assert {"job": jid3, "status": jobs.DONE} in report["reconciled"], \
        f"recover must reconcile the crash-fired job to DONE from its receipt: {report}"
    job_now = rk.weave().get(jid3).content
    assert job_now["status"] == jobs.DONE and job_now.get("recovered") is True, \
        "the reconciled job must be DONE and flagged recovered (provenance of the repair)"
    assert job_now["result"] == fired_res["result_cell"], \
        "the reconciled job's result must point at the REAL pre-crash receipt (truthful)"
    assert rk.lease_uses(rk.weave(), lease3) == uses_before == 1, \
        "recover must NOT re-invoke — the lease use-count is unchanged (exactly-once)"
    assert jid3 not in {c.id for c in jobs.due(rk, 0)}, "a reconciled job is no longer due"
    line("  recover: reconciles the crash-fired job to DONE from its own receipt WITHOUT "
         "re-invoking — lease uses unchanged (1), result points at the real receipt ✓")

    # ── (d) WIRED INTO THE REACTOR — a plain tick after restart recovers, never false-fails. ─
    db4 = os.path.join(tempfile.mkdtemp(), "weft.db")
    kc4 = _kernel(db4)
    jid4, lease4, runner4 = _enqueue(kc4, "payroll")
    _crash_fire(kc4, lease4, runner4)
    rk4 = Kernel(db4, fresh=False)
    uses4 = rk4.lease_uses(rk4.weave(), lease4)
    summary = reactor.tick(rk4, 0)
    assert {"job": jid4, "status": jobs.DONE} in summary["recovered"], \
        f"reactor.tick must RECOVER the crash-fired job (not false-fail it): {summary['recovered']}"
    assert rk4.weave().get(jid4).content["status"] == jobs.DONE
    assert not any(j["job"] == jid4 for j in summary["jobs"]), \
        "a recovered job must NOT also be re-run by the tick's due-lane"
    assert rk4.lease_uses(rk4.weave(), lease4) == uses4, "the reactor recovery fired no second effect"
    line("  wired: a plain reactor.tick after restart RECOVERS the crash-fired job to done "
         "(under `recovered`) instead of re-running it into a false FAILED ✓")

    # ── (e) IDEMPOTENT — a second recover / re-tick is a no-op. ───────────────────────
    again = resume.recover(rk4, 0)
    assert again["recovered"] == 0 and again["reconciled"] == [], f"recover must be idempotent: {again}"
    retick = reactor.tick(rk4, 0)
    assert retick["recovered"] == [] and retick["quiet"] is True, \
        f"a re-tick after recovery must be a quiet no-op: {retick}"
    line("  idempotent: a second recover (and a re-tick) find nothing to reconcile — a clean "
         "no-op; recovery repairs exactly once ✓")

    # ── (f) REPAIR, NOT RE-RUN — a job that NEVER fired still runs normally. ───────────
    db5 = os.path.join(tempfile.mkdtemp(), "weft.db")
    kc5 = _kernel(db5)
    jid5, lease5, runner5 = _enqueue(kc5, "digest")            # never fired
    fresh_report = resume.recover(kc5, 0)
    assert fresh_report["recovered"] == 0, "recover must not touch a job that never fired"
    s5 = reactor.tick(kc5, 0)
    assert any(j["job"] == jid5 and j["status"] == jobs.DONE for j in s5["jobs"]), \
        f"a never-fired due job must be RUN to done by the normal lane: {s5['jobs']}"
    assert s5["recovered"] == [], "the never-fired job is a fresh run, not a recovery"
    line("  repair, not re-run: a job that never fired is left for the normal due-lane, which "
         "runs it to done — recover reconciles only already-fired jobs, starting no fresh effect ✓")

    line("  → durable execution is now crash-RESUMABLE: an interrupted job is reconciled to "
         "its true receipt outcome on restart (exactly-once, never a lying FAILED), the "
         "reactor recovers before it runs due work, and recovery is an idempotent no-op when "
         "there is nothing to repair.")
