"""PLAN1 — planning / decomposition into a task DAG.

A `plan` decomposes an *objective* into an ordered set of subtask Cells — `plan_step`s
— wired together by `depends_on` EDGEs into a directed acyclic graph. Planning
*structures* work; it never executes it. Nothing here invokes an effect, spawns a
worker, or delegates: it shapes the work so the kernel's delegation loop (`_delegate`,
read-only to us) *could* later turn a ready step into a brief.

Laws this module upholds:
  - **Decima's OWN assertions.** A plan, its steps, and its edges are authored by
    Decima (a trusted principal) and live on the Weft — they are provenance, not an
    untrusted intake. There is no recall-vs-instruct boundary to cross here: Decima
    is structuring its own work, not obeying a payload.
  - **Ints, not floats.** Any signed numeric content (step ordinals, counts) is an
    int. No floats reach the log.
  - **No ambient authority / no effects.** A step records a *suggested* capability
    name as a string hint; it grants nothing and invokes nothing. `ready_steps`
    returns the delegable frontier shaped as briefs; turning a brief into a real
    grant still runs through the kernel's authorize/Morta gates.
  - **Fail closed.** A plan whose `depends_on` edges contain a cycle is REJECTED at
    `plan()` time (raised), never half-committed as a runnable DAG.

Shape on the Weft:
  - a `plan` Cell (the objective + the ordered list of its step ids);
  - one `plan_step` Cell per subtask (objective + suggested capability + status);
  - a `has_step` EDGE  `plan → has_step → step`        (membership / provenance);
  - a `depends_on` EDGE `step → depends_on → prereq`   (the DAG's arcs).

`status` lives on the step Cell and is `pending` until marked `done` (LWW overwrite,
the model's default merge), which is how a later layer becomes ready.

Public `model`/`weave`/`hashing` API only — no core edit.
"""
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

PLAN = "plan"
PLAN_STEP = "plan_step"
HAS_STEP = "has_step"
DEPENDS_ON = "depends_on"

PENDING = "pending"
DONE = "done"

# Default capability hint when a step does not name one. It is a STRING shaped like
# a delegation brief's `capability`; it confers no authority on its own.
DEFAULT_CAPABILITY = "shell"


def step_id(plan_id: str, key: str) -> str:
    """Content-address a step within its plan, by the caller's stable `key`. Keying
    by (plan, key) keeps a step's identity stable so `depends_on` can name it."""
    return content_id({"plan_step": nfc(key), "of": plan_id})


def plan_id_for(objective: str, lamport: int) -> str:
    """A plan's id. Lamport keeps re-planning the same objective distinct on the
    log (a new plan, not a silent overwrite of the old one)."""
    return content_id({"plan": nfc(objective), "lamport": int(lamport)})


def _normalize_steps(steps) -> list[dict]:
    """Coerce the caller's step specs into a uniform shape.

    Each spec is a dict: {key, objective, depends_on?, capability?}. `key` is the
    stable handle other steps reference in their `depends_on`. Ordinals are assigned
    as ints in declaration order (a stable, deterministic default ordering hint)."""
    out = []
    seen = set()
    for ordinal, spec in enumerate(steps):
        if not isinstance(spec, dict):
            raise ValueError(f"plan step must be a dict, got {type(spec).__name__}")
        key = spec.get("key")
        objective = spec.get("objective")
        if not key or not str(key).strip():
            raise ValueError("plan step requires a non-empty 'key'")
        if not objective or not str(objective).strip():
            raise ValueError(f"plan step {key!r} requires a non-empty 'objective'")
        key = nfc(str(key))
        if key in seen:
            raise ValueError(f"duplicate plan step key: {key!r}")
        seen.add(key)
        deps = [nfc(str(d)) for d in (spec.get("depends_on") or [])]
        out.append({
            "key": key,
            "objective": nfc(str(objective)),
            "depends_on": deps,
            "capability": nfc(str(spec.get("capability") or DEFAULT_CAPABILITY)),
            "ordinal": int(ordinal),
        })
    return out


