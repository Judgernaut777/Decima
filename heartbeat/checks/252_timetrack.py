"""TIMETRACK1 — time-tracking / focus (a session is DATA, a duration is an INT fold).

Logical ticks (ints), no wall-clock: `at` is passed in by the caller. This check proves:
  - start opens a `time_session` Cell (int start, status open) on the Weft;
  - stop closes it via LWW and computes an INT duration = end - start (correctly);
  - report totals tracked time per activity correctly (ints);
  - an OPEN (un-stopped) session is reflected as open and contributes 0 to the totals;
  - focus_block schedules a reminder via SCHED1 (a scheduled_event, due at its tick);
  - it is DETERMINISTIC: recompute yields the same totals.

Contract: run(k, line). Fail loud.
"""
from decima import timetrack as tt
from decima import scheduling as sched


def run(k, line):
    line("\n== TIME-TRACKING / FOCUS (start → stop → report → focus_block) — TIMETRACK1 ==")
    w = lambda: k.weave()

    # 1. start: an OPEN time_session Cell at an int tick, on the Weft.
    s1 = tt.start(k, "coding", at=100)
    c1 = w().get(s1)
    assert c1 is not None and c1.type == "time_session", c1
    assert c1.content["status"] == "open" and c1.content["end"] is None
    assert c1.content["start"] == 100 and isinstance(c1.content["start"], int)
    line("  start('coding', at=100) → open time_session (int start) on the Weft ✓")

    # 2. stop: LWW-close the SAME cell, compute INT duration = end - start.
    d1 = tt.stop(k, s1, at=145)
    assert d1 == 45 and isinstance(d1, int), d1                # 145 - 100
    c1b = w().get(s1)
    assert c1b.id == s1, "stop must re-assert the SAME cell id (LWW)"
    assert c1b.content["status"] == "closed" and c1b.content["end"] == 145
    assert c1b.content["duration"] == 45
    line(f"  stop(at=145) → duration {d1} (= 145-100), session closed via LWW on same cell ✓")

    # 3. A second 'coding' session + a 'writing' session, both closed.
    s2 = tt.start(k, "coding", at=200)
    assert tt.stop(k, s2, at=230) == 30                        # 230 - 200
    s3 = tt.start(k, "writing", at=300)
    assert tt.stop(k, s3, at=360) == 60                        # 360 - 300
    line("  +coding(30) +writing(60): three closed sessions on the Weft ✓")

    # 4. An OPEN (un-stopped) session — reflected as open, contributes NO duration.
    s4 = tt.start(k, "writing", at=400)
    open_writes = tt.sessions(k, activity="writing", status="open")
    assert [o["id"] for o in open_writes] == [s4], open_writes
    assert open_writes[0]["status"] == "open" and open_writes[0]["duration"] is None
    line("  start('writing', at=400) left OPEN → reflected as open (no duration) ✓")

    # 5. report: total tracked time per activity (ints) — open session NOT summed.
    rep = tt.report(k, by="activity")
    assert rep == {"coding": 75, "writing": 60}, rep           # 45+30 ; 60 (open=0)
    assert all(isinstance(v, int) for v in rep.values()), rep
    line(f"  report(by='activity') → {rep} (open writing session adds 0) ✓")

    # 6. focus_block: schedule a pomodoro via SCHED1 — a due scheduled_event on the Weft.
    fb = tt.focus_block(k, "coding", minutes=25, at=500)
    ev = w().get(fb["event"])
    assert ev is not None and ev.type == "scheduled_event", ev
    assert ev.content["at"] == 500 and ev.content["fired"] is False
    assert fb["event"] in {e["dst"] for e in w().edges_from(fb["block"], "focus_schedule")}
    assert fb["event"] in {c.id for c in sched.due(k, now=500)}, "focus block must be due at its tick"
    blk = w().get(fb["block"])
    assert blk.content["minutes"] == 25 and blk.content["activity"] == "coding"
    line("  focus_block('coding', 25m, at=500) → scheduled_event due at 500 (focus_schedule edge) ✓")

    # 7. Determinism: recompute yields the same totals.
    assert tt.report(k, by="activity") == rep, "report must be deterministic"
    line("  determinism: report recompute matches ✓")

    # 8. Fail-loud: stop-before-start and double-stop are rejected.
    bad = tt.start(k, "bug", at=50)
    try:
        tt.stop(k, bad, at=40)
        raise AssertionError("stop before start must raise (negative duration)")
    except ValueError:
        pass
    tt.stop(k, bad, at=50)                                     # zero-duration is legal
    try:
        tt.stop(k, bad, at=60)
        raise AssertionError("double-stop must raise")
    except ValueError:
        pass
    line("  fail-loud: stop-before-start and double-stop both raise ✓")

    line("  → sessions are DATA on the Weft; durations are INT folds (end-start); report totals "
         "per activity (open sessions excluded); a focus block schedules a reminder. Deterministic.")
