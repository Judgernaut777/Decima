"""RETRACT is authorized — the other half of N7.

N7 closed who may WRITE authority (`authorship.refusal`) and left who may TAKE IT AWAY
wide open. Any principal whose key the keyring held could

    weft.append(mallory, RETRACT, {"cell": "<root's capability>"})

and the fold applied it: `retracted = True`, plus the DERIVED_AUTHORITY cascade a capability
RETRACT defaults to, which fails closed every grant descending from it. Mallory gains
nothing by it — which is exactly why R1 got the attention and this did not — but it is a
one-event, unauthenticated shutdown of any organ, any delegation subtree, and (through the
promotion record) any promotion on the log.

WHY THE TESTS ARE SHAPED AROUND THE FOLD. Unlike an ASSERT, a RETRACT body names a `cell`
id and not a type, so `Weft.append` cannot judge it: it would have to look the target up,
and it holds the store lock. So there is no door clause at all here, and two consequences
follow that the tests below pin:

  * a forged RETRACT is RECORDED — `weft.append` returns an event and `provenance` names it
    — and simply does not COUNT. Every assertion is therefore about the FOLD's verdict
    (`cell.retracted`, the cascade, `authorize`), never about an exception at the door;
  * because it is judged in the derived pass, it is judged AFTER the TERMINATE and SUPERSEDE
    substrates are applied — so a forged TERMINATE and a forged SUPERSEDE have to be undone
    too, and each has its own test.

Every test asserts the POSITIVE control alongside the refusal (the authorized principal's
retraction takes effect on the same fixture), because a "stays live" assertion passes
vacuously if the fixture never made the cell retractable in the first place. That is the
N4 trap (`tests/nona/test_promotion.py`), and it is cheap to avoid by proving both
directions on one realm.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from decima.kernel import capability, lifecycle, model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import RETRACT, Weft, WeftError
from decima.services.nona import anchors, promotion

CAP = "capability"
AGENT = "agent"
PROMOTION = "promotion"


class Realm:
    """A realm whose genesis — and therefore whose constitutional ROOT — is `root`, with the
    Reckoner anchored for `SIGNABLE_TIERS` exactly as provisioning anchors it, plus
    `mallory`, who holds a key and no authority whatsoever."""

    def __init__(self) -> None:
        self.kr = Keyring(seed=bytes(32))
        self.weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), self.kr)
        self.root = self.kr.mint("root", "root").id
        self.reckoner = self.kr.mint(anchors.RECKONER_NAME, "reckoner").id
        self.alice = self.kr.mint("alice", "agent").id
        self.mallory = self.kr.mint("mallory", "agent").id
        anchors.install_trust_anchors(self.weft, self.root, reckoner=self.reckoner)

    def weave(self) -> Weave:
        return Weave.fold(self.weft)

    def cell(self, cid: str) -> Cell:
        """The folded cell, asserted present — every test here is about a cell that EXISTS
        and whether its retraction counted, so a missing one is a broken fixture, not a
        result to assert on."""
        found = Weave.fold(self.weft).get(cid)
        assert found is not None, f"expected {cid} to be folded"
        return found

    def retract(self, author: str, cell: str, **body: Any) -> str:
        ev = self.weft.append(author, RETRACT, {"cell": cell, **body})
        return str(ev.id)

    def rows(self) -> list[tuple[str, str, str, str]]:
        return list(
            self.weft.db.execute("SELECT id, payload, author, sig FROM events ORDER BY seq")
        )


def _c(weave: Weave, cid: str) -> Cell:
    """Same non-None accessor for a weave a test already folded once."""
    found = weave.get(cid)
    assert found is not None, f"expected {cid} to be folded"
    return found


def _grant(
    realm: Realm,
    cell: str,
    *,
    granter: str,
    grantee: str,
    parent: str | None = None,
    tier: str | None = anchors.PURE,
) -> str:
    """A capability as the product really mints one: asserted BY its granter (the N7 assert
    rule already requires that), so the only open question is who may take it back."""
    content: dict[str, Any] = {
        "name": cell,
        "effect": "generated_code",
        "quarantined": False,
        "parent": parent,
        "grantee": grantee,
        "granter": granter,
        "caveats": {},
    }
    if tier is not None:
        content["declared_effect_class"] = tier
    model.assert_content(realm.weft, granter, cell, CAP, content)
    return cell


def _promotion(realm: Realm, cap: str, *, signer: str, tier: str = anchors.PURE) -> str:
    """A promotion record asserted by the signer it names (the N7 assert rule), so what is
    under test is purely who may RETRACT it."""
    cell = f"promotion:{cap}"
    model.assert_content(
        realm.weft,
        signer,
        cell,
        PROMOTION,
        {
            "capability": cap,
            "candidate": "cand",
            "evaluation_result": "ev",
            "tier": tier,
            "signer": signer,
            "from_state": "QUARANTINED",
            "to_state": "PROMOTED",
        },
    )
    return cell


# ── the hole, and that it is closed ──────────────────────────────────────────
def test_a_stranger_cannot_retract_a_grant_they_did_not_issue() -> None:
    """The headline. Before this rule the same two lines left `retracted is True`."""
    realm = Realm()
    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)

    realm.retract(realm.mallory, cap)

    cell = realm.cell(cap)
    assert cell is not None
    assert cell.retracted is False, "a retraction from a stranger must not take the grant down"
    # ...and the attempt is still ON THE LOG. Recorded, declined: the same discipline as an
    # unhonoured promote-ATTEST, so an audit can see that mallory tried.
    assert any(payload_author == realm.mallory for _, _, payload_author, _ in realm.rows())


def test_the_granter_may_take_back_what_it_handed_on() -> None:
    """The positive control for the test above, on an identical fixture — without it
    "stays live" would pass even if nothing were retractable at all."""
    realm = Realm()
    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)

    realm.retract(realm.root, cap)

    cell = realm.cell(cap)
    assert cell is not None
    assert cell.retracted is True


def test_a_non_root_granter_may_retract_its_own_delegated_grant() -> None:
    """The ocap rule: authority flows downhill and can be taken back the same way. Alice
    issues to mallory and may withdraw it; mallory may not withdraw alice's."""
    realm = Realm()
    parent = _grant(realm, "cap:parent", granter=realm.root, grantee=realm.alice)
    child = _grant(realm, "cap:child", granter=realm.alice, grantee=realm.mallory, parent=parent)

    realm.retract(realm.mallory, child)
    assert realm.cell(child).retracted is False, "the grantee is not the granter"

    realm.retract(realm.alice, child)
    assert realm.cell(child).retracted is True


