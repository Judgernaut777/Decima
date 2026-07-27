"""Nona N6: the four commands, over the real HTTP surface.

These drive ``Application.dispatch`` end to end — session, CSRF, the command service, the
approval inbox, reauth — because the properties that matter here are properties of the
PATH, not of the functions. Specifically:

  * a GATED promotion has ZERO durable effect until a proof-carrying human decision lands;
  * the approved handler RE-VALIDATES its evidence, because the world moves between the
    submission and the decision (a candidate whose source was swapped in between must not
    be promoted by a yes that was given about the old source);
  * the tiered inbox surface is derived from the FOLD, so a crafted submission cannot make
    a financial promotion render as a harmless notification;
  * refusals are honest: "no executor exists" for a tier that can never run, "not
    available" for a host with no codegen/evaluation seam, and rollback is DEMOTION rather
    than revocation.

Every negative assertion is paired with a positive control (a promotion that really does
lift quarantine, an evaluation that really is eligible), so a harness that silently stopped
working would go red rather than green.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

from decima.kernel import capability, model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import RETRACT, Weft
from decima.services.api import nona_service, routes
from decima.services.api.commands import GATED
from decima.services.api.server import build_application
from decima.services.nona import anchors, candidate, powerbox, promotion, reckoner
from tests.api.conftest import Client

ADD_ONE = "def main(x):\n    return int(x) + 1\n"
CASES: list[dict[str, Any]] = [{"in": {"x": 1}, "out": 2}, {"in": {"x": 41}, "out": 42}]

CONTAINED = {
    "no_new_privs": True,
    "network_denied": True,
    "chroot": True,
    "namespaces": True,
    "matrix_version": 1,
}
UNCONTAINED = {"no_new_privs": True, "network_denied": True, "chroot": False}


def _runner(case: dict[str, Any]) -> dict[str, Any]:
    """A deterministic stand-in for the jailed runner: honest outcomes, no spawning.

    It COMPUTES the ADD_ONE organ's semantics from the case input rather than echoing the
    case's expectation — an echoing runner would make every deterministic case pass by
    construction, which is precisely the vacuous shape this file is trying not to have.
    Adversarial cases report that the jail HELD (not SUCCEEDED + contained), which is what
    containment means for an attack. The production host is a closure over
    ``decima.workers.run_worker``; this deliberately is not, because nothing in the API
    process may ever execute a candidate."""
    if case.get("adversarial"):
        return {"status": "FAILED", "contained": True}
    args = case.get("in") or {}
    if "x" not in args:
        return {"status": "UNKNOWN"}  # unobserved, never a pass
    return {"status": "SUCCEEDED", "output": int(args["x"]) + 1}


@pytest.fixture()
def env():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    app, identity = build_application(db, seed=bytes(32), secure_cookie=True)
    return {"app": app, "identity": identity, "db": db}


@pytest.fixture()
def client(env):
    c = Client(app=env["app"], pairing_secret=env["identity"].pairing_secret)
    c.login()
    return c


@pytest.fixture()
def host():
    """Bind a containing evaluation host for the duration of one test, then restore."""
    previous = nona_service.bind_evaluation_host(
        nona_service.EvaluationHost(run=_runner, containment=dict(CONTAINED))
    )
    yield
    nona_service.bind_evaluation_host(previous)


def _weave(env) -> Weave:
    return Weave.fold(env["app"].weft)


def _of_type(env, type_: str) -> list:
    return [c for c in _weave(env).of_type(type_) if not c.retracted]


def _propose(client, *, tier: str = anchors.PURE, source: str = ADD_ONE) -> str:
    r = client.request(
        "POST",
        "/api/v1/nona/propose",
        body={
            "intent": "add one to an integer",
            "effect_class": tier,
            "source": source,
            "output_schema": {"type": "int"},
        },
    )
    assert r.status == 201, r.json()
    return r.json()["data"]["candidate"]


def _evaluate(client, cand: str, *, cases: list[dict[str, Any]] | None = None) -> dict:
    r = client.request(
        "POST",
        "/api/v1/nona/evaluate",
        body={"candidate": cand, "cases": CASES if cases is None else cases},
    )
    assert r.status == 201, r.json()
    return r.json()["data"]


def _submit_promote(client, cand: str, evaluation: str, **extra: Any) -> dict:
    r = client.request(
        "POST",
        "/api/v1/nona/promote",
        body={"candidate": cand, "evaluation": evaluation, **extra},
    )
    assert r.status == 202, r.json()
    assert r.json()["reason_code"] == "APPROVAL_REQUIRED"
    return r.json()["data"]


def _approve(client, item: str):
    return client.request("POST", "/api/v1/approvals/approve", body={"item": item}, reauth=True)


# ── the surface is registered the way the contract says ──────────────────────
def test_the_two_authority_moving_commands_are_gated_and_the_other_two_are_not():
    assert "PromoteCandidate" in GATED
    assert "RollbackPromotion" in GATED
    # Proposing and evaluating write a proposal and evidence: no outward effect, so gating
    # them would spend the operator's attention on the two steps that cannot hurt them.
    assert "ProposeCapability" not in GATED
    assert "EvaluateCandidate" not in GATED


@pytest.mark.parametrize(
    "path,command",
    [
        ("/api/v1/nona/propose", "ProposeCapability"),
        ("/api/v1/nona/evaluate", "EvaluateCandidate"),
        ("/api/v1/nona/promote", "PromoteCandidate"),
        ("/api/v1/nona/rollback", "RollbackPromotion"),
    ],
)
def test_command_routes_are_write_level_and_never_reauth(path, command):
    route = routes.match("POST", path)
    assert route is not None and route.target == command
    assert route.kind == routes.COMMAND
    # A gated command is submitted at `write` and still cannot bypass the inbox. Giving it
    # a `reauth` route would let it skip the inbox record that makes the decision auditable.
    assert route.auth == routes.WRITE


@pytest.mark.parametrize(
    "path,target",
    [
        ("/api/v1/nona/candidates", "nona_candidates"),
        ("/api/v1/nona/candidates/detail", "nona_candidate"),
        ("/api/v1/nona/decisions", "nona_decisions"),
        ("/api/v1/nona/discover", "nona_discover"),
    ],
)
def test_reader_routes_are_read_level_and_carry_no_id_in_the_path(path, target):
    route = routes.match("GET", path)
    assert route is not None and route.target == target
    assert route.auth == routes.READ and route.kind == routes.READER
    assert "{" not in path


def test_every_nona_command_is_registered_in_the_closed_dispatch_table(env):
    registered = env["app"].commands.commands()
    for name in ("ProposeCapability", "EvaluateCandidate", "PromoteCandidate", "RollbackPromotion"):
        assert name in registered
    # And an unknown neighbour still fails closed (no handler ⇒ nothing runs).
    assert env["app"].commands.execute("PromoteCandidateNow", {}).reason_code == "UNKNOWN_COMMAND"


# ── the store is anchored at construction, or promotion is impossible ─────────
def test_a_freshly_built_store_is_anchored_so_promotion_is_reachable(env):
    promoters = anchors.trusted_promoters(_weave(env))
    assert promoters, "without an anchor every promotion refuses and the whole lane is dead"
    tiers = next(iter(promoters.values()))
    assert set(tiers) == set(anchors.SIGNABLE_TIERS)


def test_the_anchor_is_installed_once_and_not_re_asserted_on_every_open(env):
    weft = env["app"].weft
    before = weft.count()
    again = nona_service.ensure_store_anchor(weft, weft.keyring, env["identity"].app)
    assert again["installed"] is False
    assert again["reason"] == "already anchored and honoured"
    assert weft.count() == before, "an idempotent bootstrap must not append on every restart"


def test_an_anchor_asserted_by_a_non_genesis_principal_is_refused_not_faked():
    """The fold honours a `promoter` anchor only when its author is the store's genesis
    author. Writing one anyway would leave a cell that LOOKS like authority and confers
    nothing — the confusing failure the construction-time install exists to avoid."""
    keyring = Keyring(seed=bytes(32))
    weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), keyring)
    first = keyring.mint("someone_else", "root").id
    later = keyring.mint("app", "app").id
    model.assert_content(weft, first, "note:1", "note", {"text": "the genesis of this store"})

    before = weft.count()
    out = nona_service.ensure_store_anchor(weft, keyring, later)

    assert out["installed"] is False
    assert "genesis" in out["reason"]
    assert weft.count() == before
    assert anchors.trusted_promoters(Weave.fold(weft)) == {}


# ── ProposeCapability: the default refuses rather than inventing ─────────────
def test_proposing_with_no_source_and_no_codegen_refuses_and_writes_nothing(client, env):
    before = env["app"].weft.count()
    r = client.request(
        "POST", "/api/v1/nona/propose", body={"intent": "do a thing", "effect_class": "pure"}
    )
    assert r.status == 501
    assert r.json()["reason_code"] == nona_service.NOT_AVAILABLE
    assert "stub organ" in r.json()["error"]
    assert env["app"].weft.count() == before
    assert _of_type(env, candidate.CANDIDATE) == []


def test_a_bound_codegen_authors_a_candidate_that_is_born_quarantined(client, env):
    calls: list[str] = []

    def codegen(intent: str) -> str:
        calls.append(intent)
        return ADD_ONE

    previous = nona_service.bind_codegen(codegen)
    try:
        r = client.request(
            "POST",
            "/api/v1/nona/propose",
            body={"intent": "add one to an integer", "effect_class": "pure"},
        )
    finally:
        nona_service.bind_codegen(previous)

    assert r.status == 201, r.json()
    assert calls == ["add one to an integer"]
    cell = _weave(env).get(r.json()["data"]["candidate"])
    assert cell is not None
    assert cell.content["lifecycle"] == candidate.QUARANTINED
    assert cell.content["source_is_data"] is True
    assert cell.content["quarantine"]["sandbox_only"] is True
    # Binding a generator confers nothing: no capability exists yet.
    assert _of_type(env, "capability") == []


def test_an_unknown_effect_class_is_refused_at_the_boundary(client, env):
    r = client.request(
        "POST",
        "/api/v1/nona/propose",
        body={"intent": "x", "effect_class": "root_access", "source": ADD_ONE},
    )
    assert r.status == 400 and r.json()["reason_code"] == "BAD_REQUEST"
    assert _of_type(env, candidate.CANDIDATE) == []


# ── EvaluateCandidate: refuse rather than record something weaker ─────────────
def test_evaluating_with_no_host_bound_refuses_and_records_no_result(client, env):
    previous = nona_service.bind_evaluation_host(None)
    try:
        cand = _propose(client)
        before = env["app"].weft.count()
        r = client.request("POST", "/api/v1/nona/evaluate", body={"candidate": cand})
    finally:
        nona_service.bind_evaluation_host(previous)

    assert r.status == 501 and r.json()["reason_code"] == nona_service.NOT_AVAILABLE
    assert env["app"].weft.count() == before
    assert _of_type(env, reckoner.EVALUATION_RESULT) == []


def test_a_host_that_cannot_deliver_containment_refuses_to_evaluate(client, env):
    """Decision 5: a recorded result must mean the same thing on every host, so a host that
    would produce a weaker one produces none."""
    previous = nona_service.bind_evaluation_host(
        nona_service.EvaluationHost(run=_runner, containment=dict(UNCONTAINED))
    )
    try:
        cand = _propose(client)
        r = client.request(
            "POST", "/api/v1/nona/evaluate", body={"candidate": cand, "cases": CASES}
        )
    finally:
        nona_service.bind_evaluation_host(previous)

    assert r.status == 409
    assert r.json()["reason_code"] == nona_service.EVALUATION_REFUSED
    assert "chroot" in r.json()["error"]
    assert _of_type(env, reckoner.EVALUATION_RESULT) == []


def test_an_evaluation_records_integers_evidence_and_an_eligible_verdict(client, env, host):
    cand = _propose(client)
    data = _evaluate(client, cand)

    assert data["promote_eligible"] is True, data["verdict_reason"]
    metrics = data["evidence"]["metrics"]
    assert metrics["deterministic_cases"] == 2 and metrics["deterministic_pass"] == 2
    # The adversarial cases came from the ROOT-side baseline, never from the candidate.
    assert metrics["hostile_cases"] == len(nona_service.BASELINE_ADVERSARIAL)
    assert metrics["hostile_contained"] == metrics["hostile_cases"]
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in metrics.values())
    # A model's opinion is recorded and powerless, and the gate never saw it.
    assert data["evidence"]["model_judge"]["authority"] is False
    suite = _weave(env).get(data["suite"])
    assert suite is not None
    assert any(c.get("adversarial") for c in suite.content["cases"])
    assert all(c.get("origin") == reckoner.BASELINE for c in suite.content["cases"]), (
        "Decision 6: the candidate may not author the attacks it is judged by"
    )


def test_a_failing_candidate_is_recorded_as_evidence_and_blocks_promotion(client, env, host):
    cand = _propose(client)
    data = _evaluate(client, cand, cases=[{"in": {"x": 1}, "out": 999}])

    assert data["promote_eligible"] is False
    assert "deterministic" in data["verdict_reason"]
    # The refusal IS the evidence: a result exists, and promotion later cites it and fails.
    assert len(_of_type(env, reckoner.EVALUATION_RESULT)) == 1

    item = _submit_promote(client, cand, data["evaluation"])["item"]
    approved = _approve(client, item)
    assert approved.json()["data"]["enacted"] is False
    assert _of_type(env, promotion.PROMOTION) == []


def test_a_rug_pull_is_a_high_finding_even_when_every_case_passes(client, env, host):
    """Cases show what the code DID; imports show what it CAN do. A `pure` candidate that
    imports `socket` passes every case here and is still refused."""
    cand = _propose(client, source="import socket\n\n\ndef main(x):\n    return int(x) + 1\n")
    data = _evaluate(client, cand)

    assert data["promote_eligible"] is False
    assert "high security finding" in data["verdict_reason"]
    rules = {f["rule"] for f in data["evidence"]["findings"]}
    assert "scan.rug_pull" in rules


# ── PromoteCandidate: nothing happens until a human decision lands ───────────
def test_submitting_a_promotion_has_no_durable_effect_beyond_the_inbox_item(client, env, host):
    cand = _propose(client)
    evaluation = _evaluate(client, cand)["evaluation"]

    data = _submit_promote(client, cand, evaluation)

    assert _of_type(env, promotion.PROMOTION) == []
    assert _of_type(env, "capability") == [], "no organ grant is minted by merely asking"
    item = _weave(env).get(data["item"])
    assert item is not None and item.content["status"] == "pending"
    # The args copied verbatim into signed content are IDS ONLY — never the source.
    assert set(item.content["deferred_args"]) == {"candidate", "evaluation"}
    assert ADD_ONE not in str(item.content)


def test_approving_the_promotion_lifts_quarantine_through_the_recorded_decision(client, env, host):
    cand = _propose(client)
    evaluation = _evaluate(client, cand)["evaluation"]
    item = _submit_promote(client, cand, evaluation)["item"]

    approved = _approve(client, item)

    assert approved.status == 200, approved.json()
    inner = approved.json()["data"]["inner"]
    assert approved.json()["data"]["enacted"] is True
    cap = _weave(env).get(inner["capability"])
    assert cap is not None
    assert cap.content["quarantined"] is False
    assert "sandbox_only" not in cap.content["caveats"]
    assert inner["quarantined"] is False
    assert inner["signer"] in anchors.trusted_promoters(_weave(env))
    # The promotion cites its evidence, so "why is this live?" is a graph walk.
    promo = _weave(env).get(inner["promotion"])
    assert promo is not None
    assert promo.content["evaluation_result"] == evaluation
    assert promo.content["candidate"] == cand
    # An ungated pure organ costs NO further prompts: nothing was written to clear a gate
    # that does not exist.
    assert inner["prompt_plan"]["approval_scope"] == powerbox.SCOPE_NONE
    assert inner["capability_approval"] is None
    assert capability.capability_approvals(_weave(env)) == set()


def test_a_gated_organ_gets_exactly_one_capability_scoped_approval(client, env, host):
    """Design §5.8 point 3: one prompt per organ, not one per call. The operator asks for
    the organ to be born gated; the ONE durable approval is written at promotion time,
    authored by the human whose recorded decision released it."""
    cand = _propose(client)
    evaluation = _evaluate(client, cand)["evaluation"]
    item = _submit_promote(client, cand, evaluation, requires_approval=True)["item"]

    inner = _approve(client, item).json()["data"]["inner"]

    cap = _weave(env).get(inner["capability"])
    assert cap is not None
    # Morta survives promotion: the lift strips sandbox_only and nothing else.
    assert cap.content["caveats"]["requires_approval"] is True
    assert cap.content["quarantined"] is False
    assert inner["prompt_plan"]["approval_scope"] == powerbox.SCOPE_CAPABILITY
    assert inner["prompt_plan"]["prompts_per_call"] == 0
    approval = _weave(env).get(inner["capability_approval"])
    assert approval is not None
    assert approval.content["scope"] == "capability"
    assert approval.content["approver"] == env["identity"].human
    assert capability.capability_approvals(_weave(env)) == {inner["capability"]}
    # Exactly one, and it is content-addressed on the capability so it cannot multiply.
    assert len(_of_type(env, capability.APPROVAL)) == 1
    assert inner["capability_approval"] == capability.approval_id(inner["capability"], None)


def test_the_approved_handler_re_validates_the_digest_it_was_approved_against(client, env, host):
    """The world moves between the submission and the decision. A candidate whose source was
    swapped after the operator read it must not be promoted by that yes."""
    cand = _propose(client)
    evaluation = _evaluate(client, cand)["evaluation"]
    item = _submit_promote(client, cand, evaluation)["item"]

    # Swap the source, keeping the RECORDED digest — the rug-pull an ASSERT makes possible.
    # N7 guards only the four cells authority is READ from; `candidate` is not one of them, so
    # this rewrite is still permitted and re-validating the digest is still the real defence.
    cell = _weave(env).get(cand)
    assert cell is not None
    model.assert_content(
        env["app"].weft,
        env["identity"].app,
        cand,
        candidate.CANDIDATE,
        {**cell.content, "source": "def main(x):\n    return 0\n"},
    )

    approved = _approve(client, item)

    assert approved.json()["data"]["enacted"] is False
    assert approved.json()["reason_code"] == nona_service.EVIDENCE_STALE
    assert _of_type(env, promotion.PROMOTION) == []
    assert _of_type(env, "capability") == [], "no organ is built over unevaluated source"


def test_a_candidate_whose_source_was_swapped_cannot_even_be_evaluated(client, env, host):
    """The digest binding is checked at EVERY boundary, not once. Evaluation is the boundary
    with no later guard behind it: if a tampered candidate could be evaluated, the resulting
    result would be evidence about code nobody ran."""
    cand = _propose(client)
    cell = _weave(env).get(cand)
    assert cell is not None
    model.assert_content(
        env["app"].weft,
        env["identity"].app,
        cand,
        candidate.CANDIDATE,
        {**cell.content, "source": "def main(x):\n    return 0\n"},
    )

    r = client.request("POST", "/api/v1/nona/evaluate", body={"candidate": cand, "cases": CASES})

    assert r.status == 409 and r.json()["reason_code"] == nona_service.EVIDENCE_STALE
    assert _of_type(env, reckoner.EVALUATION_RESULT) == []


def test_a_promotion_cannot_cite_evidence_recorded_against_superseded_source(client, env, host):
    """The nastier drift: the candidate is re-asserted with new source AND a correctly
    updated digest, so the cell is internally consistent and every self-check passes. Only
    the CROSS-check — evaluation digest vs candidate digest — notices that the evidence is
    about different code. Without it, an operator's yes about version 1 promotes version 2."""
    cand = _propose(client)
    stale = _evaluate(client, cand)["evaluation"]
    item = _submit_promote(client, cand, stale)["item"]

    swapped = "def main(x):\n    return int(x) + 2\n"
    cell = _weave(env).get(cand)
    assert cell is not None
    model.assert_content(
        env["app"].weft,
        env["identity"].app,
        cand,
        candidate.CANDIDATE,
        {
            **cell.content,
            "source": swapped,
            "implementation_digest": candidate.implementation_digest(swapped),
        },
    )

    approved = _approve(client, item)

    assert approved.json()["reason_code"] == nona_service.EVIDENCE_STALE
    assert "not the code that was evaluated" in approved.json()["error"]
    assert _of_type(env, promotion.PROMOTION) == []
    assert _of_type(env, "capability") == []


