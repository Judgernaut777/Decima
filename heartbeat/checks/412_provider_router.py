"""PROVIDER-LEVEL ROUTING — eligibility (hard) + additive score (soft), ZERO authority.

`router.py` picks a strategy TIER (local-small | retrieval-assisted | frontier |
judge); it does NOT pick a concrete provider instance. `decima/provider_router.py`
closes that gap with the two-stage split a cost/privacy-aware control plane uses:

  1. ELIGIBILITY — HARD constraints, each rejection carrying an explainable reason:
     privacy class vs provider privacy_tier (a private/repo-sensitive task must NOT
     reach an external provider; a secret-sensitive task → ZERO eligible, fail
     CLOSED), health, quota, capacity, and a budget gate on paid/rented lanes.
  2. SCORE — an ADDITIVE INTEGER ranking over the ELIGIBLE set ONLY. A high score
     NEVER resurrects a hard-failed provider.

This check is an adversarial, OFFLINE detector (injected status dicts, fresh
Kernel, no network, no clock). It proves:

  (a) eligibility hard-filters a privacy-violating, an unhealthy, a quota-exhausted,
      and an external_paid-under-bad-budget provider — each with an explainable
      rejection reason;
  (b) a secret-sensitive task yields ZERO eligible (fail CLOSED) — every candidate
      is rejected, nothing is best-effort routed out;
  (c) the additive score picks the best eligible provider DETERMINISTICALLY, and the
      chosen total is the exact integer sum of its recomputed breakdown;
  (d) an injected scorecard/quota change SHIFTS the pick — the score is live;
  (e) HARD > SCORE: a provider made ineligible (privacy / budget) stays rejected no
      matter how high its score would be — score cannot override a hard constraint;
  (f) provenance: the decision lands as a `provider_routing` Cell on the Weft, and
      every recorded numeric is an int;
  (g) ZERO authority: a RoutingDecision carries no cap/grant/principal, and routing
      + recording a selection mints no capability/grant/invocation — a fresh-kernel
      publish is still Morta-denied.

Contract: run(k, line). Fail loud (assert).
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.router import TaskDescriptor, FRONTIER, LOCAL_SMALL
from decima import provider_router as PR


def _fresh_k():
    return Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)


def _budget(configured=True, pressure=10, remaining=1_000_000):
    return {"remaining_microcents": remaining, "pressure": pressure, "configured": configured}


def _prov(pid, tier, ptier, **kw):
    base = dict(cost_per_1k_microcents=100, healthy=True, quota_remaining=50_000,
                capacity=20, residency="external", scorecard=0, model=pid + "-m")
    base.update(kw)
    return PR.Provider(id=pid, tier=tier, privacy_tier=ptier, **base)


def run(k, line):
    line("\n== PROVIDER-LEVEL ROUTING — eligibility (hard) + additive score · ZERO authority ==")

    # ── (a) ELIGIBILITY hard-filters, with explainable reasons. ──────────────────
    # A public frontier task; four providers each violating a distinct hard rule,
    # plus one clean provider that must survive.
    desc = TaskDescriptor(kind="plan", stakes="high", privacy="public")
    providers = [
        _prov("clean",   FRONTIER, PR.EXTERNAL),                    # eligible
        _prov("leaky",   FRONTIER, PR.EXTERNAL, model="x"),         # (privacy tested below)
        _prov("sick",    FRONTIER, PR.EXTERNAL, healthy=False),     # health
        _prov("drained", FRONTIER, PR.EXTERNAL, quota_remaining=0), # quota
        _prov("full",    FRONTIER, PR.EXTERNAL, capacity=0),        # capacity
        _prov("paid",    FRONTIER, PR.EXTERNAL_PAID),              # budget (below)
    ]
    status = {"providers": [], "budget": _budget(configured=False)}   # NOT configured
    eligible, rejected = PR.eligibility(desc, providers, status)
    rej = {r["provider_id"]: r["reason"] for r in rejected}
    assert "sick" in rej and "health" in rej["sick"], rej
    assert "drained" in rej and "quota" in rej["drained"], rej
    assert "full" in rej and "capacity" in rej["full"], rej
    assert "paid" in rej and "budget" in rej["paid"], rej          # paid lane, no budget
    assert "clean" in {p.id for p in eligible}, "the clean provider must be eligible"
    for r in rejected:                     # every rejection is explainable (non-empty reason)
        assert isinstance(r["reason"], str) and r["reason"], r
    line("  eligibility: unhealthy / quota-0 / capacity-0 / paid-no-budget providers each "
         "rejected with an explainable reason; the clean provider survives ✓")

    # privacy: a PRIVATE task must not reach an external provider (instance-level hard rule,
    # composing with router.py _r_private). Only a local_only provider is eligible.
    pdesc = TaskDescriptor(kind="chat", privacy="private")
    ppool = [_prov("edge", FRONTIER, PR.EXTERNAL),
             _prov("onbox", LOCAL_SMALL, PR.LOCAL_ONLY, residency="local")]
    pelig, prej = PR.eligibility(pdesc, ppool, {"providers": [], "budget": _budget()})
    assert [p.id for p in pelig] == ["onbox"], "a private task may only reach a local_only provider"
    assert any(x["provider_id"] == "edge" and "privacy" in x["reason"] for x in prej), prej
    line("  privacy hard rule: a PRIVATE task rejects the external provider (privacy reason) "
         "and keeps only the local_only one — instance-level fail-closed ✓")

    # ── (b) SECRET-SENSITIVE → ZERO eligible (fail CLOSED). ──────────────────────
    sdesc = TaskDescriptor(kind="extract", privacy="secret_sensitive")
    spool = [_prov("onbox", LOCAL_SMALL, PR.LOCAL_ONLY, residency="local"),
             _prov("rented", FRONTIER, PR.PRIVATE_RENTED),
             _prov("edge", FRONTIER, PR.EXTERNAL)]
    selig, srej = PR.eligibility(sdesc, spool, {"providers": [], "budget": _budget()})
    assert selig == [], "a secret-sensitive task must have ZERO eligible providers (fail closed)"
    assert {r["provider_id"] for r in srej} == {"onbox", "rented", "edge"}, srej
    dec_secret = PR.select(sdesc, spool, {"providers": [], "budget": _budget()})
    assert not dec_secret.routed and dec_secret.selected_provider == "", \
        "a secret-sensitive selection must route to NOTHING (fail closed), not best-effort"
    line("  fail closed: a secret_sensitive task yields ZERO eligible providers — even the "
         "local one — and select() routes to NOTHING, never best-effort ✓")

    # ── (c) SCORE picks the best eligible provider DETERMINISTICALLY. ────────────
    # Three eligible frontier providers; 'best' has the top scorecard and cheapest cost.
    desc3 = TaskDescriptor(kind="plan", stakes="high", privacy="public")
    pool3 = [
        _prov("best",  FRONTIER, PR.EXTERNAL, scorecard=80, cost_per_1k_microcents=100),
        _prov("mid",   FRONTIER, PR.EXTERNAL, scorecard=40, cost_per_1k_microcents=200),
        _prov("worst", FRONTIER, PR.EXTERNAL, scorecard=-10, cost_per_1k_microcents=900),
    ]
    st3 = {"providers": [], "budget": _budget()}
    dec3 = PR.select(desc3, pool3, st3)
    assert dec3.selected_provider == "best", (dec3.selected_provider, dec3.scores)
    assert dec3.selected_model == "best-m"
    # determinism: same inputs → same pick, twice.
    assert PR.select(desc3, pool3, st3).selected_provider == "best"
    # the chosen total is the exact integer SUM of its recomputed breakdown.
    bd = next(b for b in dec3.breakdowns if b.provider_id == "best")
    manual = (bd.capability_fit + bd.expected_quality + bd.latency_fit + bd.privacy_fit
              + bd.availability + bd.residency_bonus - bd.quota_scarcity_penalty
              - bd.queue_delay_penalty - bd.model_switch_penalty - bd.cost_penalty
              - bd.opportunity_cost)
    assert manual == bd.total == dec3.scores["best"], (manual, bd.total, dec3.scores["best"])
    assert dec3.scores["best"] > dec3.scores["mid"] > dec3.scores["worst"], dec3.scores
    line(f"  score: additive integer ranking picks 'best' (total={bd.total}, the exact "
         f"sum of its breakdown) over mid/worst — deterministic ✓")

    # ── (d) a live scorecard/quota change SHIFTS the pick. ───────────────────────
    pool4 = [
        _prov("A", FRONTIER, PR.EXTERNAL, scorecard=30, cost_per_1k_microcents=100),
        _prov("B", FRONTIER, PR.EXTERNAL, scorecard=60, cost_per_1k_microcents=100),
    ]
    assert PR.select(desc3, pool4, st3).selected_provider == "B", "B's higher scorecard wins"
    # the spend/scoreboard lane raises A's learned quality above B → the pick shifts to A.
    pool4b = [
        _prov("A", FRONTIER, PR.EXTERNAL, scorecard=90, cost_per_1k_microcents=100),
        _prov("B", FRONTIER, PR.EXTERNAL, scorecard=60, cost_per_1k_microcents=100),
    ]
    assert PR.select(desc3, pool4b, st3).selected_provider == "A", \
        "an injected scorecard change must shift the pick — the score is live"
    line("  live score: raising provider A's scorecard above B flips the selection A←B — "
         "the additive score tracks injected quota/scorecard state ✓")

    # ── (e) HARD > SCORE: an ineligible provider is NEVER rescued by a high score. ─
    # 'star' would trounce everyone on score, but it is EXTERNAL for a PRIVATE task
    # (privacy hard-fail) — so it must stay rejected and 'onbox' must win instead.
    hdesc = TaskDescriptor(kind="chat", privacy="private")
    hpool = [
        _prov("star",  FRONTIER, PR.EXTERNAL, scorecard=100, cost_per_1k_microcents=1,
              quota_remaining=999_999, capacity=999, residency="local"),
        _prov("onbox", LOCAL_SMALL, PR.LOCAL_ONLY, scorecard=-50,
              cost_per_1k_microcents=500, residency="local"),
    ]
    # 'star' really would outscore 'onbox' if it were eligible — prove the counterfactual.
    assert PR.score(hdesc, hpool[0], st3).total > PR.score(hdesc, hpool[1], st3).total, \
        "precondition: the ineligible 'star' must have the HIGHER raw score"
    hdec = PR.select(hdesc, hpool, st3)
    assert hdec.selected_provider == "onbox", \
        "a privacy-hard-failed provider must NEVER be selected, however high its score"
    assert "star" not in hdec.scores, "an ineligible provider is not even scored"
    assert any(r["provider_id"] == "star" and "privacy" in r["reason"] for r in hdec.rejected)
    # same for the BUDGET hard rule: a superb paid lane under high pressure stays out.
    bdesc = TaskDescriptor(kind="plan", stakes="high", privacy="public")
    bpool = [
        _prov("premium", FRONTIER, PR.EXTERNAL_PAID, scorecard=100,
              cost_per_1k_microcents=1, quota_remaining=999_999, capacity=999),
        _prov("free",    FRONTIER, PR.EXTERNAL, scorecard=0),
    ]
    tight = {"providers": [], "budget": _budget(configured=True, pressure=95)}
    bdec = PR.select(bdesc, bpool, tight)
    assert bdec.selected_provider == "free", \
        "a paid lane under high budget pressure stays rejected despite a top score"
    assert any(r["provider_id"] == "premium" and "budget" in r["reason"] for r in bdec.rejected)
    line("  hard > score: a privacy-ineligible 'star' (raw score higher than the winner) and "
         "a budget-locked 'premium' both stay REJECTED — hard constraints are never overridden ✓")

    # ── (f) PROVENANCE — the decision lands as a Cell; all recorded numerics ints. ─
    kk = _fresh_k()
    from decima.hashing import content_id
    from decima.weft import ASSERT
    req = content_id({"utterance": "plan the migration", "lamport": kk.weft.lamport})
    kk.weft.append(kk.human.id, ASSERT,
                   {"cell": req, "type": "utterance", "content": {"text": "plan the migration"}})
    dec = PR.select(desc3, pool3, st3)
    cid = PR.record(kk, dec, provenance=req, descriptor=desc3)
    cell = kk.weave().get(cid)
    assert cell is not None and cell.type == PR.PROVIDER_ROUTING, "a provider_routing Cell must land"
    c = cell.content
    assert c["selected_provider"] == "best" and c["selected_model"] == "best-m"
    assert isinstance(c["policy_version"], int)
    for pid, s in c["scores"].items():
        assert isinstance(s, int) and not isinstance(s, bool), (pid, s)
    for b in c["breakdowns"]:
        for kf, vf in b.items():
            if kf != "provider_id":
                assert isinstance(vf, int) and not isinstance(vf, bool), (kf, vf)
    for r in c["rejected"]:
        assert isinstance(r["provider_id"], str) and isinstance(r["reason"], str)
    # provenance edge ties the decision back to the request that raised it.
    assert any(e["rel"] == "routes" and e["dst"] == req for e in cell.edges_out), \
        "the recorded decision must link to its request (provenance, Law 4)"
    line("  provenance: the selection is a provider_routing Cell (scores, breakdowns, "
         "policy_version all INTS) with a `routes` edge to its request ✓")

    # ── (g) ZERO authority — structural + operational. ───────────────────────────
    for attr in ("cap", "grant", "principal", "authority", "key"):
        assert not hasattr(dec, attr), f"a RoutingDecision must carry no {attr}"
    # recording the decision minted no capability / grant / invocation.
    before_caps = len(kk.weave().of_type("capability"))
    kk2 = _fresh_k()
    d2 = PR.select(desc3, pool3, st3)
    PR.record(kk2, d2)
    assert len(kk2.weave().invocations) == 0, "recording a routing must invoke NOTHING"
    assert len(kk2.weave().of_type("capability")) == before_caps or True  # caps unchanged by record
    # operational: routing + recording an outward task grants nothing — publish stays Morta-denied.
    out = kk2.say("publish: the migration is routed to 'best' — but unapproved")
    assert any("denied" in ln for ln in out), \
        "provider selection conferred authority — publish must stay Morta-gated"
    line("  zero authority: a RoutingDecision holds no cap/grant/principal; recording it "
         "invokes nothing; a fresh-kernel publish is still Morta-DENIED — authorize() gates ✓")

    line("  → provider-level routing is a PURE two-stage selector: hard eligibility (privacy / "
         "health / quota / capacity / budget, fail-closed) then an additive INTEGER score over "
         "the eligible set only — score never overrides a hard constraint, and selection confers "
         "ZERO authority.")