def test_a_forged_revoke_does_not_cascade_the_grants_beneath_it() -> None:
    """The damage a capability RETRACT does is not local: the fold defaults it to a
    DERIVED_AUTHORITY cascade. If step 1e ran AFTER the closure walk instead of before it,
    the parent would come back live and the child would stay failed closed."""
    realm = Realm()
    parent = _grant(realm, "cap:parent", granter=realm.root, grantee=realm.alice)
    child = _grant(realm, "cap:child", granter=realm.alice, grantee=realm.mallory, parent=parent)

    realm.retract(realm.mallory, parent)

    weave = realm.weave()
    assert _c(weave, parent).retracted is False
    assert _c(weave, child).retracted is False, "no cascade may derive from a declined revoke"
    assert _c(weave, child).cascaded is False


def test_a_real_revoke_still_cascades() -> None:
    """Positive control for the cascade: root's revoke fails closed the subtree, so the test
    above is about authorship and not about a broken cascade."""
    realm = Realm()
    parent = _grant(realm, "cap:parent", granter=realm.root, grantee=realm.alice)
    child = _grant(realm, "cap:child", granter=realm.alice, grantee=realm.mallory, parent=parent)

    lifecycle.revoke(realm.weft, realm.root, parent)

    weave = realm.weave()
    assert _c(weave, parent).retracted is True
    assert _c(weave, child).retracted is True and _c(weave, child).cascaded is True


