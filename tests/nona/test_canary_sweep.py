"""Wiring the canary: the sweep that makes it behaviour, and the panel that shows it.

Wave N5 built `monitor_canary`, tested it thoroughly, and wired it to NOTHING — zero
production callers, no route exposing organ health, no Shell surface. Suspend-on-breach and
auto-revoke-on-high-finding therefore existed and could not fire. That is the exact trap the
design names for `Weave.canary_health` itself ("dead safety code is a liability"), reproduced
one layer up: N5 fixed the zero-TESTS half and left the zero-CALLERS half.

So the property under test here is not "does the monitor work" — `test_canary.py` covers that
— it is "does anything CALL it, does it act with authority the fold will honour, and does the
operator see the same facts it acts on".

THE AUTHORITY PART IS THE SHARP ONE. Since RETRACT is authorized
(`kernel/authorship.py::retract_refusal`), a retraction from a principal that is neither the
promotion's signer nor a root-anchored promoter for its tier is RECORDED AND THEN DECLINED BY
THE FOLD. A sweep signed by the wrong principal would look like it ran, report actions, and
change nothing — automation that is worse than no automation, because it reads as protection.
`test_the_sweep_acts_with_authority_the_fold_honours` is the test that would catch it.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from decima.kernel import model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.services.nona import anchors, monitor, promotion
from tests.nona.test_canary import ADD_ONE, World, _world

HIGH = "high"


def _twin(source: str = ADD_ONE) -> World:
    return _world(source)


def _breach(world: World, times: int = 2) -> None:
    """Drive real failures through the real invoke seam — never a hand-written receipt, so
    what the sweep folds is what the product would actually have written."""
    for _ in range(times):
        try:
            world.invoke(x="not an int")
        except Exception:  # noqa: BLE001 - the organ failing IS the fixture
            pass


# ── the sweep exists and finds what is promoted ──────────────────────────────
def test_promoted_organs_reads_liveness_not_a_flag() -> None:
    """ "Promoted" is derived from promotion liveness, the same fact quarantine is derived
    from — so the sweep's worklist can never disagree with what the kernel enforces."""
    world = _twin()
    assert monitor.promoted_organs(world.weave()) == [world.capability]

    promotion.rollback(world.weft, world.reckoner, world.promotion, reason="demote")
    assert monitor.promoted_organs(world.weave()) == []


def test_a_healthy_sweep_checks_the_organ_and_changes_nothing() -> None:
    world = _twin()
    result = monitor.sweep(world.weft, world.weave())

    assert result["checked"] == [world.capability]
    assert result["actions"] == []
    assert world.cap_cell().retracted is False


def test_the_sweep_demotes_a_breached_organ_without_being_told_which() -> None:
    """The whole point of a sweep: nobody names the organ. It is found, measured and
    contained from the fold alone."""
    world = _twin()
    _breach(world)

    result = monitor.sweep(world.weft, world.weave())

    assert [a["action"] for a in result["actions"]] == [monitor.SUSPENDED]
    assert result["actions"][0]["capability"] == world.capability
    weave = world.weave()
    prom = weave.get(world.promotion)
    assert prom is not None and prom.retracted is True
    cap = weave.get(world.capability)
    assert cap is not None and cap.content.get("quarantined") is True


def test_the_sweep_acts_with_authority_the_fold_honours() -> None:
    """The test that a wrong-signer sweep would fail. Each action is signed by its own
    promotion's `signer`; a retraction from anyone else is recorded and DECLINED, so the
    sweep would report an action that did not happen.

    Proven by contrast on one fixture: the sweep's demotion sticks, while the identical
    retraction from a stranger does not."""
    world = _twin()
    _breach(world)
    stranger = world.keyring.mint("stranger", "agent").id

    # The stranger tries first, and changes nothing.
    promotion.rollback(world.weft, stranger, world.promotion, reason="not mine to take")
    prom = world.weave().get(world.promotion)
    assert prom is not None and prom.retracted is False

    # The sweep, choosing the signer itself, does.
    result = monitor.sweep(world.weft, world.weave())
    assert result["actions"][0]["signer"] == world.reckoner
    prom = world.weave().get(world.promotion)
    assert prom is not None and prom.retracted is True


def test_an_organ_whose_promotion_names_no_signer_is_reported_not_acted_on() -> None:
    """Fail closed and SAY SO. Writing a retraction the fold would decline is the failure
    mode this branch exists to avoid; reporting it is what lets a human notice."""
    keyring = Keyring(seed=bytes(32))
    weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), keyring)
    root = keyring.mint("root", "root").id
    reck = keyring.mint(anchors.RECKONER_NAME, "reckoner").id
    anchors.install_trust_anchors(weft, root, reckoner=reck)
    model.assert_content(
        weft,
        root,
        "cap:organ",
        "capability",
        {
            "name": "organ",
            "effect": "generated_code",
            "declared_effect_class": anchors.PURE,
            "quarantined": False,
            "parent": None,
            "grantee": root,
            "granter": root,
            "caveats": {},
        },
    )
    model.assert_content(
        weft,
        root,
        "promotion:signerless",
        promotion.PROMOTION,
        {
            "capability": "cap:organ",
            "candidate": "c",
            "evaluation_result": "e",
            "tier": anchors.PURE,
            "signer": None,
        },
    )

    result = monitor.sweep(weft, Weave.fold(weft))

    assert result["unsigned"] == ["cap:organ"]
    assert result["checked"] == [] and result["actions"] == []