def _validate_acyclic(specs: list[dict]) -> list[str]:
    """Validate the DAG and return a topological order of step KEYS. Raises
    ValueError if any `depends_on` names an unknown key or if a cycle exists —
    fail closed, so a cyclic plan is never committed as a runnable DAG.

    Kahn's algorithm: if not every node is emitted, the remainder forms a cycle."""
    keys = {s["key"] for s in specs}
    for s in specs:
        for d in s["depends_on"]:
            if d not in keys:
                raise ValueError(
                    f"plan step {s['key']!r} depends on unknown step {d!r}")
        if s["key"] in s["depends_on"]:
            raise ValueError(f"plan step {s['key']!r} depends on itself (cycle)")

    # indegree = number of unmet prerequisites for each step.
    indeg = {s["key"]: len(set(s["depends_on"])) for s in specs}
    dependents: dict[str, list[str]] = {k: [] for k in keys}
    for s in specs:
        for d in set(s["depends_on"]):
            dependents[d].append(s["key"])

    # Stable frontier: declaration order (by ordinal) among the currently-ready.
    order_of = {s["key"]: s["ordinal"] for s in specs}
    ready = sorted([k for k, n in indeg.items() if n == 0], key=lambda k: order_of[k])
    topo: list[str] = []
    while ready:
        k = ready.pop(0)
        topo.append(k)
        newly = []
        for child in dependents[k]:
            indeg[child] -= 1
            if indeg[child] == 0:
                newly.append(child)
        # re-sort the frontier so the order stays deterministic by declaration.
        ready = sorted(ready + newly, key=lambda k: order_of[k])

    if len(topo) != len(specs):
        stuck = sorted(set(keys) - set(topo))
        raise ValueError(
            f"plan is cyclic — these steps are in a dependency cycle: {stuck}")
    return topo


def plan(k, objective: str, steps, *, author: str | None = None,
         scope: str | None = None) -> dict:
    """Decompose `objective` into a DAG of `plan_step` Cells on the Weft.

    `steps` is a list of dicts: {key, objective, depends_on?, capability?}. The DAG
    is validated ACYCLIC before anything beyond the spec coercion is committed — a
    cyclic plan raises ValueError and asserts no step/edge cells (fail closed).

    Returns {plan, steps: {key: step_id}, topo: [step_id...], objective}. The plan
    Cell, every step Cell, and the membership + dependency EDGEs are Decima's own
    trusted assertions, so they carry full provenance on the Weft. No effect runs.
    """
    objective = nfc(str(objective))
    author = author or k.decima_agent_id
    specs = _normalize_steps(steps)
    # Validate FIRST — fail closed before writing any plan/step/edge to the log.
    topo_keys = _validate_acyclic(specs)

    pid = plan_id_for(objective, k.weft.lamport)
    id_of = {s["key"]: step_id(pid, s["key"]) for s in specs}

    plan_content = {
        "objective": objective,
        "status": PENDING,
        "step_count": int(len(specs)),
        "steps": [id_of[s["key"]] for s in specs],   # declaration-order step ids
    }
    if scope is not None:
        plan_content["scope"] = nfc(str(scope))
    assert_content(k.weft, author, pid, PLAN, plan_content)

    # One Cell per step, then the membership edge plan → has_step → step.
    for s in specs:
        sid = id_of[s["key"]]
        assert_content(k.weft, author, sid, PLAN_STEP, {
            "plan": pid,
            "key": s["key"],
            "objective": s["objective"],
            "capability": s["capability"],   # a brief HINT — confers no authority
            "ordinal": s["ordinal"],
            "status": PENDING,
        })
        assert_edge(k.weft, author, pid, HAS_STEP, sid)

    # The DAG arcs: step → depends_on → prerequisite. Asserted after every step
    # cell exists so both endpoints are real cells.
    for s in specs:
        sid = id_of[s["key"]]
        for d in s["depends_on"]:
            assert_edge(k.weft, author, sid, DEPENDS_ON, id_of[d])

    return {
        "plan": pid,
        "steps": id_of,
        "topo": [id_of[key] for key in topo_keys],
        "objective": objective,
    }


