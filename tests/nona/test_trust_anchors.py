"""Nona N1: trust anchors, the sandbox principal, and the kernel's promotion path.

These are the first tests to exercise `Weave`'s tiered-promotion machinery at all. That
machinery was implemented but unreachable from the shipping product, so the properties it
claims — a trusted attest lifts quarantine, an untrusted one does not, a self-declared
anchor is filtered, Morta survives the lift — had never been asserted anywhere. Wave N1
makes the loop's foundation real, so these tests are the point of the wave, not a
by-product of it.

The invariant under all of them: PROMOTION AUTHORITY IS DATA THE ROOT ASSERTED, and the
fold decides who holds it. Nothing here is configuration and nothing is ambient.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from decima.kernel import model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import ATTEST, Weft
from decima.runtime import cells
from decima.services.nona import anchors

CAP = "capability"


def _weft() -> tuple[Weft, Keyring]:
    kr = Keyring(seed=bytes(32))
    return Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), kr), kr


def _quarantined_candidate(weft: Weft, author: str, cell: str, tier: str) -> str:
    """A capability Cell as Nona mints one: QUARANTINED, sandbox_only, and carrying the
    declared tier the promotion will be signed against."""
    model.assert_content(
        weft,
        author,
        cell,
        CAP,
        {
            "effect": "generated_code",
            "declared_effect_class": tier,
            "quarantined": True,
            "caveats": {"sandbox_only": True, "requires_approval": True},
        },
    )
    return cell


def _promote(weft: Weft, author: str, target: str) -> None:
    """A promote-ATTEST — the event a promoter signs to lift a candidate's quarantine.

    `target_cell` is the key the fold resolves (`Weave._apply`, ATTEST arm). Getting it
    wrong makes every "stays quarantined" assertion below pass VACUOUSLY, which is exactly
    how a fail-closed test suite can look green while testing nothing — hence
    `test_a_trusted_promoter_lifts_quarantine`, the positive control that proves the
    mechanism fires at all.
    """
    weft.append(author, ATTEST, {"target_cell": target, "promote": True})


# ── the anchor itself ────────────────────────────────────────────────────────
def test_root_anchor_makes_the_reckoner_a_trusted_promoter():
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    reckoner = kr.mint(anchors.RECKONER_NAME, "reckoner").id

    anchors.install_trust_anchors(weft, root, reckoner=reckoner)

    weave = Weave.fold(weft)
    assert anchors.trusted_promoters(weave) == {reckoner: sorted(anchors.SIGNABLE_TIERS)}


def test_installing_anchors_is_idempotent():
    """Re-provisioning re-asserts the SAME anchor cell rather than accreting a second one
    that would have to be reconciled."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    reckoner = kr.mint(anchors.RECKONER_NAME, "reckoner").id

    first = anchors.install_trust_anchors(weft, root, reckoner=reckoner)
    second = anchors.install_trust_anchors(weft, root, reckoner=reckoner)

    assert first["promoter_cell"] == second["promoter_cell"]
    weave = Weave.fold(weft)
    promoters = [c for c in weave.cells.values() if c.type == anchors.PROMOTER]
    assert len(promoters) == 1


def test_an_unsignable_tier_is_refused_at_the_door():
    """`network` has no executable path, so no principal is given authority to bless it —
    anchoring one would grant power over something that can never run."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    reckoner = kr.mint(anchors.RECKONER_NAME, "reckoner").id

    with pytest.raises(ValueError, match="un-signable"):
        anchors.install_trust_anchors(weft, root, reckoner=reckoner, tiers=(anchors.NETWORK,))


# ── the promotion path the anchors unlock ────────────────────────────────────
def test_a_trusted_promoter_lifts_quarantine():
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    reckoner = kr.mint(anchors.RECKONER_NAME, "reckoner").id
    anchors.install_trust_anchors(weft, root, reckoner=reckoner)
    _quarantined_candidate(weft, root, "cap:sum", anchors.PURE)

    _promote(weft, reckoner, "cap:sum")

    cap = Weave.fold(weft).get("cap:sum")
    assert cap is not None
    assert cap.content["quarantined"] is False
    assert "sandbox_only" not in cap.content["caveats"]


def test_morta_survives_the_lift():
    """Promotion strips `sandbox_only` and NOTHING else. An unstrippable approval gate is
    still unstrippable after an organ is promoted — that is what makes the gate a floor
    rather than a phase."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    reckoner = kr.mint(anchors.RECKONER_NAME, "reckoner").id
    anchors.install_trust_anchors(weft, root, reckoner=reckoner)
    _quarantined_candidate(weft, root, "cap:sum", anchors.PURE)

    _promote(weft, reckoner, "cap:sum")

    cap = Weave.fold(weft).get("cap:sum")
    assert cap is not None
    assert cap.content["caveats"]["requires_approval"] is True


