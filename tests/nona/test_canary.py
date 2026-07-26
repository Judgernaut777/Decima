"""Nona N5: the canary fold, and the two different things it is allowed to do about it.

`Weave.canary_health` shipped in the kernel with zero callers and zero tests. Exercising it
for the first time turned up three facts worth stating in a test file rather than a commit
message:

  * the `result` Cell shape it folds had **no producer** in `decima/` — the runtime's
    `record_receipt` writes a different type with no `of` field and could never satisfy it;
  * its `ok is False` half was **unreachable**, because `WorkerResponse` has no `ok` field;
  * its high-finding half had **no producer either** — nothing had ever written a `finding`
    Cell or a `found_in` edge, so `high_findings` was structurally always 0.

That last one is why this file never asserts `high_findings == 0` as evidence of anything.
A test that promoted an organ, ran it, and checked for no findings would have passed
identically against a monitor that could not detect a finding at all. Instead the counter is
proved to MOVE: 0, then a medium finding leaves it at 0, then a high finding takes it to 1
and the monitor acts.

And the two actions are deliberately different. A breach DEMOTES (rollback: the promotion
Cell is retracted, the organ re-quarantines, grants survive, it can be re-promoted). A high
finding REVOKES (terminal, DERIVED_AUTHORITY cascade). The tests below pin both halves of
that asymmetry, including that the wrong one is not silently taken.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any

from decima.kernel import capability, model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.services.nona import anchors, candidate, executor, monitor, promotion, reckoner
from decima.services.nona.reckoner import Metrics

ADD_ONE = "def main(x):\n    return int(x) + 1\n"
RAISES = "def main(x):\n    raise ValueError('the organ blew up')\n"


@dataclass
class World:
    weft: Weft
    keyring: Keyring
    root: str
    reckoner: str
    holder: str
    agent: str
    capability: str
    promotion: str

    def weave(self) -> Weave:
        return Weave.fold(self.weft)

    def agent_cell(self) -> Cell:
        cell = self.weave().get(self.agent)
        assert cell is not None
        return cell

    def cap_cell(self) -> Cell:
        cell = self.weave().get(self.capability)
        assert cell is not None
        return cell

    def invoke(self, **args: Any) -> dict[str, Any]:
        return executor.invoke_organ(
            self.weft, self.weave(), self.keyring, self.agent_cell(), self.capability, args
        )


def _bootstrap(weft: Weft, keyring: Keyring, source: str = ADD_ONE) -> World:
    """The whole loop against a caller-supplied Weft, so a twin can replay it byte for byte."""
    root = keyring.mint("root", "root").id
    reck = keyring.mint(anchors.RECKONER_NAME, "reckoner").id
    holder = keyring.mint("holder", "operator").id
    anchors.install_trust_anchors(weft, root, reckoner=reck)

    proposed = candidate.propose_candidate(
        weft,
        reck,
        intent="add one",
        declared_effect_class=anchors.PURE,
        source=source,
        output_schema={"type": "int"},
    )
    built = executor.build_capability(
        weft,
        Weave.fold(weft),
        reck,
        candidate=proposed["cell"],
        tier=anchors.PURE,
        name="add_one",
        grantee=holder,
        granter=reck,
    )
    agent = cells.create_agent(
        weft,
        root,
        objective="use the organ",
        principal=holder,
        capability_grant_ids=[built["capability"]],
    )
    verdict = reckoner.gate(
        Metrics(deterministic_cases=2, deterministic_pass=2, hostile_cases=1, hostile_contained=1)
    )
    evaluation = reckoner.record_result(
        weft,
        reck,
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
        weft,
        Weave.fold(weft),
        reck,
        capability=built["capability"],
        candidate=proposed["cell"],
        evaluation=evaluation,
        tier=anchors.PURE,
    )
    return World(
        weft=weft,
        keyring=keyring,
        root=root,
        reckoner=reck,
        holder=holder,
        agent=agent,
        capability=built["capability"],
        promotion=promoted["promotion"],
    )


def _world(source: str = ADD_ONE, *, seed: bytes = bytes(32)) -> World:
    keyring = Keyring(seed=seed)
    weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), keyring)
    return _bootstrap(weft, keyring, source)


def _denial(world: World) -> str:
    weave = world.weave()
    agent = weave.get(world.agent)
    assert agent is not None
    _ok, _why, code = capability.authorize_detail(weave, agent, world.capability, {}, world.holder)
    return code


# ── the fold now has a producer ──────────────────────────────────────────────
def test_canary_health_folds_the_receipts_the_invoke_seam_writes() -> None:
    """The first end-to-end exercise of `canary_health` in the product's history."""
    world = _world()
    assert world.weave().canary_health(world.capability)["invocations"] == 0

    world.invoke(x=1)
    world.invoke(x=2)

    health = world.weave().canary_health(world.capability)
    assert health["invocations"] == 2
    assert health["receipts"] == 2, "every INVOKE must leave a receipt the canary can see"
    assert health["failures"] == 0
    assert health["healthy"] is True
    assert health["breach"] is False


