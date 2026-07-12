"""Gate the external READ-CONTRACT surface (``decima.read_contract``).

This is the executable contract for Lane 2: BrainConnect / Connect code against the
*exact* surface exercised here. Its whole job is to FAIL LOUDLY on upstream drift —
if an underlying projection method is renamed, retyped, or a projection ``version`` is
bumped, or the pinned version string / ``__all__`` / ``READ_MODELS`` fall out of sync,
one of these assertions breaks instead of a downstream consumer silently breaking.

It opens a *seeded* Weft (via the shared projections conftest, the same public
model/runtime seams a real system uses) and drives EVERY ``ReadModels`` accessor plus the
composed ``agent_forest`` / ``agent_tree`` — so a rename of any delegated projection method
surfaces here as an ``AttributeError`` at call time.
"""

from __future__ import annotations

import sys
from types import FrameType

import decima.read_contract as rc
from decima.projections.activity import ActivityProjection
from decima.projections.agents import AgentsProjection
from decima.projections.approvals import ApprovalsProjection
from decima.projections.knowledge import KnowledgeProjection
from decima.projections.projects import ProjectsProjection
from decima.projections.tasks import TasksProjection
from decima.read_contract import (
    APPROVAL_STATES,
    KNOWLEDGE_TYPES,
    PINNED_PROJECTION_VERSIONS,
    READ_CONTRACT_VERSION,
    READ_MODELS,
    ReadModels,
    open_read_models,
)
from decima.runtime import cells
from tests.projections.conftest import advance, new_weft, seed_base

# The six Weft-folded projections this contract pins (artifacts is application-layer).
_WEFT_PROJECTIONS = ("tasks", "projects", "agents", "approvals", "knowledge", "activity")


def _seeded() -> tuple[ReadModels, dict]:
    """A fully-folded facade over a seeded + advanced Weft (the shared fixture)."""
    weft, author, _db, _kr = new_weft()
    ids = seed_base(weft, author)
    advance(weft, author, ids)  # a tail an incremental refresh() must fold
    return open_read_models(weft), ids


def test_read_contract_version_is_pinned_at_0_1() -> None:
    # A change here is a deliberate contract bump — it must be reviewed, never incidental.
    assert READ_CONTRACT_VERSION == "0.1"
    assert rc.READ_CONTRACT_VERSION == "0.1"


def test_all_and_read_models_names_resolve() -> None:
    # Every advertised public name must actually resolve on the module.
    assert rc.__all__, "__all__ must not be empty"
    for name in rc.__all__:
        assert hasattr(rc, name), f"__all__ advertises {name!r} but it does not resolve"

    # READ_MODELS is honest: exactly the six Weft projections plus the types-only artifacts.
    assert set(READ_MODELS) == set(_WEFT_PROJECTIONS) | {"artifacts"}

    # PINNED_PROJECTION_VERSIONS covers ONLY the six Weft projections. "artifacts" is
    # application-layer (types-only in v0.1) — it is intentionally NOT pinned here, so the
    # documented `{m: PINNED_PROJECTION_VERSIONS[m] for m in READ_MODELS}` KeyErrors on it.
    assert set(PINNED_PROJECTION_VERSIONS) == set(_WEFT_PROJECTIONS)
    assert "artifacts" in READ_MODELS
    assert "artifacts" not in PINNED_PROJECTION_VERSIONS
    try:
        {m: PINNED_PROJECTION_VERSIONS[m] for m in READ_MODELS}
    except KeyError as exc:
        assert exc.args[0] == "artifacts"
    else:  # pragma: no cover - guards the documented asymmetry
        raise AssertionError("expected artifacts to be absent from PINNED_PROJECTION_VERSIONS")

    # The artifacts shapes ARE re-exported for typing even though there is no accessor.
    assert "WorkspaceArtifact" in rc.__all__ and "WorkspaceRun" in rc.__all__

    # Each of the six pinned models resolves to a real ReadModels accessor (activity's
    # primary accessor is `timeline`, not `activity`).
    primary_accessor = {
        "tasks": "tasks",
        "projects": "projects",
        "agents": "agents",
        "approvals": "approvals",
        "knowledge": "knowledge",
        "activity": "timeline",
    }
    for name in _WEFT_PROJECTIONS:
        assert callable(getattr(ReadModels, primary_accessor[name])), f"missing accessor {name!r}"


def test_pinned_projection_versions_match_the_projections() -> None:
    # A drift-catch: an upstream projection `version` bump must break this pin.
    assert PINNED_PROJECTION_VERSIONS == {
        "tasks": TasksProjection.version,
        "projects": ProjectsProjection.version,
        "agents": AgentsProjection.version,
        "approvals": ApprovalsProjection.version,
        "knowledge": KnowledgeProjection.version,
        "activity": ActivityProjection.version,
    }
    # Enumerated sets are re-exported and non-empty.
    assert APPROVAL_STATES and KNOWLEDGE_TYPES