# ── the mode substrates, which are applied BEFORE the rule is judged ─────────
def test_a_forged_terminate_does_not_stick() -> None:
    """TERMINATE writes `_terminated` during apply — before the target's type is even known
    — and step 1c re-closes the cell from that substrate on every pass. So the rule has to
    run after 1c, or a forged TERMINATE would be permanently unrecoverable."""
    realm = Realm()
    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)

    lifecycle.terminate(realm.weft, realm.mallory, cap)
    assert realm.cell(cap).retracted is False

    lifecycle.terminate(realm.weft, realm.root, cap)
    cell = realm.cell(cap)
    assert cell.retracted is True and cell.cascade_root is True


def test_a_forged_supersede_does_not_redirect_the_cell() -> None:
    """SUPERSEDE is the subtler one: it points `superseded_by` at a replacement, so
    `Weave.current()` would resolve a grant to a cell of the forger's choosing."""
    realm = Realm()
    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)
    evil = _grant(realm, "cap:evil", granter=realm.mallory, grantee=realm.mallory)

    lifecycle.supersede(realm.weft, realm.mallory, cap, replacement=evil)

    cell = realm.cell(cap)
    assert cell.retracted is False
    assert cell.superseded_by is None, "a stranger may not redirect a grant to their own cell"

    lifecycle.supersede(realm.weft, realm.root, cap, replacement=evil)
    assert realm.cell(cap).superseded_by == evil


# ── promotions: demotion is an authority decision ────────────────────────────
def test_a_stranger_cannot_demote_a_promoted_organ() -> None:
    """Quarantine is derived from promotion liveness (N4), so retracting the promotion
    record re-quarantines the organ. That made an unauthorized RETRACT a way to switch any
    promoted organ off."""
    realm = Realm()
    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)
    prom = _promotion(realm, cap, signer=realm.reckoner)

    realm.retract(realm.mallory, prom)

    weave = realm.weave()
    assert _c(weave, prom).retracted is False
    assert _c(weave, cap).content.get("quarantined") is not True, "the organ must stay live"


def test_the_signer_may_take_back_its_own_promotion() -> None:
    """N4's whole mechanism — `promotion.rollback` is a RETRACT — still works, and this is
    the positive control for the test above."""
    realm = Realm()
    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)
    prom = _promotion(realm, cap, signer=realm.reckoner)

    promotion.rollback(realm.weft, realm.reckoner, prom, reason="re-evaluate")

    weave = realm.weave()
    assert _c(weave, prom).retracted is True
    assert _c(weave, cap).content.get("quarantined") is True


def test_an_anchored_promoter_may_demote_an_organ_it_did_not_promote() -> None:
    """The clause the canary needs. `monitor.monitor_canary` demotes on a breach and revokes
    on a HIGH finding; it is not necessarily the principal that signed the promotion, so
    without the anchored-promoter clause an automatic containment action would be declined by
    the fold — the automation would appear to work and change nothing."""
    realm = Realm()
    other = realm.kr.mint("second-promoter", "reckoner").id
    anchors.install_trust_anchors(realm.weft, realm.root, reckoner=other)
    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)
    prom = _promotion(realm, cap, signer=realm.reckoner)

    realm.retract(other, prom)

    assert realm.cell(prom).retracted is True


def test_a_principal_cannot_anchor_itself_into_retraction_authority() -> None:
    """The anchored clause is the one part of this rule that reads OTHER cells, so the
    obvious attack is to write the cell it reads. Two layers already stop that and this
    pins both: N7's assert rule refuses the self-anchor AT THE DOOR, and even taking the
    write as given the fold honours a `promoter` only from the genesis author — so the
    retraction is declined either way."""
    realm = Realm()
    try:
        anchors.install_trust_anchors(realm.weft, realm.mallory, reckoner=realm.mallory)
        raise AssertionError("a self-declared promoter anchor must be refused at the door")
    except WeftError as exc:
        assert "only the realm root may assert a `promoter`" in str(exc)

    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)
    prom = _promotion(realm, cap, signer=realm.reckoner)
    realm.retract(realm.mallory, prom)

    assert realm.cell(prom).retracted is False


def test_a_tier_less_grant_is_not_an_unguarded_retraction_path() -> None:
    """The legacy shape that declares no effect class routed to "any promoter" before N7.
    It must not become the way around this rule either."""
    realm = Realm()
    cap = _grant(realm, "cap:legacy", granter=realm.root, grantee=realm.alice, tier=None)

    realm.retract(realm.mallory, cap)

    assert realm.cell(cap).retracted is False


