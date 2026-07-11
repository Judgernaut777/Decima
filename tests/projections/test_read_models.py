"""Per-read-model behaviour + fidelity to the canonical kernel fold.

Each read-model is exercised against a seeded Weft, and the task/agent status view
is asserted EQUAL to what the trusted ``decima.kernel.weave`` fold materializes for
the same cells — the disposable projection tracks the canonical store, it does not
diverge from it.
"""

from __future__ import annotations

from decima.kernel.weave import Weave
from decima.projections.activity import ActivityProjection
from decima.projections.agents import AgentsProjection
from decima.projections.approvals import (
    CONSUMED,
    EXPIRED,
    PENDING,
    ApprovalsProjection,
)
from decima.projections.engine import ProjectionDriver
from decima.projections.knowledge import KnowledgeProjection
from decima.projections.projects import ProjectsProjection
from decima.projections.search import SearchIndex
from decima.projections.tasks import TasksProjection
from decima.runtime import cells
from decima.runtime.cells import StepStatus
from tests.projections.conftest import advance, new_weft, seed_base


def _driver(factory):
    weft, author, _db, _kr = new_weft()
    ids = seed_base(weft, author)
    d = ProjectionDriver(weft)
    d.register(factory())
    return weft, author, ids, d, d.get(factory.name)


def test_tasks_status_deps_ready_and_due():
    weft, author, ids, d, tasks = _driver(TasksProjection)
    a = tasks.get(ids["a"])
    assert a.status == StepStatus.PENDING and a.ready is True   # no deps
    assert tasks.get(ids["b"]).ready is False                   # dep A not done
    assert {t.id for t in tasks.due(10)} == {ids["a"]}          # deadline 10

    cells.set_status(weft, author, Weave.fold(weft).get(ids["a"]), StepStatus.SUCCEEDED)
    d.update()
    assert tasks.get(ids["a"]).status == StepStatus.SUCCEEDED
    assert {t.id for t in tasks.ready_tasks()} == {ids["b"], ids["c"]}


def test_tasks_view_matches_kernel_fold():
    weft, author, ids, d, tasks = _driver(TasksProjection)
    advance(weft, author, ids)
    d.update()
    weave = Weave.fold(weft)
    for t in tasks.tasks():
        assert t.status == weave.get(t.id).content["status"], f"{t.id} status drift"


def test_projects_objective_status_members():
    _weft, _author, ids, _d, proj = _driver(ProjectsProjection)
    p = proj.get(ids["plan"])
    assert p.objective == "ship it"
    assert set(p.step_ids) == {ids["a"], ids["b"], ids["c"], ids["d"]}
    assert p.member_agent_ids == (ids["parent"],)   # only A is assigned
    assert p.task_count == 4 and p.completed_count == 0


def test_agents_hierarchy_and_budget():
    _weft, _author, ids, _d, agents = _driver(AgentsProjection)
    parent = agents.get(ids["parent"])
    assert parent.token_budget == 1000 and parent.monetary_budget == 500
    assert parent.child_ids == (ids["child"],)
    assert {a.id for a in agents.roots()} == {ids["parent"]}
    assert {a.id for a in agents.children_of(ids["parent"])} == {ids["child"]}


def test_approvals_buckets_pending_then_consumed():
    weft, author, ids, d, appr = _driver(ApprovalsProjection)
    assert {a.item for a in appr.by_state(PENDING)} == {ids["approval"]}
    advance(weft, author, ids)          # a decision approves + runs it
    d.update()
    a = appr.approvals()[0]
    assert a.state == CONSUMED and a.ran is True and a.decision == "approved"
    assert appr.counts()[CONSUMED] == 1


def test_approvals_expire_at_the_logical_frontier():
    weft, author, _db, _kr = new_weft()
    from decima.kernel.inbox import ITEM
    from decima.kernel.model import assert_content
    assert_content(weft, author, "inbox_item:stale", ITEM,
                   {"capability": "cap:x", "description": "stale", "expires_at": 1})
    d = ProjectionDriver(weft)
    d.register(ApprovalsProjection())
    appr = d.get("approvals")
    # Still pending while the frontier has not passed expires_at.
    assert appr.approvals()[0].state == PENDING
    # Advance the logical frontier (max lamport) well past expires_at=1.
    for i in range(3):
        assert_content(weft, author, f"filler:{i}", "note", {"text": f"f{i}"})
    d.update()
    assert appr.approvals()[0].state == EXPIRED


def test_activity_timeline_is_ordered_and_filterable():
    _weft, author, _ids, _d, act = _driver(ActivityProjection)
    seqs = [e.seq for e in act.entries]
    assert seqs == sorted(seqs), "timeline not in seq order"
    assert all(e.author == author for e in act.timeline(principal=author))
    notes = act.timeline(cell_type="note")
    assert notes and all(e.cell_type == "note" for e in notes)
    assert act.digest()["by_verb"].get("asserted", 0) > 0


def test_knowledge_notes_documents_links_provenance_and_trust():
    _weft, _author, ids, _d, know = _driver(KnowledgeProjection)
    note1 = know.get(ids["note1"])
    assert note1.type == "note" and note1.instruction_eligible is True
    assert note1.trust == "trusted"
    assert {"rel": "references", "dst": ids["doc1"]} in [dict(link) for link in note1.links]
    assert note1.provenance                         # asserting event ids present
    # An untrusted note is DATA (fails closed on instruction-eligibility).
    assert know.get(ids["note2"]).instruction_eligible is False
    assert {d.id for d in know.documents()} == {ids["doc1"]}


def test_search_is_exact_derived_and_carries_trust():
    _weft, _author, ids, _d, know = _driver(KnowledgeProjection)
    index = SearchIndex(know)
    hits = index.query("roadmap alpha")
    assert hits[0].cell == ids["note1"] and hits[0].score >= 1
    assert hits[0].instruction_eligible is True and hits[0].trust == "trusted"
    # Rebuild reproduces the same index (deterministic, re-foldable).
    fp = index.fingerprint()
    assert index.rebuild().fingerprint() == fp
    # A term nobody wrote returns nothing (exact lexical match).
    assert index.query("nonexistentzzz") == []
