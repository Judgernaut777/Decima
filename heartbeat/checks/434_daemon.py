"""DURABLE RUN-LOOP across a restart (Phase 4 close-out · always-on substrate).

Durable JOBS already survive a restart (they are Cells) and `reactor.tick` is idempotent
per-frontier — but the RUN-LOOP itself had no durable memory of HOW FAR it has beaten:
`run_until` ticks an in-memory sequence and defaults its start to `k.weft.lamport`, so a
naive restart either RE-SCANS from an arbitrary start or SKIPS the beats between the last
processed frontier and now. DAEMON1 puts the loop's PROGRESS itself on the Weft — a
`loop_checkpoint` Cell recording the highest frontier FULLY ticked — so a fresh process
resumes exactly where the last one stopped.

This check proves, offline + deterministically (fresh Kernels reconstructed over ONE
temp db, logical int frontiers, no clock):

  (a) DURABLE CURSOR — `advance(k, N)` records a checkpoint; a NEW Kernel over the SAME
      weft.db folds `checkpoint == N` (the loop's progress survives a restart, as a Cell,
      with int-only content);
  (b) RESUME-NOT-RESTART (load-bearing) — beats armed in the gap (a probe job due at
      frontier M > N, a reminder in between) stay pending at checkpoint N; after a Kernel
      reconstruction, `resume(k, M)` ticks EXACTLY the frontiers (N, M] — the pending
      beats fire exactly once (probe counter +1, lease use-count 1) and NO frontier <= N
      is re-ticked (the pre-restart beat's counter and lease uses do NOT double);
  (c) NO SKIP — everything due in (N, M] actually fires by the time `resume` returns:
      the gap job is DONE, the gap reminder is fired at its own frontier; and a quiet
      span still moves the cursor (progress is recorded even when nothing is due);
  (d) IDEMPOTENT — `advance(k, N)` when already checkpointed at >= N is a genuine no-op:
      ticks nothing, fires nothing, moves no cursor, appends NOTHING to the Weft;
  (e) FAIL CLOSED — a float (and a bool) `upto` is a TypeError before anything ticks; an
      `upto` below the checkpoint is a ValueError (the cursor never moves backward).

Mutation-resistance (the load-bearing line): revert `frontiers = list(range(cp + 1,
upto + 1))` in `daemon.advance` to a fixed start (e.g. `range(0, upto + 1)`) and (b)/(d)
go RED — `resume` reports re-ticked frontiers <= N and the "no-op" re-advance ticks a
whole sweep again: a restart re-fires already-processed beats instead of continuing.

Contract: run(k, line). Fail loud (assert). Owns fresh Kernels reconstructed over one db.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import daemon, jobs, executor
from decima import scheduling as sched

# A check-local, deterministic effect so the probe is HERMETIC — our own name, never a
# shared effect like 'echo' (the module-global executor registry persists across checks).
_PROBE_EFFECT = "daemon_probe"

# Every invocation of the probe effect lands here — the double-fire detector: if any
# already-processed beat were re-INVOKED across the restart, this list would grow twice.
_CALLS = []


def _probe_cap(k):
    """Register the counting probe effect and mint+grant a capability for it to the
    decima orchestrator (so `jobs.enqueue` can attenuate it into a job lease)."""
    executor.register(_PROBE_EFFECT,
                      lambda impl, args: (_CALLS.append(str(args.get("text", ""))),
                                          {"out": "beat"})[1])
    cap_id = k._assert_cap(_PROBE_EFFECT, _PROBE_EFFECT)
    k.grant(cap_id, k.decima_agent_id)
    return cap_id


def run(k, line):
    line("\n== DURABLE RUN-LOOP — the heartbeat resumes across a restart ==")
    del _CALLS[:]

    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    k1 = Kernel(db, fresh=True)
    cap = _probe_cap(k1)

    # ── Arm the beats. One probe job due BEFORE the checkpoint (fires pre-restart), one
    # due in the restart gap, plus a plain reminder in between — all logical ints.
    assert daemon.checkpoint(k1) == daemon.NEVER == -1, \
        "a loop that has never run must fold the NEVER sentinel (-1)"
    jid_early = jobs.enqueue(k1, "early-beat", capability=cap, run_at=2,
                             max_uses=1, window=100_000)
    jid_gap = jobs.enqueue(k1, "gap-beat", capability=cap, run_at=6,
                           max_uses=1, window=100_000)
    ev_gap = sched.schedule(k1, "gap-reminder", 5)

    # ── (a) DURABLE CURSOR — advance to N=3, then fold the checkpoint on a NEW Kernel. ──
    s1 = daemon.advance(k1, 3)
    assert s1["from"] == -1 and s1["to"] == 3 and s1["ticked"] == [0, 1, 2, 3], \
        f"the first advance must tick 0..3 from the NEVER sentinel: {s1}"
    assert jobs.status(k1, jid_early)["status"] == jobs.DONE, \
        "the beat due at frontier 2 must have run within the sweep"
    assert len(_CALLS) == 1, f"the pre-restart beat fired exactly once: {_CALLS}"
    assert daemon.checkpoint(k1) == 3 and daemon.beats(k1) == 4

    k2 = Kernel(db, fresh=False)                      # a fresh process over the SAME log
    assert daemon.checkpoint(k2) == 3, \
        "the loop's progress must FOLD back on a reconstructed Kernel (a Cell, not a variable)"
    cursor = k2.weave().get(daemon.cursor_id())
    assert cursor is not None and cursor.type == daemon.LOOP_CHECKPOINT
    for key in ("frontier", "beats"):
        v = cursor.content[key]
        assert isinstance(v, int) and not isinstance(v, bool), \
            f"loop_checkpoint.{key} must be an int (ints-not-floats), got {type(v).__name__}"
    line("  durable cursor: advance(k, 3) records ONE loop_checkpoint Cell; a NEW Kernel "
         "over the same weft.db folds checkpoint == 3 — the loop's progress survives a "
         "restart as a fold, int-only ✓")

    # ── (b) RESUME-NOT-RESTART — the restart continues; no frontier <= N is re-ticked. ──
    lease_early = jobs.status(k2, jid_early)["lease"]
    assert k2.lease_uses(k2.weave(), lease_early) == 1
    r = daemon.resume(k2, 6)
    assert r["resumed_from"] == 3 and r["from"] == 3 and r["to"] == 6, f"resume summary: {r}"
    assert r["ticked"] == [4, 5, 6], \
        f"resume must tick EXACTLY the frontiers (3, 6] — never re-tick <= checkpoint: {r}"
    assert len(_CALLS) == 2, \
        f"the gap beat fired exactly once and the pre-restart beat did NOT re-fire: {_CALLS}"
    assert k2.lease_uses(k2.weave(), lease_early) == 1, \
        "no frontier <= N was re-ticked: the pre-restart beat's lease use-count is unchanged"
    assert jobs.status(k2, jid_early)["status"] == jobs.DONE, \
        "the already-processed beat keeps its DONE outcome (never clobbered by the restart)"
    line("  resume-not-restart: after a Kernel reconstruction resume(k, 6) ticks exactly "
         "[4, 5, 6] — the pending beat fires ONCE (probe count 2, lease uses 1) and no "
         "already-processed frontier is re-beaten ✓")

    # ── (c) NO SKIP — everything armed in the gap fired by the time resume returned. ────
    assert jobs.status(k2, jid_gap)["status"] == jobs.DONE, \
        "the beat due at frontier 6 must fire by the time resume(k, 6) returns (no skip)"
    ev = k2.weave().get(ev_gap)
    assert ev.content["fired"] is True and ev.content["fired_at"] == 5, \
        f"the reminder armed at frontier 5 must fire AT 5 within the resumed sweep: {ev.content}"
    assert daemon.checkpoint(k2) == 6 and daemon.beats(k2) == 7
    quiet = daemon.advance(k2, 8)                     # a span with nothing due
    assert quiet["ticked"] == [7, 8] and quiet["fired"] == 0 and quiet["quiet"] is True
    assert daemon.checkpoint(k2) == 8, \
        "a quiet span still moves the cursor — progress is recorded even when nothing is due"
    line("  no skip: the gap job is DONE, the gap reminder fired at its own frontier (5), "
         "and a quiet span still advances the durable cursor to 8 ✓")

    # ── (d) IDEMPOTENT — re-advancing to an already-checkpointed frontier is a no-op. ───
    lamport_before = k2.weft.lamport
    noop = daemon.advance(k2, 8)
    assert noop == {"from": 8, "to": 8, "ticked": [], "fired": 0, "quiet": True}, \
        f"advance to the current checkpoint must tick NOTHING and fire NOTHING: {noop}"
    assert k2.weft.lamport == lamport_before, \
        "the no-op re-advance must append NOTHING to the Weft (no new checkpoint Cell)"
    assert len(_CALLS) == 2 and daemon.checkpoint(k2) == 8, \
        "the no-op moved no cursor and re-fired no beat"
    line("  idempotent: advance(k, 8) when already checkpointed at 8 ticks nothing, fires "
         "nothing, appends nothing — safe to call blindly on every restart ✓")

    # ── (e) FAIL CLOSED — float/bool frontier refused; the cursor never rewinds. ────────
    for bad in (8.0, True):
        try:
            daemon.advance(k2, bad)
            raise AssertionError(f"a {type(bad).__name__} frontier was accepted (must fail loud)")
        except TypeError:
            pass
    try:
        daemon.resume(k2, 5)                          # below the folded checkpoint (8)
        raise AssertionError("an upto below the checkpoint was accepted (cursor rewound)")
    except ValueError:
        pass
    assert daemon.checkpoint(k2) == 8 and len(_CALLS) == 2, \
        "a refused advance leaves the cursor and the world untouched"
    k3 = Kernel(db, fresh=False)                      # one more restart, for good measure
    assert daemon.checkpoint(k3) == 8, "the final cursor folds back on yet another restart"
    line("  fail closed: a float/bool upto is a TypeError, an upto below the checkpoint is "
         "a ValueError — the cursor never moves backward, and it still folds to 8 on yet "
         "another fresh Kernel ✓")

    line("  → the run-loop is now DURABLE: its progress is a loop_checkpoint Cell folded "
         "from the Weft, so a restart RESUMES the heartbeat from exactly the last fully-"
         "ticked frontier — no beat re-fired, no beat skipped, the cursor monotone and "
         "int-only, and re-advancing a done sweep a true no-op.")
