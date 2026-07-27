"""Nona N7: `ASSERT` is authorized, and design risk R1 is closed.

R1 (design §5.7 point 3 / §6) was the widest hole in the trust model, and it was not a
missing check — it was that every check `capability.authorize` makes is a READ of the
folded graph, and `Weft.append` let any key-holding principal WRITE that graph. The
attack, quoted from the design:

    any principal whose key the keyring holds can ASSERT a capability Cell with
    `quarantined: False`, `grantee: <self>`, `parent: None` AND an Agent Cell whose
    `envelope` contains it — and `authorize` will pass.

Every test below is written so it FAILS if the rule is removed. They assert a SPECIFIC
denial code or a SPECIFIC refusal, never `code != <the old code>` — the vacuous shape N4
shipped (`tests/nona/test_promotion.py:414`, a `!= QUARANTINED` assertion on a capability
that also carried `requires_approval`, which passed whether the promotion did anything or
not). Verified by deleting each rule and watching the corresponding test go red.

WHAT THE THREE LAYERS ARE FOR, because the tests are organised around them:

  * the WRITE DOOR (`Weft.append` → `decima.kernel.authorship.refusal`) refuses a local
    write it can judge from the body alone. It is hygiene: it protects only what THIS
    process writes NOW.
  * the ACCEPTANCE GATE (`Weft.ingest` → `acceptance.recheck_assert_authority`) applies the
    same rule to a synced event, judged at that event's CAUSAL FRONTIER.
  * the FOLD and the READ (`Weave.cell_asserted_by` → `capability.verify_delegation`,
    `Weave._cascade_retractions`, the sandbox conferral) are the actual BOUNDARY: they are
    what holds for a log already on disk, a restored backup, or a forgery whose own
    frontier made its author root. `test_a_forgery_whose_own_frontier_crowned_it_still
    _confers_nothing` is that case, and it is the one that proves a door-only N7 would
    have been theatre.

One rule lives ONLY in the fold, because no write door can express it: which of a guarded
cell's CONCURRENT assertions the realm MATERIALIZES. Deciding that needs the folded head set
and each head's author, so `Weave._may_supersede_head` gates it and `Weave.cell_asserted_by`
derives its answer from the head that actually materialized. Those tests are the
"── the ADJUDICATION pivot ──" section below, and they are the ones that caught R1 reopening
after N7 shipped: two events (a concurrent self-grant plus one adjudication ATTEST) put the
content of one principal behind the attribution of another.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest

from decima.kernel import capability, model
from decima.kernel.capability import DenialCode
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import ASSERT, ATTEST, Weft, WeftError
from decima.runtime import cells
from decima.services.nona import anchors, candidate, executor, promotion, reckoner
from decima.services.nona.reckoner import Metrics

CAP = "capability"
AGENT = "agent"
PROMOTION = "promotion"
PURE_SOURCE = "def main(x):\n    return x + 1\n"


class Realm:
    """A realm whose genesis — and therefore whose constitutional ROOT — is `root`, with
    the Reckoner anchored exactly as `provision.first_run` anchors it, plus a hostile
    principal `mallory` that holds a key and nothing else."""

    def __init__(self) -> None:
        self.kr = Keyring(seed=bytes(32))
        self.weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), self.kr)
        self.root = self.kr.mint("root", "root").id
        self.reckoner = self.kr.mint(anchors.RECKONER_NAME, "reckoner").id
        self.mallory = self.kr.mint("mallory", "agent").id
        # Root writes the genesis event, so no later principal can become `_genesis_author`
        # (a later parentless event necessarily gets a higher seq — see `Weave._apply`).
        anchors.install_trust_anchors(self.weft, self.root, reckoner=self.reckoner)

    def weave(self) -> Weave:
        return Weave.fold(self.weft)

    def cell(self, cid: str) -> Cell:
        cell = self.weave().get(cid)
        assert cell is not None, f"expected {cid} to be folded"
        return cell

    def rows(self) -> list[tuple[str, str, str, str]]:
        return _rows(self.weft)


def _real_quarantined_organ(realm: Realm, cell: str = "cap:organ") -> str:
    """A capability as Nona really mints one: ROOT-asserted, QUARANTINED, `sandbox_only`,
    carrying the declared tier its promotion must be signed against, and granted to
    mallory — so every check EXCEPT quarantine already passes for mallory. That is what
    makes the forged-promotion test non-vacuous: the only thing standing between mallory
    and the organ is the promotion record's authorship."""
    model.assert_content(
        realm.weft,
        realm.root,
        cell,
        CAP,
        {
            "name": "organ",
            "effect": "generated_code",
            "declared_effect_class": anchors.PURE,
            "quarantined": True,
            "parent": None,
            "grantee": realm.mallory,
            "granter": realm.root,
            "caveats": {"sandbox_only": True},
        },
    )
    return cell


def _assert_on_branch(
    realm: Realm,
    author: str,
    cell: str,
    cell_type: str,
    content: dict[str, Any],
    parents: list[str],
) -> Any:
    """An ASSERT on an explicit causal branch — the only way to create a CONCURRENT head,
    and the primitive the adjudication attacks below are built from."""
    return realm.weft.append(
        author,
        ASSERT,
        {"cell": cell, "type": cell_type, "kind": "CONTENT", "content": content},
        parents=parents,
    )


def _adjudicate(weft: Weft, author: str, cell: str, *, winner: str, evidence: list[str]) -> Any:
    """The adjudication ATTEST of MERGE_SEMANTICS §4.1: SELECT one head, supersede the
    others it names as `evidence`."""
    return weft.append(
        author,
        ATTEST,
        {
            "target_cell": cell,
            "predicate": "adjudicates",
            "resolution": "select",
            "winner": winner,
            "evidence": list(evidence),
        },
    )


def _rows(weft: Weft) -> list[tuple[str, str, str, str]]:
    """The raw sync rows of any Weft, in commit order — what a peer would hand over."""
    return list(weft.db.execute("SELECT id, payload, author, sig FROM events ORDER BY seq ASC"))


