"""An approval cannot be minted without the approver's possession proof (P1.2).

The product enacts a human approval at ``CommandService._approve_invocation`` (the API
``POST /api/v1/approvals/approve`` route, level REAUTH). Recording that decision — and
enacting the deferred effect — now REQUIRES a possession proof: the human principal's
Ed25519 signature bound to THIS exact approval item, verified at the command boundary
before anything is written. This mirrors ``capability.verify_proof`` for invokes.

The HTTP host mints that proof only after a fresh reauth proved a live human (covered by
tests/api/test_approval_gate.py). These tests attack the boundary DIRECTLY, in-process,
bypassing the reauth HTTP layer, to pin what the precondition enforces at the command
service itself: an approval with NO proof, a FORGED signature, or a proof bound to a
DIFFERENT item is refused (operation-binding + anti-replay + signature verification). In a
split-custody deployment (the human key held outside the app custodian, per SECURITY.md)
this makes the gate unforgeable; in the dev single-seed profile it still closes the
"record an approval with no approver proof at all" hole and establishes the enforcement
seam — it does not, on its own, stop a caller that can also sign as the human (that is the
single-master-seed exposure P1.1 documents, not this gate).
"""

from __future__ import annotations

from decima.kernel.weave import Weave


def _has_export(app) -> bool:
    """True once an ``artifact_export`` receipt exists — i.e. the gated effect ran."""
    return bool(Weave.fold(app.weft).of_type("artifact_export"))


def _enqueue_export(client) -> str:
    """Import an artifact then submit the gated export; return the pending item id."""
    art_id = client.request(
        "POST", "/api/v1/artifacts/import", body={"name": "r", "body": "x"}
    ).json()["data"]["id"]
    submitted = client.request("POST", "/api/v1/artifacts/export", body={"id": art_id})
    assert submitted.json()["reason_code"] == "APPROVAL_REQUIRED"
    return submitted.json()["data"]["item"]


def test_direct_approval_without_proof_is_refused(client, env):
    """Calling the approve command in-process with NO possession proof fails closed:
    no decision is recorded and the deferred effect never runs."""
    app = env["app"]
    item_id = _enqueue_export(client)

    res = app.commands.execute("ApproveInvocation", {"item": item_id})

    assert res.ok is False
    assert res.reason_code == "APPROVAL_PROOF_REQUIRED"
    assert res.http_status == 401
    assert not _has_export(app)  # fail closed — effect not run


def test_valid_possession_proof_enacts_approval(client, env):
    """A proof signed by the human principal and bound to this item enacts the effect —
    the boundary verifies a REAL signature, not merely the presence of a field."""
    app = env["app"]
    item_id = _enqueue_export(client)

    proof = app.commands.mint_approval_proof(item_id)
    res = app.commands.execute("ApproveInvocation", {"item": item_id, "approval_proof": proof})

    assert res.ok is True, res
    assert res.data["enacted"] is True
    assert _has_export(app)


def test_proof_bound_to_another_item_is_refused(client, env):
    """A valid proof minted for item A does not approve item B: the bind pins the exact
    item, so a captured proof can never be replayed against a different approval."""
    app = env["app"]
    item_a = _enqueue_export(client)
    item_b = _enqueue_export(client)

    proof_for_a = app.commands.mint_approval_proof(item_a)
    res = app.commands.execute("ApproveInvocation", {"item": item_b, "approval_proof": proof_for_a})

    assert res.ok is False
    assert res.reason_code == "APPROVAL_PROOF_REQUIRED"
    assert not _has_export(app)  # neither item was enacted


def test_forged_signature_is_refused(client, env):
    """A proof with the right approver + bind but a bogus signature fails closed — the
    signature must verify under the human's public key."""
    app = env["app"]
    item_id = _enqueue_export(client)

    forged = {
        "approver": app.identity.human,
        "approval_bind": app.commands.approval_bind(item_id),
        "approver_sig": "00" * 64,
    }
    res = app.commands.execute("ApproveInvocation", {"item": item_id, "approval_proof": forged})

    assert res.ok is False
    assert res.reason_code == "APPROVAL_PROOF_REQUIRED"
    assert not _has_export(app)