def test_an_unanchored_principal_cannot_lift_quarantine():
    """The fail-closed core: an un-anchored principal's promote-ATTEST is still RECORDED
    as evidence, but it does not lift anything."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    stranger = kr.mint("stranger", "agent").id
    anchors.install_trust_anchors(weft, root, reckoner=kr.mint("nona.reckoner", "reckoner").id)
    _quarantined_candidate(weft, root, "cap:sum", anchors.PURE)

    _promote(weft, stranger, "cap:sum")

    cap = Weave.fold(weft).get("cap:sum")
    assert cap is not None
    assert cap.content["quarantined"] is True
    assert cap.content["caveats"]["sandbox_only"] is True


def test_a_self_declared_anchor_is_filtered_out():
    """The attack this anchor design exists to stop: a principal asserts its OWN promoter
    cell and then signs its own promotion. Only the genesis author's anchors are honoured,
    so the self-declaration confers nothing."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    attacker = kr.mint("attacker", "agent").id
    # Root writes genesis first, so the attacker can never become `_genesis_author`.
    _quarantined_candidate(weft, root, "cap:evil", anchors.PURE)
    model.assert_content(
        weft,
        attacker,
        anchors.promoter_cell_id(attacker),
        anchors.PROMOTER,
        {"principal": attacker, "tiers": list(anchors.SIGNABLE_TIERS)},
    )

    _promote(weft, attacker, "cap:evil")

    weave = Weave.fold(weft)
    cap = weave.get("cap:evil")
    assert cap is not None
    assert cap.content["quarantined"] is True, "a self-declared anchor must confer nothing"
    assert attacker not in anchors.trusted_promoters(weave)


def test_a_promoter_may_not_sign_outside_its_declared_tiers():
    """Authority is per-tier, not blanket: an anchor for `pure` does not bless a
    `read_only` candidate."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    reckoner = kr.mint(anchors.RECKONER_NAME, "reckoner").id
    anchors.install_trust_anchors(weft, root, reckoner=reckoner, tiers=(anchors.PURE,))
    _quarantined_candidate(weft, root, "cap:reads", anchors.READ_ONLY)

    _promote(weft, reckoner, "cap:reads")

    cap = Weave.fold(weft).get("cap:reads")
    assert cap is not None
    assert cap.content["quarantined"] is True


def test_retracting_the_anchor_withdraws_promotion_authority():
    """Revocation needs no code path of its own: RETRACT the anchor and the next fold
    stops honouring that principal's signatures (fail closed)."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    reckoner = kr.mint(anchors.RECKONER_NAME, "reckoner").id
    anchors.install_trust_anchors(weft, root, reckoner=reckoner)
    _quarantined_candidate(weft, root, "cap:later", anchors.PURE)

    from decima.kernel.weft import RETRACT

    weft.append(root, RETRACT, {"cell": anchors.promoter_cell_id(reckoner), "mode": "WITHDRAW"})
    _promote(weft, reckoner, "cap:later")

    weave = Weave.fold(weft)
    cap = weave.get("cap:later")
    assert cap is not None
    assert cap.content["quarantined"] is True
    assert anchors.trusted_promoters(weave) == {}


# ── the sandbox principal ────────────────────────────────────────────────────
def test_a_sandbox_agent_records_its_quarantine_runtime_durably():
    """`sandbox` is a folded fact, not an inference from the envelope: "was this run
    sandboxed?" stays answerable from the log even after the grants are retracted."""
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    aid = cells.create_agent(
        weft,
        root,
        objective="evaluate a candidate",
        principal=kr.mint(anchors.SANDBOX_NAME, "agent").id,
        sandbox=True,
    )

    agent = Weave.fold(weft).get(aid)
    assert agent is not None
    assert agent.content["sandbox"] is True


def test_an_ordinary_agent_is_not_sandboxed():
    weft, kr = _weft()
    root = kr.mint("root", "root").id
    aid = cells.create_agent(weft, root, objective="do real work", principal=root)

    agent = Weave.fold(weft).get(aid)
    assert agent is not None
    assert agent.content["sandbox"] is False
