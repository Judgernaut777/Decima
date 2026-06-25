"""PROJ1 — a projects / kanban board capability.

A `project` groups a set of work items — `ptask` Cells — onto a simple kanban
board with three columns: todo | doing | done. Projects STRUCTURE work the way
planning does; nothing here executes an effect, spawns a worker, or delegates.

NOTE on the cell type: a task is a `ptask` (project task), NOT a `task`. The
kernel's delegation task-tree renderer expects a delegation-task schema
(delegator_name etc.) on a `task` cell and would crash on a foreign one — so a
project's work item gets its own DISTINCT type.

Laws this module upholds:
  - **Decima's OWN assertions.** A project, its tasks, and their edges are authored
    by Decima (a trusted principal) and live on the Weft — provenance, not an
    untrusted intake.
  - **Ints, not floats.** No numeric content here, but any count returned is an int.
  - **No ambient authority / no effects.** Moving a task between columns is an LWW
    overwrite of its `state`; it grants nothing and invokes nothing.
  - **Compose, don't fork.** `seed_from_plan` reuses PLAN1's PUBLIC api
    (`planning.ready_steps`) to turn a plan's delegable frontier into ptasks; it
    duplicates no planning logic.

Shape on the Weft:
  - a `project` Cell (the board's name);
  - one `ptask` Cell per work item (title + state), default state `todo`;
  - an `in_project` EDGE  `ptask → in_project → project`  (membership / provenance).

`state` lives on the ptask Cell and is reconciled LWW (the model's default merge),
so the latest `move` wins — exactly the semantics a kanban column needs.

Public `model`/`weave`/`hashing`/`planning` API only — no core edit.
"""
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc
from decima import planning

PROJECT = "project"
PTASK = "ptask"
IN_PROJECT = "in_project"

# The kanban columns. `todo` is where a fresh task lands.
TODO = "todo"
DOING = "doing"
DONE = "done"
STATES = (TODO, DOING, DONE)


def project_id_for(name: str, lamport: int) -> str:
    """A project's id. Lamport keeps re-creating the same name distinct on the log
    (a new board, not a silent overwrite of the old one)."""
    return content_id({"project": nfc(name), "lamport": int(lamport)})


def ptask_id(project_id: str, key: str) -> str:
    """Content-address a task within its project by a stable `key`. Keying by
    (project, key) keeps a task's identity stable across re-assertions."""
    return content_id({"ptask": nfc(key), "of": project_id})


def create_project(k, name: str, *, author: str | None = None) -> str:
    """Create a `project` Cell on the Weft and return its id. Decima's own trusted
    assertion — full provenance, no effect."""
    name = nfc(str(name))
    if not name.strip():
        raise ValueError("project requires a non-empty name")
    author = author or k.decima_agent_id
    pid = project_id_for(name, k.weft.lamport)
    assert_content(k.weft, author, pid, PROJECT, {"name": name})
    return pid


def add_task(k, project: str, title: str, *, key: str | None = None,
             author: str | None = None) -> str:
    """Add a `ptask` Cell (state="todo") to `project` and edge it to the board.
    Returns the ptask id. `key` (default: the title) makes the id stable so the
    same logical task re-asserts onto one cell."""
    title = nfc(str(title))
    if not title.strip():
        raise ValueError("ptask requires a non-empty title")
    author = author or k.decima_agent_id
    proj = k.weave().get(project)
    if proj is None or proj.type != PROJECT:
        raise ValueError(f"not a project: {project}")
    key = nfc(str(key)) if key else title
    tid = ptask_id(project, key)
    assert_content(k.weft, author, tid, PTASK, {
        "project": project,
        "key": key,
        "title": title,
        "state": TODO,
    })
    assert_edge(k.weft, author, tid, IN_PROJECT, project)
    return tid


def move(k, ptask: str, state: str, *, author: str | None = None) -> str:
    """Move a ptask to a kanban column: todo | doing | done. An LWW overwrite of the
    task's `state` (the model's default merge) — the latest move wins. Returns the
    ptask id. Rejects an unknown column (fail closed)."""
    state = nfc(str(state))
    if state not in STATES:
        raise ValueError(f"unknown kanban state {state!r}; expected one of {STATES}")
    author = author or k.decima_agent_id
    cell = k.weave().get(ptask)
    if cell is None or cell.type != PTASK:
        raise ValueError(f"not a ptask: {ptask}")
    assert_content(k.weft, author, ptask, PTASK, {**cell.content, "state": state})
    return ptask


def _tasks(weave, project_id: str) -> list:
    """The live ptask Cells belonging to `project_id`."""
    return [c for c in weave.of_type(PTASK)
            if c.content.get("project") == project_id and not c.retracted]


def board(k, project: str) -> dict:
    """The kanban board: tasks grouped by state. Returns {todo: [...], doing: [...],
    done: [...]}, each a list of {ptask, title, state} dicts. Folded from the Weave,
    so it is deterministic and time-travelable like all state."""
    w = k.weave()
    columns: dict[str, list] = {s: [] for s in STATES}
    for cell in _tasks(w, project):
        state = cell.content.get("state", TODO)
        if state not in columns:
            columns[state] = []
        columns[state].append({
            "ptask": cell.id,
            "title": cell.content.get("title"),
            "state": state,
        })
    return columns


def seed_from_plan(k, project: str, plan_id: str, *, author: str | None = None) -> list[str]:
    """Turn a PLAN1 plan's READY steps (its delegable frontier) into todo ptasks on
    `project`. Composes planning's PUBLIC api — `planning.ready_steps` — so it owns
    no planning logic. Each ready step becomes one `ptask` whose stable key is the
    step id (so re-seeding is idempotent — the same step lands on the same task).
    Returns the ids of the created/refreshed ptasks, in frontier order."""
    author = author or k.decima_agent_id
    proj = k.weave().get(project)
    if proj is None or proj.type != PROJECT:
        raise ValueError(f"not a project: {project}")
    out = []
    for brief in planning.ready_steps(k, plan_id):
        # Key by the step id so a step seeds exactly one ptask (idempotent).
        tid = add_task(k, project, brief["objective"], key=brief["step"], author=author)
        out.append(tid)
    return out
