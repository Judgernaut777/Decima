"""IR1 — incident-response playbooks (CAPABILITY_MAP Part C, the "incident-commander").

This module is the blue-team layer ABOVE triage: TRIAGE1 correlates DET1 findings
into `incident` Cells and *proposes* a response; IR1 turns that proposal into an
actual, ordered RESPONSE — without ever crossing into execution. It COMPOSES three
existing capabilities through their PUBLIC apis, owning none of their logic:

  - **triage** (read) — to read the incident's kind/severity off the Weft;
  - **planning** (PLAN1) — to shape the response as an acyclic DAG of `plan_step`s
    (isolate → investigate → remediate → verify); a higher-severity incident gets a
    longer playbook (extra containment / forensics / hardening steps);
  - **projects** (PROJ1) — to open those steps as a kanban of `ptask`s and drive a
    visible board.

  `respond(k, incident)` builds the plan, opens the project, and links both back to
  the incident with `responds_to` provenance EDGEs on the Weft.
  `status(k, incident)` folds the response project's board (open vs done steps).

Laws this module upholds:
  - **No ambient authority / no effects.** A remediation step is a PROPOSAL. IR1
    structures the response (plan + board); it grants nothing and invokes nothing.
    Actually *executing* a step (isolate a host, kill a process) still goes through
    the kernel's authorize / Morta gates — IR1 never does.
  - **Ints, not floats.** Step ordinals and counts are ints (delegated to PLAN1/PROJ1).
  - **Provenance on the Weft.** The plan, the project, and the `responds_to` edges
    linking them to the incident are all Decima's own signed Cells/EDGEs.
  - **Compose, don't fork.** Every plan/step/board operation is a PUBLIC call into
    planning/projects; triage is read-only here. No core or sibling-module edit.

Shape on the Weft (atop what planning/projects already write):
  - a `responds_to` EDGE  `plan    → responds_to → incident`
  - a `responds_to` EDGE  `project → responds_to → incident`
so the response is reachable from the incident (and vice-versa) for audit.
"""
from __future__ import annotations

from decima.model import assert_edge
from decima import planning, projects, triage

RESPONDS_TO = "responds_to"

# A response is always these four phases. A higher-severity incident inserts EXTRA
# steps (deeper containment / forensics / hardening) WITHIN the same skeleton, so a
# critical incident yields a strictly longer, ordered playbook than a low one.
PHASES = ("isolate", "investigate", "remediate", "verify")


def _incident(k, incident):
    """Read the TRIAGE1 incident Cell (by id or by passing the Cell itself)."""
    w = k.weave()
    cell = incident if hasattr(incident, "content") else w.get(incident)
    if cell is None or cell.type != triage.INCIDENT:
        raise ValueError(f"not an incident: {incident!r}")
    return cell


def playbook_steps(incident_content: dict) -> list[dict]:
    """Map an incident's kind/severity to an ORDERED list of response-step specs
    (PLAN1 step dicts: {key, objective, depends_on?, capability?}). The four phases
    always run isolate → investigate → remediate → verify; severity DEEPENS the
    playbook by inserting extra steps, so a higher-severity incident gets strictly
    more steps than a lower one (and a different shape).

    Pure function of the incident content — no Weft access — so it is trivially
    testable and deterministic.
    """
    score = int(incident_content.get("score", 2))
    sources = incident_content.get("sources") or []
    key = incident_content.get("key", "incident")
    n_src = max(1, len(sources))

    steps: list[dict] = []

    # 1) ISOLATE — contain the blast radius. Critical incidents add a hard
    #    network-quarantine of every implicated source before anything else.
    if score >= 4:
        steps.append({"key": "quarantine",
                      "objective": f"network-quarantine {n_src} implicated source(s)",
                      "capability": "isolate"})
    steps.append({"key": "isolate",
                  "objective": f"isolate affected source(s) for rule {key!r}",
                  "depends_on": ["quarantine"] if score >= 4 else [],
                  "capability": "isolate"})

    # 2) INVESTIGATE — understand it. High+ incidents add forensic collection.
    steps.append({"key": "investigate",
                  "objective": "investigate scope and root cause from the findings",
                  "depends_on": ["isolate"], "capability": "analyze"})
    if score >= 3:
        steps.append({"key": "forensics",
                      "objective": "collect forensic evidence (memory + disk image)",
                      "depends_on": ["investigate"], "capability": "analyze"})

    remediate_dep = "forensics" if score >= 3 else "investigate"

    # 3) REMEDIATE — fix it (a PROPOSAL; executing it is Morta-gated, not here).
    steps.append({"key": "remediate",
                  "objective": "remediate: remove the threat and restore service",
                  "depends_on": [remediate_dep], "capability": "remediate"})
    if score >= 4:
        steps.append({"key": "harden",
                      "objective": "harden: patch + add a detection so it cannot recur",
                      "depends_on": ["remediate"], "capability": "remediate"})

    verify_dep = "harden" if score >= 4 else "remediate"

    # 4) VERIFY — confirm closure.
    steps.append({"key": "verify",
                  "objective": "verify remediation held and the incident is closed",
                  "depends_on": [verify_dep], "capability": "verify"})
    return steps


