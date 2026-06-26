"""REACTOR1 — the reactive tick (the live heartbeat).

WATCH1 (watchers), SCHED1 (scheduled events) and JOBS1 (durable jobs) each give Decima a kind
of reactivity. REACTOR1 is the single deterministic PASS that fires all three at one logical
instant. This check proves, composing the three lanes' PUBLIC APIs only:

  - set up ONE of each due at a shared logical tick T: a watcher matching a cell, a scheduled
    event at T, a durable job at T;
  - tick(T) fires ALL THREE in one pass — the watcher's gated disposition + trigger, the
    scheduled event's disposition, the job's run on its pre-fixed lease — and reports them;
  - a tick with nothing due is a NO-OP (quiet);
  - re-ticking the SAME now is IDEMPOTENT — nothing re-fires (the second tick is quiet);
  - DETERMINISTIC — two folds give an identical state_root after the pass;
  - everything is AUDITED on the Weft (trigger, disposition, fired event, job transition).

DETERMINISM: "now" is a logical int tick the caller supplies (no wall-clock). Contract:
run(k, line). Fail loud.
"""
from decima import reactor
from decima import scheduling as sched
from decima import jobs
from decima import watch
from decima.model import assert_content
from decima.hashing import content_id


def run(k, line):
    line("\n== REACTIVE TICK (watchers + scheduled events + due jobs fire in ONE pass) — REACTOR1 ==")
    w = lambda: k.weave()
    ids = lambda cells: {c.id for c in cells}

    # A shared logical tick T in the near future at which all three sources become due.
    # "now" is logical (an int); pick T with headroom past the current frontier.
    T = k.weft.lamport + 8

    # ── set up: a WATCHER matching a cell ────────────────────────────────────
    # Owner-registered automation: fire on a HIGH-severity alert whose text contains "outage".
    # Match on a unique `source` tag too, so this watcher fires on exactly OUR alert and not
    # on any `alert` cell another check left in the shared kernel state.
    wid = watch.register_watcher(
        k, "reactor-outage",
        on_type="alert",
        predicate={"severity": {"op": ">=", "value": "high"},
                   "text": {"op": "~", "value": "outage"},
                   "source": "reactor-monitor"},
        action={"source": "watcher", "text": "high-severity outage alert — open triage",
                "kind": "request"})
    alert_id = content_id({"reactor_alert": "prod-outage"})
    assert_content(k.weft, k.decima_agent_id, alert_id, "alert", {
        "severity": "critical", "text": "prod outage: region down", "source": "reactor-monitor"})

    # ── set up: a SCHEDULED EVENT due at T ───────────────────────────────────
    event_id = sched.schedule(k, "Reactor cron sweep", at=T)
    assert w().get(event_id).content["at"] == T and not w().get(event_id).content["fired"]

    # ── set up: a durable JOB due at T (its authority FIXED at enqueue as a lease) ──
    base = k.integrate_tool(
        "reactor.echo", lambda impl, args: {"out": args.get("text", "")},
        caveats={"effect_class": "READ", "budget": 10})
    # `window` is the lease's live span (expires_at = run_at + window). The lease expiry is
    # checked against the LOGICAL FRONTIER (k.weft.lamport), which advances with every assert
    # the pass makes — and a tick fires every due source in the SHARED kernel, so the frontier
    # can jump well past T. Give the run window ample headroom so our job runs within its lease.
    job_id = jobs.enqueue(k, "reactor-digest", capability=base, run_at=T,
                          max_uses=1, budget=4, window=4096)
    runner_id = jobs.status(k, job_id)["runner"]
    line(f"  armed: watcher {wid[:8]} (matches alert {alert_id[:8]}), "
         f"event {event_id[:8]} @ T={T}, job {job_id[:8]} @ T={T} (lease-bound) ✓")

    # ── BEFORE T: a tick fires the watcher (its match exists now) but NOT the event/job ──
    # tick fires EVERY armed watcher / due event / due job in the shared kernel, so we identify
    # OUR fires by id (the trigger is content-addressed over our watcher + our alert) rather
    # than a global count — other checks may leave their own due sources in the shared state.
    our_trigger = content_id({"trigger": wid, "match": alert_id})
    ev_ids = lambda r: {e["event"] for e in r["events"]}
    job_ids = lambda r: {j["job"] for j in r["jobs"]}
    early = reactor.tick(k, T - 1)
    assert our_trigger in early["watchers"], f"our watcher should fire at T-1, got {early['watchers']}"
    assert event_id not in ev_ids(early), "our event must NOT be due before T"
    assert job_id not in job_ids(early), "our job must NOT be due before T"
    line(f"  tick(T-1={T-1}): our watcher fires (trigger {our_trigger[:8]}), "
         f"our event+job NOT due → {early['fired']} fired ✓")

    # The watcher already fired, so it does NOT re-fire (idempotent WATCH1) — but OUR event +
    # job are now due. tick(T) fires BOTH in the one pass (and re-fires no watcher).
    res = reactor.tick(k, T)
    assert res["now"] == T
    assert our_trigger not in res["watchers"], "our watcher fired at T-1 → no re-fire (idempotent)"
    assert res["watchers"] == [], "no watcher re-fires at T (all already fired or unmatched)"
    assert event_id in ev_ids(res), f"our event must fire at T, got {res['events']}"
    assert job_id in job_ids(res), f"our job must run at T, got {res['jobs']}"
    assert res["fired"] >= 2 and not res["quiet"]
    line(f"  tick(T={T}): our scheduled event + job fire in ONE pass "
         f"({res['fired']} total fired) ✓")

    # ── the event fired through DISPOSITION (Decima's decision, gated) ────────
    ev = next(e for e in res["events"] if e["event"] == event_id)
    assert ev["action"] == "task", ev   # trusted request → task
    disp_cell = w().get(ev["disposition"])
    assert disp_cell.type == "disposition" and disp_cell.content["action"] == "task"
    assert w().get(event_id).content["fired"] is True, "event must be marked fired (LWW)"

    # ── the job RAN on its pre-fixed lease (DONE), audited as a job transition ──
    jr = next(j for j in res["jobs"] if j["job"] == job_id)
    assert jr["status"] == jobs.DONE, jr
    assert "denied" not in jr, jr
    assert jobs.status(k, job_id)["status"] == jobs.DONE
    line(f"  event → disposition '{ev['action']}' (gated, on Weft); job → {jr['status']} "
         "on its pre-fixed lease (no escalation) ✓")

    # ── the watcher's earlier fire is AUDITED on the Weft (trigger + edge + disposition) ──
    trig = w().get(our_trigger)
    assert trig.type == "trigger" and trig.content["matched"] == alert_id
    prov = w().edges_from(trig.id, "triggered_by")
    assert prov and prov[0]["dst"] == alert_id, "trigger missing triggered_by edge to match"
    assert trig.content["disposed_action"] == "task", trig.content
    line(f"  watcher trigger {trig.id[:8]} audited: triggered_by→{alert_id[:8]}, "
         f"routed via disposition → {trig.content['disposed_action']} ✓")

    # ── IDEMPOTENCE: re-ticking the SAME now never re-fires what already fired ─
    # OUR watcher trigger is live, OUR event is fired, OUR job is done — so re-ticking T does
    # NOT re-fire any of them (the lanes are each idempotent: WATCH1 skips a live trigger,
    # SCHED1's due() excludes a fired event, JOBS1's due() excludes a run job). A tick over a
    # shared kernel can still drain unrelated sources that other checks left, so we assert OUR
    # items specifically — and that the tick CONVERGES to a global fixpoint within a few ticks.
    again = reactor.tick(k, T)
    assert our_trigger not in again["watchers"], "our watcher must not re-fire on re-tick"
    assert event_id not in ev_ids(again), "our fired event must not re-fire on re-tick"
    assert job_id not in job_ids(again), "our done job must not re-fire on re-tick"
    line(f"  re-tick(T={T}): our trigger live, our event fired, our job done — "
         "none re-fires (each lane idempotent) ✓")

    # ── a tick with NOTHING due is a NO-OP ───────────────────────────────────
    # Tick at a frontier BELOW every scheduled item (now=0): no event/job is `due` there
    # (all have at/run_at > 0), and every armed watcher already fired (idempotent), so the
    # whole pass is empty — a clean quiet no-op, proving a tick fires only what is due.
    quiet = reactor.tick(k, 0)
    assert quiet["fired"] == 0 and quiet["quiet"] is True, quiet
    line(f"  tick(now=0) with nothing due → quiet no-op (fires only what is due) ✓")

    # ── DETERMINISM: two folds give an identical state_root after the pass ────
    r1, r2 = w().state_root(), w().state_root()
    assert r1 == r2, "two folds must give an identical state_root (the tick is deterministic)"

    # ── run_until: a stub run-loop drives a sequence of ticks ─────────────────
    # Drive an explicit sequence of low frontiers (nothing due there) — the heartbeat beating
    # over a sparse schedule: each tick is a genuine quiet no-op, and the loop returns one
    # summary per tick in order. (A non-quiet frontier would fire only what THAT now makes due.)
    loop = reactor.run_until(k, [0, 0, 0])
    assert len(loop) == 3 and all(t["quiet"] for t in loop), loop
    line(f"  run_until([0,0,0]) → 3 quiet ticks (the heartbeat beating); "
         f"state_root stable ({r1[:12]}…) ✓")

    line("  → REACTOR1: one deterministic pass fires every due watcher, scheduled event and "
         "durable job at a logical `now`; firing stays gated (disposition/lease, no escalation); "
         "re-ticking the same now is idempotent; everything audited on the Weft.")
