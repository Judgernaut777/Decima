"""INSTALL / SELF-UPDATE — the system updates its OWN code through the attested,
versioned, rollback-able promotion spine (Phase 5 · full surface).

`decima/selfupdate.py` gives Decima ONE way to install a new version of itself: a
candidate is proposed (born quarantined, default-deny), evaluated, PROMOTED through
the same tiered/trusted attestation gate the forge-real loop uses (§7), and only
then ACTIVATED by moving an append-only LWW `active_version` pointer Cell — a move
that is itself Morta-gated (`requires_approval`). Rollback is just moving the
pointer back to the still-present prior version. The law under test:

  AN UNATTESTED / UNSIGNED UPDATE CAN NEVER GO LIVE.

This check is an adversarial detector, offline + deterministic (fresh Kernel,
logical ints, no clock, INJECTED deterministic codegen). It proves:

  (a) ATTESTED UPDATE GOES LIVE — propose → evaluate → promote (trusted signer) →
      activate (with Morta approval) → `active(name)` is the new int version, and
      the activated capability really runs the new version's code;
  (b) UNATTESTED UPDATE IS REFUSED (load-bearing) — an evaluation-failed candidate
      cannot be promoted (`PromotionBlocked`); a never-promoted version cannot be
      activated; a WRONG SIGNER produces no lift and promotion fails closed; a
      FORGED promotion record pointing at a still-quarantined capability cannot
      activate (the gate re-derives promotion from the WEAVE, not from a claim);
      and activating WITHOUT the Morta approval is refused — `active(name)` is
      unchanged by every one of these;
  (c) ROLLBACK RESTORES THE PRIOR VERSION — after v2 goes live, `rollback` returns
      the pointer to v1 (still present on the Log, nothing deleted): active == v1,
      v1's BEHAVIOR is back (the zz-probe output flips), and the pointer history
      still folds ALL moves append-only while v2's cells stay live;
  (d) INTS + AUDIT — versions are ints (a float/bool version is refused at the
      door); propose/promote/activate/rollback each leave audited Cells with
      provenance; and NO ambient authority is minted — a still-quarantined forged
      capability stays uninvocable even when granted (promotion is the only
      authority path).

Mutation-resistance (the load-bearing line): neuter the attestation gate in
`selfupdate._require_promoted` (the `quarantined is not False` refusal) and the
forged-record case in (b) goes RED — an unattested update goes live.

Contract: run(k, line). Fail loud (assert). Owns its own fresh Kernel + effect.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.weft import ASSERT
from decima.hashing import content_id
from decima.model import assert_content
from decima import candidate as C
from decima import reckoner as R
from decima import promotion as P
from decima import selfupdate as SU

NAME = "decima.core"
GOAL = "normalize user text: collapse whitespace and lowercase"

# v1 — the incumbent behavior (the shared deterministic normalizer source).
V1_SOURCE = C.NORMALIZER_SOURCE
# v2 — passes the SAME evaluation suite (no 'z' in the seeded cases, the hostile
# case, or the fuzz alphabet) but BEHAVES differently on a 'zz' probe input — the
# observable that proves rollback restores v1's behavior, not just a number.
V2_SOURCE = (
    "def normalize(text):\n"
    "    s = ' '.join(str(text).split()).lower()\n"
    "    if 'zz' in s:\n"
    "        return s.upper()\n"
    "    return s\n"
)
# a broken candidate — fails the deterministic stage (never promote-eligible).
BAD_SOURCE = "def normalize(text):\n    return str(text).upper()\n"

PROBE = {"text": "  zz Top  "}          # v1 → "zz top"; v2 → "ZZ TOP"


def _consumer(kk, name="update_consumer"):
    """A plain (non-sandbox) agent holding nothing — invokes only what is granted."""
    p = kk.keyring.mint(name, "agent")
    aid = content_id({"agent": name})
    kk.weft.append(kk.root.id, ASSERT, {"cell": aid, "type": "agent",
        "content": {"principal": p.id, "objective": "run the active version",
                    "envelope": [], "sandbox": False}})
    return aid


def _run_active(kk, consumer):
    """Invoke the CURRENTLY ACTIVE version's capability (read off the pointer fold)."""
    cap = SU.active_record(kk, NAME)["cap"]
    res = kk.invoke(kk.weave().get(consumer), cap, dict(PROBE))
    assert res.get("status") == "SUCCEEDED", f"active version must run: {res}"
    return res["ok"]["out"]


def _int_ok(x, what):
    assert isinstance(x, int) and not isinstance(x, bool), \
        f"{what} must be an int (ints-not-floats), got {type(x).__name__} {x!r}"