def _mallory_agent(realm: Realm, envelope: list[str], *, sandbox: bool = False) -> str:
    body: dict[str, Any] = {"principal": realm.mallory, "envelope": list(envelope)}
    if sandbox:
        body["sandbox"] = True
    model.assert_content(realm.weft, realm.mallory, "agent:mal", AGENT, body)
    return "agent:mal"


# ── the attack from design §5.7 point 3, reproduced verbatim ──────────────────


def test_the_documented_r1_attack_mints_a_grant_that_authorizes_nothing() -> None:
    """§5.7 point 3, as written: a self-asserted capability (`quarantined: False`,
    `grantee: self`, `parent: None`) plus a self-asserted agent envelope holding it.

    Before N7 this returned `(True, "ok")` — every check passed, on inputs the attacker
    wrote. Now the one check the attacker does not control fires: `verify_delegation` asks
    WHO ASSERTED the cell, and a root grant asserted by a non-root, non-anchored principal
    confers nothing. The cell is still on the log — the write door cannot tell this shape
    apart from the Reckoner minting an organ — and that is exactly the point: refusing to
    DERIVE authority is the security property, not refusing the write."""
    realm = Realm()
    model.assert_content(
        realm.weft,
        realm.mallory,
        "cap:evil",
        CAP,
        capability.capability_content(
            "evil",
            "shell",
            quarantined=False,
            parent=None,
            grantee=realm.mallory,
            granter=realm.mallory,  # naming yourself granter satisfies the door, not the fold
        ),
    )
    agent = _mallory_agent(realm, ["cap:evil"])
    weave = realm.weave()

    assert weave.get("cap:evil") is not None, "the forgery is on the log; it just buys nothing"
    allowed, why, code = capability.authorize_detail(
        weave, realm.cell(agent), "cap:evil", {}, realm.mallory
    )
    assert allowed is False
    assert code == DenialCode.UNAUTHORIZED_GRANT
    assert "is not authority" in why


def test_a_grant_written_in_someone_elses_name_never_reaches_the_log() -> None:
    """The other half of the same forgery: keep root as `granter` so the chain check would
    pass, and write the cell yourself. That IS decidable from the body alone, so the door
    refuses it and nothing is recorded (fail closed, the rotation check's precedent)."""
    realm = Realm()
    before = realm.weft.count()

    with pytest.raises(WeftError, match="must be asserted by its own `granter`"):
        model.assert_content(
            realm.weft,
            realm.mallory,
            "cap:evil",
            CAP,
            capability.capability_content(
                "evil", "shell", grantee=realm.mallory, granter=realm.root
            ),
        )

    assert realm.weft.count() == before
    assert realm.weave().get("cap:evil") is None


def test_hijacking_a_live_grant_by_re_asserting_it_is_refused_at_the_door() -> None:
    """An ASSERT is last-writer-wins, so before N7 the cheapest attack on a REAL grant was
    to re-assert it with a new `grantee`. Keeping root's `granter` (which the chain check
    needs) is refused at the door; changing it to yourself makes the grant a self-issued
    root grant, which the fold refuses — there is no third option."""
    realm = Realm()
    organ = _real_quarantined_organ(realm)
    stranger = realm.kr.mint("stranger", "agent").id
    live = realm.cell(organ).content

    with pytest.raises(WeftError, match="must be asserted by its own `granter`"):
        model.assert_content(realm.weft, realm.mallory, organ, CAP, {**live, "grantee": stranger})
    assert realm.cell(organ).content["grantee"] == realm.mallory, "the live grant is untouched"


# ── the hole N4 opened: the promotion record's own authorship ─────────────────


def test_a_forged_promotion_record_cannot_lift_a_real_quarantine() -> None:
    """N4's derived quarantine reads `promotion.content['signer']` and, until N7, never
    asked who WROTE the record. So mallory could assert `{capability: <a real root-asserted
    quarantined cap>, tier: 'pure', signer: <the Reckoner's pid>}` and the FOLD would lift
    quarantine and strip `sandbox_only` on the strength of a field mallory chose. That
    defeated the whole N1–N4 anchor mechanism, and the design (written pre-N4) does not
    name it.

    The organ here is real: root asserted it, mallory is its grantee, and quarantine is the
    only thing denying the invocation — so if the forgery worked, this test would see
    `(True, "ok")`."""
    realm = Realm()
    organ = _real_quarantined_organ(realm)
    before = realm.weft.count()

    with pytest.raises(WeftError, match="must be asserted by the signer it names"):
        model.assert_content(
            realm.weft,
            realm.mallory,
            "promotion:forged",
            PROMOTION,
            {"capability": organ, "tier": anchors.PURE, "signer": realm.reckoner},
        )
    assert realm.weft.count() == before

    agent = _mallory_agent(realm, [organ])
    cap = realm.cell(organ)
    assert cap.content["quarantined"] is True
    assert cap.content["caveats"]["sandbox_only"] is True
    _allowed, _why, code = capability.authorize_detail(
        realm.weave(), realm.cell(agent), organ, {}, realm.mallory
    )
    assert code == DenialCode.QUARANTINED


def test_a_promotion_record_the_fold_already_holds_is_re_checked_by_the_fold() -> None:
    """The door is not the boundary, so the fold must refuse the same forgery on its own.
    Mallory writes the record on ITS OWN log — where mallory is the genesis author, so the
    door permits it — and syncs it into the realm. Acceptance takes it (at that event's
    frontier mallory really is root; see the module docstring), and the derived-quarantine
    pass then refuses to count it, because `cell_asserted_by` says mallory wrote a record naming
    the Reckoner as signer."""
    realm = Realm()
    organ = _real_quarantined_organ(realm)

    hostile = Weft(os.path.join(tempfile.mkdtemp(), "hostile.db"), realm.kr)
    model.assert_content(
        hostile,
        realm.mallory,
        "promotion:forged",
        PROMOTION,
        {"capability": organ, "tier": anchors.PURE, "signer": realm.reckoner},
    )
    statuses = [
        realm.weft.ingest(row)
        for row in hostile.db.execute("SELECT id, payload, author, sig FROM events ORDER BY seq")
    ]
    assert statuses == ["ingested"], "the forgery IS on the log — the fold is what refuses it"

    weave = realm.weave()
    assert weave.get("promotion:forged") is not None
    cap = weave.get(organ)
    assert cap is not None
    # The security property FIRST, so this test goes red on the defect and not on a missing
    # accessor when the rule is stashed out (that is how the N4 hole was demonstrated).
    assert cap.content["quarantined"] is True, "a promotion its signer did not write is not one"
    assert cap.content["caveats"]["sandbox_only"] is True
    assert weave.cell_asserted_by("promotion:forged") == realm.mallory


