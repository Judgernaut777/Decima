"""Nona N4: promotion, rollback, and the derived quarantine flag.

The headline claim this wave makes true: **promotion is a signature and rollback is a
retraction.** `VISION.md` always said so; before N4 it was not implementable, because an
ATTEST has no cell id to retract and the lift mutated the capability's content (so a later
ordinary ASSERT could silently re-quarantine it, and two branches could disagree).

So the tests here are mostly about the *derived* flag: that it follows the promotion's
liveness, that it survives re-folding and arrival order, that rollback is not revocation, and
that Morta's floor is untouched in both directions.

The last test is the design's stated acceptance criterion (`specs/NONA_RECKONER.md`): run the
whole bootstrap, retract the promotion, prove the invocation is then denied, and replay to
the same `state_root`.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

from decima.kernel import capability, model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.services.nona import anchors, promotion, reckoner
from decima.services.nona.reckoner import Metrics

_CAP = "cap:organ"
_CANDIDATE = "candidate:organ"


def _install() -> tuple[Weft, Keyring, str, str]:
    kr = Keyring(seed=bytes(32))
    weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), kr)
    root = kr.mint("root", "root").id
    reck = kr.mint(anchors.RECKONER_NAME, "reckoner").id
    anchors.install_trust_anchors(weft, root, reckoner=reck)
    return weft, kr, root, reck


def _quarantined_cap(weft: Weft, root: str, tier: str = anchors.PURE) -> str:
    model.assert_content(
        weft,
        root,
        _CAP,
        "capability",
        {
            "effect": "generated_code",
            "declared_effect_class": tier,
            "quarantined": True,
            # A Morta floor rides along, so every test below can check it survives.
            "caveats": {"sandbox_only": True, "requires_approval": True},
        },
    )
    return _CAP


def _eligible_evaluation(weft: Weft, author: str) -> str:
    verdict = reckoner.gate(
        Metrics(
            deterministic_cases=2,
            deterministic_pass=2,
            hostile_cases=1,
            hostile_contained=1,
        )
    )
    assert verdict.eligible
    return reckoner.record_result(
        weft,
        author,
        candidate_cell=_CANDIDATE,
        suite_cell="suite:s",
        implementation_digest="blob_d",
        verdict=verdict,
        containment={
            "no_new_privs": True,
            "network_denied": True,
            "chroot": True,
            "namespaces": True,
            "matrix_version": 1,
        },
    )


def _failing_evaluation(weft: Weft, author: str) -> str:
    verdict = reckoner.gate(Metrics(deterministic_cases=2, deterministic_pass=1))
    assert not verdict.eligible
    return reckoner.record_result(
        weft,
        author,
        candidate_cell=_CANDIDATE,
        suite_cell="suite:s",
        implementation_digest="blob_d",
        verdict=verdict,
        containment={"no_new_privs": True, "network_denied": True, "chroot": True},
    )


# ── promotion requires evidence and authority ────────────────────────────────
def test_a_promotion_lifts_quarantine():
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)

    promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )

    got = Weave.fold(weft).get(cap)
    assert got is not None
    assert got.content["quarantined"] is False
    assert "sandbox_only" not in got.content["caveats"]


def test_morta_survives_promotion():
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)
    promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )
    got = Weave.fold(weft).get(cap)
    assert got is not None
    assert got.content["caveats"]["requires_approval"] is True


def test_a_promotion_may_not_cite_a_failing_evaluation():
    """The check that makes the gate mean something at the promotion boundary too."""
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    bad = _failing_evaluation(weft, reck)

    with pytest.raises(promotion.PromotionRefused, match="not promote-eligible"):
        promotion.promote(
            weft,
            Weave.fold(weft),
            reck,
            capability=cap,
            candidate=_CANDIDATE,
            evaluation=bad,
            tier=anchors.PURE,
        )
    got = Weave.fold(weft).get(cap)
    assert got is not None and got.content["quarantined"] is True


def test_an_unanchored_signer_cannot_promote():
    weft, kr, root, _reck = _install()
    stranger = kr.mint("stranger", "agent").id
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, stranger)

    with pytest.raises(promotion.PromotionRefused, match="not a trusted promoter"):
        promotion.promote(
            weft,
            Weave.fold(weft),
            stranger,
            capability=cap,
            candidate=_CANDIDATE,
            evaluation=ev,
            tier=anchors.PURE,
        )


def test_a_network_tier_organ_cannot_be_promoted_at_all():
    """Not "gated" — it has NO EXECUTOR. Saying "requires approval" for something that can
    never run would teach the user to click yes."""
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root, tier="network")
    ev = _eligible_evaluation(weft, reck)

    with pytest.raises(promotion.PromotionRefused, match="no executor"):
        promotion.promote(
            weft,
            Weave.fold(weft),
            reck,
            capability=cap,
            candidate=_CANDIDATE,
            evaluation=ev,
            tier="network",
        )
    assert promotion.signer_policy("network") == promotion.NOT_EXECUTABLE


def test_the_signer_policy_makes_the_two_auto_tiers_explicit():
    """Owner Decision 1: exactly `pure` and `read_only` compound without a human."""
    auto = {t for t, p in promotion.SIGNER_POLICY.items() if p == promotion.AUTOMATED}
    assert auto == {"pure", "read_only"}


# ── rollback is a retraction, and that is the whole mechanism ────────────────
def test_rollback_requarantines_the_capability():
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)
    out = promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )
    assert Weave.fold(weft).get(cap).content["quarantined"] is False  # type: ignore[union-attr]

    promotion.rollback(weft, root, out["promotion"], reason="regression found")

    got = Weave.fold(weft).get(cap)
    assert got is not None
    assert got.content["quarantined"] is True
    assert got.content["caveats"]["sandbox_only"] is True


def test_rollback_is_demotion_not_revocation():
    """The distinction N4 makes expressible: "this needs re-evaluation" is not "this must
    never run again". The capability itself is untouched — not retracted, not cascaded."""
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)
    out = promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )
    promotion.rollback(weft, root, out["promotion"])

    got = Weave.fold(weft).get(cap)
    assert got is not None
    assert got.retracted is False, "demotion must not retract the organ"
    assert got.cascaded is False, "demotion must not cascade"


