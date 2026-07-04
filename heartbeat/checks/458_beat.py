"""PRODUCTION BEAT DRIVER — the always-on loop gets a real caller (Batch A wiring).

Cycles 53-56 built the always-on SUBSTRATE as check-proven libraries — `daemon`
(the durable loop cursor), `reactor.tick` (the deterministic pass), `observ` (the
folded operational report), `backup` (the tamper-evident log manifest) — but NOTHING
in the running system called them: `run.py` was a REPL that never beat. This check
proves the wiring that makes "always-on" a BEHAVIOR:

  - `shell.do_beat` — the production caller: sweep the durable run-loop from its
    Weft-folded checkpoint through the current logical frontier (`daemon.resume`,
    one `reactor.tick` per frontier), printing the tick summary;
  - `shell.do_metrics` / `do_backup` / `do_restore` — the substrate surface on the
    same operator prompt (`observ.dashboard_lines`, `backup.backup/verify/restore`);
  - `run.resume_loop` — the boot-time hook: a warm restart CONTINUES the loop from
    the durable checkpoint (no beat re-fired, none skipped); a world whose loop has
    never beaten boots exactly as before ([]— resuming is continuing, not starting).

This check proves, offline + deterministically (fresh Kernels over tmp dbs, logical
int frontiers, no clock, no network — the Shell object is driven directly and its
stdout captured):

  (a) BEAT DRIVES THE LOOP (load-bearing): arm due work (an enqueued durable job at
      run_at=0 + a scheduled event at at=0), then invoke `do_beat("")` — the daemon
      checkpoint moves from NEVER to the pre-beat frontier AND the due work FIRES
      (the job runs to DONE through its pre-fixed lease; the event fires). A re-beat
      is quiet (fired 0) and a beat at an already-checkpointed frontier is a genuine
      no-op (the cursor never moves backward).
  (b) SUBSTRATE SURFACE: `metrics` prints the folded report and appends NOTHING (a
      pure lens); `backup <path>` exports a manifest that verifies and `restore`
      round-trips it through Weft.ingest into a fresh db; a TAMPERED manifest is
      refused fail-closed — no destination db is ever created.
  (c) BOOT RESUME: on a fresh Kernel over the SAME db (a restart), `run.resume_loop`
      continues from the durable checkpoint — work that became due while "down"
      fires, the already-run job is NOT re-fired (its single-use lease count is
      unchanged), and a second resume fires nothing. On a never-beaten world the
      hook returns [] and appends nothing (keyless boot behavior unchanged).

Mutation-resistance (the load-bearing line): in `shell.do_beat`, drop
`out = daemon.resume(self.k, upto)` (make the command a no-op) and (a) goes RED —
the checkpoint stays NEVER and the due job/event never fire from the command.

Contract: run(k, line). Fail loud (assert). Owns fresh Kernels; registers its OWN
hermetic effect (`beat_probe`), never 'echo'.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import pathlib
import tempfile

from decima.kernel import Kernel
from decima.shell import Shell
from decima.weft import Weft
from decima import daemon, jobs, scheduling as sched, backup, executor

# A check-local, deterministic effect so the probe is HERMETIC — independent of any
# other check's registrations (executor.register overwrites by name; never 'echo').
_PROBE_EFFECT = "beat_probe"


def _probe_cap(k):
    """Register the pure probe effect and mint+grant a capability for it to the
    decima orchestrator (public kernel APIs only) so `jobs.enqueue` can attenuate
    it into a job lease. A driver step confers no authority — the job still runs
    through ONLY its pre-fixed lease."""
    executor.register(_PROBE_EFFECT,
                      lambda impl, args: {"out": "beat:" + str(args.get("text", ""))})
    cap_id = k._assert_cap(_PROBE_EFFECT, _PROBE_EFFECT)
    k.grant(cap_id, k.decima_agent_id)
    return cap_id


def _run(sh, command, arg=""):
    """Drive one shell command directly (the do_ method) and capture its stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        getattr(sh, "do_" + command)(arg)
    return buf.getvalue()