def test_a_promotion_cannot_cite_an_evaluation_of_another_candidate(client, env, host):
    first = _propose(client)
    other = _propose(client, source="def main(x):\n    return int(x) + 2\n")
    evaluation = _evaluate(client, other)["evaluation"]

    item = _submit_promote(client, first, evaluation)["item"]
    approved = _approve(client, item)

    assert approved.json()["reason_code"] == nona_service.EVIDENCE_STALE
    assert _of_type(env, promotion.PROMOTION) == []


def test_a_network_tier_is_refused_for_having_no_executor_not_for_lacking_approval(
    client, env, host
):
    """Design Decision 2: prompting for something that can never run teaches people to
    click yes. The refusal names the ABSENCE of an executor."""
    cand = _propose(client, tier=anchors.NETWORK, source="def main(u):\n    return str(u)\n")
    evaluation = _evaluate(client, cand)
    assert evaluation["promote_eligible"] is True  # it evaluated fine; it just cannot run

    item = _submit_promote(client, cand, evaluation["evaluation"])["item"]
    approved = _approve(client, item)

    assert approved.json()["reason_code"] == nona_service.PROMOTION_REFUSED
    # The SENTENCE survives the approval envelope, not just the code: a UI must be able to
    # say "no executor exists" rather than inventing "requires approval".
    assert "NO EXECUTOR" in approved.json()["error"]
    assert _of_type(env, promotion.PROMOTION) == []
    rows = client.request("GET", "/api/v1/nona/candidates").json()["items"]
    row = next(r for r in rows if r["candidate"] == cand)
    assert row["executable"] is False and "no executor" in row["note"]


