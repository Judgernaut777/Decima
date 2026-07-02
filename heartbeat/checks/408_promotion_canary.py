"""TRUSTED, TIERED PROMOTION + CANARY + VERSIONING (NONA_RECKONER §7–§10).

Stage B's Reckoner produces promote-eligibility EVIDENCE; Stage C turns it into a GOVERNED
lifecycle transition. The single core edit this cycle (decima/weave.py) closes the trust gap
in the promote fold: a capability's quarantine is lifted by a promote-ATTEST ONLY when its
author is a TRUSTED PROMOTER for the candidate's declared effect-class TIER (§7). Everything
else — building the impl-digest edge, tiered signers, canary health, suspension/revocation,
versioning, rollback — is additive in decima/promotion.py.

This lane is an adversarial detector, not a tautology. It proves (offline, fresh Kernel,
INJECTED fake codegen, no network/clock/key):

  (0) THE GATE BITES BOTH WAYS — the trusted Reckoner DOES promote a pure candidate (so the
      negative cases below aren't vacuous), while an UNTRUSTED principal's promote-ATTEST does
      NOT lift quarantine, and a principal that SELF-DECLARES itself a promoter is ignored
      (only a ROOT-declared anchor is trusted — fail closed);
  (a) TIERED SIGNERS (§7): a network/outward candidate cannot auto-promote on the Reckoner
      alone — it needs the HUMAN tier; a financial one needs MORTA, and Morta's approval
      caveat is UNSTRIPPABLE (it survives promotion);
  (b) promotion GRANTS AN EDGE to the immutable implementation_digest and NEVER mutates
      candidate code (§7) — the capability references the source by digest;
  (c) CANARY (§8): a threshold breach folds from receipts to a SUSPENSION proposal (→
      SUSPENDED, next invoke denied); a HIGH-severity security finding auto-revokes the lease
      under a pre-authorized Morta policy (revoke + incident, next invoke denied);
  (d) VERSIONING/SUPERSEDE (§10): a new version registers higher (latest-wins) and supersedes
      the incumbent — differential-regression gated (a regressing version is refused).

Contract: run(k, line). Fail loud (assert).
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.weft import ASSERT, ATTEST
from decima.hashing import content_id, nfc_deep
from decima.model import assert_content, assert_edge
from decima import candidate as C
from decima import reckoner as R
from decima import promotion as P
from decima import manifest as M

INTENT = "normalize user text: collapse whitespace and lowercase"

# A candidate that PASSES evaluation but drifts on a real-world input eval's sample missed:
# it raises on any input containing 'zz' — absent from the seeded cases AND the fuzz alphabet
# ("abcDEFghiJKL  \t\n0123456789.,-", no 'z'), so evaluation is clean. Canary catches it.
DRIFT_SOURCE = (
    "def normalize(text):\n"
    "    s = ' '.join(str(text).split()).lower()\n"
    "    if 'zz' in s:\n"
    "        raise ValueError('canary drift on zz')\n"
    "    return s\n"
)
# A regressing v2: self-consistent + correct on the seeded cases, but strips punctuation, so
# it DIFFERS from the incumbent on punctuated fuzz inputs — only the differential catches it.
REGRESS_SOURCE = (
    "def normalize(text):\n"
    "    s = ''.join(c for c in str(text) if c not in '.,-')\n"
    "    return ' '.join(s.split()).lower()\n"
)


def _consumer(kk, name="consumer"):
    """Mint a plain (non-sandbox) agent that holds nothing — the 'another agent' a grant
    exposes a promoted capability to."""
    p = kk.keyring.mint(name, "agent")
    aid = content_id({"agent": name})
    kk.weft.append(kk.root.id, ASSERT, {"cell": aid, "type": "agent",
        "content": {"principal": p.id, "objective": "use a promoted organ",
                    "envelope": [], "sandbox": False}})
    return aid


def run(k, line):
    line("\n== TRUSTED+TIERED PROMOTION · CANARY · VERSIONING (Stage C, §7–§10) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    P.install_trust_anchors(kk)

    # ── (0) THE TRUST GATE BITES BOTH WAYS. ────────────────────────────────────────────
    # Positive baseline: the trusted Reckoner DOES promote a pure candidate (so every
    # negative case below is non-vacuous).
    good = C.author_candidate(kk, INTENT, C.fake_normalizer_codegen,
                              declared_effect_class="pure", name="puregood")
    ev = R.evaluate(kk, good)
    assert ev.promote_eligible, ev.reason
    res = P.promote(kk, good, ev, tier="pure")
    assert res.promoted is True and res.to_state == "PROMOTED", str(res)
    assert kk.weave().get(res.cap_id).content.get("quarantined") is False, \
        "the trusted Reckoner must lift a pure candidate's quarantine (baseline)"

    # Negative: an UNTRUSTED principal's promote-ATTEST does NOT lift quarantine.
    cap_u, _ = P.build_capability(kk, good, "pure", name="untrusted_target")
    impostor = kk.keyring.mint("impostor", "agent")
    kk.weft.append(impostor.id, ATTEST, {"target_cell": cap_u, "promote": True, "tier": "pure"})
    assert kk.weave().get(cap_u).content.get("quarantined") is True, \
        "an untrusted principal's promote-ATTEST lifted quarantine — the trust gap is open"
    # ...and the attestation IS recorded (evidence), it just carries no promotion authority.
    assert any(a["by"] == impostor.id for a in kk.weave().get(cap_u).attestations), \
        "the untrusted attestation should still be recorded as evidence"

    # Negative: a principal that SELF-DECLARES a promoter anchor is ignored (only a
    # ROOT-declared anchor is trusted). This makes the genesis-author filter load-bearing.
    forged_anchor = content_id({"promoter": impostor.id, "role": "self"})
    kk.weft.append(impostor.id, ASSERT, {"cell": forged_anchor, "type": "promoter",
        "content": {"principal": impostor.id, "role": "self", "tiers": ["pure"]}})
    kk.weft.append(impostor.id, ATTEST, {"target_cell": cap_u, "promote": True, "tier": "pure"})
    assert kk.weave().get(cap_u).content.get("quarantined") is True, \
        "a self-declared promoter anchor granted promotion authority — fail-closed broken"
    line("  trust gate: trusted Reckoner promotes a pure candidate; an untrusted principal — "
         "even one that self-declares a promoter anchor — CANNOT lift quarantine ✓")

    # ── (0b) GENESIS-SPOOF: a forged second genesis cannot hijack the root anchor (§7). ─
    # THREAT (a promotion-boundary leak an earlier gate missed): the trusted-promoter anchor
    # is the realm ROOT. If root were anchored on the earliest-FOLDED event (the min
    # (lamport, event_id)), an attacker could mint a SECOND parentless (lamport==1) event
    # and GRIND its content-addressed id below the real genesis so it folds FIRST —
    # hijacking `_genesis_author`. Its self-declared `promoter` would then be honored and
    # would LIFT quarantine on a financial (Morta-gated) cap with NO trusted signer. The fix
    # anchors root on the UNFORGEABLE genesis (the smallest local `seq`), which no id grind
    # can beat. Here we mount the full attack via the SAME raw surface the threat model uses
    # (`weft.append(attacker, …, parents=[])`) and prove quarantine STAYS closed.
    spoof_cand = C.author_candidate(kk, INTENT, C.fake_normalizer_codegen,
                                    declared_effect_class="financial", name="spooffin")
    cap_spoof, _ = P.build_capability(kk, spoof_cand, "financial", name="spoof_fin_target")
    assert kk.weave().get(cap_spoof).content.get("quarantined") is True, \
        "the financial cap must be born quarantined (attack baseline)"
    attacker = kk.keyring.mint("genesis_impostor", "agent")
    # The attacker self-declares itself a financial promoter and promote-ATTESTs the cap.
    # These descend from the LIVE head (high lamport) so they fold AFTER the target exists
    # — i.e. the promote path is genuinely reached (the ATTEST lands on the cap as evidence).
    forged_pr = content_id({"promoter": attacker.id, "role": "usurp"})
    kk.weft.append(attacker.id, ASSERT, {"cell": forged_pr, "type": "promoter",
        "content": {"principal": attacker.id, "role": "usurp", "tiers": ["financial"]}})
    kk.weft.append(attacker.id, ATTEST,
                   {"target_cell": cap_spoof, "promote": True, "tier": "financial"})
    # Now GRIND a SECOND parentless genesis whose content-addressed id sorts BEFORE the real
    # genesis — the id-order hijack the old anchor was vulnerable to. Deterministic + offline:
    # every principal id and the boot payload are seed-independent, so the real genesis id is
    # a constant and this grind terminates in a fixed, tiny number of steps (no clock/urandom).
    real_genesis = next(iter(kk.weft.events())).id
    def _genesis_id(author, body):
        return content_id({"parents": [], "author": author, "authorized": None,
                           "verb": ASSERT, "body": nfc_deep(body), "lamport": 1}, kind="event")
    spoof_body = None
    for n in range(100000):
        body = {"cell": content_id({"usurp_genesis": n}), "type": "note", "content": {"n": n}}
        if _genesis_id(attacker.id, body) < real_genesis:
            spoof_body = body
            break
    assert spoof_body is not None, "could not grind a lower-id genesis (attack precondition unmet)"
    spoof_ev = kk.weft.append(attacker.id, ASSERT, spoof_body, parents=[])
    assert spoof_ev.id < real_genesis and not spoof_ev.parents and spoof_ev.lamport == 1, \
        "the forged genesis must be parentless (lamport 1) and sort before the real genesis"
    w_spoof = kk.weave()
    # The forged genesis folds FIRST (lowest (lamport, event_id)) but is NOT the smallest seq
    # — so the constitutional root anchor is STILL the true root, never the attacker.
    assert w_spoof._genesis_author == kk.root.id, \
        "a grinded second genesis hijacked the root anchor — the seq-anchor is not load-bearing"
    # The attacker's promote-ATTEST IS recorded as evidence (the promote path was reached)…
    assert any(a["by"] == attacker.id for a in w_spoof.get(cap_spoof).attestations), \
        "the attacker's promote-ATTEST should reach the target as recorded evidence"
    # …but it carried NO promotion authority: quarantine on the financial cap HELD.
    assert w_spoof.get(cap_spoof).content.get("quarantined") is True, \
        "a genesis-spoofing attacker lifted quarantine on a financial cap — the §7 boundary leaked"
    line("  genesis-spoof: a forged second genesis grinds its id below the real genesis and "
         "folds first, but the seq-anchored root holds — the attacker's self-promoter is ignored "
         "and a financial cap's quarantine stays CLOSED ✓")

    # ── (a) TIERED SIGNERS (§7): network needs the human tier; financial needs Morta. ──
    net = C.author_candidate(kk, INTENT, C.fake_normalizer_codegen,
                             declared_effect_class="network", name="netnorm")
    cap_net, _ = P.build_capability(kk, net, "network", name="net_target")
    # The Reckoner ALONE cannot promote an outward/network candidate.
    kk.weft.append(kk.reckoner.id, ATTEST, {"target_cell": cap_net, "promote": True, "tier": "network"})
    assert kk.weave().get(cap_net).content.get("quarantined") is True, \
        "the Reckoner alone auto-promoted a network candidate — human tier is required (§7)"
    # A HUMAN attestation satisfies the network tier.
    kk.weft.append(kk.human.id, ATTEST, {"target_cell": cap_net, "promote": True, "tier": "network"})
    assert kk.weave().get(cap_net).content.get("quarantined") is False, \
        "a human attestation must satisfy the network tier"

    fin = C.author_candidate(kk, INTENT, C.fake_normalizer_codegen,
                             declared_effect_class="financial", name="finnorm")
    cap_fin, _ = P.build_capability(kk, fin, "financial", name="fin_target")
    assert kk.weave().get(cap_fin).content["caveats"].get("requires_approval") is True, \
        "a financial candidate must carry Morta's requires_approval floor"
    # Neither the Reckoner NOR the human tier can promote a financial candidate.
    kk.weft.append(kk.reckoner.id, ATTEST, {"target_cell": cap_fin, "promote": True})
    kk.weft.append(kk.human.id, ATTEST, {"target_cell": cap_fin, "promote": True})
    assert kk.weave().get(cap_fin).content.get("quarantined") is True, \
        "reckoner+human auto-promoted a financial candidate — Morta is required (§7)"
    # Morta (root authority) promotes it — and its approval caveat is UNSTRIPPABLE.
    kk.weft.append(kk.root.id, ATTEST, {"target_cell": cap_fin, "promote": True})
    fin_cell = kk.weave().get(cap_fin)
    assert fin_cell.content.get("quarantined") is False, "Morta must be able to promote financial"
    assert fin_cell.content["caveats"].get("requires_approval") is True, \
        "promotion STRIPPED Morta's approval caveat — it must be unstrippable (§7)"
    line("  tiered signers: network needs the human tier (Reckoner alone refused); financial "
         "needs Morta (Reckoner+human refused), and Morta's approval caveat survives ✓")

    # ── (b) PROMOTION GRANTS AN EDGE to the immutable impl digest — never mutates code. ─
    cap_cell = kk.weave().get(res.cap_id)
    assert cap_cell.content["implementation_digest"] == good["implementation_digest"], \
        "the promoted cap must carry the candidate's immutable implementation_digest"
    assert cap_cell.content["impl"]["source_blobs"] == good["source_blobs"], \
        "the promoted cap references the candidate's source by digest — code is never mutated"
    assert any(e["rel"] == "impl_of" and e["dst"] == good["cell"] for e in cap_cell.edges_out), \
        "promotion must grant an EDGE to the candidate/impl (provenance)"
    line("  §7: promotion grants an EDGE to the immutable implementation_digest; the "
         "candidate source is referenced by digest, never rewritten ✓")

    # ── (c) CANARY (§8): breach → suspension; high finding → auto-revoke. ──────────────
    drift = C.author_candidate(kk, INTENT, lambda _i: DRIFT_SOURCE,
                               declared_effect_class="workspace_write", name="driftnorm")
    dev = R.evaluate(kk, drift)
    assert dev.promote_eligible, "the drift candidate must PASS evaluation (else canary proves nothing)"
    dres = P.promote(kk, drift, dev, tier="workspace_write")
    assert dres.to_state == "CANARY" and dres.promoted, \
        "a workspace_write candidate promotes to CANARY (automated promote + canary, §7)"
    consumer = _consumer(kk, "canary_user")
    P.grant_to(kk, dres.cap_id, consumer)
    # Healthy run first — real generated code, real result.
    ok = kk.invoke(kk.weave().get(consumer), dres.cap_id, {"text": "  Hi There  "})
    assert ok.get("status") == "SUCCEEDED" and ok["ok"]["out"] == "hi there", \
        f"canary healthy invoke must run the real generated code: {ok}"
    assert kk.weave().canary_health(dres.cap_id)["healthy"] is True, "healthy canary must fold healthy"
    # A real-world input eval's sample missed drives a FAILURE receipt → breach.
    bad = kk.invoke(kk.weave().get(consumer), dres.cap_id, {"text": "buzz off"})
    assert bad["ok"]["ok"] is False, "the drift input must produce a failing (ok:False) receipt"
    health = kk.weave().canary_health(dres.cap_id)
    assert health["failures"] >= 1 and health["breach"] is True, health
    mon = P.monitor_canary(kk, dres.cap_id)
    assert mon["action"] == "suspended" and "suspension" in mon, mon
    susp = kk.weave().get(mon["suspension"])
    assert susp.type == "suspension" and susp.content["to_state"] == "SUSPENDED", susp.content
    denied = kk.invoke(kk.weave().get(consumer), dres.cap_id, {"text": "hi"})
    assert "denied" in denied, "a SUSPENDED canary must fail closed on the next invoke"

    # High-severity finding → automatic lease revocation under Morta policy.
    hf = C.author_candidate(kk, INTENT, C.fake_normalizer_codegen,
                            declared_effect_class="workspace_write", name="hfnorm")
    hres = P.promote(kk, hf, R.evaluate(kk, hf), tier="workspace_write")
    fid = content_id({"finding": "rugpull", "cap": hres.cap_id})
    assert_content(kk.weft, kk.reckoner.id, fid, "finding",
                   {"severity": "high", "detection": "rugpull", "source": hres.cap_id})
    assert_edge(kk.weft, kk.reckoner.id, fid, "found_in", hres.cap_id)
    assert kk.weave().canary_health(hres.cap_id)["high_findings"] >= 1, "the high finding must fold in"
    mh = P.monitor_canary(kk, hres.cap_id)
    assert mh["action"] == "revoked" and "incident" in mh, mh
    inc = kk.weave().get(mh["incident"])
    assert inc.type == "incident" and any(e["rel"] == "incident_for" and e["dst"] == hres.cap_id
                                          for e in inc.edges_out), inc.content
    assert kk.weave().get(hres.cap_id).retracted is True, "a high finding must auto-revoke the lease"
    line("  canary: a threshold breach folds from receipts → SUSPENSION (next invoke denied); "
         "a HIGH-severity finding auto-revokes the lease + asserts an incident ✓")

    # ── (d) VERSIONING/SUPERSEDE (§10), differential-regression gated. ─────────────────
    v1 = C.author_candidate(kk, INTENT, C.fake_normalizer_codegen,
                            declared_effect_class="pure", name="ver")
    r1 = P.promote(kk, v1, R.evaluate(kk, v1), tier="pure")
    P.register_version(kk, "ver", 1)
    assert [c.content["version"] for c in M.registry(kk) if c.content["name"] == "ver"] == [1]
    # A functionally-equivalent v2 (different digest, same behavior) passes the differential
    # gate and SUPERSEDES the incumbent (latest-wins).
    v2src = C.NORMALIZER_SOURCE + "# equivalent revision v2\n"
    v2 = C.author_candidate(kk, INTENT, lambda _i: v2src, declared_effect_class="pure", name="ver")
    assert v2["implementation_digest"] != v1["implementation_digest"], "v2 must be a new build"
    ev2 = R.evaluate(kk, v2, incumbent=v1)
    assert ev2.promote_eligible, "an equivalent v2 must pass the differential gate"
    r2 = P.promote(kk, v2, ev2, tier="pure")
    P.supersede(kk, "ver", r1.cap_id, r2.cap_id, version=2)
    assert [c.content["version"] for c in M.registry(kk) if c.content["name"] == "ver"] == [2], \
        "the registry must be latest-wins (v2 supersedes v1)"
    v1_cell = kk.weave().get(r1.cap_id)
    assert v1_cell.superseded_by == r2.cap_id and v1_cell.retracted, \
        "the incumbent must be superseded (tombstoned, points forward to v2)"
    # A REGRESSING version is refused by the differential gate — promotion is blocked.
    v3 = C.author_candidate(kk, INTENT, lambda _i: REGRESS_SOURCE,
                            declared_effect_class="pure", name="ver")
    ev3 = R.evaluate(kk, v3, incumbent=v1)
    assert ev3.promote_eligible is False and any(f["stage"] == "differential" for f in ev3.failures), \
        "a regressing version must fail the differential stage"
    try:
        P.promote(kk, v3, ev3, tier="pure")
        raise AssertionError("a differential-regressing version was promoted (gate not enforced)")
    except P.PromotionBlocked:
        pass
    line("  versioning: an equivalent v2 supersedes v1 (latest-wins registry, incumbent "
         "tombstoned); a differential-REGRESSING version is refused (regression-gated) ✓")

    line("  → promotion is a TRUSTED, TIERED signature over deterministic evidence; canary "
         "health governs the lease; versioning supersedes; and the untrusted can never lift.")