def _load_run_module():
    """Load heartbeat/run.py as a module (top level is side-effect free: main() is
    guarded and every import lives inside a function)."""
    path = pathlib.Path(__file__).resolve().parent.parent / "run.py"
    spec = importlib.util.spec_from_file_location("decima_run_458", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(k, line):
    line("\n== PRODUCTION BEAT DRIVER — the always-on loop gets a real caller ==")

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "weft.db")
    sh = Shell(db, fresh=True)
    k1 = sh.k

    # ── (a) BEAT DRIVES THE LOOP — the command actually MAKES the heartbeat beat. ──
    cap = _probe_cap(k1)
    jid = jobs.enqueue(k1, "beat-work", capability=cap, run_at=0, max_uses=1,
                       window=100_000)
    eid = sched.schedule(k1, "beat-reminder", 0)
    lease = jobs.status(k1, jid)["lease"]
    assert daemon.checkpoint(k1) == daemon.NEVER, "the loop must start never-beaten"
    assert k1.weave().get(jid).content["status"] == jobs.ENQUEUED
    frontier = int(k1.weft.lamport)

    out_a = _run(sh, "beat")                              # ← the production caller
    assert daemon.checkpoint(k1) == frontier, \
        (f"do_beat must advance the durable checkpoint to the pre-beat frontier "
         f"{frontier}, folded {daemon.checkpoint(k1)} — the beat did not drive the loop")
    assert k1.weave().get(jid).content["status"] == jobs.DONE, \
        "the due job must FIRE from the beat (reactor.tick ran through daemon.resume)"
    assert k1.weave().get(eid).content["fired"] is True, \
        "the due scheduled event must FIRE from the beat"
    assert k1.lease_uses(k1.weave(), lease) == 1, "the job fired through its lease, once"
    assert "beat: checkpoint e-1" in out_a and "fired" in out_a, out_a
    line(f"  beat: checkpoint e-1 → e{frontier} — the due job ran to DONE through its "
         f"pre-fixed lease and the scheduled event fired; the loop cursor is now a "
         f"durable Cell on the Weft ✓")

    # A RE-BEAT is quiet (only the checkpoint's own frontier is new; nothing due) and
    # a beat at an already-checkpointed frontier is a genuine no-op (cursor monotone).
    cp = daemon.checkpoint(k1)
    out_re = _run(sh, "beat")
    assert daemon.checkpoint(k1) > cp and "fired 0" in out_re, \
        f"a re-beat must sweep only the new frontiers and fire nothing: {out_re}"
    assert k1.lease_uses(k1.weave(), lease) == 1, "a re-beat must never re-fire the job"
    events_before = k1.weft.count()
    out_noop = _run(sh, "beat", str(daemon.checkpoint(k1)))
    assert "quiet" in out_noop and k1.weft.count() == events_before, \
        f"a beat at the checkpointed frontier must tick nothing and append nothing: {out_noop}"
    out_bad = _run(sh, "beat", "1.5")
    assert "usage" in out_bad, "a non-int frontier must be refused at the door"
    line("  idempotent + monotone: a re-beat fires nothing (lease uses unchanged), a "
         "beat at the checkpointed frontier is a no-op, a float frontier is refused ✓")

    # ── (b) SUBSTRATE SURFACE — metrics is a pure lens; backup→restore round-trips. ──
    before = k1.weft.count()
    out_m = _run(sh, "metrics")
    assert k1.weft.count() == before, "metrics must be a PURE lens — it appends nothing"
    for token in ("events", "invocations", "jobs", "spend"):
        assert token in out_m, f"the folded report must include {token!r}: {out_m}"
    line("  metrics: prints the folded operational report (events/invocations/jobs/"
         "spend) and appends NOTHING — Law 5, a lens not a Cell ✓")

    manifest = os.path.join(tmp, "backup.json")
    out_b = _run(sh, "backup", manifest)
    assert os.path.exists(manifest) and "backed up" in out_b, out_b
    with open(manifest) as f:
        blob = json.load(f)
    ok, reason = backup.verify(blob)
    assert ok and blob["count"] == k1.weft.count(), \
        f"the exported manifest must verify and cover the whole log: {reason}"

    dest = os.path.join(tmp, "restored.db")
    out_r = _run(sh, "restore", f"{manifest} {dest}")
    assert "restored" in out_r and os.path.exists(dest), out_r
    assert Weft(dest, k1.keyring).count() == blob["count"], \
        "restore must round-trip EVERY event through Weft.ingest into the fresh db"

    # TAMPER: bump one payload's lamport — the content id no longer matches, so the
    # shell refuses BEFORE any database is touched (fail closed, no partial world).
    bad = copy.deepcopy(blob)
    row = bad["events"][len(bad["events"]) // 2]
    payload = json.loads(row[1])
    payload["lamport"] = int(payload["lamport"]) + 1
    row[1] = json.dumps(payload)
    tampered = os.path.join(tmp, "tampered.json")
    with open(tampered, "w") as f:
        json.dump(bad, f)
    dest2 = os.path.join(tmp, "never.db")
    out_t = _run(sh, "restore", f"{tampered} {dest2}")
    assert "refused" in out_t and "mismatch" in out_t, \
        f"a tampered manifest must be refused loud: {out_t}"
    assert not os.path.exists(dest2), \
        "a refused restore must create NO destination db — fail closed, no partial world"
    line(f"  backup→restore: {blob['count']} events export as a root-chained manifest, "
         f"verify, and round-trip through Weft.ingest; a tampered payload is refused "
         f"before a single row touches a database ✓")

    # ── (c) BOOT RESUME — a restart CONTINUES the loop from the durable checkpoint. ──
    run_mod = _load_run_module()
    cp1 = daemon.checkpoint(k1)
    k2 = Kernel(db, fresh=False)                          # the restart
    assert daemon.checkpoint(k2) == cp1, \
        "the loop cursor must fold back identically on a fresh Kernel (durable)"
    cap2 = _probe_cap(k2)
    jid2 = jobs.enqueue(k2, "post-restart", capability=cap2, run_at=cp1 + 1,
                        max_uses=1, window=100_000)       # became due while "down"
    lines = run_mod.resume_loop(k2)
    assert lines and "resumed" in lines[0], f"the boot hook must resume: {lines}"
    assert daemon.checkpoint(k2) > cp1, "the boot resume must advance the checkpoint"
    assert k2.weave().get(jid2).content["status"] == jobs.DONE, \
        "the boot resume must FIRE work that became due after the checkpoint (no skip)"
    assert k2.weave().get(jid).content["status"] == jobs.DONE
    assert k2.lease_uses(k2.weave(), lease) == 1, \
        "the boot resume must NOT re-fire an already-beaten job (no re-fire)"
    results_after = len(k2.weave().of_type("result"))
    lines2 = run_mod.resume_loop(k2)
    assert len(k2.weave().of_type("result")) == results_after, \
        f"a second boot resume must fire nothing (idempotent): {lines2}"

    # A world whose loop has NEVER beaten boots exactly as before: [] and no append.
    k3 = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    fresh_count = k3.weft.count()
    assert run_mod.resume_loop(k3) == [] and daemon.checkpoint(k3) == daemon.NEVER, \
        "a never-beaten world must boot unchanged — resuming is continuing, not starting"
    assert k3.weft.count() == fresh_count, "the no-op hook must append nothing"
    line("  boot resume: a fresh Kernel over the same db folds the checkpoint back, "
         "run.resume_loop continues the loop (work due while down fires, nothing "
         "re-fires, a second resume is quiet), and a never-beaten world boots as "
         "before ([]) ✓")

    line("  → \"always-on\" is now a BEHAVIOR, not a library: the operator's `beat` "
         "drives daemon.resume → reactor.tick off the durable Weft cursor, metrics/"
         "backup/restore sit on the same prompt (restore fails closed on tamper), and "
         "a restart CONTINUES the heartbeat — no beat re-fired, none skipped.")