def test_a_tier_with_no_anchored_promoter_is_refused_as_unanchored_authority(client, env, host):
    """`workspace_write` needs a HUMAN-signed promotion and no principal is anchored for it
    (`anchors.SIGNABLE_TIERS` deliberately excludes it), so the refusal is about missing
    ANCHORED AUTHORITY — data on the log — rather than a missing click."""
    cand = _propose(client, tier="workspace_write")
    evaluation = _evaluate(client, cand)["evaluation"]

    item = _submit_promote(client, cand, evaluation)["item"]
    approved = _approve(client, item)

    assert approved.json()["reason_code"] == nona_service.PROMOTION_REFUSED
    assert _of_type(env, promotion.PROMOTION) == []
    assert capability.capability_approvals(_weave(env)) == set(), (
        "a floored tier must never acquire a blanket approval, not even a stray one"
    )


def test_a_denied_promotion_never_runs_and_cannot_then_be_approved(client, env, host):
    cand = _propose(client)
    evaluation = _evaluate(client, cand)["evaluation"]
    item = _submit_promote(client, cand, evaluation)["item"]

    denied = client.request("POST", "/api/v1/approvals/deny", body={"item": item, "reason": "no"})
    assert denied.status == 200
    assert _of_type(env, promotion.PROMOTION) == []

    again = _approve(client, item)
    assert again.status == 409 and again.json()["reason_code"] == "ALREADY_DECIDED"
    assert _of_type(env, promotion.PROMOTION) == []


