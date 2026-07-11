"""The CRITICAL acceptance test (invariant 2): a projection is DISPOSABLE.

Build a Weft with plans/tasks/agents/notes/approvals, project it INCREMENTALLY,
then DELETE every projection and REBUILD purely from the Weft — the rebuilt state
must EQUAL the incremental state (state_root AND field-by-field view). Plus: a
retracted note stops appearing, deleting the search index does not delete
knowledge, and a projection version bump forces a clean rebuild.
"""

from __future__ import annotations

from decima.kernel.weft import RETRACT
from decima.projections.activity import ActivityProjection
from decima.projections.agents import AgentsProjection
from decima.projections.approvals import ApprovalsProjection
from decima.projections.engine import ProjectionDriver
from decima.projections.knowledge import KnowledgeProjection
from decima.projections.projects import ProjectsProjection
from decima.projections.search import SearchIndex
from decima.projections.tasks import TasksProjection
from tests.projections.conftest import advance, new_weft, seed_base

_FACTORIES = (
    TasksProjection, ProjectsProjection, AgentsProjection,
    ApprovalsProjection, ActivityProjection, KnowledgeProjection,
)


def test_rebuild_equals_incremental():
    weft, author, _db, _kr = new_weft()
    ids = seed_base(weft, author)

    # Incremental path: build the projections early, then fold ONLY the tail.
    incremental = ProjectionDriver(weft)
    for factory in _FACTORIES:
        incremental.register(factory())        # initial build over the base
    advance(weft, author, ids)                  # more history lands
    incremental.update()                        # incremental fold of the tail only

    # The projections above are now current WITHOUT ever having been rebuilt from
    # scratch after `advance`. Prove the tail was actually folded incrementally.
    assert incremental.lag("tasks") == 0
    assert incremental.get("tasks").last_seq == weft.count()

    # Rebuild path: brand-new projections, folded from the whole Weft in one shot.
    rebuilt = ProjectionDriver(weft)
    for factory in _FACTORIES:
        rebuilt.register(factory())             # register() rebuilds from genesis

    for name in incremental.names():
        inc, reb = incremental.get(name), rebuilt.get(name)
        assert inc.state_root() == reb.state_root(), f"{name}: state_root differs"
        assert inc.view() == reb.view(), f"{name}: view differs field-by-field"
        assert inc.checkpoint() == reb.checkpoint(), f"{name}: checkpoint differs"


def test_retracted_note_stops_appearing():
    weft, author, _db, _kr = new_weft()
    ids = seed_base(weft, author)

    driver = ProjectionDriver(weft)
    driver.register(KnowledgeProjection())
    know = driver.get("knowledge")
    assert ids["note2"] in {k.id for k in know.notes()}

    weft.append(author, RETRACT, {"cell": ids["note2"]})
    driver.update()
    assert ids["note2"] not in {k.id for k in know.notes()}, "retracted note still shown"
    # A full rebuild agrees — the retraction is durable in the Weft, not the view.
    incremental_root = know.state_root()
    assert incremental_root == driver.rebuild("knowledge").state_root


def test_deleting_search_index_does_not_delete_knowledge():
    weft, author, _db, _kr = new_weft()
    seed_base(weft, author)

    driver = ProjectionDriver(weft)
    driver.register(KnowledgeProjection())
    know = driver.get("knowledge")

    index = SearchIndex(know)
    hits = index.query("spec")
    assert hits and any(h.type == "document" for h in hits)
    before = {k.id for k in know.items()}

    # Deleting the derived index loses nothing canonical: knowledge is untouched.
    del index
    after = {k.id for k in know.items()}
    assert after == before and before, "knowledge changed when the index was dropped"

    # And the index re-derives byte-identically from the same fold (Law-5 cache).
    assert SearchIndex(know).fingerprint() == SearchIndex(know).fingerprint()


def test_version_bump_triggers_clean_rebuild():
    weft, author, _db, _kr = new_weft()
    seed_base(weft, author)

    driver = ProjectionDriver(weft)
    driver.register(TasksProjection())
    tasks = driver.get("tasks")
    good_view = tasks.view()

    # Corrupt the live projection, then simulate a deployed schema bump. update()
    # must MIGRATE BY REBUILD (not incrementally patch the corrupted view).
    tasks.fold.reset()
    tasks.last_seq = weft.count()               # pretend it was "current" but wrong
    assert tasks.view() != good_view            # corruption is visible
    tasks.version = 2

    result = driver.update()["tasks"]
    assert result.version == 2
    assert tasks.view() == good_view, "version bump did not rebuild from the Weft"
    assert driver.lag("tasks") == 0