def test_a_forgery_whose_own_frontier_crowned_it_still_confers_nothing() -> None:
    """The case a write-door-only N7 would have missed entirely, and the reason the rule
    lives in the fold: a PARENTLESS forged event. Its causal frontier is empty, so no root
    is anchored in it and the acceptance gate cannot judge it — it ingests. In the realm's
    own fold, though, the genesis anchor is root, and every read refuses: the promoter
    anchor is filtered, the self-issued grant is `UNAUTHORIZED_GRANT`."""
    realm = Realm()
    hostile = Weft(os.path.join(tempfile.mkdtemp(), "hostile.db"), realm.kr)
    # On its own virgin log mallory writes the genesis, so mallory IS root there.
    model.assert_content(
        hostile,
        realm.mallory,
        anchors.promoter_cell_id(realm.mallory),
        anchors.PROMOTER,
        {"principal": realm.mallory, "tiers": list(anchors.SIGNABLE_TIERS)},
    )
    model.assert_content(
        hostile,
        realm.mallory,
        "cap:evil",
        CAP,
        capability.capability_content(
            "evil", "shell", quarantined=False, grantee=realm.mallory, granter=realm.mallory
        ),
    )
    rows = list(hostile.db.execute("SELECT id, payload, author, sig FROM events ORDER BY seq ASC"))
    assert [json.loads(r[1])["parents"] == [] for r in rows][0] is True
    assert [realm.weft.ingest(r) for r in rows] == ["ingested", "ingested"]

    agent = _mallory_agent(realm, ["cap:evil"])
    weave = realm.weave()
    allowed, _why, code = capability.authorize_detail(
        weave, realm.cell(agent), "cap:evil", {}, realm.mallory
    )
    assert allowed is False, "a self-issued grant that arrived by sync is still self-issued"
    assert code == DenialCode.UNAUTHORIZED_GRANT
    assert realm.mallory not in anchors.trusted_promoters(weave)
    assert weave.genesis_author() == realm.root, "a later parentless event never becomes root"


# ── the sandbox privilege, and the tier-less lift the design left open ────────


def test_a_self_minted_sandbox_agent_is_refused_at_the_door() -> None:
    """`sandbox` is the one flag that makes a QUARANTINED capability invocable and satisfies
    the `sandbox_only` Morta caveat, so minting your own sandbox agent would be promoting
    yourself out of quarantine by declaration."""
    realm = Realm()
    organ = _real_quarantined_organ(realm)

    with pytest.raises(WeftError, match="only the realm root may assert an agent cell"):
        _mallory_agent(realm, [organ], sandbox=True)
    assert realm.weave().get("agent:mal") is None


def test_an_agent_cell_that_claims_sandbox_without_root_confers_nothing_at_read_time() -> None:
    """The read-side half, which is the half that holds for a log already on disk: an agent
    cell carrying `sandbox` that root did not assert is denied `UNAUTHORIZED_SANDBOX`, so it
    cannot be laundered into the quarantine bypass even if it reached the log by a path this
    build's door never saw."""
    realm = Realm()
    organ = _real_quarantined_organ(realm)
    hostile = Weft(os.path.join(tempfile.mkdtemp(), "hostile.db"), realm.kr)
    model.assert_content(
        hostile,
        realm.mallory,
        "agent:mal",
        AGENT,
        {"principal": realm.mallory, "envelope": [organ], "sandbox": True},
    )
    for row in hostile.db.execute("SELECT id, payload, author, sig FROM events ORDER BY seq"):
        realm.weft.ingest(row)

    weave = realm.weave()
    agent = weave.get("agent:mal")
    assert agent is not None and agent.content["sandbox"] is True
    _allowed, _why, code = capability.authorize_detail(weave, agent, organ, {}, realm.mallory)
    assert code == DenialCode.UNAUTHORIZED_SANDBOX


def test_a_tier_less_capability_can_no_longer_lift_its_own_quarantine() -> None:
    """The residual the design NAMED and did not close (§6 R1: "a self-asserted TIER-LESS
    capability still gets the legacy any-attest lift"). `_is_trusted_promoter` returned True
    for every principal when the capability declared no tier, so mallory could assert a
    quarantined tier-less grant and lift it with its own promote-ATTEST — which would have
    made the rest of this wave decorative. A tier-less capability now requires an ANCHORED
    promoter too."""
    realm = Realm()
    model.assert_content(
        realm.weft,
        realm.mallory,
        "cap:tierless",
        CAP,
        capability.capability_content(
            "tierless",
            "shell",
            quarantined=True,
            grantee=realm.mallory,
            granter=realm.mallory,
            caveats={"sandbox_only": True},
        ),
    )
    realm.weft.append(
        realm.mallory, ATTEST, {"target_cell": "cap:tierless", "promote": True, "claim": "ok"}
    )
    agent = _mallory_agent(realm, ["cap:tierless"])

    cap = realm.cell("cap:tierless")
    assert cap.content["quarantined"] is True, "an unanchored principal lifts nothing"
    assert cap.content["caveats"]["sandbox_only"] is True
    allowed, _why, _code = capability.authorize_detail(
        realm.weave(), realm.cell(agent), "cap:tierless", {}, realm.mallory
    )
    assert allowed is False