def test_a_failing_organ_is_counted_as_a_failure_by_the_fold() -> None:
    world = _world(source=RAISES)
    world.invoke(x=1)
    health = world.weave().canary_health(world.capability)
    assert health["receipts"] == 1
    assert health["failures"] == 1
    assert health["breach"] is True
    assert health["healthy"] is False


def test_the_failure_threshold_is_a_threshold_and_not_a_constant() -> None:
    """`max_failures` must actually be consulted — one failure under a limit of one is fine."""
    world = _world(source=RAISES)
    world.invoke(x=1)
    assert world.weave().canary_health(world.capability, max_failures=1)["breach"] is False
    assert world.weave().canary_health(world.capability, max_failures=0)["breach"] is True


# ── breach → DEMOTION, not revocation ────────────────────────────────────────
def test_a_canary_breach_suspends_by_rolling_the_promotion_back() -> None:
    world = _world(source=RAISES)
    world.invoke(x=1)
    assert _denial(world) != capability.DenialCode.QUARANTINED  # live before the monitor runs

    out = monitor.monitor_canary(world.weft, world.weave(), world.root, world.capability)

    assert out["action"] == monitor.SUSPENDED
    assert out["rolled_back"] == [world.promotion]
    # DEMOTION: the organ is quarantined again...
    assert world.cap_cell().content["quarantined"] is True
    assert world.cap_cell().content["caveats"]["sandbox_only"] is True
    assert _denial(world) == capability.DenialCode.QUARANTINED
    # ...but it is NOT revoked, and the grant it was issued under is untouched. That is the
    # whole difference between "re-evaluate this" and "this must never run again".
    assert world.cap_cell().retracted is False
    assert _denial(world) != capability.DenialCode.REVOKED
    agent = world.agent_cell()
    assert world.capability in agent.content["envelope"]
    # And the suspension is a Cell of its own, edged to the capability.
    suspension = world.weave().get(out["suspension"])
    assert suspension is not None
    assert suspension.type == monitor.SUSPENSION
    assert suspension.content["to_state"] == "QUARANTINED"


def test_a_suspended_organ_can_be_promoted_again_on_new_evidence() -> None:
    """Demotion is reversible by construction — that is what makes it not a revocation."""
    world = _world(source=RAISES)
    world.invoke(x=1)
    monitor.monitor_canary(world.weft, world.weave(), world.root, world.capability)
    assert _denial(world) == capability.DenialCode.QUARANTINED

    verdict = reckoner.gate(
        Metrics(deterministic_cases=3, deterministic_pass=3, hostile_cases=1, hostile_contained=1)
    )
    fresh = reckoner.record_result(
        world.weft,
        world.reckoner,
        candidate_cell="candidate:re-evaluated",
        suite_cell="suite:s2",
        implementation_digest="blob_x",
        verdict=verdict,
        containment={
            "no_new_privs": True,
            "network_denied": True,
            "chroot": True,
            "namespaces": True,
            "matrix_version": 1,
        },
    )
    promotion.promote(
        world.weft,
        world.weave(),
        world.reckoner,
        capability=world.capability,
        candidate="candidate:re-evaluated",
        evaluation=fresh,
        tier=anchors.PURE,
    )
    assert _denial(world) != capability.DenialCode.QUARANTINED


# ── high finding → TERMINAL revocation, with the cascade ─────────────────────
def test_the_high_finding_counter_actually_moves() -> None:
    """Guard against the vacuous pass: prove the counter can be 0 for the right reason.

    Nothing in `decima/` had ever written a `finding` Cell, so `high_findings` was
    structurally always 0 — a test that merely asserted 0 would have proved nothing. Here it
    is 0, then still 0 for a MEDIUM finding, then 1 for a HIGH one.
    """
    world = _world()
    assert world.weave().canary_health(world.capability)["high_findings"] == 0

    monitor.record_finding(
        world.weft, world.root, world.capability, severity="medium", rule="scan.noise", detail="x"
    )
    assert world.weave().canary_health(world.capability)["high_findings"] == 0

    monitor.record_finding(
        world.weft,
        world.root,
        world.capability,
        severity="high",
        rule="scan.rug_pull",
        detail="imports socket without declaring network reach",
    )
    assert world.weave().canary_health(world.capability)["high_findings"] == 1


def test_a_medium_finding_does_not_move_the_organ() -> None:
    world = _world()
    world.invoke(x=1)
    monitor.record_finding(
        world.weft, world.root, world.capability, severity="medium", rule="scan.noise"
    )
    out = monitor.monitor_canary(world.weft, world.weave(), world.root, world.capability)
    assert out["action"] is None
    assert world.cap_cell().retracted is False
    assert _denial(world) != capability.DenialCode.QUARANTINED


