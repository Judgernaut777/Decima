"""FITNESS1 — Workouts & training: PRIVATE activity as DATA, composed from health/goals/sched.

Fitness is the same recall-vs-instruct law as HEALTH1, one notch up the stack: a person's
workouts are sensitive DATA that must (a) never be obeyed as an instruction and (b) never leak
into a general recall. We don't re-implement that boundary — we MIRROR HEALTH1's triple-layer
privacy on every workout point and COMPOSE the existing capabilities:

  - log_workout → a PRIVATE `workout` Cell, the HEALTH1 pattern exactly: its own Cell type
    (NOT in memory's recall taxonomy), a per-kind private `scope` (`fitness:private:<kind>`),
    and `instruction_eligible=False` + `recallable=False` + `citable=False` stamped on it. So a
    general recall can't consider it, a scope-blind read can't name its scope, and even a
    retriever that looked would skip it — and the brain may never act on a workout as a command.
    Provenance grounds each point with a `supported_by` edge (WEFT Law 4). Duration and every
    metric are INTS in minor units (seconds, reps, metres) — a float is rejected (WEFT §4/§7).

  - plan → a recurring training session via SCHED1: `scheduling.schedule(..., repeat_every)`.
    A `fitness_plan` Cell carries the cadence and links to its `scheduled_event` by a
    `plan_schedule` edge. The schedule is the only authority; the plan Cell is just intent.

  - progress → a deterministic INT fold over a kind's logged workouts: count / total-duration /
    latest / delta (latest − first). A read-only projection, never a new authority.

  - link_goal → a fitness goal via GOALS1: a goal is a wager on yourself. We `goals.set_goal`
    (which binds a WV1 wager) and tie it to fitness with a `fitness_goal` edge — settling the
    wager is how a training target "completes".

OWNS only heartbeat/decima/fitness.py. Composes PUBLIC APIs (model/health-pattern/goals/
scheduling) — no kernel code, no ambient authority: every assert is authored.
"""
from __future__ import annotations

from decima import model
from decima import scheduling as sched
from decima import goals
from decima.hashing import content_id, nfc

WORKOUT = "workout"
PLAN = "fitness_plan"
# Private scope keyed to the workout kind — never a general realm scope, so a
# scope-blind general recall cannot name it by accident (mirrors health.SCOPE_PREFIX).
SCOPE_PREFIX = "fitness:private"


def fitness_scope(kind: str) -> str:
    """The private scope a kind's workouts live in — never a general realm scope."""
    return f"{SCOPE_PREFIX}:{nfc(kind)}"


