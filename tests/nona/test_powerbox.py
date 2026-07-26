"""Nona N6: the capability broker — least authority as the default path.

The broker's whole value is that it can never widen authority while making narrow grants
cheap to get. So these tests are arranged around the four ways it could betray that:

  * it could issue something WIDER than it holds (`attenuate` writes non-numeric keys
    verbatim, so nothing but the structural proof stops this);
  * it could let a requester's proposed scope talk the policy out of routing an approval
    (which is what happens if the Morta/tier floor is merged AFTER the decision);
  * it could issue from a source it does not hold, producing grants that look perfect and
    are denied `DELEGATION_INVALID` on every use;
  * it could keep working after the authority it descends from was taken back.

Every refusal test here is paired with a POSITIVE CONTROL — a brokered grant that really
does authorize an invocation — because without one, "the grant was denied" is exactly what
a broken harness produces, and the whole file would pass while proving nothing.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

from decima.kernel import capability, model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.services.nona import anchors, candidate, executor, powerbox, promotion, reckoner
from decima.services.nona.reckoner import Metrics

PURE_SOURCE = "def main(x):\n    return int(x) + 1\n"


class World:
    """A store with a root, a broker, and an agent that asks for things."""

    def __init__(self) -> None:
        self.keyring = Keyring(seed=bytes(32))
        self.weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), self.keyring)
        self.root = self.keyring.mint("root", "root").id
        self.broker = self.keyring.mint("powerbox", "broker").id
        self.reckoner = self.keyring.mint(anchors.RECKONER_NAME, "reckoner").id
        self.holder = self.keyring.mint("holder", "operator").id
        anchors.install_trust_anchors(self.weft, self.root, reckoner=self.reckoner)
        self.agent = cells.create_agent(
            self.weft,
            self.root,
            objective="ask for the narrowest thing that works",
            principal=self.holder,
        )

    def weave(self) -> Weave:
        return Weave.fold(self.weft)

    def agent_cell(self) -> Cell:
        cell = self.weave().get(self.agent)
        assert cell is not None
        return cell

    def capabilities(self) -> set[str]:
        return {
            cid
            for cid, c in self.weave().cells.items()
            if c.type == "capability" and not c.retracted
        }

    def authorize(self, grant: str) -> tuple[bool, str, str]:
        """The real ocap check, exactly as the kernel runs it before an INVOKE."""
        weave = self.weave()
        return capability.authorize_detail(weave, self.agent_cell(), grant, {}, self.holder)


def _echo_source(world: World, caveats: dict[str, Any] | None = None) -> str:
    return powerbox.install_broker_source(
        world.weft,
        world.root,
        broker=world.broker,
        name="echo",
        effect="echo",
        caveats=caveats,
    )


def _promoted_organ(world: World, *, grantee: str | None = None) -> tuple[str, str]:
    """A real promoted `pure` organ, held by `grantee` (default: the broker).

    Built through the shipping path — candidate → capability → evaluation → promotion — so
    the delegation and quarantine tests below are about the actual machinery rather than a
    hand-written cell that happens to have the right keys.
    """
    proposed = candidate.propose_candidate(
        world.weft,
        world.reckoner,
        intent="add one to an integer",
        declared_effect_class=anchors.PURE,
        source=PURE_SOURCE,
        output_schema={"type": "int"},
    )
    built = executor.build_capability(
        world.weft,
        world.weave(),
        world.reckoner,
        candidate=proposed["cell"],
        tier=anchors.PURE,
        name="add_one",
        grantee=grantee if grantee is not None else world.broker,
        # The Reckoner mints the organ grant and therefore IS its granter (N7: a grant is
        # asserted only by the principal that issues it — root does not lend its name).
        granter=world.reckoner,
    )
    verdict = reckoner.gate(
        Metrics(
            deterministic_cases=2,
            deterministic_pass=2,
            hostile_cases=1,
            hostile_contained=1,
        )
    )
    evaluation = reckoner.record_result(
        world.weft,
        world.reckoner,
        candidate_cell=proposed["cell"],
        suite_cell="suite:s",
        implementation_digest=proposed["implementation_digest"],
        verdict=verdict,
        containment={
            "no_new_privs": True,
            "network_denied": True,
            "chroot": True,
            "namespaces": True,
            "matrix_version": 1,
        },
    )
    promoted = promotion.promote(
        world.weft,
        world.weave(),
        world.reckoner,
        capability=built["capability"],
        candidate=proposed["cell"],
        evaluation=evaluation,
        tier=anchors.PURE,
    )
    cap = world.weave().get(built["capability"])
    assert cap is not None and not cap.content.get("quarantined"), "the organ must be live"
    return str(built["capability"]), str(promoted["promotion"])


# ── the positive control: a brokered grant really does authorize ──────────────
def test_a_brokered_grant_actually_authorizes_an_invocation():
    """Without this, every "denied" assertion below could be a broken harness."""
    world = World()
    _echo_source(world)

    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="echo",
        purpose="say the thing back",
    )

    assert "denied" not in out, out
    grant = out["granted"]
    assert out["needs_approval"] is False  # `echo` is low risk; no human is spent on it
    allowed, why, code = world.authorize(grant)
    assert allowed, (why, code)
    assert code == capability.DenialCode.OK
    # And the grant is in the requester's envelope — the broker handed it over, and holding
    # it is what authority means (Law 2: no ambient authority).
    assert grant in world.agent_cell().content["envelope"]


def test_the_grant_the_broker_issues_is_downhill_of_the_source_it_holds():
    world = World()
    source = _echo_source(world, {"budget": 100})
    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="echo",
        purpose="say the thing back",
        scope={"budget": 5},
        duration=32,
    )
    child = world.weave().get(out["granted"])
    parent = world.weave().get(source)
    assert child is not None and parent is not None
    assert child.content["parent"] == source
    assert child.content["granter"] == world.broker
    assert child.content["grantee"] == world.holder
    assert child.content["caveats"]["budget"] == 5  # narrowed, not inherited
    # `duration` became an absolute bound on the LOGICAL frontier — an int, never a clock.
    assert isinstance(child.content["caveats"]["expires_at"], int)
    ok, why = capability.attenuation_valid(child.content, parent.content)
    assert ok, why


# ── it cannot widen: the structural proof, not the good intentions ────────────
def test_a_hand_widened_child_is_refused_structurally_and_no_grant_is_written():
    """`attenuate` writes non-numeric `stricter` keys VERBATIM, so a caller CAN build a
    child that drops the parent's Morta gate. Only `attenuation_valid` catches it, and
    only if the broker runs it before issuing. It does — and nothing is written."""
    world = World()
    source = _echo_source(world, {"requires_approval": True, "budget": 10})
    base = world.weave().get(source)
    assert base is not None

    widened = capability.attenuate(
        base.content,
        {"requires_approval": False},  # dropping the floor: not downhill
        source,
        grantee=world.holder,
        granter=world.broker,
    )
    assert widened["caveats"]["requires_approval"] is False, "attenuate really does write it"

    before = world.capabilities()
    with pytest.raises(powerbox.BrokerRefused, match="attenuation invalid"):
        powerbox.issue_grant(
            world.weft,
            world.weave(),
            broker=world.broker,
            source=source,
            child=widened,
            requester_cell=world.agent,
            request="cap_request:test",
        )
    assert world.capabilities() == before  # no grant cell, not even a refused one
    assert world.agent_cell().content.get("envelope", []) == []


def test_a_hand_widened_numeric_bound_is_refused_too():
    world = World()
    source = _echo_source(world, {"budget": 10})
    base = world.weave().get(source)
    assert base is not None
    child = capability.attenuate(
        base.content, {"budget": 5}, source, grantee=world.holder, granter=world.broker
    )
    child["caveats"]["budget"] = 5000  # hand-edited AFTER the min() clamp

    before = world.capabilities()
    with pytest.raises(powerbox.BrokerRefused, match="attenuation invalid"):
        powerbox.issue_grant(
            world.weft,
            world.weave(),
            broker=world.broker,
            source=source,
            child=child,
            requester_cell=world.agent,
            request="cap_request:test",
        )
    assert world.capabilities() == before


def test_a_request_has_no_vocabulary_for_dropping_a_constraint():
    """The narrowing function copies boolean constraints only when TRUTHY, so
    `{"requires_approval": False}` in a request is not "please drop the gate" — it is
    nothing at all, and the floor survives into the issued child."""
    world = World()
    _echo_source(world, {"requires_approval": True})
    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="echo",
        purpose="please ungate me",
        scope={"requires_approval": False, "sandbox_only": False},
    )
    child = world.weave().get(out["granted"])
    assert child is not None
    assert child.content["caveats"]["requires_approval"] is True


def test_a_float_scope_bound_is_refused_and_records_the_denial():
    world = World()
    _echo_source(world)
    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="echo",
        purpose="spend 1.5",
        scope={"budget": 1.5},
    )
    assert "granted" not in out
    assert "plain int" in out["denied"]
    recorded = [r for r in powerbox.requests(world.weave()) if r["request"] == out["request"]]
    assert recorded and recorded[0]["status"] == powerbox.DENIED


# ── floors beat scope, and a floored tier is never auto-approved ──────────────
def test_a_floored_tier_is_never_auto_approved_however_the_request_is_phrased():
    """HAZARD: `with_morta_floor` is keyed by EFFECT name, and a Nona organ's effect is
    `generated_code`, which has NO floor. The tier floor is what makes a financial organ
    gated, and no requested scope can reach around it."""
    world = World()
    powerbox.install_broker_source(
        world.weft,
        world.root,
        broker=world.broker,
        name="ledger",
        effect="generated_code",
    )
    assert capability.with_morta_floor("generated_code", {}) == {}, (
        "if this ever gains a floor, the tier floor below is no longer the only guard"
    )

    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="ledger",
        purpose="move money quietly",
        scope={"budget": 10**9, "requires_approval": False},
        tier="financial",
    )
    assert out["needs_approval"] is True
    assert out["caveats"]["requires_approval"] is True
    assert out["caveats"]["reversible_only"] is True  # MORTA_FLOORS['financial'], intact
    # And the grant is really gated at the kernel: no approval Cell exists, so authorize
    # refuses it. The broker never hands out a cleared financial grant.
    allowed, _why, code = world.authorize(out["granted"])
    assert not allowed and code == capability.DenialCode.APPROVAL_REQUIRED


def test_a_low_risk_effect_does_not_launder_a_high_blast_radius_tier():
    """The tier floor, isolated. `echo` is a LOW_RISK effect and would auto-issue on its
    own; declared at a tier whose promotion needs a human, it is gated anyway. Without the
    tier floor derived from `promotion.SIGNER_POLICY`, this exact request auto-issues an
    ungated grant — which is how a workspace_write organ would slip through a broker that
    trusted `with_morta_floor` alone (it is keyed by EFFECT name and knows nothing of
    tiers)."""
    world = World()
    _echo_source(world)
    assert capability.morta_floor("echo") == {}
    assert capability.morta_floor("workspace_write") == {}

    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="echo",
        purpose="write to the workspace, but call it echo",
        tier="workspace_write",
    )
    assert out["needs_approval"] is True
    assert out["caveats"]["requires_approval"] is True
    assert powerbox.tier_floor("workspace_write") == {"requires_approval": True}
    allowed, _why, code = world.authorize(out["granted"])
    assert not allowed and code == capability.DenialCode.APPROVAL_REQUIRED


def test_an_unclassified_tier_defaults_to_a_human():
    world = World()
    powerbox.install_broker_source(
        world.weft, world.root, broker=world.broker, name="mystery", effect="generated_code"
    )
    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="mystery",
        purpose="who knows",
        tier="something_nobody_declared",
    )
    assert out["needs_approval"] is True
    assert powerbox.policy_decision("generated_code", None, "who knows") == powerbox.APPROVAL


def test_a_pure_organ_is_auto_issued_because_that_is_the_prompt_budget_working():
    world = World()
    powerbox.install_broker_source(
        world.weft, world.root, broker=world.broker, name="add_one", effect="generated_code"
    )
    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="add_one",
        purpose="add one",
        tier=anchors.PURE,
    )
    assert out["needs_approval"] is False
    assert "requires_approval" not in out["caveats"]


def test_a_network_tier_is_denied_rather_than_offered_for_approval():
    """A prompt for something that can never run only teaches the user to click yes."""
    world = World()
    powerbox.install_broker_source(
        world.weft, world.root, broker=world.broker, name="fetch", effect="generated_code"
    )
    before = world.capabilities()
    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="fetch",
        purpose="reach the network",
        tier=anchors.NETWORK,
    )
    assert "granted" not in out and promotion.NOT_EXECUTABLE in out["denied"]
    assert powerbox.policy_decision("generated_code", anchors.NETWORK, "x") == powerbox.DENY
    assert world.capabilities() == before


# ── the plumbing: hold what you broker, and write only your requester's cell ──
def test_the_broker_cannot_broker_a_source_it_does_not_hold():
    """`verify_delegation` requires `child.granter == parent.grantee`, and it runs at
    AUTHORIZE time — so this shape would otherwise issue cleanly and be denied on every
    single use with a code nobody could trace back to here."""
    world = World()
    stranger = world.keyring.mint("stranger", "agent").id
    source = "capability:not-the-brokers"
    model.assert_content(
        world.weft,
        world.root,
        source,
        "capability",
        capability.capability_content(
            name="echo", effect="echo", grantee=stranger, granter=world.root
        ),
    )
    assert "echo" not in powerbox.brokerable_sources(world.weave(), world.broker)

    base = world.weave().get(source)
    assert base is not None
    child = capability.attenuate(
        base.content, {}, source, grantee=world.holder, granter=world.broker
    )
    with pytest.raises(powerbox.BrokerRefused, match="not the grantee"):
        powerbox.issue_grant(
            world.weft,
            world.weave(),
            broker=world.broker,
            source=source,
            child=child,
            requester_cell=world.agent,
            request="cap_request:test",
        )


def test_the_envelope_write_touches_one_cell_and_one_field():
    """R1 bound: `ASSERT` is not authorized in this kernel yet, so the broker could in
    principle rewrite an agent's whole Cell. It appends one grant id and preserves the
    rest — asserted field by field, because "we only meant to append" is not a guarantee."""
    world = World()
    _echo_source(world)
    before = dict(world.agent_cell().content)

    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="echo",
        purpose="say it back",
    )
    after = dict(world.agent_cell().content)
    assert after["envelope"] == [*before.get("envelope", []), out["granted"]]
    assert {k: v for k, v in after.items() if k != "envelope"} == {
        k: v for k, v in before.items() if k != "envelope"
    }


def test_a_quarantined_source_brokers_nothing():
    world = World()
    proposed = candidate.propose_candidate(
        world.weft,
        world.reckoner,
        intent="add one",
        declared_effect_class=anchors.PURE,
        source=PURE_SOURCE,
    )
    built = executor.build_capability(
        world.weft,
        world.weave(),
        world.reckoner,
        candidate=proposed["cell"],
        tier=anchors.PURE,
        name="add_one",
        grantee=world.broker,
        granter=world.reckoner,  # N7: the minting principal is the granter
    )
    cap = world.weave().get(built["capability"])
    assert cap is not None and cap.content["quarantined"] is True

    assert "add_one" not in powerbox.brokerable_sources(world.weave(), world.broker)
    base = world.weave().get(built["capability"])
    assert base is not None
    with pytest.raises(powerbox.BrokerRefused, match="quarantined"):
        powerbox.issue_grant(
            world.weft,
            world.weave(),
            broker=world.broker,
            source=built["capability"],
            child=capability.attenuate(
                base.content, {}, built["capability"], grantee=world.holder, granter=world.broker
            ),
            requester_cell=world.agent,
            request="cap_request:test",
        )


# ── the authority the child descends from can be taken back ───────────────────
def test_rolling_back_the_parent_promotion_makes_the_brokered_child_fail_closed():
    """A brokered child of a promoted organ has NO promotion Cell of its own, so the
    derived-quarantine fold never touches it. Its safety comes entirely from
    `_caveats_downhill` at authorize time: rollback re-adds `sandbox_only` to the parent,
    and a child that lacks it is no longer downhill."""
    world = World()
    organ, promotion_cell = _promoted_organ(world)
    out = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="add_one",
        purpose="add one to an integer",
        tier=anchors.PURE,
    )
    grant = out["granted"]
    allowed, why, _code = world.authorize(grant)
    assert allowed, why  # positive control: it worked BEFORE the rollback

    promotion.rollback(world.weft, world.root, promotion_cell, reason="needs re-evaluation")

    parent = world.weave().get(organ)
    assert parent is not None and parent.content["caveats"]["sandbox_only"] is True
    allowed, why, code = world.authorize(grant)
    assert not allowed
    assert code == capability.DenialCode.DELEGATION_INVALID, why
    # And the grant itself was NOT destroyed — demotion is not revocation.
    child = world.weave().get(grant)
    assert child is not None and not child.retracted


# ── the audit trail ──────────────────────────────────────────────────────────
def test_every_request_records_its_outcome_on_the_log():
    world = World()
    _echo_source(world)
    granted = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="echo",
        purpose="say it back",
    )
    denied = powerbox.request_capability(
        world.weft,
        world.weave(),
        broker=world.broker,
        requester_cell=world.agent,
        name="nothing_like_this",
        purpose="a source that does not exist",
    )
    audit = {r["request"]: r for r in powerbox.requests(world.weave())}
    assert audit[granted["request"]]["status"] == powerbox.GRANTED
    assert audit[granted["request"]]["decision"]["grant"] == granted["granted"]
    assert audit[denied["request"]]["status"] == powerbox.DENIED
    assert "no brokerable source" in audit[denied["request"]]["decision"]["reason"]
    # The requests are keyed on `weft.head`, never a clock or urandom: replayable ids.
    assert all(rid.startswith("cap_request:") for rid in audit)


def test_the_request_id_is_deterministic_from_log_data_alone():
    world = World()
    at = world.weft.head
    first = powerbox.request_id(world.agent, "echo", "say it back", at=at)
    second = powerbox.request_id(world.agent, "echo", "say it back", at=at)
    assert first == second
    assert first != powerbox.request_id(world.agent, "echo", "other purpose", at=at)


# ── prompt-volume discipline (design §5.8 points 3-4) ────────────────────────
def test_an_automated_tier_organ_spends_one_prompt_per_organ_and_none_per_call():
    plan = powerbox.prompt_plan(anchors.PURE, {"requires_approval": True})
    assert plan["approval_scope"] == "capability"
    assert plan["capability_scoped_approval"] is True
    assert plan["invocation_approvals"] is False
    assert plan["prompts_per_organ"] == 1
    assert plan["prompts_per_call"] == 0
    assert plan["surface"] == powerbox.NOTIFICATION
    assert plan["rollback_affordance"] is True


def test_an_ungated_pure_organ_costs_no_prompts_at_all():
    plan = powerbox.prompt_plan(anchors.PURE, {})
    assert plan["approval_scope"] == "none"
    assert plan["prompts_per_organ"] == 0 and plan["prompts_per_call"] == 0


def test_a_floored_tier_never_gets_a_durable_blanket_approval():
    """The failure mode being designed against: a blanket "yes" on a financial organ is
    exactly the prompt a user learns to click through."""
    for tier in ("workspace_write", "financial"):
        plan = powerbox.prompt_plan(tier, {})
        assert plan["capability_scoped_approval"] is False, tier
        assert plan["invocation_approvals"] is True, tier
        assert plan["prompts_per_call"] == 1, tier
    assert powerbox.prompt_plan("workspace_write", {})["surface"] == powerbox.CANARY
    financial = powerbox.prompt_plan("financial", {})
    assert financial["surface"] == powerbox.EXPLICIT
    assert financial["evidence_inline"] is True
    assert financial["floor"]["reversible_only"] is True


def test_the_surface_of_an_unknown_tier_is_the_explicit_one():
    plan = powerbox.prompt_plan("no_such_tier", {})
    assert plan["surface"] == powerbox.EXPLICIT
    assert plan["invocation_approvals"] is True


def test_auto_tiers_are_derived_from_the_promotion_policy_not_a_second_list():
    """If a tier is ever moved off AUTOMATED, the broker must stop auto-issuing it in the
    same commit — so the table is derived, and this asserts the derivation."""
    assert powerbox.AUTO_TIERS == frozenset(
        t for t, p in promotion.SIGNER_POLICY.items() if p == promotion.AUTOMATED
    )
    assert anchors.PURE in powerbox.AUTO_TIERS
    assert "financial" not in powerbox.AUTO_TIERS