# ── idempotence: safe to run on any schedule, including twice ────────────────
def test_sweeping_twice_on_unchanged_evidence_changes_nothing_the_second_time() -> None:
    """A scheduled pass runs whether or not anything happened, so a second run on identical
    evidence must be a no-op — not a second incident, not a second suspension."""
    world = _twin()
    _breach(world)

    first = monitor.sweep(world.weft, world.weave())
    root_after_first = world.weave().state_root()
    second = monitor.sweep(world.weft, world.weave())

    assert len(first["actions"]) == 1
    assert second["actions"] == [], "a demoted organ has no live promotion left to act on"
    assert second["checked"] == [], "...and drops out of the worklist entirely"
    assert world.weave().state_root() == root_after_first


def test_a_sweep_over_a_healthy_realm_is_deterministic_and_writes_nothing() -> None:
    world = _twin()
    before = world.weave().state_root()

    monitor.sweep(world.weft, world.weave())
    monitor.sweep(world.weft, world.weave())

    assert world.weave().state_root() == before


def test_the_sweep_visits_organs_in_a_deterministic_order() -> None:
    """Several organs on one realm: the worklist must be ordered by CONTENT (id) and not by
    insertion or dict iteration, or two peers folding the same log could contain them in
    different orders — and with a cascade in play, order is observable.

    Built by asserting the cells directly rather than running the promote pipeline three
    times: what is under test is the ordering of `promoted_organs`, and a pipeline would only
    add ways for the fixture to be wrong."""
    world = _twin()
    for name in ("zeta", "alpha", "mu"):
        cap = f"cap:{name}"
        model.assert_content(
            world.weft,
            world.root,
            cap,
            "capability",
            {
                "name": name,
                "effect": "generated_code",
                "declared_effect_class": anchors.PURE,
                "quarantined": False,
                "parent": None,
                "grantee": world.holder,
                "granter": world.root,
                "caveats": {},
            },
        )
        model.assert_content(
            world.weft,
            world.reckoner,
            f"promotion:{name}",
            promotion.PROMOTION,
            {
                "capability": cap,
                "candidate": "c",
                "evaluation_result": "e",
                "tier": anchors.PURE,
                "signer": world.reckoner,
            },
        )

    organs = monitor.promoted_organs(world.weave())

    assert len(organs) == 4, "the bootstrapped organ plus the three added here"
    assert organs == sorted(organs), "id order, not insertion order"
    assert monitor.sweep(world.weft, world.weave())["checked"] == organs


# ── the panel reads exactly what the sweep acts on ───────────────────────────
def test_organ_health_reports_the_same_facts_the_sweep_acts_on() -> None:
    """The failure mode a separate health table would have: a panel that says HEALTHY while
    enforcement demotes. Both sides read one fold, so this compares them directly."""
    world = _twin()
    _breach(world)

    panel = monitor.organ_health(world.weft, world.weave())
    assert [h["capability"] for h in panel] == [world.capability]
    assert panel[0]["breach"] is True
    assert panel[0]["signer"] == world.reckoner

    acted = monitor.sweep(world.weft, world.weave())
    assert acted["actions"][0]["capability"] == panel[0]["capability"]


def test_the_panel_writes_nothing() -> None:
    """A read is a read. If rendering the canary could move an organ, opening a screen would
    be an authority-bearing act."""
    world = _twin()
    _breach(world)
    before = world.weave().state_root()

    monitor.organ_health(world.weft, world.weave())

    assert world.weave().state_root() == before


def test_the_panel_surfaces_an_unattributed_finding_without_counting_it() -> None:
    """Planted evidence is shown, never acted on — otherwise a sandboxed candidate could
    trigger a permanent cascading revoke of someone else's grant by asserting one Cell."""
    world = _twin()
    stranger = world.keyring.mint("stranger", "agent").id
    monitor.record_finding(
        world.weft, stranger, capability=world.capability, rule="scan.rug_pull", severity=HIGH
    )

    panel = monitor.organ_health(world.weft, world.weave())
    assert panel[0]["high_findings"] == 0
    assert panel[0]["unattributed_high_findings"] == 1

    result = monitor.sweep(world.weft, world.weave())
    assert result["actions"] == [], "an unanchored finding moves nothing"
    assert result["unattributed"] == [{"capability": world.capability, "count": 1}]


def test_an_attributed_high_finding_is_revoked_by_the_sweep() -> None:
    """The positive control for the test above, and the terminal path end to end: an anchored
    auditor's finding takes the organ down through the sweep, not through a direct call."""
    world = _twin()
    monitor.record_finding(
        world.weft,
        world.reckoner,
        capability=world.capability,
        rule="scan.rug_pull",
        severity=HIGH,
    )

    result = monitor.sweep(world.weft, world.weave())

    assert [a["action"] for a in result["actions"]] == [monitor.REVOKED]
    cap = world.weave().get(world.capability)
    assert cap is not None and cap.retracted is True and cap.cascade_root is True


def _panel(world: World) -> dict[str, Any]:
    return monitor.organ_health(world.weft, world.weave())[0]


def test_the_panel_names_the_tier_from_the_grant_not_from_the_promotion() -> None:
    """The tier the operator reads must be the one the executor and the promoter policy read,
    so it is taken off the capability itself."""
    world = _twin()
    assert _panel(world)["tier"] == anchors.PURE
