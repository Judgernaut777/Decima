"""The agent read-model — a disposable hierarchy/status/budget view over agents.

Reads the runtime's Agent Cells from the fold and presents the agent forest: each
agent's parent, children, status, and budgets (token / monetary / deadline — ints
on the logical frontier, never wall-clock). It asserts nothing and is rebuildable
from the Weft. Deterministic: children and the flattened list are sorted by id.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from decima.projections.engine import BaseProjection
from decima.runtime.cells import AGENT


@dataclass(frozen=True)
class AgentView:
    id: str
    parent_agent_id: str | None
    objective: str
    status: str
    principal: str | None
    token_budget: int | None
    monetary_budget: int | None
    deadline: int | None
    child_ids: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_agent_id": self.parent_agent_id,
            "objective": self.objective,
            "status": self.status,
            "principal": self.principal,
            "token_budget": self.token_budget,
            "monetary_budget": self.monetary_budget,
            "deadline": self.deadline,
            "child_ids": list(self.child_ids),
        }


class AgentsProjection(BaseProjection):
    name = "agents"
    version = 1

    def _children_map(self) -> dict[str, list[str]]:
        children: dict[str, list[str]] = {}
        for c in self.fold.of_type(AGENT):
            parent = c.content.get("parent_agent_id")
            if parent is not None:
                children.setdefault(parent, []).append(c.id)
        return children

    def agents(self) -> list[AgentView]:
        children = self._children_map()
        out: list[AgentView] = []
        for c in self.fold.of_type(AGENT):
            out.append(AgentView(
                id=c.id,
                parent_agent_id=c.content.get("parent_agent_id"),
                objective=c.content.get("objective", ""),
                status=c.content.get("status", ""),
                principal=c.content.get("principal"),
                token_budget=c.content.get("token_budget"),
                monetary_budget=c.content.get("monetary_budget"),
                deadline=c.content.get("deadline"),
                child_ids=tuple(sorted(children.get(c.id, []))),
            ))
        return sorted(out, key=lambda v: v.id)

    def roots(self) -> list[AgentView]:
        """Agents with no live parent in the fold (the tops of the forest)."""
        ids = {c.id for c in self.fold.of_type(AGENT)}
        return [a for a in self.agents()
                if a.parent_agent_id is None or a.parent_agent_id not in ids]

    def children_of(self, agent_id: str) -> list[AgentView]:
        by_id = {a.id: a for a in self.agents()}
        parent = by_id.get(agent_id)
        if parent is None:
            return []
        return [by_id[cid] for cid in parent.child_ids if cid in by_id]

    def get(self, agent_id: str) -> AgentView | None:
        for a in self.agents():
            if a.id == agent_id:
                return a
        return None

    def view(self) -> object:
        return [a.as_dict() for a in self.agents()]
