"""DASH1 — the unified "today" dashboard: the home-screen projection over the Weave.

This is the workspace's home screen (specs/CAPABILITY_MAP.md §D4 — the workspace as
projections over the Weave). It answers one human question — "what should I look at
right now?" — by COMPOSING four existing public projections into a single structured
view:

  - recent ACTIVITY      ← timeline.timeline  (the human "what happened lately" feed);
  - pending NOTIFICATIONS ← notify.notifications + notify.order  (the in-box, urgent→low);
  - DUE reminders         ← scheduling.due(now)  (clock-parameterized, caller owns `now`);
  - OPEN project tasks    ← projects.board  (the todo + doing columns of every board).

It is a PURE, READ-ONLY consumer of each module's PUBLIC read API. It NEVER mutates a
cell, appends to the Weft, invokes an effect, or acts — it only READS and arranges what
the modules already expose. There is no new authority here and no new state: a dashboard
is a lens, not an actor (Law: read-only composition; honor the trust boundary).

Determinism: every number is an int, `now` is supplied by the caller (no wall-clock),
and the view is a pure function of the current fold — recomputing `today(k, now=...)`
on an unchanged Weave yields an equal view. Tamper-evidence rides along: the activity
section carries timeline's `verifiable`/`error` so a tampered log surfaces, never hides.

Public `timeline`/`notify`/`scheduling`/`projects`/`weave` API only — no core edit.
"""
from __future__ import annotations

from decima import timeline, notify, scheduling, projects


# How many recent activity entries the home screen surfaces by default. An int — the
# dashboard is a glance, not the full feed (the full feed is timeline.timeline itself).
_RECENT = 5

# The kanban columns that count as "open" / actionable work on the home screen.
_OPEN_STATES = (projects.TODO, projects.DOING)


def _activity(k, *, last: int) -> dict:
    """Recent ACTIVITY — the human feed, via timeline's PUBLIC projection (read-only).
    Carries timeline's tamper-evidence (`verifiable`/`error`) straight through so the
    home screen never presents a tampered log as a clean feed."""
    tl = timeline.timeline(k, last=last)
    return {
        "entries": tl["entries"],            # newest LAST, as timeline yields
        "count": tl["count"],
        "verifiable": tl["verifiable"],
        "error": tl["error"],
    }


def _notifications(k) -> dict:
    """Pending NOTIFICATIONS — the in-box, ordered urgent→low via notify's PUBLIC
    `notifications` + `order` (read-only). `pending` counts the UNREAD ones (the ones
    actually wanting attention); the entries carry title + priority for the glance."""
    w = k.weave()
    ordered = notify.order(k)                # urgent→low, deterministic (id tiebreak)
    items = []
    pending = 0
    for nid in ordered:
        c = w.get(nid)
        if c is None:
            continue
        status = c.content.get("status", "unread")
        if status == "unread":
            pending += 1
        items.append({
            "notification": nid,
            "title": c.content.get("title"),
            "priority": c.content.get("priority"),
            "priority_rank": int(c.content.get("priority_rank", 0)),
            "status": status,
        })
    return {"items": items, "count": len(items), "pending": pending}


def _due(k, *, now: int) -> dict:
    """DUE reminders — scheduling's clock-parameterized projection at `now` (read-only).
    `now` is the caller's logical tick; nothing here reads a wall-clock."""
    cells = scheduling.due(k, now)           # at <= now, not yet fired, (at, id) order
    items = [{
        "event": c.id,
        "title": c.content.get("title"),
        "at": int(c.content["at"]),
    } for c in cells]
    return {"items": items, "count": len(items), "now": int(now)}


def _open_tasks(k) -> dict:
    """OPEN project tasks — the todo + doing columns of EVERY board, via projects'
    PUBLIC `board` projection (read-only). Each item names its project + column so the
    home screen shows which board a task belongs to."""
    w = k.weave()
    items = []
    by_project: dict[str, int] = {}
    for proj in w.of_type(projects.PROJECT):
        name = proj.content.get("name", proj.id)
        b = projects.board(k, proj.id)
        for state in _OPEN_STATES:
            for t in b.get(state, []):
                items.append({
                    "ptask": t["ptask"],
                    "title": t["title"],
                    "state": t["state"],
                    "project": proj.id,
                    "project_name": name,
                })
                by_project[name] = by_project.get(name, 0) + 1
    # Deterministic order: by project name, then column (todo before doing), then id.
    _state_rank = {projects.TODO: 0, projects.DOING: 1, projects.DONE: 2}
    items.sort(key=lambda it: (it["project_name"], _state_rank.get(it["state"], 9),
                               it["ptask"]))
    return {"items": items, "count": len(items), "by_project": by_project}


def today(k, *, now: int, recent: int = _RECENT) -> dict:
    """The single "today" view: a structured home screen composing four public
    projections over the current Weave.

    Sections (each read-only from its module's public API):
      - `activity`      — the `recent` newest timeline entries (+ tamper-evidence);
      - `notifications` — the in-box, urgent→low, with an unread `pending` count;
      - `due`           — reminders due at `now` (the caller owns the clock);
      - `open_tasks`    — the todo + doing ptasks of every project board.

    `now` MUST be an int logical tick (no wall-clock; scheduling enforces this). The
    view is a pure function of the fold: recomputing on an unchanged Weave is equal.
    It reads, it never mutates or acts."""
    if not isinstance(now, int) or isinstance(now, bool):
        raise TypeError(f"now must be an int logical tick, got {type(now).__name__}")
    return {
        "now": int(now),
        "activity": _activity(k, last=recent),
        "notifications": _notifications(k),
        "due": _due(k, now=now),
        "open_tasks": _open_tasks(k),
    }


def render(view: dict) -> list[str]:
    """Render a `today` view to concise human lines — the home screen as text.
    Read-only: it formats the view, touching nothing."""
    now = view["now"]
    act = view["activity"]
    notes = view["notifications"]
    due = view["due"]
    tasks = view["open_tasks"]

    out = [f"— today (now={now}) —"]

    out.append(f"  due now: {due['count']}")
    for it in due["items"]:
        out.append(f"    ⏰ at {it['at']:<4} {it['title']}")

    out.append(f"  notifications: {notes['pending']} unread / {notes['count']} total")
    for it in notes["items"]:
        flag = "•" if it["status"] == "unread" else " "
        out.append(f"    {flag} [{str(it['priority']):<6}] {it['title']}")

    out.append(f"  open tasks: {tasks['count']}")
    for it in tasks["items"]:
        out.append(f"    ▸ {it['state']:<5} {it['title']}  ({it['project_name']})")

    out.append(f"  recent activity: {act['count']} (verifiable={act['verifiable']})")
    for e in act["entries"]:
        out.append(f"    e{e['seq']:<3} {e['author_name']:<10} {e['description']}")

    return out
