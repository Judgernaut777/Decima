"""PROJ1 — a projects / kanban board capability. This check proves:
  - an objective decomposes into a `project` with `ptask` work items (type "ptask",
    NEVER "task" — the kernel's task-tree renderer would crash on a foreign "task");
  - a fresh task lands in `todo`; `move` walks it todo → doing → done (LWW);
  - `board` groups tasks by state into the three kanban columns correctly;
  - `seed_from_plan` composes PLAN1's ready_steps frontier into todo ptasks;
  - the project, its tasks, and their membership edges all live on the Weft.

Contract: run(k, line). Fail loud.
"""
from decima import projects, planning


def run(k, line):
    line("\n== PROJECTS / KANBAN BOARD (project → ptask columns) — PROJ1 ==")
    w = lambda: k.weave()

    # Create a project and add three tasks — each lands in `todo`.
    proj = projects.create_project(k, "launch site")
    pc = w().get(proj)
    assert pc is not None and pc.type == "project", pc
    t_design = projects.add_task(k, proj, "design the layout")
    t_build = projects.add_task(k, proj, "build the pages")
    t_copy = projects.add_task(k, proj, "write the copy")
    for tid in (t_design, t_build, t_copy):
        c = w().get(tid)
        assert c is not None and c.type == "ptask", c           # ptask, NOT task
        assert c.content["state"] == "todo", c.content
    line(f"  created project 'launch site' + 3 ptasks (type='ptask'), all in todo ✓")

    # On the Weft: the project, its ptask cells, and the in_project edges.
    tasks_on_weft = [c for c in w().of_type("ptask")
                     if c.content.get("project") == proj]
    assert len(tasks_on_weft) == 3, tasks_on_weft
    edges = w().edges_from(t_design, projects.IN_PROJECT)
    assert [e["dst"] for e in edges] == [proj], edges
    line(f"  on the Weft: 1 project + 3 ptask cells, in_project edges wired ✓")

    # Move one task todo → doing → done (LWW: the latest move wins).
    projects.move(k, t_design, "doing")
    assert w().get(t_design).content["state"] == "doing"
    projects.move(k, t_design, "done")
    assert w().get(t_design).content["state"] == "done"
    line(f"  moved 'design' todo → doing → done (LWW, latest wins) ✓")

    # Move a second task to doing — board() must group all three correctly.
    projects.move(k, t_build, "doing")
    b = projects.board(k, proj)
    assert {x["ptask"] for x in b["done"]} == {t_design}, b["done"]
    assert {x["ptask"] for x in b["doing"]} == {t_build}, b["doing"]
    assert {x["ptask"] for x in b["todo"]} == {t_copy}, b["todo"]
    line(f"  board() groups by state: todo={len(b['todo'])} doing={len(b['doing'])} "
         f"done={len(b['done'])} ✓")

    # An unknown column is rejected (fail closed).
    bad = False
    try:
        projects.move(k, t_copy, "archived")
    except ValueError:
        bad = True
    assert bad, "an unknown kanban state MUST be rejected"
    assert w().get(t_copy).content["state"] == "todo"   # unchanged
    line(f"  move to unknown column 'archived' → REJECTED, state unchanged ✓")

    # seed_from_plan: compose PLAN1's ready frontier into todo ptasks.
    pl = planning.plan(k, "ship feature", [
        {"key": "spec", "objective": "write the spec", "capability": "shell"},
        {"key": "impl", "objective": "implement it", "depends_on": ["spec"],
         "capability": "shell"},
    ])
    seeded = projects.seed_from_plan(k, proj, pl["plan"])
    # Only `spec` is ready (impl depends on it) → exactly one seeded ptask.
    assert len(seeded) == 1, seeded
    seed_cell = w().get(seeded[0])
    assert seed_cell.type == "ptask" and seed_cell.content["state"] == "todo"
    assert seed_cell.content["title"] == "write the spec", seed_cell.content
    # Idempotent: re-seeding the same frontier hits the same cell, no duplicate.
    again = projects.seed_from_plan(k, proj, pl["plan"])
    assert again == seeded, (again, seeded)
    seeded_now = [c for c in w().of_type("ptask")
                  if c.content.get("project") == proj and c.content.get("title") == "write the spec"]
    assert len(seeded_now) == 1, seeded_now
    line(f"  seed_from_plan → 1 todo ptask from the ready frontier (idempotent) ✓")

    line("  → projects structure work into a kanban board on the Weft; moves are LWW, "
         "seeding composes PLAN1's frontier, and nothing here grants or executes.")