# ── RollbackPromotion: demotion, and the incident that says so ───────────────
def _promote(client, env, host_unused=None) -> dict:
    cand = _propose(client)
    evaluation = _evaluate(client, cand)["evaluation"]
    item = _submit_promote(client, cand, evaluation)["item"]
    return _approve(client, item).json()["data"]["inner"]


def test_submitting_a_rollback_changes_nothing_until_it_is_approved(client, env, host):
    live = _promote(client, env)
    r = client.request("POST", "/api/v1/nona/rollback", body={"promotion": live["promotion"]})
    assert r.status == 202 and r.json()["reason_code"] == "APPROVAL_REQUIRED"

    cap = _weave(env).get(live["capability"])
    assert cap is not None and cap.content["quarantined"] is False
    assert _of_type(env, nona_service.INCIDENT) == []


def test_an_approved_rollback_re_quarantines_without_revoking_anything(client, env, host):
    live = _promote(client, env)
    item = client.request(
        "POST", "/api/v1/nona/rollback", body={"promotion": live["promotion"]}
    ).json()["data"]["item"]

    inner = _approve(client, item).json()["data"]["inner"]

    weave = _weave(env)
    cap = weave.get(live["capability"])
    assert cap is not None
    assert cap.content["quarantined"] is True  # derived from the promotion's liveness
    assert cap.content["caveats"]["sandbox_only"] is True
    assert cap.retracted is False, "rollback is DEMOTION: the capability survives"
    assert inner["revoked"] is False
    promo = weave.get(live["promotion"])
    assert promo is not None and promo.retracted is True
    # R5: the rollback says on the log that it contains and compensates, and its to_state
    # distinguishes it from the monitor's auto-REVOKE incident without parsing prose.
    incident = weave.get(inner["incident"])
    assert incident is not None
    assert incident.content["to_state"] == "QUARANTINED"
    assert incident.content["reason"] == nona_service.ROLLBACK_REASON
    assert "never claims to undo" in incident.content["note"]


