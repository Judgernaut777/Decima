"""The task read-model — a disposable list/status/deps/due view over plan steps.

Reads the runtime's Plan Step Cells (``decima.runtime.cells``) from the fold and
presents them as tasks: description, status, dependencies, deadline, assignee, and
a derived ``ready`` flag (all dependencies SUCCEEDED). It asserts nothing and is
rebuildable from the Weft (invariant 2). Deterministic: every list is sorted by id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from decima.projections.engine import BaseProjection
from decima.runtime.cells import PLAN_STEP, StepStatus


@dataclass(frozen=True)
class TaskView:
    id: str
    plan_id: str | None
    description: str
    status: str
    dependency_ids: tuple[str, ...]
    assigned_agent_id: str | None
    deadline: int | None
    ready: bool

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "description": self.description,
            "status": self.status,
            "dependency_ids": list(self.dependency_ids),
            "assigned_agent_id": self.assigned_agent_id,
            "deadline": self.deadline,
            "ready": self.ready,
        }


class TasksProjection(BaseProjection):
    name = "tasks"
    version = 1

    def _status_by_id(self) -> dict[str, str]:
        return {c.id: cast(str, c.content.get("status")) for c in self.fold.of_type(PLAN_STEP)}

    def _deps_satisfied(self, deps: list[str], statuses: dict[str, str]) -> bool:
        return all(statuses.get(d) == StepStatus.SUCCEEDED for d in deps)

    def tasks(self) -> list[TaskView]:
        statuses = self._status_by_id()
        out: list[TaskView] = []
        for c in self.fold.of_type(PLAN_STEP):
            deps = list(c.content.get("dependency_ids", []))
            status = c.content.get("status")
            runnable = status in (StepStatus.PENDING, StepStatus.BLOCKED, StepStatus.READY)
            out.append(
                TaskView(
                    id=c.id,
                    plan_id=c.content.get("plan_id"),
                    description=c.content.get("description", ""),
                    status=cast(str, status),
                    dependency_ids=tuple(deps),
                    assigned_agent_id=c.content.get("assigned_agent_id"),
                    deadline=c.content.get("deadline"),
                    ready=bool(runnable and self._deps_satisfied(deps, statuses)),
                )
            )
        return sorted(out, key=lambda t: t.id)

    def by_status(self, status: str) -> list[TaskView]:
        return [t for t in self.tasks() if t.status == status]

    def ready_tasks(self) -> list[TaskView]:
        return [t for t in self.tasks() if t.ready]

    def due(self, before: int) -> list[TaskView]:
        """Non-terminal tasks whose logical deadline is at/earlier than ``before``."""
        return [
            t
            for t in self.tasks()
            if t.deadline is not None
            and t.deadline <= before
            and t.status not in StepStatus.TERMINAL
        ]

    def get(self, task_id: str) -> TaskView | None:
        for t in self.tasks():
            if t.id == task_id:
                return t
        return None

    def view(self) -> object:
        return [t.as_dict() for t in self.tasks()]
