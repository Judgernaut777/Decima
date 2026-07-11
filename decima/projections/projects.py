"""The project read-model — a disposable objective/status/members view over plans.

Reads the runtime's Plan Cells and their Plan Steps from the fold: a project's
objective, status, its member steps, the agents assigned to those steps, and a
progress count. It asserts nothing and is rebuildable from the Weft. Deterministic:
member/step lists are sorted, counts are ints.
"""

from __future__ import annotations

from dataclasses import dataclass

from decima.projections.engine import BaseProjection
from decima.runtime.cells import PLAN, PLAN_STEP, StepStatus


@dataclass(frozen=True)
class ProjectView:
    id: str
    objective: str
    status: str
    creator_principal: str | None
    step_ids: tuple[str, ...]
    member_agent_ids: tuple[str, ...]
    task_count: int
    completed_count: int

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "status": self.status,
            "creator_principal": self.creator_principal,
            "step_ids": list(self.step_ids),
            "member_agent_ids": list(self.member_agent_ids),
            "task_count": self.task_count,
            "completed_count": self.completed_count,
        }


class ProjectsProjection(BaseProjection):
    name = "projects"
    version = 1

    def _steps_of(self, plan_id: str) -> list:
        return [c for c in self.fold.of_type(PLAN_STEP)
                if c.content.get("plan_id") == plan_id]

    def projects(self) -> list[ProjectView]:
        out: list[ProjectView] = []
        for p in self.fold.of_type(PLAN):
            steps = self._steps_of(p.id)
            members = sorted({s.content.get("assigned_agent_id") for s in steps
                              if s.content.get("assigned_agent_id")})
            done = sum(1 for s in steps
                       if s.content.get("status") == StepStatus.SUCCEEDED)
            out.append(ProjectView(
                id=p.id,
                objective=p.content.get("objective", ""),
                status=p.content.get("status", ""),
                creator_principal=p.content.get("creator_principal"),
                step_ids=tuple(sorted(s.id for s in steps)),
                member_agent_ids=tuple(members),
                task_count=len(steps),
                completed_count=int(done),
            ))
        return sorted(out, key=lambda v: v.id)

    def get(self, project_id: str) -> ProjectView | None:
        for v in self.projects():
            if v.id == project_id:
                return v
        return None

    def view(self) -> object:
        return [v.as_dict() for v in self.projects()]