def test_full_accessor_surface_over_seeded_weft() -> None:
    """Drive EVERY ReadModels accessor. Renaming any delegated projection method upstream
    turns one of these calls into an AttributeError, failing the gate."""
    rm, ids = _seeded()

    # keeping-current seam
    assert rm.refresh() is not None
    checkpoints = rm.checkpoints()
    assert set(checkpoints) == set(_WEFT_PROJECTIONS)

    # tasks: tasks / ready_tasks / by_status / due / get
    all_tasks = rm.tasks()
    assert {t.id for t in all_tasks} >= {ids["a"], ids["b"], ids["c"], ids["d"]}
    assert isinstance(rm.ready_tasks(), list)
    assert isinstance(rm.tasks_by_status("SUCCEEDED"), list)
    assert isinstance(rm.tasks_due(10), list)
    assert rm.task(ids["a"]) is not None
    assert rm.task("task:nonexistent") is None

    # projects
    assert {p.id for p in rm.projects()} == {ids["plan"]}
    assert rm.project(ids["plan"]) is not None

    # agents: agents / agent / roots / children / forest / tree(alias)
    assert {a.id for a in rm.agents()} >= {ids["parent"], ids["child"]}
    assert rm.agent(ids["parent"]) is not None
    assert {a.id for a in rm.agent_roots()} == {ids["parent"]}
    assert {a.id for a in rm.agent_children(ids["parent"])} == {ids["child"]}
    forest = rm.agent_forest()
    assert forest and forest[0]["agent"]["id"] == ids["parent"]
    assert forest[0]["children"][0]["agent"]["id"] == ids["child"]
    # agent_tree is the ADR-0008 name; it is the SAME composition as agent_forest.
    assert rm.agent_tree() == forest

    # approvals: approvals / pending / by_state / counts
    assert isinstance(rm.approvals(), list)
    assert isinstance(rm.pending_approvals(), list)
    for state in APPROVAL_STATES:
        assert isinstance(rm.approvals_by_state(state), list)
    counts = rm.approval_counts()
    assert set(counts) == set(APPROVAL_STATES)

    # knowledge: knowledge / notes / documents / knowledge_item
    assert {k.id for k in rm.knowledge()} >= {ids["note1"], ids["note2"], ids["doc1"]}
    assert {n.id for n in rm.notes()} >= {ids["note1"], ids["note2"]}
    assert {d.id for d in rm.documents()} == {ids["doc1"]}
    assert rm.knowledge_item(ids["note1"]) is not None
    assert rm.knowledge_item("note:nonexistent") is None

    # activity: timeline (all keyword filters) / digest
    assert isinstance(rm.timeline(), list)
    assert isinstance(rm.timeline(last=5, principal="whoever", cell_type="note"), list)
    assert isinstance(rm.activity_digest(), dict)


def test_seeded_knowledge_item_surfaces_instruction_eligible_and_trust() -> None:
    """The trust bit ADR-0008/Lane-5 requires BrainConnect to honor: a per-item
    ``instruction_eligible`` (bool) and its derived ``trust`` (trusted/untrusted)."""
    rm, ids = _seeded()

    trusted = rm.knowledge_item(ids["note1"])
    assert trusted is not None
    assert isinstance(trusted.instruction_eligible, bool)
    assert trusted.instruction_eligible is True
    assert trusted.trust == "trusted"

    untrusted = rm.knowledge_item(ids["note2"])
    assert untrusted is not None
    assert isinstance(untrusted.instruction_eligible, bool)
    assert untrusted.instruction_eligible is False
    assert untrusted.trust == "untrusted"

    # Derived-trust invariant holds for EVERY surfaced item, and as_dict() carries both.
    for item in rm.knowledge():
        assert item.trust == ("trusted" if item.instruction_eligible else "untrusted")
        d = item.as_dict()
        assert d["instruction_eligible"] == item.instruction_eligible
        assert d["trust"] == item.trust


def _stack_depth() -> int:
    depth = 0
    frame: FrameType | None = sys._getframe()
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


def test_agent_forest_is_depth_safe_and_deterministic() -> None:
    """A deep root→child chain must NOT raise RecursionError. Proven by constraining the
    recursion limit to a small headroom over the current stack while the chain is far
    deeper: the iterative forest passes; a per-level recursive one would overflow. Output
    ordering stays deterministic (roots + children id-sorted, tree == forest)."""
    weft, author, _db, _kr = new_weft()
    depth = 400
    parent: str | None = None
    for i in range(depth):
        parent = cells.create_agent(
            weft,
            author,
            objective=f"a{i}",
            principal=author,
            parent_agent_id=parent,
            agent_id=f"agent:{i:05d}",
        )
    rm = open_read_models(weft)

    old_limit = sys.getrecursionlimit()
    headroom = 80  # << depth (400): a recursive per-level walk would blow this
    try:
        sys.setrecursionlimit(_stack_depth() + headroom)
        forest = rm.agent_forest()
        tree = rm.agent_tree()
    finally:
        sys.setrecursionlimit(old_limit)

    assert forest == tree  # alias identity preserved by the iterative build
    assert len(forest) == 1  # a single chain → one root
    # Walk the whole chain: it is exactly `depth` deep, id-sorted at each level.
    node = forest[0]
    walked = 0
    while True:
        walked += 1
        assert node["agent"]["id"] == f"agent:{walked - 1:05d}"
        children = node["children"]
        if not children:
            break
        assert [c["agent"]["id"] for c in children] == sorted(c["agent"]["id"] for c in children)
        node = children[0]
    assert walked == depth