def test_the_anchored_reckoner_still_lifts_a_tier_less_capability() -> None:
    """The positive control for the clause above: narrowing "anyone" to "an anchored
    promoter" must not become "nobody", or the detection/forge paths that rely on the
    tier-less lift would silently stop working."""
    realm = Realm()
    model.assert_content(
        realm.weft,
        realm.root,
        "cap:legacy",
        CAP,
        capability.capability_content(
            "legacy", "echo", quarantined=True, grantee=realm.root, granter=realm.root
        ),
    )
    realm.weft.append(
        realm.reckoner, ATTEST, {"target_cell": "cap:legacy", "promote": True, "claim": "ok"}
    )
    assert realm.cell("cap:legacy").content["quarantined"] is False


# ── the legitimate paths must keep working (positive controls) ────────────────


def test_the_reckoner_still_mints_promotes_and_runs_a_real_organ() -> None:
    """The whole N2→N5 loop as the product actually drives it: the Reckoner (root-anchored,
    NOT root) proposes a candidate, mints the organ grant as its own granter, signs the
    promotion, and the holder's invocation authorizes. If N7's rule were "only root may
    assert a capability", this would be red — which is precisely why the rule is
    "root, or a principal root anchored", expressed as a pure function of folded state."""
    realm = Realm()
    holder = realm.kr.mint("holder", "operator").id
    proposed = candidate.propose_candidate(
        realm.weft,
        realm.reckoner,
        intent="add one",
        declared_effect_class=anchors.PURE,
        source=PURE_SOURCE,
        output_schema={"type": "int"},
    )
    built = executor.build_capability(
        realm.weft,
        realm.weave(),
        realm.reckoner,
        candidate=proposed["cell"],
        tier=anchors.PURE,
        name="add_one",
        grantee=holder,
        granter=realm.reckoner,
    )
    cap_id = built["capability"]
    assert realm.cell(cap_id).content["quarantined"] is True

    verdict = reckoner.gate(
        Metrics(deterministic_cases=2, deterministic_pass=2, hostile_cases=1, hostile_contained=1)
    )
    evaluation = reckoner.record_result(
        realm.weft,
        realm.reckoner,
        candidate_cell=proposed["cell"],
        suite_cell="suite:add_one",
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
    promotion.promote(
        realm.weft,
        realm.weave(),
        realm.reckoner,
        capability=cap_id,
        candidate=proposed["cell"],
        evaluation=evaluation,
        tier=anchors.PURE,
    )
    agent = cells.create_agent(
        realm.weft,
        realm.root,
        objective="use the organ",
        principal=holder,
        capability_grant_ids=[cap_id],
    )

    cap = realm.cell(cap_id)
    assert cap.content["quarantined"] is False, "a real promotion still lifts"
    allowed, why, code = capability.authorize_detail(
        realm.weave(), realm.cell(agent), cap_id, {}, holder
    )
    assert (allowed, why, code) == (True, "ok", DenialCode.OK)


def test_a_root_asserted_sandbox_agent_still_runs_a_quarantined_organ() -> None:
    """The quarantine RUNTIME must survive N7 or nothing can ever be evaluated: the point of
    a sandbox agent is to invoke a not-yet-promoted capability. Root confers it; that is the
    whole difference from the refused case above."""
    realm = Realm()
    sandbox_pid = realm.kr.mint(anchors.SANDBOX_NAME, "agent").id
    model.assert_content(
        realm.weft,
        realm.root,
        "cap:organ",
        CAP,
        {
            "effect": "generated_code",
            "declared_effect_class": anchors.PURE,
            "quarantined": True,
            "parent": None,
            "grantee": sandbox_pid,
            "granter": realm.root,
            "caveats": {"sandbox_only": True},
        },
    )
    agent = cells.create_agent(
        realm.weft,
        realm.root,
        objective="evaluate a candidate",
        principal=sandbox_pid,
        capability_grant_ids=["cap:organ"],
        sandbox=True,
    )

    allowed, why, code = capability.authorize_detail(
        realm.weave(), realm.cell(agent), "cap:organ", {}, sandbox_pid
    )
    assert (allowed, why, code) == (True, "ok", DenialCode.OK)


def test_a_delegated_grant_still_walks_to_root_when_every_hop_wrote_its_own() -> None:
    """Attenuation is the product's normal path and it must stay legal: root issues a source
    grant to a broker, the broker issues a narrowed child to a holder, and each hop is
    ASSERTED by the principal that issued it. The chain therefore satisfies both the old
    check (`granter == parent.grantee`) and N7's authorship check at every hop."""
    realm = Realm()
    broker = realm.kr.mint("broker", "broker").id
    holder = realm.kr.mint("holder", "operator").id
    source = capability.capability_content(
        "echo", "echo", caveats={"budget": 10}, grantee=broker, granter=realm.root
    )
    model.assert_content(realm.weft, realm.root, "cap:source", CAP, source)
    child = capability.attenuate(
        source, {"budget": 5}, "cap:source", grantee=holder, granter=broker
    )
    model.assert_content(realm.weft, broker, "cap:child", CAP, child)
    agent = cells.create_agent(
        realm.weft,
        realm.root,
        objective="use the delegated grant",
        principal=holder,
        capability_grant_ids=["cap:child"],
    )

    weave = realm.weave()
    valid, why, code = capability.verify_delegation_detail(weave, realm.cell("cap:child"))
    assert (valid, why, code) == (True, "ok", DenialCode.OK)
    allowed, _why, _code = capability.authorize_detail(
        weave, realm.cell(agent), "cap:child", {"cost": 1}, holder
    )
    assert allowed is True


def test_a_delegated_grant_someone_else_wrote_is_refused_at_the_broker_hop() -> None:
    """The delegated half of the authorship rule, tested where it bites: mallory's child of
    a REAL source grant. The chain is well-formed (`granter == parent.grantee`, caveats
    downhill, parent live), so before N7 it authorized; the only defect is that mallory
    wrote a grant naming the broker as granter — which the door refuses, and which the fold
    refuses for any log that already holds one."""
    realm = Realm()
    broker = realm.kr.mint("broker", "broker").id
    source = capability.capability_content(
        "echo", "echo", caveats={"budget": 10}, grantee=broker, granter=realm.root
    )
    model.assert_content(realm.weft, realm.root, "cap:source", CAP, source)
    child = capability.attenuate(
        source, {"budget": 5}, "cap:source", grantee=realm.mallory, granter=broker
    )

    with pytest.raises(WeftError, match="must be asserted by its own `granter`"):
        model.assert_content(realm.weft, realm.mallory, "cap:child", CAP, child)

    hostile = Weft(os.path.join(tempfile.mkdtemp(), "hostile.db"), realm.kr)
    model.assert_content(hostile, realm.mallory, "cap:child", CAP, child)
    for row in hostile.db.execute("SELECT id, payload, author, sig FROM events ORDER BY seq"):
        realm.weft.ingest(row)
    agent = _mallory_agent(realm, ["cap:child"])

    weave = realm.weave()
    assert weave.get("cap:child") is not None
    _valid, why, code = capability.verify_delegation_detail(weave, realm.cell("cap:child"))
    assert code == DenialCode.UNAUTHORIZED_GRANT
    assert "names" in why and "granter" in why
    _allowed, _why, acode = capability.authorize_detail(
        weave, realm.cell(agent), "cap:child", {"cost": 1}, realm.mallory
    )
    assert acode == DenialCode.UNAUTHORIZED_GRANT


# ── the ADJUDICATION pivot: attribution must name the head that materialized ──
#
# N7 as first shipped recorded authorship per CELL, filled from the max-(lamport, event_id)
# ASSERT, on the reasoning that the max-order assert is always the head the register
# materialized. That is true of ASSERTs alone and FALSE once an adjudication ATTEST
# (MERGE_SEMANTICS §4) is in play: `select` moves `_reg_superseded`, so `content` becomes a
# concurrent branch while the recorded author still named the branch adjudicated AWAY. The
# content `authorize` trusted and the asserter it was attributed to were then different
# principals — which reopened R1 in full through all three of its read-side rules.
#
# Two independent layers close it, and the tests below are split along that seam:
#   * `Weave.cell_asserted_by` is DERIVED from `_reg_live(cid)[-1]`, so the author it names
#     is by construction the author of the bytes in `content`. Tested with a LEGITIMATE
#     (root-authored) adjudication, where the gate below cannot mask the answer.
#   * `Weave._may_supersede_head` refuses to let a principal supersede a guarded head it did
#     not write, so the pivot cannot happen at all. Tested with mallory's own ATTEST.


def _forked_grant(realm: Realm, cell: str = "cap:echo") -> tuple[Any, Any]:
    """Two mutually concurrent assertions of the SAME capability cell, with root's at a
    strictly HIGHER lamport so it is the max-order head by construction (not by whichever
    way the event ids happened to sort). Root's grant is real and narrow — `transform`,
    budget 1; mallory's is the R1 forgery — `shell`, no caveats, granter herself, which the
    door permits because she names herself."""
    fork = realm.rows()[0][0]
    mal = _assert_on_branch(
        realm,
        realm.mallory,
        cell,
        CAP,
        capability.capability_content(
            "echo", "shell", quarantined=False, grantee=realm.mallory, granter=realm.mallory
        ),
        [fork],
    )
    filler = _assert_on_branch(realm, realm.root, "note:spacer", "note", {"n": 1}, [fork])
    root_grant = _assert_on_branch(
        realm,
        realm.root,
        cell,
        CAP,
        capability.capability_content(
            "echo", "transform", grantee=realm.mallory, granter=realm.root, caveats={"budget": 1}
        ),
        [filler.id],
    )
    weave = realm.weave()
    heads = [eid for eid, _lam, _val in weave._reg_live(cell)]
    assert set(heads) == {mal.id, root_grant.id}, "premise: two mutually concurrent heads"
    assert heads[-1] == root_grant.id, "premise: root's assertion is the max-order head"
    assert weave.cell_asserted_by(cell) == realm.root
    return root_grant, mal


def test_a_root_adjudication_re_attributes_a_grant_to_the_principal_that_wrote_it() -> None:
    """The attribution layer, isolated. Root legitimately resolves a conflict on a cell two
    principals both asserted, and selects mallory's branch. `content` is then mallory's — so
    the asserter the ocap gate reads MUST be mallory, and her self-issued root grant must be
    refused `UNAUTHORIZED_GRANT`.

    This is the test that fails on the shipped N7: the max-order ASSERT was root's, so
    `cell_asserted_by` said ROOT, `_grant_authorship` short-circuited on `asserter == root`,
    and `authorize_detail` returned `(True, "ok", "OK")` on mallory's `shell` grant — the
    exact verdict R1 is about. Nothing here is hostile except the content of one ASSERT: the
    adjudication is root's own, so no write gate can substitute for getting this right."""
    realm = Realm()
    root_grant, mal = _forked_grant(realm)
    _adjudicate(realm.weft, realm.root, "cap:echo", winner=mal.id, evidence=[root_grant.id])

    weave = realm.weave()
    cell = realm.cell("cap:echo")
    assert cell.content["effect"] == "shell", "premise: root's select did materialize mallory's"
    assert cell.content["granter"] == realm.mallory
    assert weave.cell_asserted_by("cap:echo") == realm.mallory, (
        "the asserter named must be the author of the CONTENT, not of the branch that lost"
    )
    agent = _mallory_agent(realm, ["cap:echo"])
    allowed, why, code = capability.authorize_detail(
        realm.weave(), realm.cell(agent), "cap:echo", {}, realm.mallory
    )
    assert allowed is False
    assert code == DenialCode.UNAUTHORIZED_GRANT
    assert "self-issued grant is not authority" in why


def test_a_hostile_adjudication_cannot_supersede_a_head_it_did_not_write() -> None:
    """The gate layer. Mallory's own ATTEST names root's head as evidence and her forgery as
    winner. Superseding a guarded head is an authority decision — it chooses which assertion
    of a `capability` the realm materializes — and it was subject to no check whatsoever, so
    one ATTEST turned a concurrent self-grant (which the door permits) into the realm's
    answer. Now only root or the head's own author may supersede it, so root's grant stands.

    Note what else this closes: re-selecting a head runs no `_caveats_downhill` check, so the
    pivot also silently WIDENED authority. Root's `budget: 1` is asserted here to survive.

    The refused ATTEST is still RECORDED as an attestation — evidence that changed nothing,
    the same shape the promote-ATTEST fails closed in (NONA_RECKONER §7). That is why this is
    enforced in the fold rather than by refusing the write: the trail is worth keeping."""
    realm = Realm()
    root_grant, mal = _forked_grant(realm)
    pivot = _adjudicate(
        realm.weft, realm.mallory, "cap:echo", winner=mal.id, evidence=[root_grant.id]
    )

    weave = realm.weave()
    cell = realm.cell("cap:echo")
    assert cell.content["effect"] == "transform", "root's head was not superseded"
    assert cell.content["caveats"] == {"budget": 1}, "the caveat was not widened away"
    assert weave.cell_asserted_by("cap:echo") == realm.root
    assert mal.id in weave._reg_heads["cap:echo"], "the losing branch stays in history (§4.1)"
    assert weave._reg_superseded.get("cap:echo", set()) == set(), "nothing was superseded"
    assert [a["event"] for a in cell.attestations] == [pivot.id], "recorded, and inert"

    agent = _mallory_agent(realm, ["cap:echo"])
    _allowed, _why, code = capability.authorize_detail(
        realm.weave(), realm.cell(agent), "cap:echo", {"cost": 9999}, realm.mallory
    )
    assert code == DenialCode.BUDGET_EXCEEDED, "root's caveat, not mallory's caveat-free grant"


def test_an_adjudicated_promoter_anchor_is_attributed_to_its_writer_not_to_root() -> None:
    """The promoter rule under the same pivot — full realm compromise if it slips. Mallory
    forges the anchor cell id `promoter:<reckoner>` on her OWN log (where she is genesis, so
    her door permits it) naming HERSELF, hands it over by SYNC, and root then resolves the
    resulting conflict in her favour. The anchor's content now names mallory, so the cell is
    no longer ROOT-asserted and must confer nothing: no tiered promotion, no root-grant
    minting, no appearance in `trusted_promoters`.

    On the shipped N7 the max-order ASSERT was root's, so `_is_trusted_promoter(mallory,
    'pure')` and `may_mint_root_grant(mallory)` both returned True — mallory became the
    realm's root-anchored promoter, which defeats the whole N1-N4 anchor mechanism."""
    realm = Realm()
    pc = anchors.promoter_cell_id(realm.reckoner)
    # Re-assert the real anchor linearly, so root's head is the max-order one by lamport.
    anchors.install_trust_anchors(realm.weft, realm.root, reckoner=realm.reckoner)

    hostile = Weft(os.path.join(tempfile.mkdtemp(), "hostile.db"), realm.kr)
    forged = model.assert_content(
        hostile,
        realm.mallory,
        pc,
        anchors.PROMOTER,
        {"principal": realm.mallory, "tiers": list(anchors.SIGNABLE_TIERS)},
    )
    assert [realm.weft.ingest(r) for r in _rows(hostile)] == ["ingested"]

    baseline = realm.weave()
    root_head = [eid for eid, _lam, _val in baseline._reg_live(pc)][-1]
    assert baseline.cell_asserted_by(pc) == realm.root, "premise: root's head is max-order"
    _adjudicate(realm.weft, realm.root, pc, winner=forged.id, evidence=[root_head])

    weave = realm.weave()
    assert realm.cell(pc).content["principal"] == realm.mallory
    assert weave.cell_asserted_by(pc) == realm.mallory, "the anchor is no longer root's word"
    assert weave._is_trusted_promoter(realm.mallory, anchors.PURE) is False
    assert weave.may_mint_root_grant(realm.mallory) is False
    assert anchors.trusted_promoters(weave) == {}, "no anchor is root-asserted any more"


def test_an_adjudicated_sandbox_agent_confers_no_sandbox_privilege() -> None:
    """The sandbox conferral under the same pivot. Root asserts the real sandbox agent;
    mallory forges the same cell id on her own log with `principal: <herself>` and the
    quarantined organ in the envelope (parentless, so her frontier holds no root and the
    acceptance gate cannot judge it — N7's documented residual); root then resolves the
    conflict in her favour. `content` is hers, so the cell is not root-conferred and the
    quarantined `sandbox_only` organ must stay unrunnable.

    On the shipped N7 `cell_asserted_by` still said ROOT, so `agent_is_sandbox` was True for
    mallory and authorizing the quarantined capability returned `(True, "ok", "OK")`."""
    realm = Realm()
    organ = _real_quarantined_organ(realm)
    sandbox = realm.kr.mint(anchors.SANDBOX_NAME, "sandbox").id
    real = model.assert_content(
        realm.weft,
        realm.root,
        "agent:sbx",
        AGENT,
        {"principal": sandbox, "envelope": [], "sandbox": True},
    )

    hostile = Weft(os.path.join(tempfile.mkdtemp(), "hostile.db"), realm.kr)
    forged = model.assert_content(
        hostile,
        realm.mallory,
        "agent:sbx",
        AGENT,
        {"principal": realm.mallory, "envelope": [organ], "sandbox": True},
    )
    assert [realm.weft.ingest(r) for r in _rows(hostile)] == ["ingested"]
    baseline = realm.weave()
    assert baseline.cell_asserted_by("agent:sbx") == realm.root, "premise: root's head wins"

    _adjudicate(realm.weft, realm.root, "agent:sbx", winner=forged.id, evidence=[real.id])

    weave = realm.weave()
    agent = weave.get("agent:sbx")
    assert agent is not None and agent.content["principal"] == realm.mallory
    assert agent.content["sandbox"] is True, "the flag is still claimed; it is not conferred"
    assert weave.cell_asserted_by("agent:sbx") == realm.mallory, "root did not confer this"
    _allowed, _why, code = capability.authorize_detail(weave, agent, organ, {}, realm.mallory)
    assert code == DenialCode.UNAUTHORIZED_SANDBOX


def test_a_guarded_cell_that_materializes_outside_a_register_confers_nothing() -> None:
    """A TYPE_DEF is not itself a guarded type, so ANY principal may redeclare `capability`'s
    merge class — and an OR-set / map / counter / append-log has no single asserting head for
    `cell_asserted_by` to name. Because the answer is now DERIVED from `_reg_live`, that view
    answers None and every read fails closed.

    It has to, because the pre-fix behaviour was not a denial of service but an ESCALATION:
    materializing a capability as an OR-set replaces its content with `{'elements': []}`, so
    `quarantined`, `caveats.sandbox_only`, `requires_approval` and `grantee` all VANISH,
    while a per-cell authorship map still said ROOT asserted it — and `authorize_detail`
    returned `(True, "ok", "OK")` to mallory on a root organ granted to somebody else. The
    residual that remains (any principal can DoS the type's merge class) is in SECURITY.md."""
    realm = Realm()
    model.define_type(realm.weft, realm.mallory, CAP, merge_class="or-set")
    organ = model.assert_content(
        realm.weft,
        realm.root,
        "cap:organ",
        CAP,
        {
            "name": "organ",
            "declared_effect_class": anchors.PURE,
            "quarantined": True,
            "parent": None,
            "grantee": realm.kr.mint("someone", "operator").id,
            "granter": realm.root,
            "caveats": {"sandbox_only": True, "requires_approval": True},
        },
    ).body["cell"]
    agent = _mallory_agent(realm, [organ])

    weave = realm.weave()
    assert realm.cell(organ).content == {"elements": []}, "premise: it did NOT stay a register"
    assert weave.cell_asserted_by(organ) is None, "no single asserting head → no honest answer"
    _allowed, why, code = capability.authorize_detail(
        weave, realm.cell(agent), organ, {}, realm.mallory
    )
    assert code == DenialCode.UNAUTHORIZED_GRANT
    assert "no recorded asserter" in why


# ── positive controls for the adjudication gate (§4 still works) ──────────────


def test_an_ordinary_cell_is_still_adjudicated_by_whoever_signs_the_attest() -> None:
    """MERGE_SEMANTICS §4 is unchanged for everything that is not authority-bearing: the
    signed ATTEST is the authority for resolving a claim or a schema conflict, and the gate
    must not have quietly turned that into a root-only operation."""
    realm = Realm()
    fork = realm.rows()[0][0]
    b = _assert_on_branch(realm, realm.mallory, "note:n", "note", {"text": "mallory's"}, [fork])
    filler = _assert_on_branch(realm, realm.root, "note:spacer", "note", {"n": 1}, [fork])
    a = _assert_on_branch(realm, realm.root, "note:n", "note", {"text": "root's"}, [filler.id])
    assert set(realm.weave()._reg_heads["note:n"]) == {a.id, b.id}
    assert realm.cell("note:n").content == {"text": "root's"}, "premise: root's head is max-order"

    _adjudicate(realm.weft, realm.mallory, "note:n", winner=b.id, evidence=[a.id])
    assert realm.cell("note:n").content == {"text": "mallory's"}, "§4 unchanged off the TCB path"
    assert realm.weave()._reg_superseded["note:n"] == {a.id}


def test_a_principal_may_still_adjudicate_away_a_guarded_head_it_wrote_itself() -> None:
    """The gate refuses superseding SOMEONE ELSE'S head, not withdrawing your own. A broker
    that forked its own delegated grant must be able to resolve that conflict without root."""
    realm = Realm()
    broker = realm.kr.mint("broker", "broker").id
    holder = realm.kr.mint("holder", "operator").id
    source = capability.capability_content(
        "echo", "echo", caveats={"budget": 10}, grantee=broker, granter=realm.root
    )
    model.assert_content(realm.weft, realm.root, "cap:source", CAP, source)
    fork = realm.rows()[-1][0]
    wide = _assert_on_branch(
        realm,
        broker,
        "cap:child",
        CAP,
        capability.attenuate(source, {"budget": 9}, "cap:source", grantee=holder, granter=broker),
        [fork],
    )
    narrow = _assert_on_branch(
        realm,
        broker,
        "cap:child",
        CAP,
        capability.attenuate(source, {"budget": 1}, "cap:source", grantee=holder, granter=broker),
        [fork],
    )
    assert set(realm.weave()._reg_heads["cap:child"]) == {wide.id, narrow.id}

    _adjudicate(realm.weft, broker, "cap:child", winner=narrow.id, evidence=[wide.id])
    weave = realm.weave()
    assert realm.cell("cap:child").content["caveats"]["budget"] == 1
    assert weave.cell_asserted_by("cap:child") == broker
    assert capability.verify_delegation_detail(weave, realm.cell("cap:child")) == (
        True,
        "ok",
        DenialCode.OK,
    )


# ── determinism and substrate (Law 5) ────────────────────────────────────────


def test_the_authorship_map_survives_an_incremental_fold() -> None:
    """`_assert_author` is fold substrate, so it MUST be in `_CHECKPOINT_ATTRS` — the trap
    `_terminated`/`_superseded` were added there to avoid. Miss it and an incremental fold
    or snapshot resume produces a Weave with an empty authorship map, which (because the
    rule fails closed) would silently deny every legitimately delegated grant after a
    restore. Asserted as EQUALITY with the genesis fold, not as "still works"."""
    realm = Realm()
    broker = realm.kr.mint("broker", "broker").id
    holder = realm.kr.mint("holder", "operator").id
    source = capability.capability_content(
        "echo", "echo", caveats={"budget": 10}, grantee=broker, granter=realm.root
    )
    model.assert_content(realm.weft, realm.root, "cap:source", CAP, source)
    base = Weave.fold(realm.weft)
    checkpoint = base.checkpoint()

    child = capability.attenuate(
        source, {"budget": 5}, "cap:source", grantee=holder, granter=broker
    )
    model.assert_content(realm.weft, broker, "cap:child", CAP, child)

    genesis = Weave.fold(realm.weft)
    resumed = Weave.fold_incremental(realm.weft, checkpoint)
    assert resumed.state_root() == genesis.state_root()
    assert resumed.genesis_author() == genesis.genesis_author() == realm.root
    for cid in ("cap:source", "cap:child"):
        cell = resumed.get(cid)
        assert cell is not None
        assert resumed.cell_asserted_by(cid) == genesis.cell_asserted_by(cid)
        assert capability.verify_delegation_detail(resumed, cell) == (True, "ok", DenialCode.OK)


def test_the_verdict_is_identical_on_a_refold_and_a_replay() -> None:
    """Law 5: the rule reads only folded state (`_assert_author`, the genesis anchor, cell
    content), so two folds of the same log — and a fold of a log rebuilt by SYNC in a
    different delivery order — must reach the same verdict and the same state_root."""
    realm = Realm()
    organ = _real_quarantined_organ(realm)
    _mallory_agent(realm, [organ])
    first, second = realm.weave(), realm.weave()
    assert first.state_root() == second.state_root()

    mirror = Weft(os.path.join(tempfile.mkdtemp(), "mirror.db"), realm.kr)
    rows = realm.rows()
    for row in reversed(rows):  # a hostile delivery order; orphans are retried below
        mirror.ingest(row)
    for row in rows:
        mirror.ingest(row)
    mirrored = Weave.fold(mirror)
    assert mirrored.state_root() == first.state_root()
    assert mirrored.cell_asserted_by(organ) == first.cell_asserted_by(organ) == realm.root


def test_an_authority_bearing_cell_may_never_be_sealed() -> None:
    """`UNSEALABLE_TYPES` now covers every type the authorization path reads. A sealed
    payload whose key is later destroyed folds to `content={}` — so the binding principal
    (`granter`, `signer`, `sandbox`) would simply vanish from a cell the kernel judges
    authority by. That direction happens to fail closed; a kernel authority input a vault
    eviction can blank is not something to leave to luck."""
    from decima.kernel import sealing

    realm = Realm()
    vault = sealing.DirectoryPayloadVault(tempfile.mkdtemp())
    sealed_weft = Weft(os.path.join(tempfile.mkdtemp(), "sealed.db"), realm.kr, vault=vault)
    for cell_type, content in (
        (CAP, {"granter": realm.root, "grantee": realm.root}),
        (AGENT, {"principal": realm.root, "envelope": []}),
        (PROMOTION, {"capability": "cap:x", "signer": realm.root}),
        (anchors.PROMOTER, {"principal": realm.root, "tiers": [anchors.PURE]}),
    ):
        with pytest.raises(WeftError, match="may never be sealed"):
            model.assert_sealed(sealed_weft, realm.root, f"cell:{cell_type}", cell_type, content)


# ── the fail-closed branches of the predicate itself ─────────────────────────


def test_the_authorship_predicate_fails_closed_on_a_malformed_binding() -> None:
    """`authorship.refusal` is the one rule three enforcement sites share, so its edges are
    worth pinning directly. A binding field that is absent, null, or not a string is NEVER
    coerced: it simply matches no author, which refuses. An untested fail-closed branch is
    how a fail-OPEN regression gets in."""
    from decima.kernel import authorship

    root, mallory = "prn_root", "prn_mal"
    assert authorship.refusal("note", {"anything": True}, mallory, root) is None
    assert authorship.refusal(authorship.CAPABILITY, {}, root, root) is None, "root writes freely"

    unbound: tuple[object, ...] = ({}, {"granter": None}, {"granter": 7}, "not a dict", None)
    for content in unbound:
        assert authorship.refusal(authorship.CAPABILITY, content, mallory, root) is not None
    unsigned: tuple[object, ...] = ({}, {"signer": None}, {"signer": 7}, {"signer": root})
    for content in unsigned:
        assert authorship.refusal(authorship.PROMOTION, content, mallory, root) is not None
    assert authorship.refusal(authorship.PROMOTION, {"signer": mallory}, mallory, root) is None
    assert authorship.refusal(authorship.AGENT, {"principal": mallory}, mallory, root) is None


def test_the_predicate_permits_a_virgin_log_because_the_first_writer_becomes_root() -> None:
    """`root is None` means no genesis is anchored in the view being judged. There is no
    authority to usurp yet — whoever commits the first event BECOMES root — so the write is
    permitted and the fold judges it again once the anchor exists. Documented, deliberate,
    and load-bearing: without it `provision.first_run` could not assert the very first
    promoter anchor."""
    from decima.kernel import authorship

    assert authorship.refusal(authorship.PROMOTER, {"principal": "p"}, "p", None) is None
    assert authorship.refusal(authorship.PROMOTER, {"principal": "p"}, "p", "prn_root") is not None


def test_the_acceptance_gate_refuses_an_assert_whose_frontier_it_cannot_rebuild() -> None:
    """An authority decision is never made on a partial view: if the ancestor closure is not
    present locally the gate refuses rather than guessing. Ordinary (unguarded) assertions
    return before any fold runs."""
    from decima.kernel import acceptance, authorship

    realm = Realm()
    guarded = {
        "verb": "ASSERT",
        "author": realm.mallory,
        "parents": ["evt_does_not_exist"],
        "body": {"cell": "cap:x", "type": CAP, "content": {"granter": realm.mallory}},
    }
    assert acceptance.recheck_assert_authority(realm.weft, guarded) == (
        False,
        authorship.UNAUTHORIZED_ASSERT,
    )
    assert acceptance.recheck_assert_authority(
        realm.weft, {**guarded, "parents": "not-a-list"}
    ) == (False, authorship.UNAUTHORIZED_ASSERT)
    assert acceptance.recheck_assert_authority(realm.weft, {**guarded, "author": 7}) == (
        False,
        authorship.UNAUTHORIZED_ASSERT,
    )
    # Nothing to judge: another verb, a non-dict body, an unguarded type.
    assert acceptance.recheck_assert_authority(realm.weft, {**guarded, "verb": "RETRACT"}) == (
        True,
        "ok",
    )
    assert acceptance.recheck_assert_authority(realm.weft, {**guarded, "body": None}) == (
        True,
        "ok",
    )
    unguarded = {**guarded, "body": {"cell": "note:x", "type": "note", "content": {}}}
    assert acceptance.recheck_assert_authority(realm.weft, unguarded) == (True, "ok")