def test_rolling_back_twice_fails_closed(client, env, host):
    live = _promote(client, env)
    item = client.request(
        "POST", "/api/v1/nona/rollback", body={"promotion": live["promotion"]}
    ).json()["data"]["item"]
    assert _approve(client, item).json()["data"]["enacted"] is True

    second = client.request(
        "POST", "/api/v1/nona/rollback", body={"promotion": live["promotion"]}
    ).json()["data"]["item"]
    approved = _approve(client, second)
    assert approved.json()["reason_code"] == nona_service.ALREADY_ROLLED_BACK


def test_a_gated_submission_carries_ids_only_and_no_free_text(client, env, host):
    """`execute` defers a gated command BEFORE its handler runs, so anything in `args` is
    signed onto the log unvalidated. The contract is therefore ids only: the rollback takes
    no operator prose, and its rationale is a fixed sentence the handler supplies."""
    live = _promote(client, env)
    item_id = client.request(
        "POST", "/api/v1/nona/rollback", body={"promotion": live["promotion"]}
    ).json()["data"]["item"]

    item = _weave(env).get(item_id)
    assert item is not None
    assert set(item.content["deferred_args"]) == {"promotion"}
    inner = _approve(client, item_id).json()["data"]["inner"]
    incident = _weave(env).get(inner["incident"])
    assert incident is not None
    assert incident.content["reason"] == nona_service.ROLLBACK_REASON