def test_a_high_finding_auto_revokes_and_cascades_to_delegated_grants() -> None:
    world = _world()
    world.invoke(x=1)

    # A downstream grant attenuated from the organ — the thing a revocation must reach.
    parent = world.cap_cell()
    delegate = world.keyring.mint("delegate", "worker").id
    child_content = capability.attenuate(
        parent.content, {}, world.capability, grantee=delegate, granter=world.holder
    )
    model.assert_content(
        world.weft, world.holder, "capability:delegated", executor.CAPABILITY, child_content
    )

    monitor.record_finding(
        world.weft,
        world.root,
        world.capability,
        severity="high",
        rule="scan.rug_pull",
        detail="imports socket without declaring network reach",
    )
    assert world.weave().canary_health(world.capability)["high_findings"] == 1

    out = monitor.monitor_canary(world.weft, world.weave(), world.root, world.capability)

    assert out["action"] == monitor.REVOKED
    assert _denial(world) == capability.DenialCode.REVOKED
    assert world.cap_cell().retracted is True
    # TERMINAL and CASCADING: the delegated grant fails closed with it.
    child = world.weave().get("capability:delegated")
    assert child is not None
    assert child.retracted is True and child.cascaded is True
    # The incident is recorded, and is a different Cell type from a suspension.
    incident = world.weave().get(out["incident"])
    assert incident is not None
    assert incident.type == monitor.INCIDENT
    assert incident.content["to_state"] == "REVOKED"
    assert "suspension" not in out


def test_a_high_finding_wins_over_a_breach() -> None:
    """Both signals at once takes the stronger action — a finding is not re-evaluable."""
    world = _world(source=RAISES)
    world.invoke(x=1)
    monitor.record_finding(
        world.weft, world.root, world.capability, severity="high", rule="containment.escaped"
    )
    health = world.weave().canary_health(world.capability)
    assert health["breach"] is True and health["high_findings"] == 1

    out = monitor.monitor_canary(world.weft, world.weave(), world.root, world.capability)
    assert out["action"] == monitor.REVOKED
    assert world.cap_cell().retracted is True


def test_a_healthy_canary_takes_no_action_at_all() -> None:
    world = _world()
    world.invoke(x=1)
    before = len(list(world.weft.events()))

    out = monitor.monitor_canary(world.weft, world.weave(), world.root, world.capability)

    assert out["action"] is None
    assert out["health"]["healthy"] is True
    assert len(list(world.weft.events())) == before, "measuring must not write"
    assert _denial(world) == capability.DenialCode.OK


def test_the_monitor_never_hands_a_capability_id_to_rollback() -> None:
    """Rollback targets the PROMOTION Cell. Passing the capability id would RETRACT the
    capability — and the fold still defaults a capability RETRACT to DERIVED_AUTHORITY even
    with mode WITHDRAW, so that mistake is a silent cascade where a demotion was meant."""
    world = _world(source=RAISES)
    world.invoke(x=1)
    out = monitor.monitor_canary(world.weft, world.weave(), world.root, world.capability)
    assert out["rolled_back"] == [world.promotion]
    assert world.capability not in out["rolled_back"]
    rolled = world.weave().get(world.promotion)
    assert rolled is not None and rolled.retracted is True
    assert world.cap_cell().retracted is False


# ── Law 5: the whole path replays ────────────────────────────────────────────
def _full_path(name: str) -> str:
    keyring = Keyring(seed=bytes(32))
    weft = Weft(os.path.join(tempfile.mkdtemp(), name), keyring)
    world = _bootstrap(weft, keyring, ADD_ONE)
    world.invoke(x=41)
    world.invoke(x=1)
    monitor.record_finding(
        world.weft, world.root, world.capability, severity="medium", rule="scan.noise"
    )
    monitor.monitor_canary(world.weft, world.weave(), world.root, world.capability)
    return world.weave().state_root()


def test_the_whole_promote_invoke_monitor_path_replays_to_an_identical_state_root() -> None:
    """Two independent Wefts, the same script, the same folded state.

    This is the test the derived nonce exists for. The reference invoke seam mints
    `os.urandom(16).hex()`, which lands in the invocation bind, the INVOKE body, the event id
    and the receipt's idempotency key — under that shape these two roots could never match,
    and every organ invocation would be an unreplayable hole in the log.
    """
    assert _full_path("a.db") == _full_path("b.db")


def test_refolding_the_same_log_yields_the_same_state_root() -> None:
    world = _world()
    world.invoke(x=41)
    monitor.monitor_canary(world.weft, world.weave(), world.root, world.capability)
    assert Weave.fold(world.weft).state_root() == Weave.fold(world.weft).state_root()
