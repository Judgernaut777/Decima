"""JOBS1 — durable job queue: a scheduled job is a FUTURE AUTHORITY GRANT.

CAPABILITY_MAP B2: "A scheduled job is a *future authority grant* — fix its capability set
at enqueue, Morta-review." This check proves the module makes that literal, composing
SCHED1 (scheduling), LEASE1 (time-locked + use-bounded grants), and the kernel ocap path:

  - enqueue FIXES the job's authority AT ENQUEUE by minting a LEASE (a downhill grant,
    time-locked to run_at + use-bounded) to a single-purpose runner — Morta-reviewable;
  - the job becomes DUE at run_at (due(now) is a clock-parameterized projection; a future
    job is excluded);
  - run uses ONLY the pre-fixed lease; an attempt to EXCEED it (a cost over the lease
    budget) fails CLOSED at the SAME authorize gate as any invoke — no escalation;
  - the lease is TIME-LOCKED: running a job past its expiry frontier fails CLOSED;
  - status is tracked (enqueued → done / failed) and the whole queue is on the Weft.

DETERMINISM: "now" is the logical frontier (lamport / int tick); ints not floats; fail
CLOSED. Contract: run(k, line). Fail loud.
"""
from decima import jobs


def run(k, line):
    line("\n== DURABLE JOB QUEUE (a scheduled job = a FUTURE AUTHORITY GRANT) — JOBS1 ==")
    w = lambda: k.weave()
    ids = lambda cells: {c.id for c in cells}

    # A fresh, no-cost READ capability the orchestrator holds — the base the job's lease
    # is attenuated DOWN from (the shared kernel's bootstrap caps have spent budgets).
    base = k.integrate_tool(
        "jobs.echo", lambda impl, args: {"out": args.get("text", "")},
        caveats={"effect_class": "READ", "budget": 10})

    # ── 1. ENQUEUE fixes the future authority grant (a lease) AT ENQUEUE ───────
    now0 = k.weft.lamport
    run_at = now0 + 6                          # a logical tick in the near future (int)
    job = jobs.enqueue(k, "nightly-report", capability=base, run_at=run_at,
                       max_uses=1, budget=4)
    st = jobs.status(k, job)
    assert st["status"] == jobs.ENQUEUED, st
    lease = w().get(st["lease"])
    # The lease IS the future authority grant: a capability Cell on the Weft, fixed now —
    # time-locked to the run window and use-bounded. Morta-reviewable like any grant.
    assert lease is not None and lease.type == "capability", "lease must be a capability Cell"
    cav = lease.content["caveats"]
    assert cav["expires_at"] == st["expires_at"] and cav["max_uses"] == 1 and cav["budget"] == 4, cav
    assert cav["expires_at"] > run_at, "lease is live across the job's run window, not just one tick"
    assert isinstance(cav["expires_at"], int) and isinstance(run_at, int), "ticks are ints"
    line(f"  enqueued 'nightly-report' @ tick {run_at}: authority FIXED as lease "
         f"{st['lease'][:8]} (expires_at {cav['expires_at']}, max_uses 1, budget 4) — on the Weft ✓")

    # ── 2. due(now) is a clock-parameterized projection — future job excluded ──
    assert job not in ids(jobs.due(k, now=now0)), "job must NOT be due before run_at"
    d = jobs.due(k, now=run_at)
    assert job in ids(d), "job must be due at run_at"
    line(f"  due(now<{run_at}) excludes it; due(now={run_at}) returns it — logical clock ✓")

    # ── 3. OVER-REACH fails CLOSED: cost beyond the lease budget is denied ─────
    # The runner holds NOTHING but the one lease (no ambient authority). Invoking with a
    # cost over the pre-fixed budget is an attempt to use authority beyond the lease — it
    # fails closed at the SAME authorize gate as any invoke, and marks the job failed.
    runner = w().get(st["runner"])
    over = jobs.run(k, runner, job, now=run_at, args={"text": "too costly", "cost": 9})
    assert over["status"] == jobs.FAILED and "denied" in over, over
    assert "budget" in over["denied"], over["denied"]
    assert jobs.status(k, job)["status"] == jobs.FAILED, "over-reach must mark the job failed"
    line(f"  over-reach (cost 9 > lease budget 4) → ✋ {over['denied']} — job FAILED, "
         "no escalation past the pre-fixed lease ✓")

    # ── 4. A job run within its lease SUCCEEDS using only the pre-fixed grant ──
    job2 = jobs.enqueue(k, "send-digest", capability=base, run_at=k.weft.lamport + 4,
                        max_uses=1, budget=4)
    st2 = jobs.status(k, job2)
    runner2 = w().get(st2["runner"])
    r = jobs.run(k, runner2, job2, now=st2["run_at"], args={"text": "digest sent", "cost": 2})
    assert r["status"] == jobs.DONE and "ok" in r, r
    assert r["ok"]["out"] == "digest sent", r
    assert jobs.status(k, job2)["status"] == jobs.DONE
    assert job2 not in ids(jobs.due(k, now=st2["run_at"] + 50)), "a done job is not due again"
    # Durable: a completed job runs at most once — re-running is fail-loud.
    try:
        jobs.run(k, runner2, job2, now=st2["run_at"])
        raise AssertionError("re-running a done job must raise (durable: runs once)")
    except ValueError:
        pass
    line(f"  run within lease → DONE (out {r['ok']['out']!r}); not due again; "
         "re-run raises (durable, runs once) ✓")

    # ── 5. TIME-LOCK fails CLOSED: running PAST the lease expiry is denied ─────
    # expires_at = run_at + 1, so advance the logical frontier past it, then run. The
    # time-locked lease lapses (LEASE1) and the job fails closed — a missed job cannot
    # fire late with stale authority. "now" here is weft.lamport (logical), never a clock.
    late_at = k.weft.lamport + 3
    job3 = jobs.enqueue(k, "expire-me", capability=base, run_at=late_at, max_uses=1, window=4)
    st3 = jobs.status(k, job3)
    runner3 = w().get(st3["runner"])
    while k.weft.lamport <= st3["expires_at"]:      # advance frontier past expires_at
        k.say("echo tick")
    assert k.weft.lamport > st3["expires_at"], "frontier must be past the lease expiry"
    late = jobs.run(k, runner3, job3, now=k.weft.lamport, args={"text": "too late"})
    assert late["status"] == jobs.FAILED and "denied" in late, late
    assert "expired" in late["denied"], late["denied"]
    line(f"  run past expiry (frontier {k.weft.lamport} > expires_at {st3['expires_at']}) "
         f"→ ✋ {late['denied']} — time-locked authority fails CLOSED ✓")

    # ── 6. EVERYTHING ON THE WEFT (Law 1): job, lease, schedule + edges folded ─
    assert {job, job2, job3} <= ids(w().of_type(JOB := "job")), "jobs must be job Cells"
    auth_edges = w().edges_from(job, "authority")
    sched_edges = w().edges_from(job, "scheduled_as")
    assert auth_edges and auth_edges[0]["dst"] == st["lease"], "job→authority→lease edge on Weft"
    assert sched_edges, "job→scheduled_as→scheduled_event edge on Weft"
    # Determinism: two independent folds give an identical state_root.
    r1, r2 = w().state_root(), w().state_root()
    assert r1 == r2, "two folds must give an identical state_root (jobs are deterministic)"
    line(f"  on the Weft: job/lease/schedule Cells + authority+scheduled_as edges; "
         f"two folds → identical state_root ({r1[:12]}…) ✓")

    line("  → a durable job FIXES its capability set at enqueue as a time-locked, "
         "use-bounded LEASE — the future authority grant. Running uses ONLY that lease; "
         "exceeding it or running past its window fails CLOSED. No ambient authority, "
         "no escalation; the whole queue is a deterministic fold over the Log.")
