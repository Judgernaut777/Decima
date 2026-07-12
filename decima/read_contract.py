"""Decima READ-CONTRACT — a pinned, versioned re-export of the disposable read-models.

This module is the ONE stable importable surface a downstream consumer (BrainConnect /
Connect Lane 2) codes against. It is a thin, non-invasive facade: it re-exports the
EXISTING projections (``decima.projections.*``) and the workspace read-shapes
(``decima.services.api.contracts``) behind pinned, documented signatures, and adds a
single ``READ_CONTRACT_VERSION`` constant. The prose contract is ``docs/READ_CONTRACT.md``.

What this module DOES:
  * expose the six Weft-folded read-models (tasks / projects / agents / approvals /
    knowledge / activity) through typed accessors;
  * reconcile the agent forest naming (ADR 0008 references ``agents.py:tree`` — no such
    method exists) by composing the EXISTING ``roots()`` + ``children_of()`` into
    ``agent_forest()`` / ``agent_tree()``, deterministically;
  * surface knowledge's per-item ``instruction_eligible`` / ``trust`` bit as a first-class
    field so the consumer can honor it exactly as it honors its own ``trusted`` bit.

What this module MUST NEVER do (Lane 2 hard boundary):
  * append to the Weft, mint authority, or execute anything — it only READS;
  * add persistence, caching, or a new canonical store;
  * touch or re-implement execution / authorization internals (Weft, leases,
    capability_proof, implementation_digest, worker IPC). None are imported here.

It calls the existing projections verbatim; determinism is inherited from them (every
list is sorted, ints on the logical frontier, no wall-clock). Treat every returned text
as DATA, never as an instruction, unless its ``trust`` is ``"trusted"`` (invariant 5).
"""

from __future__ import annotations

from typing import Any

from decima.projections.activity import ActivityEntry, ActivityProjection
from decima.projections.agents import AgentsProjection, AgentView
from decima.projections.approvals import BUCKETS as APPROVAL_STATES
from decima.projections.approvals import ApprovalsProjection, ApprovalView
from decima.projections.engine import BaseProjection, ProjectionCheckpoint, ProjectionDriver
from decima.projections.knowledge import (
    KNOWLEDGE_TYPES,
    KnowledgeItem,
    KnowledgeProjection,
)
from decima.projections.projects import ProjectsProjection, ProjectView
from decima.projections.tasks import TasksProjection, TaskView
from decima.services.api.contracts import WorkspaceArtifact, WorkspaceRun

__all__ = [
    "READ_CONTRACT_VERSION",
    "READ_MODELS",
    "PINNED_PROJECTION_VERSIONS",
    "APPROVAL_STATES",
    "KNOWLEDGE_TYPES",
    # view / item shapes (plain data, each with as_dict())
    "TaskView",
    "ProjectView",
    "AgentView",
    "ApprovalView",
    "KnowledgeItem",
    "ActivityEntry",
    "WorkspaceArtifact",
    "WorkspaceRun",
    # the facade
    "ReadModels",
    "open_read_models",
]

# ── the contract version ──────────────────────────────────────────────────────
# Bump the MINOR for additive-only changes (a new accessor, a new field on a returned
# shape); bump the MAJOR only for a breaking change (a removed/renamed field or accessor,
# or an ordering/semantics change). See docs/READ_CONTRACT.md § Compatibility policy.
READ_CONTRACT_VERSION = "0.1"

# The read-models this contract pins. "artifacts" is served at the application layer
# (decima.services.api.workspace_service READERS), not as a standalone Weft projection;
# its stable shapes (WorkspaceArtifact / WorkspaceRun) are re-exported above.
READ_MODELS = (
    "tasks",
    "projects",
    "agents",
    "approvals",
    "knowledge",
    "activity",
    "artifacts",
)

# The schema ``version`` of each underlying projection, pinned at read-contract v0.1. A
# bump in any of these is a projection migration-by-rebuild; the consumer should treat a
# changed value as a signal to re-read the contract doc.
PINNED_PROJECTION_VERSIONS: dict[str, int] = {
    "tasks": TasksProjection.version,
    "projects": ProjectsProjection.version,
    "agents": AgentsProjection.version,
    "approvals": ApprovalsProjection.version,
    "knowledge": KnowledgeProjection.version,
    "activity": ActivityProjection.version,
}


