"""DASH1 — the unified "today" dashboard (the home-screen projection over the Weave).

This check proves the dashboard is a READ-ONLY composition of four public projections:
  - it seeds a little real state through each module's PUBLIC api (schedule a due
    reminder, open a project task, raise a notification);
  - today(k, now=...) returns the four sections, each drawn from the right module with
    the right content (the due reminder, the open ptask, the notification, the activity);
  - it is DETERMINISTIC: recomputing today() on the unchanged Weave yields an EQUAL view;
  - it is READ-ONLY: computing today()/render() adds NO events to the Weft and mutates
    nothing (the Weft length and the fold are unchanged across the calls);
  - render() turns the view into concise human lines.

Contract: run(k, line). Fail loud.
"""
from decima import dashboard, scheduling, projects, notify
from decima.model import assert_content
from decima.hashing import content_id


def run(k, line):
    line("\n== TODAY DASHBOARD (activity + notifications + due + open tasks) — DASH1 ==")
    w = lambda: k.weave()

    NOW = 50

    # -- seed a little real state through each module's PUBLIC api -------------
    # A reminder due by NOW, and one in the future (must NOT show as due now).
    due_evt = scheduling.schedule(k, "Renew the TLS cert", at=40)
    future_evt = scheduling.schedule(k, "Quarterly review", at=200)

    # A project with one open (todo) task and one done task (done must NOT show).
    proj = projects.create_project(k, "dashboard demo")
    open_task = projects.add_task(k, proj, "wire up the home screen")
    done_task = projects.add_task(k, proj, "draft the spec")
    projects.move(k, done_task, "done")

    # A notification raised from a source cell (a finding → an in-box notification).
    src = content_id({"dash_finding": "d1"})
    assert_content(k.weft, k.decima_agent_id, src, "finding", {
        "detection": "demo-det", "severity": "high", "excerpt": "event d1",
        "source": "host-1", "rule": "demo-rule"})
    note = notify.notify(k, src, "high finding on host-1", priority="high")
    line("  seeded: 1 due reminder (+1 future), 1 open ptask (+1 done), 1 notification ✓")

    # -- 1. today() composes the four sections with the right content ----------
    view = dashboard.today(k, now=NOW)
    assert set(view) >= {"activity", "notifications", "due", "open_tasks"}, view.keys()

    # DUE: the at<=NOW reminder is present; the future one is excluded.
    due_ids = {it["event"] for it in view["due"]["items"]}
    assert due_evt in due_ids, "the due reminder is missing from the dashboard"
    assert future_evt not in due_ids, "a future reminder leaked into 'due now'"
    line(f"  due: {view['due']['count']} (the at=40 reminder; future at=200 excluded) ✓")

    # OPEN TASKS: the todo task shows; the done task does not.
    task_ids = {it["ptask"] for it in view["open_tasks"]["items"]}
    assert open_task in task_ids, "the open ptask is missing from the dashboard"
    assert done_task not in task_ids, "a done ptask leaked into 'open tasks'"
    states = {it["state"] for it in view["open_tasks"]["items"]}
    assert states <= {"todo", "doing"}, f"open tasks must be todo/doing only: {states}"
    line(f"  open tasks: {view['open_tasks']['count']} (todo shows; done excluded) ✓")

    # NOTIFICATIONS: the raised notification shows, unread, ordered urgent→low.
    note_ids = {it["notification"] for it in view["notifications"]["items"]}
    assert note in note_ids, "the notification is missing from the dashboard"
    ranks = [it["priority_rank"] for it in view["notifications"]["items"]]
    assert ranks == sorted(ranks, reverse=True), f"notifications not urgent→low: {ranks}"
    assert view["notifications"]["pending"] >= 1, "expected at least one unread notification"
    line(f"  notifications: {view['notifications']['pending']} unread, ordered urgent→low ✓")

    # ACTIVITY: the human feed is present and verifiable (untampered).
    assert view["activity"]["verifiable"] is True, view["activity"]["error"]
    assert view["activity"]["count"] >= 1, "expected some recent activity"
    line(f"  activity: {view['activity']['count']} recent entries (verifiable) ✓")

    # -- 2. DETERMINISTIC: recompute on the unchanged Weave → an EQUAL view -----
    view2 = dashboard.today(k, now=NOW)
    assert view2 == view, "today() is not deterministic on an unchanged Weave"
    line("  recompute on unchanged Weave → identical view (deterministic) ✓")

    # -- 3. READ-ONLY: today()/render() add NO events and mutate nothing -------
    before = k.weft.lamport
    fold_before = w()
    _ = dashboard.today(k, now=NOW)
    lines = dashboard.render(view)
    after = k.weft.lamport
    assert after == before, f"dashboard appended to the Weft (lamport {before}→{after})"
    # The reminder/task/notification are untouched by the read.
    assert w().get(due_evt).content["fired"] is False, "dashboard fired a reminder (mutated)"
    assert w().get(open_task).content["state"] == "todo", "dashboard moved a ptask (mutated)"
    assert w().get(note).content["status"] == "unread", "dashboard marked a notification read"
    assert len(fold_before.cells) == len(w().cells), "dashboard changed the cell set"
    line(f"  today()/render() added 0 events (lamport={after}), mutated nothing (read-only) ✓")

    # -- 4. render(): concise human lines --------------------------------------
    assert lines and lines[0].startswith("— today"), lines[:1]
    assert any("due now" in s for s in lines) and any("open tasks" in s for s in lines)
    for s in lines:
        line(f"    {s}")

    line("  → DASH1: the home screen is a read-only lens — it composes activity, "
         "notifications, due reminders, and open tasks from each module's public API, "
         "deterministically, and never mutates or acts.")