# ── the readers: derived from the fold, and untrusted where it matters ───────
def test_the_detail_reader_hands_back_the_source_as_untrusted_data(client, env, host):
    cand = _propose(client)
    body = client.request("GET", "/api/v1/nona/candidates/detail", query={"id": cand}).json()

    assert body["source"] == ADD_ONE
    assert body["source_is_data"] is True
    assert body["instruction_eligible"] is False
    assert body["trust"] == "untrusted"
    assert body["tier"] == anchors.PURE
    assert body["signer_policy"] == promotion.AUTOMATED
    assert body["prompt_plan"]["surface"] == powerbox.NOTIFICATION


def test_the_detail_reader_reports_a_rolled_back_promotion_as_not_live(client, env, host):
    """The audit surface must contradict nothing: a withdrawn promotion reads not-live here.

    The detail reader is the ONLY place the operator sees the promotion records themselves,
    and the Shell renders each record's state straight off this payload. So the record's key
    set is part of the contract, pinned here on purpose: rename or drop ``live`` and the
    screen's pill loses its condition and silently reports every promotion — withdrawn ones
    included — as still in force. ``tests/shell/test_nona_screen.py`` holds the other half,
    checking that the screen reads only fields that appear below.
    """
    cand = _propose(client)
    evaluation = _evaluate(client, cand)["evaluation"]
    item = _submit_promote(client, cand, evaluation)["item"]
    live = _approve(client, item).json()["data"]["inner"]

    detail = client.request("GET", "/api/v1/nona/candidates/detail", query={"id": cand}).json()
    (before,) = detail["promotions"]
    assert before["cell"] == live["promotion"]
    assert before["live"] is True, "positive control: the reader reported it live first"
    assert set(before) == {"cell", "signer", "tier", "evaluation_result", "live"}

    rollback_item = client.request(
        "POST", "/api/v1/nona/rollback", body={"promotion": live["promotion"]}
    ).json()["data"]["item"]
    assert _approve(client, rollback_item).json()["data"]["enacted"] is True

    after = client.request("GET", "/api/v1/nona/candidates/detail", query={"id": cand}).json()
    (record,) = after["promotions"]
    assert record["cell"] == live["promotion"], "the record survives — demotion is not erasure"
    assert record["live"] is False
    assert set(record) == set(before), "the record's shape must not change under rollback"
    # And the quarantine the same payload reports agrees with it, on the same read.
    assert after["quarantined"] is True