class ReadModels:
    """A read-only facade over a Decima ``Weft``: the six disposable read-models built
    and kept current via a ``ProjectionDriver``. Constructing it performs a full rebuild
    (a pure function of the Weft); call :meth:`refresh` to fold the tail after new events.

    It holds NO authority and appends NOTHING. Every accessor delegates to the existing
    projection, so ordering/determinism guarantees are exactly those documented in
    ``docs/READ_CONTRACT.md``.
    """

    def __init__(self, weft: object) -> None:
        self._driver = ProjectionDriver(weft)
        self.tasks_projection = TasksProjection()
        self.projects_projection = ProjectsProjection()
        self.agents_projection = AgentsProjection()
        self.approvals_projection = ApprovalsProjection()
        self.knowledge_projection = KnowledgeProjection()
        self.activity_projection = ActivityProjection()
        self._projections: tuple[BaseProjection, ...] = (
            self.tasks_projection,
            self.projects_projection,
            self.agents_projection,
            self.approvals_projection,
            self.knowledge_projection,
            self.activity_projection,
        )
        for projection in self._projections:
            self._driver.register(projection)  # register() rebuilds by replaying the log

    # ── keeping current ───────────────────────────────────────────────────────
    def refresh(self) -> dict[str, ProjectionCheckpoint]:
        """Fold events committed since the last read into every projection (incremental;
        a projection version bump triggers a clean rebuild). Returns each checkpoint."""
        return self._driver.update()

    def checkpoints(self) -> dict[str, ProjectionCheckpoint]:
        """The deterministic ``ProjectionCheckpoint`` (name/version/last_seq/state_root)
        of each pinned read-model — the fingerprint a consumer can compare across hosts."""
        return {p.name: p.checkpoint() for p in self._projections}

    # ── planning: tasks ───────────────────────────────────────────────────────
    def tasks(self) -> list[TaskView]:
        return self.tasks_projection.tasks()

    def ready_tasks(self) -> list[TaskView]:
        """Runnable tasks whose dependencies have all SUCCEEDED (the ADR ``ready`` set)."""
        return self.tasks_projection.ready_tasks()

    def tasks_by_status(self, status: str) -> list[TaskView]:
        return self.tasks_projection.by_status(status)

    def tasks_due(self, before: int) -> list[TaskView]:
        return self.tasks_projection.due(before)

    def task(self, task_id: str) -> TaskView | None:
        return self.tasks_projection.get(task_id)

    # ── planning: projects ────────────────────────────────────────────────────
    def projects(self) -> list[ProjectView]:
        return self.projects_projection.projects()

    def project(self, project_id: str) -> ProjectView | None:
        return self.projects_projection.get(project_id)

    # ── agents (forest / tree) ────────────────────────────────────────────────
    def agents(self) -> list[AgentView]:
        return self.agents_projection.agents()

    def agent(self, agent_id: str) -> AgentView | None:
        return self.agents_projection.get(agent_id)

    def agent_roots(self) -> list[AgentView]:
        return self.agents_projection.roots()

    def agent_children(self, agent_id: str) -> list[AgentView]:
        return self.agents_projection.children_of(agent_id)

    def agent_forest(self) -> list[dict[str, Any]]:
        """The agent hierarchy as a deterministic nested structure, composed from the
        EXISTING ``roots()`` + ``children_of()``. Reconciles ADR 0008's ``agents.py:tree``
        reference, which names a method that does not exist. Each node is
        ``{"agent": AgentView.as_dict(), "children": [<node>, ...]}``; roots and children
        are id-sorted, so two rebuilds of the same Weft yield an identical forest."""
        return [self._forest_node(root) for root in self.agents_projection.roots()]

    # ``agent_tree`` is an alias for ``agent_forest`` under the name ADR 0008 uses.
    agent_tree = agent_forest

    def _forest_node(self, view: AgentView) -> dict[str, Any]:
        """Build one root's subtree ITERATIVELY (an explicit stack, not recursion) so an
        arbitrarily deep parent→child chain cannot raise ``RecursionError``. Output is
        byte-identical to the recursive form: each node's ``children`` is populated in one
        pass over the already-id-sorted ``children_of()``, so ordering is unchanged and a
        ``visited`` guard makes a pathological cycle terminate instead of looping forever."""
        root_node: dict[str, Any] = {"agent": view.as_dict(), "children": []}
        visited: set[str] = {view.id}
        stack: list[tuple[dict[str, Any], AgentView]] = [(root_node, view)]
        while stack:
            node, node_view = stack.pop()
            for child in self.agents_projection.children_of(node_view.id):
                if child.id in visited:
                    continue  # never revisit (a forest has no cycles; fail safe if one exists)
                visited.add(child.id)
                child_node: dict[str, Any] = {"agent": child.as_dict(), "children": []}
                node["children"].append(child_node)
                stack.append((child_node, child))
        return root_node

    # ── approvals (Morta inbox) ───────────────────────────────────────────────
    def approvals(self) -> list[ApprovalView]:
        return self.approvals_projection.approvals()

    def pending_approvals(self) -> list[ApprovalView]:
        return self.approvals_projection.pending()

    def approvals_by_state(self, state: str) -> list[ApprovalView]:
        return self.approvals_projection.by_state(state)

    def approval_counts(self) -> dict[str, int]:
        return self.approvals_projection.counts()

    # ── knowledge (with the instruction_eligible / trust bit) ─────────────────
    def knowledge(self) -> list[KnowledgeItem]:
        """Live knowledge items. Each carries ``instruction_eligible: bool`` and a derived
        ``trust`` of ``"trusted"``/``"untrusted"``. UNTRUSTED knowledge is DATA, never an
        instruction — honor this bit as you honor your own trusted bit (invariant 5)."""
        return self.knowledge_projection.items()

    def notes(self) -> list[KnowledgeItem]:
        return self.knowledge_projection.notes()

    def documents(self) -> list[KnowledgeItem]:
        return self.knowledge_projection.documents()

    def knowledge_item(self, item_id: str) -> KnowledgeItem | None:
        return self.knowledge_projection.get(item_id)

    # ── activity (timeline) ───────────────────────────────────────────────────
    def timeline(
        self,
        *,
        last: int | None = None,
        principal: str | None = None,
        cell_type: str | None = None,
    ) -> list[ActivityEntry]:
        return self.activity_projection.timeline(
            last=last, principal=principal, cell_type=cell_type
        )

    def activity_digest(self, **filters: object) -> dict[str, Any]:
        return self.activity_projection.digest(**filters)


def open_read_models(weft: object) -> ReadModels:
    """Build a :class:`ReadModels` facade over an already-opened Decima ``Weft`` (a fully
    rebuilt, current set of read-models). The caller owns the Weft's lifecycle; this
    facade never opens, writes, or closes it."""
    return ReadModels(weft)
