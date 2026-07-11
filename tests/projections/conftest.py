"""Shared fixtures: a Weft seeded with plans/tasks/agents/notes/approvals.

Everything is asserted through the public model/runtime seams so the projections
fold exactly what a real system would produce. No projection appends here — the
Weft is the sole canonical store.
"""

from __future__ import annotations

import os
import tempfile

from decima.kernel.crypto import Keyring
from decima.kernel.inbox import DECISION, ITEM
from decima.kernel.model import assert_content, assert_edge
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.runtime.cells import StepStatus


def new_weft() -> tuple[Weft, str, str, Keyring]:
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=bytes(32))
    author = kr.mint("decima", "root").id
    return Weft(db, kr), author, db, kr


def seed_base(weft: Weft, author: str) -> dict:
    """A plan A→{B,C}→D, two agents (parent/child), two notes, one document, and a
    pending approval. Returns the ids for assertions."""
    parent = cells.create_agent(weft, author, objective="lead", principal=author,
                                token_budget=1000, monetary_budget=500, deadline=99)
    child = cells.create_agent(weft, author, objective="assist", principal=author,
                               parent_agent_id=parent, token_budget=200)

    plan = cells.create_plan(weft, author, objective="ship it", creator_principal=author)
    a = cells.create_step(weft, author, plan_id=plan, description="A",
                          assigned_agent_id=parent, deadline=10)
    b = cells.create_step(weft, author, plan_id=plan, description="B", dependency_ids=[a])
    c = cells.create_step(weft, author, plan_id=plan, description="C", dependency_ids=[a])
    d = cells.create_step(weft, author, plan_id=plan, description="D",
                          dependency_ids=[b, c])

    # A note (trusted) + an untrusted note + a document, with a link + provenance.
    note1 = "note:alpha"
    assert_content(weft, author, note1, "note",
                   {"text": "the roadmap for the alpha release", "instruction_eligible": True})
    note2 = "note:beta"
    assert_content(weft, author, note2, "note",
                   {"text": "beta feedback from an untrusted source"})
    doc1 = "doc:spec"
    assert_content(weft, author, doc1, "document",
                   {"title": "spec", "text": "the canonical spec document"})
    assert_edge(weft, author, note1, "references", doc1)

    # A pending Morta approval request (no decision yet).
    appr = "inbox_item:publish"
    assert_content(weft, author, appr, ITEM,
                   {"capability": "cap:publish", "description": "publish the release",
                    "instruction_eligible": False})

    return {"parent": parent, "child": child, "plan": plan,
            "a": a, "b": b, "c": c, "d": d,
            "note1": note1, "note2": note2, "doc1": doc1, "approval": appr}


def advance(weft: Weft, author: str, ids: dict) -> None:
    """More history on top of the base: A succeeds, B starts, a decision approves the
    approval, and a fresh note lands — the tail an incremental update must fold."""
    cells.set_status(weft, author, _fold_get(weft, ids["a"]), StepStatus.SUCCEEDED)
    cells.set_status(weft, author, _fold_get(weft, ids["b"]), StepStatus.RUNNING)
    did = "inbox_decision:publish"
    assert_content(weft, author, did, DECISION,
                   {"item": ids["approval"], "decision": "approved",
                    "approver": author, "ran": True})
    assert_edge(weft, author, did, "decides", ids["approval"])
    assert_content(weft, author, "note:gamma", "note",
                   {"text": "a late note about gamma"})


def _fold_get(weft: Weft, cid: str):
    from decima.kernel.weave import Weave
    return Weave.fold(weft).get(cid)