def test_morta_survives_demotion_too():
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)
    out = promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )
    promotion.rollback(weft, root, out["promotion"])
    got = Weave.fold(weft).get(cap)
    assert got is not None
    assert got.content["caveats"]["requires_approval"] is True


def test_re_promoting_the_same_evidence_is_one_promotion():
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)
    a = promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )
    b = promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )
    assert a["promotion"] == b["promotion"]


# ── the derived flag is order-independent and idempotent ─────────────────────
def test_a_later_assert_of_the_capability_cannot_silently_requarantine_it():
    """The exact hazard the reference's content-mutating lift had: an ordinary ASSERT of the
    capability folding AFTER the promotion used to re-quarantine it. A derived flag cannot
    be raced."""
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)
    promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )

    # An innocuous later re-assertion (a description edit, a re-registration) that carries
    # the ORIGINAL quarantined:True content.
    model.assert_content(
        weft,
        root,
        cap,
        "capability",
        {
            "effect": "generated_code",
            "declared_effect_class": anchors.PURE,
            "quarantined": True,
            "caveats": {"sandbox_only": True, "requires_approval": True},
            "note": "edited later",
        },
    )

    got = Weave.fold(weft).get(cap)
    assert got is not None
    assert got.content["quarantined"] is False, (
        "a later ASSERT must not undo a live promotion — that is what deriving the flag buys"
    )


def test_folding_twice_yields_the_same_state_root():
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)
    promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )
    assert Weave.fold(weft).state_root() == Weave.fold(weft).state_root()


def test_promotion_state_is_an_auditable_read():
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)
    out = promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )

    state = promotion.promotion_state(Weave.fold(weft), cap)
    assert state["quarantined"] is False
    assert len(state["live_promotions"]) == 1
    assert state["live_promotions"][0]["evaluation_result"] == ev

    promotion.rollback(weft, root, out["promotion"])
    after = promotion.promotion_state(Weave.fold(weft), cap)
    assert after["quarantined"] is True
    assert after["live_promotions"] == []
    assert len(after["promotions"]) == 1, "the rolled-back promotion stays on the record"


# ── the design's acceptance criterion ────────────────────────────────────────
def test_the_full_bootstrap_promote_invoke_rollback_deny_replay():
    """specs/NONA_RECKONER.md's bootstrap: promote an organ, prove an ordinary (non-sandbox)
    holder may reach it, RETRACT the promotion, prove the invocation is then DENIED, and
    replay to the same state_root."""
    weft, _kr, root, reck = _install()
    cap = _quarantined_cap(weft, root)
    ev = _eligible_evaluation(weft, reck)
    holder = cells.create_agent(
        weft, root, objective="use the organ", principal=root, capability_grant_ids=[cap]
    )

    # Before promotion: an ordinary holder is refused BECAUSE it is quarantined.
    weave = Weave.fold(weft)
    agent = weave.get(holder)
    assert agent is not None
    _ok, _why, code = capability.authorize_detail(weave, agent, cap, {}, root)
    assert code == capability.DenialCode.QUARANTINED

    out = promotion.promote(
        weft,
        weave,
        reck,
        capability=cap,
        candidate=_CANDIDATE,
        evaluation=ev,
        tier=anchors.PURE,
    )

    # After promotion: quarantine is no longer the reason for any refusal.
    weave = Weave.fold(weft)
    agent = weave.get(holder)
    assert agent is not None
    _ok, _why, code = capability.authorize_detail(weave, agent, cap, {}, root)
    assert code != capability.DenialCode.QUARANTINED

    # Rollback → the organ is quarantined again, so the ordinary holder is refused again.
    promotion.rollback(weft, root, out["promotion"], reason="canary regression")
    weave = Weave.fold(weft)
    agent = weave.get(holder)
    assert agent is not None
    _ok, _why, code = capability.authorize_detail(weave, agent, cap, {}, root)
    assert code == capability.DenialCode.QUARANTINED

    # And the whole history replays deterministically.
    assert Weave.fold(weft).state_root() == weave.state_root()


def _unused(*_a: Any) -> None:  # pragma: no cover - keeps linters honest about helpers
    return None