def _int(name: str, value) -> int:
    """Reject floats/bools before they reach signed content (WEFT §4/§7)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int (minor units / ticks), got {type(value).__name__}")
    return int(value)


def _int_metrics(metrics) -> dict | None:
    """Coerce/validate a metrics dict to ints (reps, distance in metres, ...); None passes through."""
    if metrics is None:
        return None
    if not isinstance(metrics, dict):
        raise TypeError("metrics must be a dict of int values (minor units)")
    return {nfc(name): _int(f"metric {name}", value) for name, value in metrics.items()}


def _workout_id(kind: str, duration: int, seq: int) -> str:
    return content_id({"workout": nfc(kind), "duration": int(duration), "seq": int(seq)})


def log_workout(k, kind: str, *, duration: int, metrics: dict | None = None,
                author: str | None = None, evidence_src: str | None = None) -> str:
    """Log one PRIVATE workout and return its Cell id.

    `duration` is an INT in minor units (seconds); `metrics` (optional) is a dict of INTs
    (reps, distance in metres, ...). The point is stamped `instruction_eligible=False` and
    `recallable=False` in a private `scope` — DATA general recall cannot surface and the brain
    can never obey (HEALTH1's triple-layer privacy). A `supported_by` edge grounds it (Law 4).
    """
    duration = _int("duration", duration)
    metrics = _int_metrics(metrics)
    kind = nfc(kind)
    author = author or k.decima_agent_id
    scope = fitness_scope(kind)
    seq = k.weft.count() + 1                    # deterministic, log-positioned id
    cid = _workout_id(kind, duration, seq)
    content = {
        "kind": kind,
        "duration": duration,
        "metrics": metrics,
        "scope": scope,
        "seq": seq,
        # the four permissions (Codex §5): DATA only — never an instruction,
        # never surfaced by general recall, never cited.
        "instruction_eligible": False,
        "recallable": False,
        "citable": False,
    }
    model.assert_content(k.weft, author, cid, WORKOUT, content)
    # provenance on the Weft: ground the point in evidence (Law 4).
    model.assert_edge(k.weft, author, cid, "supported_by", evidence_src or author)
    return cid


def _workouts(k, kind: str) -> list:
    """All workout Cells for `kind`, scope-filtered, in log (seq) order."""
    scope = fitness_scope(kind)
    kind = nfc(kind)
    out = [c for c in k.weave().of_type(WORKOUT)
           if c.content.get("kind") == kind
           and c.content.get("scope") == scope]      # authorization-first filter
    return sorted(out, key=lambda c: int(c.content.get("seq", 0)))


def history(k, kind: str) -> list:
    """The logged workouts for `kind` as DATA dicts (scope-filtered, in order)."""
    return [{
        "id": c.id,
        "kind": c.content["kind"],
        "duration": int(c.content["duration"]),
        "metrics": c.content.get("metrics"),
        "scope": c.content["scope"],
    } for c in _workouts(k, kind)]


def plan(k, *, kind: str, every: int, at: int, author: str | None = None) -> dict:
    """Plan a recurring training session: a `fitness_plan` Cell (cadence `every`, first due `at`)
    plus a repeating `scheduled_event` via `scheduling.schedule` — linked by a `plan_schedule`
    edge. `every`/`at` are ints (enforced by scheduling too). Returns {plan, event} (Cell ids)."""
    every = _int("every", every)
    at = _int("at", at)
    if every <= 0:
        raise ValueError("every must be a positive number of ticks")
    author = author or k.decima_agent_id
    kind = nfc(kind)

    # A plan is a recurring session: compose scheduling's repeat_every.
    eid = sched.schedule(k, f"workout: {kind}", at=at, repeat_every=every, author=author)

    pid = content_id({"fitness_plan": kind, "every": every, "at": k.weft.head})
    model.assert_content(k.weft, author, pid, PLAN, {
        "kind": kind, "every": every, "at": at, "event": eid,
    })
    model.assert_edge(k.weft, author, pid, "plan_schedule", eid)
    return {"plan": pid, "event": eid}


def progress(k, kind: str) -> dict | None:
    """A deterministic INT summary of a kind's training: count / total / latest / delta over the
    logged durations. `delta` is latest − first (net movement); `total` is summed duration.
    Returns None when nothing is on record — a read-only fold, all ints in minor units."""
    pts = _workouts(k, kind)
    if not pts:
        return None
    durations = [int(c.content["duration"]) for c in pts]
    return {
        "kind": nfc(kind),
        "count": len(durations),
        "total": sum(durations),
        "min": min(durations),
        "max": max(durations),
        "latest": durations[-1],
        "first": durations[0],
        "delta": durations[-1] - durations[0],
    }


def link_goal(k, target: int, *, kind: str | None = None, confidence: int = 800_000,
              name: str | None = None, author: str | None = None) -> dict:
    """Tie a fitness goal via GOALS1 — a goal is a wager on yourself. Composes `goals.set_goal`
    (which binds a fresh WV1 wager predicting `target`) and links it to fitness with a
    `fitness_goal` edge. `target`/`confidence` are ints (minor units / millionths). Returns
    {goal, wager} (Cell ids); settling the wager is how the training target completes."""
    target = _int("target", target)
    confidence = _int("confidence", confidence)
    author = author or k.decima_agent_id
    label = nfc(name) if name is not None else (f"fitness: {nfc(kind)}" if kind else "fitness goal")

    gid = goals.set_goal(k, label, target=target, confidence=confidence, author=author)
    wid = k.weave().get(gid).content["wager"]
    # mark this goal as a fitness goal on the Weft (provenance).
    model.assert_edge(k.weft, author, gid, "fitness_goal", wid)
    return {"goal": gid, "wager": wid}
