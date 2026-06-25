"""RED1 — a red-team capability scoped to an AUTHORIZED ENGAGEMENT (CAPABILITY_MAP
Part C, the offensive half of the security flagship; the red end of the PURPLE LOOP).

An offensive action is a `capability` whose caveats are the rules of engagement: an
engagement SCOPE (allowed targets), `requires_approval` (Morta), and an SB1 sandbox
profile. The reference probe is a DETERMINISTIC STUB — no real target, no real
payload, no real harm. This check proves, fail-loud:

  - an IN-scope, AUTHORIZED, APPROVED attempt → runs as a stub, is audited on the
    Weft (an INVOKE + receipt + a provenance edge), and emits a `finding` Cell in
    DET1's exact shape — which TRIAGE1 then correlates into an incident (the purple
    loop: a red-team finding becomes a blue-team fixture);
  - an OUT-of-scope target → REFUSED before any invoke (rules-of-engagement);
  - an UNAUTHORIZED principal → DENIED by ocap (a copied grant id is not a token);
  - the outward/irreversible action is Morta-gated → DENIED until approved.

Runs on its OWN fresh Kernel (it forges offensive caps and emits findings).
Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import red, triage
from decima.kernel import Kernel
from decima.hashing import content_id
from decima.weft import ASSERT


def run(_k, line):
    line("\n== RED-TEAM (authorized engagement · rules of engagement · purple loop) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated

    # An authorized engagement: probe hosts in *.lab.internal ONLY. The grant's
    # caveats ARE the rules of engagement (scope + Morta + SB1 sandbox).
    scope = ["*.lab.internal"]
    red_agent, cap_id = red.authorize_engagement(
        k, "port-scan", scope, technique="port-scan", severity="high")
    cap = k.weave().get(cap_id)
    cav = cap.content["caveats"]
    assert cav["engagement"] == scope and cav["requires_approval"] is True
    assert cav["sandbox"]["network"] is False, cav            # SB1: network-denied stub
    line(f"  engagement 'port-scan' scope={scope} · Morta-gated · sandbox(network={cav['sandbox']['network']}) "
         f"[cap {cap_id[:8]}]")

    target = "db01.lab.internal"

    # 1. UNAUTHORIZED principal: an impostor copies the public grant id into its
    #    envelope. The id is not a bearer token — ocap denies on the grantee check.
    impostor = k.keyring.mint("impostor", "agent")
    imp_id = content_id({"agent": "red-impostor"})
    k.weft.append(k.root.id, ASSERT, {
        "cell": imp_id, "type": "agent",
        "content": {"principal": impostor.id, "objective": "steal a scan",
                    "envelope": [cap_id], "sandbox": True},
    })
    r = red.probe(k, k.weave().get(imp_id), cap_id, target)
    assert "denied" in r, r
    line(f"  unauthorized principal copies the grant id → ✋ denied: {r['denied']}")

    # 2. OUT-of-scope target by the AUTHORIZED holder: refused at the rules of
    #    engagement, BEFORE any invoke is written (the cardinal red-team sin).
    r = red.probe(k, red_agent, cap_id, "victim.example.com")
    assert "refused" in r, r
    line(f"  authorized holder, OUT-of-scope target → ⊘ refused: {r['refused']}")

    # 3. IN-scope, authorized, but NOT yet approved: Morta gate denies the outward
    #    /irreversible offensive action until a human approves it.
    r = red.probe(k, red_agent, cap_id, target)
    assert "denied" in r and "approval" in r["denied"].lower(), r
    line(f"  in-scope, authorized, NOT approved → ✋ denied: {r['denied']} (Morta gate)")

    # 4. Approve (Morta) → the in-scope, authorized, approved attempt RUNS as a
    #    deterministic stub, is audited, and emits a finding with provenance.
    k.approve(cap_id)
    r = red.probe(k, red_agent, cap_id, target)
    assert "finding" in r, r
    w = k.weave()
    f = w.get(r["finding"])
    # finding is in DET1's exact shape (detection/rule/severity/source/excerpt)
    assert f.type == "finding" and f.content["rule"] == cap_id
    assert f.content["severity"] == "high" and f.content["source"] == r["asset"]
    # audited: a real INVOKE event, a result receipt, and a found_in provenance edge,
    # all signed by the RED agent's own key (not root / not the orchestrator).
    prov = w.edges_from(f.id, "found_in")
    assert prov and prov[0]["dst"] == r["asset"], prov
    assert r["signer"] == red_agent.content["principal"], r
    assert w.get(r["receipt"]) is not None, "no audit receipt on the Weft"
    line(f"  approved → in-scope attempt ran (stub) → finding {f.id[:8]} sev={f.content['severity']}; "
         f"excerpt={f.content['excerpt']!r}")
    line(f"    audited: invoke {r['invoke_event'][:8]} + receipt {r['receipt'][:8]} + found_in→{r['asset'][:8]} "
         f"(signed by red agent {r['signer'][:8]}) ✓")

    # 5. PURPLE LOOP: a SECOND in-scope host hit by the same engagement → two
    #    correlated high findings, which the BLUE-team TRIAGE1 layer escalates into
    #    ONE incident with a Morta-gated response — with zero new wiring.
    r2 = red.probe(k, red_agent, cap_id, "web01.lab.internal")
    assert "finding" in r2, r2
    incidents = triage.correlate(k)   # group_by rule (the engagement)
    assert len(incidents) == 1, [k.weave().get(i).content for i in incidents]
    inc = k.weave().get(incidents[0])
    cited = set(triage.includes(k.weave(), inc.id))
    assert cited == {r["finding"], r2["finding"]}, inc.content
    assert inc.content["severity"] in ("high", "critical"), inc.content
    resp = triage.response_of(k.weave(), inc.id)
    assert resp is not None and resp.content["requires_approval"] is True, resp
    line(f"  purple loop → 2 red-team findings CORRELATED by blue-team triage into incident "
         f"{inc.id[:8]} (sev={inc.content['severity']}); response Morta-gated ✓")
    line("  → red-team = a scoped, Morta-gated, sandboxed capability; its findings feed "
         "the same triage as blue-team detections (the purple loop).")