def test_an_unknown_candidate_detail_is_a_404_not_an_empty_shell(client):
    r = client.request("GET", "/api/v1/nona/candidates/detail", query={"id": "candidate:nope"})
    assert r.status == 404 and r.json()["reason_code"] == "NOT_FOUND"


def test_the_decisions_reader_derives_the_tier_from_the_fold_not_from_the_submission(
    client, env, host
):
    """A crafted submission must not be able to dress a high-blast-radius promotion up as a
    harmless notification. Every field beyond the two ids is re-derived from the Cells the
    item names."""
    cand = _propose(client, tier="financial")
    evaluation = _evaluate(client, cand)["evaluation"]
    # The submission LIES about its tier (and the lie is faithfully signed into the item).
    r = client.request(
        "POST",
        "/api/v1/nona/promote",
        body={
            "candidate": cand,
            "evaluation": evaluation,
            "tier": "pure",
            "surface": "notification",
        },
    )
    assert r.status == 202
    item_id = r.json()["data"]["item"]
    item = _weave(env).get(item_id)
    assert item is not None and item.content["deferred_args"]["tier"] == "pure"

    rows = client.request("GET", "/api/v1/nona/decisions").json()["items"]
    row = next(r for r in rows if r["item"] == item_id)

    assert row["tier"] == "financial"
    assert row["surface"] == powerbox.EXPLICIT
    assert row["evidence_inline"] is True
    assert row["evidence"]["promote_eligible"] is True
    assert row["prompt_plan"]["invocation_approvals"] is True
    assert row["status"] == "pending"