def run(k, line):
    line("\n== INSTALL / SELF-UPDATE — attested, versioned, rollback-able (Phase 5) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    P.install_trust_anchors(kk)
    consumer = _consumer(kk)

    # ── (a) ATTESTED UPDATE GOES LIVE — and a promoted version is REQUIRED first. ──
    u1 = SU.propose_update(kk, NAME, GOAL + " (v1)", lambda _i: V1_SOURCE, version=1)
    assert SU.active(kk, NAME) is None, "proposing an update must activate NOTHING (default-deny)"
    cand1 = kk.weave().get(u1["candidate"]["cell"])
    assert cand1.content.get("quarantined") is True and \
        cand1.content["lifecycle"] == "QUARANTINED", \
        "a proposed update must be BORN QUARANTINED"
    prop1 = kk.weave().get(u1["proposal"])
    assert prop1.content["active"] is False and \
        prop1.content["instruction_eligible"] is False, \
        "the proposal is audit DATA: inactive, never instruction-eligible"
    # a merely-proposed (never-promoted) version CANNOT be activated — fail closed.
    try:
        SU.activate(kk, NAME, 1)
        raise AssertionError("a never-promoted update was activated (gate missing)")
    except P.PromotionBlocked:
        pass
    assert SU.active(kk, NAME) is None, "the refused activation must move nothing"

    ev1 = R.evaluate(kk, u1["candidate"])
    assert ev1.promote_eligible, ev1.reason
    p1 = SU.promote_update(kk, u1, ev1, tier="workspace_write")
    assert p1["version"] == 1 and kk.weave().get(p1["cap"]).content.get("quarantined") is False, \
        "a trusted-signer promotion must lift quarantine on the version's capability"

    # WITHOUT the Morta approval the pointer move is refused (part of (b) too).
    try:
        SU.activate(kk, NAME, 1)
        raise AssertionError("activation without Morta approval went live (gate missing)")
    except SU.ActivationDenied:
        pass
    assert SU.active(kk, NAME) is None, "a Morta-refused activation must move nothing"

    acap = SU.activation_cap(kk)
    assert kk.weave().get(acap).content["caveats"].get("requires_approval") is True, \
        "the activation capability must carry the unstrippable requires_approval caveat"
    kk.approve(acap)                                   # the human/Morta approval of record
    act1 = SU.activate(kk, NAME, 1)
    assert SU.active(kk, NAME) == 1 and act1["cap"] == p1["cap"], \
        "an attested + approved update must go live as the active version"
    P.grant_to(kk, p1["cap"], consumer)
    assert _run_active(kk, consumer) == "zz top", "v1's behavior must be live (probe → 'zz top')"
    line("  attested update goes live: propose (born quarantined, default-deny) → evaluate → "
         "promote (trusted signer lifts) → Morta-approved activate → active == 1, v1 runs ✓")

    # ── (b) UNATTESTED UPDATE IS REFUSED — every bypass fails closed. ─────────────
    # (b1) an evaluation-FAILED candidate cannot be promoted at all.
    u3 = SU.propose_update(kk, NAME, GOAL + " (v3 broken)", lambda _i: BAD_SOURCE, version=3)
    ev3 = R.evaluate(kk, u3["candidate"])
    assert ev3.promote_eligible is False, "the broken candidate must fail evaluation"
    try:
        SU.promote_update(kk, u3, ev3, tier="workspace_write")
        raise AssertionError("an evaluation-failed update was promoted (evidence gate open)")
    except P.PromotionBlocked:
        pass
    assert not [c for c in kk.weave().of_type(SU.UPDATE_PROMOTION)
                if c.content.get("version") == 3], \
        "a blocked promotion must leave NO update_promotion record"
    # ...and the never-promoted v3 cannot be activated.
    try:
        SU.activate(kk, NAME, 3)
        raise AssertionError("an unpromoted version was activated — an unattested update went live")
    except P.PromotionBlocked:
        pass

    # (b2) a WRONG SIGNER produces no lift — promote_update fails CLOSED.
    u4 = SU.propose_update(kk, NAME, GOAL + " (v4 net)", lambda _i: V1_SOURCE + "# v4\n",
                           version=4, effect_class="network")
    ev4 = R.evaluate(kk, u4["candidate"])
    assert ev4.promote_eligible, ev4.reason
    try:
        SU.promote_update(kk, u4, ev4, tier="network", signer_principal=kk.reckoner.id)
        raise AssertionError("a network-tier update promoted on the Reckoner's signature alone")
    except P.PromotionBlocked:
        pass
    try:
        SU.activate(kk, NAME, 4)
        raise AssertionError("a wrong-signer version was activated")
    except P.PromotionBlocked:
        pass

    # (b3) a FORGED promotion RECORD cannot activate a still-quarantined capability:
    # the activation gate re-derives promotion from the WEAVE fold, not from a claim.
    forged_cap, _ = P.build_capability(kk, u3["candidate"], "workspace_write",
                                       name="forged_v5_target")
    assert kk.weave().get(forged_cap).content.get("quarantined") is True, \
        "the forged target must still be quarantined (attack baseline)"
    attacker = kk.keyring.mint("update_impostor", "agent")
    rid = content_id({"update_promotion": NAME, "version": 5, "cap": forged_cap})
    assert_content(kk.weft, attacker.id, rid, SU.UPDATE_PROMOTION, {
        "name": NAME, "version": 5, "cap": forged_cap, "tier": "workspace_write",
        "signer": attacker.id, "state": "PROMOTED"})    # a LIE, asserted as data
    try:
        SU.activate(kk, NAME, 5)
        raise AssertionError(
            "a FORGED promotion record activated a quarantined capability — "
            "an unattested update went live (the load-bearing gate is open)")
    except P.PromotionBlocked:
        pass
    assert SU.active(kk, NAME) == 1, \
        "every refused bypass must leave the active version UNCHANGED (still v1)"
    line("  unattested update is refused: failed evaluation → PromotionBlocked; never-promoted "
         "and wrong-signer versions cannot activate; a FORGED promotion record over a "
         "quarantined cap is refused (the gate re-folds the Weave); Morta-unapproved "
         "activation refused — active stays 1 throughout ✓")

    # ── (c) ROLLBACK RESTORES THE PRIOR VERSION (append-only, nothing deleted). ───
    u2 = SU.propose_update(kk, NAME, GOAL + " (v2)", lambda _i: V2_SOURCE, version=2)
    ev2 = R.evaluate(kk, u2["candidate"])
    assert ev2.promote_eligible, ev2.reason
    p2 = SU.promote_update(kk, u2, ev2, tier="workspace_write")
    SU.activate(kk, NAME, 2)
    assert SU.active(kk, NAME) == 2, "the attested v2 must go live"
    P.grant_to(kk, p2["cap"], consumer)
    assert _run_active(kk, consumer) == "ZZ TOP", "v2's changed behavior must be live"

    rb = SU.rollback(kk, NAME)
    assert rb["version"] == 1 and SU.active(kk, NAME) == 1, \
        "rollback must return the pointer to the immediately-prior version (v1)"
    assert SU.active_record(kk, NAME)["cap"] == p1["cap"], \
        "rollback must restore EXACTLY the prior version's capability"
    assert _run_active(kk, consumer) == "zz top", \
        "rollback must restore v1's BEHAVIOR (the zz probe flips back)"
    hist = [e["version"] for e in SU.history(kk, NAME)]
    assert hist == [1, 2, 1], \
        f"the pointer history must fold ALL moves append-only (nothing deleted): {hist}"
    v2_cap = kk.weave().get(p2["cap"])
    assert v2_cap is not None and not v2_cap.retracted and \
        v2_cap.content.get("quarantined") is False, \
        "rollback deletes nothing: v2's promoted capability stays live on the Log"
    # v1 has no prior — a further rollback is refused loud (nothing silently invented).
    try:
        SU.rollback(kk, NAME)
        raise AssertionError("rollback past the first version must fail loud")
    except SU.SelfUpdateError:
        pass
    line("  rollback: v2 live (probe → 'ZZ TOP') → rollback → active == 1 and v1's behavior "
         "is restored (probe → 'zz top'); history folds [1, 2, 1] append-only, v2's cells "
         "stay on the Log; rolling back past v1 fails loud ✓")

    # ── (d) INTS + AUDIT — and no ambient authority anywhere. ─────────────────────
    for e in SU.history(kk, NAME):
        _int_ok(e["version"], "pointer version")
        if e.get("prev") is not None:
            _int_ok(e["prev"], "pointer prev")
        assert e.get("invoke") and e.get("receipt"), \
            "every pointer move must carry its gated INVOKE + receipt (audit)"
    props = kk.weave().of_type(SU.UPDATE_PROPOSAL)
    assert len(props) == 4 and all(
        isinstance(c.content["version"], int) and not isinstance(c.content["version"], bool)
        for c in props), "every proposal is an audited Cell with an int version"
    genuine = [c for c in kk.weave().of_type(SU.UPDATE_PROMOTION)
               if c.content.get("signer") != attacker.id]
    assert {c.content["version"] for c in genuine} == {1, 2} and all(
        any(e["rel"] == "promotes_version" for e in c.edges_out) for c in genuine), \
        "each genuine promotion leaves an audited record with provenance to its capability"
    # a float (or bool) version is refused at the door — nothing lands.
    for bad_v in (2.0, True):
        try:
            SU.propose_update(kk, NAME, GOAL, lambda _i: V1_SOURCE, version=bad_v)
            raise AssertionError(f"a non-int version {bad_v!r} was accepted (ints-not-floats)")
        except SU.SelfUpdateError:
            pass
    # NO AMBIENT AUTHORITY: even GRANTED, the forged still-quarantined cap is uninvocable —
    # promotion (the trusted attestation) is the only authority path to a live version.
    P.grant_to(kk, forged_cap, consumer)
    denied = kk.invoke(kk.weave().get(consumer), forged_cap, dict(PROBE))
    assert "denied" in denied and "quarantin" in denied["denied"], \
        f"a quarantined capability must stay uninvocable even when granted: {denied}"
    line("  ints + audit: versions/prev are ints (float/bool refused at the door); "
         "propose/promote/activate/rollback each leave audited Cells with provenance; a "
         "granted-but-quarantined cap stays uninvocable (no ambient authority) ✓")

    line("  → self-update rides the SAME attested spine as every forged organ: born "
         "quarantined, promoted only by a trusted signer, activated only through Morta's "
         "gate, rolled back by moving an append-only pointer — an unattested update can "
         "never go live.")