def respond(k, incident, *, author: str | None = None) -> dict:
    """Map a TRIAGE1 incident to a remediation PLAYBOOK.

    Builds a PLAN1 plan of ordered response steps (isolate → investigate →
    remediate → verify; deeper for higher severity), then opens those steps as a
    PROJ1 project of `ptask`s, and links BOTH the plan and the project back to the
    incident with `responds_to` provenance EDGEs on the Weft.

    Returns {incident, plan, project, steps: [step_id...], severity, ptasks: [...]}.
    No effect runs: the steps are proposals; executing any still goes through the
    kernel's authorize / Morta gates.
    """
    author = author or k.decima_agent_id
    inc = _incident(k, incident)
    content = inc.content
    severity = content.get("severity", "low")

    specs = playbook_steps(content)

    # PLAN1: shape the response as an acyclic DAG of plan_steps. `plan` validates
    # acyclicity and fails closed; the scope ties the plan to the incident.
    objective = f"respond to {severity} incident {inc.id[:8]} ({content.get('key')})"
    pl = planning.plan(k, objective, specs, author=author, scope=inc.id)
    assert_edge(k.weft, author, pl["plan"], RESPONDS_TO, inc.id)

    # PROJ1: open the playbook as a kanban board. Seed only the READY frontier as
    # todo ptasks (the rest become ready as their prerequisites complete), then add
    # the not-yet-ready steps too so the whole playbook is visible on the board.
    project = projects.create_project(k, objective, author=author)
    assert_edge(k.weft, author, project, RESPONDS_TO, inc.id)

    ptasks = []
    for sid in pl["topo"]:
        scell = k.weave().get(sid)
        tid = projects.add_task(k, project, scell.content["objective"],
                                key=sid, author=author)
        ptasks.append(tid)

    return {
        "incident": inc.id,
        "plan": pl["plan"],
        "project": project,
        "steps": pl["topo"],
        "severity": severity,
        "ptasks": ptasks,
    }


def plan_of(k, incident) -> str | None:
    """The response plan id linked to `incident` via `responds_to` (or None)."""
    inc = _incident(k, incident)
    w = k.weave()
    for e in w.edges_to(inc.id, RESPONDS_TO):
        src = w.get(e["src"])
        if src is not None and src.type == planning.PLAN:
            return src.id
    return None


def project_of(k, incident) -> str | None:
    """The response project id linked to `incident` via `responds_to` (or None)."""
    inc = _incident(k, incident)
    w = k.weave()
    for e in w.edges_to(inc.id, RESPONDS_TO):
        src = w.get(e["src"])
        if src is not None and src.type == projects.PROJECT:
            return src.id
    return None


def advance(k, incident, step_id: str, *, author: str | None = None) -> dict:
    """Mark one response step done and move its board ptask to `done` — composing
    PLAN1's `mark_done` and PROJ1's `move`. Keeps the plan DAG and the kanban board
    in lock-step. Returns the post-advance status. (Recording completion only — it
    does NOT execute the step's effect; that is a Morta-gated INVOKE elsewhere.)"""
    author = author or k.decima_agent_id
    project = project_of(k, incident)
    if project is None:
        raise ValueError("no response project for this incident; call respond() first")
    planning.mark_done(k, step_id, author=author)
    # The ptask is keyed by the step id (see respond), so it is found directly.
    tid = projects.ptask_id(project, step_id)
    if k.weave().get(tid) is not None:
        projects.move(k, tid, projects.DONE, author=author)
    return status(k, incident)


def status(k, incident) -> dict:
    """The response project's board: open vs done steps.

    Returns {incident, project, plan, board: {todo, doing, done}, total, done,
    open, complete, ready: [brief...]} — folded from the Weft, deterministic and
    time-travelable. `ready` is PLAN1's delegable frontier (what could be worked
    NEXT); `board` is PROJ1's kanban grouping.
    """
    inc = _incident(k, incident)
    project = project_of(k, inc.id)
    plan_id = plan_of(k, inc.id)
    if project is None or plan_id is None:
        return {"incident": inc.id, "project": project, "plan": plan_id,
                "board": {s: [] for s in projects.STATES},
                "total": 0, "done": 0, "open": 0, "complete": False, "ready": []}

    board = projects.board(k, project)
    pstat = planning.plan_status(k, plan_id)
    return {
        "incident": inc.id,
        "project": project,
        "plan": plan_id,
        "board": board,
        "total": int(pstat["total"]),
        "done": int(pstat["done"]),
        "open": int(pstat["pending"]),
        "complete": bool(pstat["complete"]),
        "ready": planning.ready_steps(k, plan_id),
    }