# ── the trust anchors and the sandbox principal ──────────────────────────────
def test_only_root_may_withdraw_a_trust_anchor() -> None:
    """Withdrawing an anchor un-anchors every promotion that names it — the same authority
    as declaring one, so the same rule."""
    realm = Realm()
    anchor = anchors.promoter_cell_id(realm.reckoner)

    realm.retract(realm.mallory, anchor)
    assert realm.cell(anchor).retracted is False
    assert realm.reckoner in anchors.trusted_promoters(realm.weave())

    realm.retract(realm.root, anchor)
    assert realm.cell(anchor).retracted is True
    assert realm.reckoner not in anchors.trusted_promoters(realm.weave())


def test_only_root_may_withdraw_the_sandbox_principal() -> None:
    """Retracting the sandbox agent would strand every quarantined organ that needs it to
    run at all — a denial of service against the whole evaluation path."""
    realm = Realm()
    model.assert_content(
        realm.weft,
        realm.root,
        "agent:sandbox",
        AGENT,
        {"principal": "agent:sandbox", "envelope": [], "sandbox": True},
    )

    realm.retract(realm.mallory, "agent:sandbox")
    assert realm.cell("agent:sandbox").retracted is False

    realm.retract(realm.root, "agent:sandbox")
    assert realm.cell("agent:sandbox").retracted is True


def test_an_ordinary_cell_is_still_freely_retractable() -> None:
    """The rule covers `authorship.GUARDED_TYPES` and nothing else. Binding every cell's
    retraction to its author would break right-to-be-forgotten and ordinary status writes;
    that this is a residual rather than a fix is stated in SECURITY.md."""
    realm = Realm()
    model.assert_content(realm.weft, realm.alice, "note:1", "note", {"text": "hello"})

    realm.retract(realm.mallory, "note:1")

    assert realm.cell("note:1").retracted is True


# ── across sync: INGESTED everywhere, HONOURED nowhere ───────────────────────
def test_a_synced_forged_retraction_ingests_and_still_confers_nothing() -> None:
    """A forged retraction crosses sync like any other event and is declined on the far side
    too, because the rule lives in the fold and every peer folds.

    This is the one place the RETRACT rule deliberately DIVERGES from N7's assert rule, which
    refuses a forgery at the acceptance gate outright. The next test is why."""
    origin = Realm()
    cap = _grant(origin, "cap:organ", granter=origin.root, grantee=origin.alice)
    forged = origin.retract(origin.mallory, cap)

    peer = Weft(os.path.join(tempfile.mkdtemp(), "peer.db"), origin.kr)
    results = [peer.ingest(row) for row in origin.rows()]

    assert all(r in ("ingested", "duplicate") for r in results), results
    assert forged in {ev.id for ev in peer.events()}, "recorded — the attempt is evidence"
    assert _c(Weave.fold(peer), cap).retracted is False, "...and honoured nowhere"


def test_refusing_a_retraction_at_the_gate_would_orphan_the_honest_events_after_it() -> None:
    """WHY there is no acceptance-gate clause, stated as a test rather than a comment.

    The door cannot judge a RETRACT, so an ORDINARY HONEST log contains retractions the fold
    declines — unlike a forged `capability`/`promoter`, which `append` refuses outright, so a
    well-behaved log never holds one and orphaning a hostile peer's tail costs nothing.

    On a linear log every later event names the previous one as its parent. So if the gate
    refused the forged retraction, the LEGITIMATE rollback that follows it would name an event
    the peer does not have, and stay an orphan forever. The assertion below is that the real
    rollback lands — which is only possible because the forgery was ingested ahead of it."""
    origin = Realm()
    cap = _grant(origin, "cap:organ", granter=origin.root, grantee=origin.alice)
    prom = _promotion(origin, cap, signer=origin.reckoner)
    forged = origin.retract(origin.mallory, prom)  # declined by the fold, but ON the log
    promotion.rollback(origin.weft, origin.reckoner, prom, reason="the real one")

    peer = Weft(os.path.join(tempfile.mkdtemp(), "peer.db"), origin.kr)
    for row in origin.rows():
        peer.ingest(row)

    folded = Weave.fold(peer)
    assert forged in {ev.id for ev in peer.events()}
    assert _c(folded, prom).retracted is True, "the legitimate rollback must not be orphaned"
    assert _c(folded, cap).content.get("quarantined") is True
    assert folded.state_root() == origin.weave().state_root(), "no fork between peers"