def _plan_steps(weave, plan_id: str) -> list:
    """The step Cells belonging to `plan_id`, in declaration (ordinal) order."""
    steps = [c for c in weave.of_type(PLAN_STEP)
             if c.content.get("plan") == plan_id and not c.retracted]
    return sorted(steps, key=lambda c: int(c.content.get("ordinal", 0)))


def is_done(cell) -> bool:
    return cell is not None and cell.content.get("status") == DONE


def ready_steps(k, plan_id: str) -> list[dict]:
    """The frontier: steps that are still pending AND whose every `depends_on`
    prerequisite is satisfied (done) — the steps that can be delegated NOW.

    Each is returned shaped as a delegation brief: {step, key, objective,
    capability} — exactly the fields the kernel's `_delegate` reads off a spec —
    BUT this only structures; it neither spawns a worker nor grants the capability.
    Returned in declaration order so the frontier is deterministic.
    """
    w = k.weave()
    out = []
    for cell in _plan_steps(w, plan_id):
        if cell.content.get("status") == DONE:
            continue
        prereqs = [e["dst"] for e in w.edges_from(cell.id, DEPENDS_ON)]
        if all(is_done(w.get(p)) for p in prereqs):
            out.append({
                "step": cell.id,
                "key": cell.content.get("key"),
                "objective": cell.content.get("objective"),
                "capability": cell.content.get("capability", DEFAULT_CAPABILITY),
            })
    return out


def mark_done(k, step_id: str, *, author: str | None = None,
              result: str | None = None) -> str:
    """Mark a step done — Decima's own assertion (an LWW overwrite of the step's
    status, the model's default merge). This is the only state transition planning
    owns; it records completion, it does NOT execute the step's effect. Returns the
    step id."""
    author = author or k.decima_agent_id
    cell = k.weave().get(step_id)
    if cell is None or cell.type != PLAN_STEP:
        raise ValueError(f"not a plan step: {step_id}")
    content = {**cell.content, "status": DONE}
    if result is not None:
        content["result"] = nfc(str(result))
    assert_content(k.weft, author, step_id, PLAN_STEP, content)
    return step_id


def topological_order(k, plan_id: str) -> list[str]:
    """A topological ordering of the plan's step ids that respects every
    `depends_on` edge (a prerequisite always precedes its dependents). Folded from
    the Weave, so it is deterministic and time-travelable like all state. Raises if
    the committed graph is somehow cyclic (it cannot be, since `plan` rejects cycles
    — this is a defense-in-depth check)."""
    w = k.weave()
    steps = _plan_steps(w, plan_id)
    ids = {c.id for c in steps}
    order_of = {c.id: int(c.content.get("ordinal", 0)) for c in steps}

    indeg = {}
    dependents: dict[str, list[str]] = {c.id: [] for c in steps}
    for c in steps:
        prereqs = [e["dst"] for e in w.edges_from(c.id, DEPENDS_ON) if e["dst"] in ids]
        indeg[c.id] = len(set(prereqs))
        for p in set(prereqs):
            dependents[p].append(c.id)

    ready = sorted([i for i, n in indeg.items() if n == 0], key=lambda i: order_of[i])
    topo: list[str] = []
    while ready:
        i = ready.pop(0)
        topo.append(i)
        newly = []
        for child in dependents[i]:
            indeg[child] -= 1
            if indeg[child] == 0:
                newly.append(child)
        ready = sorted(ready + newly, key=lambda i: order_of[i])

    if len(topo) != len(steps):
        raise ValueError(f"committed plan {plan_id} is cyclic")
    return topo


def plan_status(k, plan_id: str) -> dict:
    """Fold the plan's progress: counts of done vs pending, and whether it is
    complete. A measurable signal, derived from the log."""
    w = k.weave()
    steps = _plan_steps(w, plan_id)
    done = sum(1 for c in steps if c.content.get("status") == DONE)
    total = len(steps)
    return {
        "plan": plan_id,
        "total": int(total),
        "done": int(done),
        "pending": int(total - done),
        "complete": total > 0 and done == total,
    }