def test_a_decision_item_whose_candidate_vanished_fails_closed_in_the_reader(client, env, host):
    cand = _propose(client)
    evaluation = _evaluate(client, cand)["evaluation"]
    item_id = _submit_promote(client, cand, evaluation)["item"]
    env["app"].weft.append(
        env["identity"].app, RETRACT, {"cell": cand, "mode": "WITHDRAW", "reason": "withdrawn"}
    )

    rows = client.request("GET", "/api/v1/nona/decisions").json()["items"]
    row = next(r for r in rows if r["item"] == item_id)
    assert row["resolves"] is False
    assert row["surface"] == powerbox.EXPLICIT


def test_the_candidates_reader_reports_the_promotion_state_it_gates_on(client, env, host):
    live = _promote(client, env)
    rows = client.request("GET", "/api/v1/nona/candidates").json()
    row = next(r for r in rows["items"] if r["capability"] == live["capability"])
    assert row["quarantined"] is False
    assert row["live_promotions"] == [live["promotion"]]
    assert row["eligible_evaluation"] in row["evaluations"]
    assert rows["signable_tiers"] == list(anchors.SIGNABLE_TIERS)


def test_the_discovery_reader_ranks_the_catalogue_and_writes_nothing(client, env, host):
    live = _promote(client, env)
    before = env["app"].weft.count()

    found = client.request(
        "GET", "/api/v1/nona/discover", query={"goal": "add one to an integer"}
    ).json()

    assert found["action"] == "use"
    assert found["capability"] == live["capability"]
    assert found["grant_required"] is True
    assert isinstance(found["threshold"], int)
    assert env["app"].weft.count() == before

    missed = client.request(
        "GET", "/api/v1/nona/discover", query={"goal": "wire money to an offshore account"}
    ).json()
    assert missed["action"] == "forge"
    assert missed["next_step"] == "ProposeCapability"
    assert env["app"].weft.count() == before


def test_the_discovery_reader_refuses_a_non_integer_threshold(client):
    r = client.request(
        "GET", "/api/v1/nona/discover", query={"goal": "anything", "threshold": "1.5"}
    )
    assert r.status == 400 and r.json()["reason_code"] == "BAD_REQUEST"


def test_a_reader_needs_a_session_and_a_command_needs_csrf(client, env, host):
    anonymous = env["app"].dispatch("GET", "/api/v1/nona/candidates", headers={})
    assert anonymous.status == 401
    no_csrf = client.request(
        "POST",
        "/api/v1/nona/propose",
        body={"intent": "x", "effect_class": "pure", "source": ADD_ONE},
        csrf=False,
    )
    assert no_csrf.status == 403
    assert _of_type(env, candidate.CANDIDATE) == []