def test_a_legitimate_retraction_replicates_normally() -> None:
    """Positive control for sync: root's own revoke crosses and both sides agree exactly."""
    origin = Realm()
    cap = _grant(origin, "cap:organ", granter=origin.root, grantee=origin.alice)
    lifecycle.revoke(origin.weft, origin.root, cap)

    peer = Weft(os.path.join(tempfile.mkdtemp(), "peer.db"), origin.kr)
    for row in origin.rows():
        peer.ingest(row)

    assert _c(Weave.fold(peer), cap).retracted is True
    assert Weave.fold(peer).state_root() == origin.weave().state_root()


# ── determinism (Law 5) ──────────────────────────────────────────────────────
def test_the_verdict_is_identical_on_a_refold_and_under_any_arrival_order() -> None:
    """The rule lives in the derived pass, so it must be a pure function of the folded graph:
    same events, same verdict, however often it runs and whatever order they arrived in.

    Two peers handed the same event set in OPPOSITE orders must agree (FOLD §11.2) — that is
    the property step 1e could plausibly break, since it is the only pass that turns a
    `retracted` flag back off."""
    realm = Realm()
    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)
    prom = _promotion(realm, cap, signer=realm.reckoner)
    realm.retract(realm.mallory, cap)
    realm.retract(realm.mallory, prom)
    promotion.rollback(realm.weft, realm.reckoner, prom, reason="the real one")

    assert realm.weave().state_root() == realm.weave().state_root(), "not idempotent"

    rows = list(realm.rows())
    roots = []
    for order in (rows, list(reversed(rows))):
        peer = Weft(os.path.join(tempfile.mkdtemp(), "peer.db"), realm.kr)
        # A reversed feed hands every event over before its parent, so each pass admits one
        # more link of the chain: retry until it stops making progress rather than guessing a
        # pass count (`ingest` returns "orphan" for an event whose parents are not present).
        seen = -1
        while len(list(peer.events())) != seen:
            seen = len(list(peer.events()))
            for row in order:
                peer.ingest(row)
        assert len(list(peer.events())) == len(rows), "the whole log must converge"
        roots.append(Weave.fold(peer).state_root())
    assert roots[0] == roots[1]

    # And the outcome the events describe holds on the peer: the real rollback landed, the
    # forged revoke of the capability did not.
    folded = Weave.fold(peer)
    assert _c(folded, prom).retracted is True
    assert _c(folded, cap).content.get("quarantined") is True


def test_one_authorized_retraction_among_forgeries_still_takes_the_cell_down() -> None:
    """`_retract_authors` is a LIST, not a winner: the question is whether ANY retraction was
    authorized, so noise from a stranger can neither cause nor prevent the real one."""
    realm = Realm()
    cap = _grant(realm, "cap:organ", granter=realm.root, grantee=realm.alice)

    realm.retract(realm.mallory, cap)
    realm.retract(realm.root, cap)
    realm.retract(realm.mallory, cap)

    assert realm.cell(cap).retracted is True


def test_a_lapsed_lease_still_fails_closed() -> None:
    """Lease expiry is DERIVED, not a RETRACT — step 1e must not resurrect a grant whose
    lease simply ran out, which has no `_retract_authors` entry to judge at all."""
    realm = Realm()
    model.assert_content(
        realm.weft,
        realm.root,
        "cap:leased",
        CAP,
        capability.capability_content(
            "leased",
            "generated_code",
            quarantined=False,
            parent=None,
            grantee=realm.alice,
            granter=realm.root,
            caveats={"max_uses": 0},
        ),
    )

    cell = realm.cell("cap:leased")
    assert cell.retracted is True and cell.lease_expired is True
