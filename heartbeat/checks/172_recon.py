"""RECON1 — authorized recon / enumeration, the FIRST stage of the red-team kill chain
(CAPABILITY_MAP Part C: "RED-TEAM (recon → reporting)"). It composes RED1's engagement
contract (scope + Morta + SB1 sandbox + ocap) and emits DET1-shaped findings that the
blue-team TRIAGE1 layer correlates — no edits to red.py / detection.py / triage.py.

Recon is an OUTWARD action (pointing tooling at a host to map its services), so it is
governed by the SAME rules as any RED1 probe. The reference enumerator is a
DETERMINISTIC STUB: it contacts no host, sends no packet, maps a FIXED fake surface
derived from the target string. This check proves, fail-loud:

  - an IN-scope, AUTHORIZED, APPROVED enumeration → runs as a stub, is audited on the
    Weft (an INVOKE + receipt, signed by the recon agent's own key), and maps a surface
    into `finding` Cells in DET1's exact shape — which TRIAGE1 correlates into an
    incident with NO new wiring (recon findings feed the same blue-team layer);
  - an OUT-of-scope target → REFUSED before any invoke (rules of engagement);
  - an UNAUTHORIZED principal copying the grant id → DENIED by ocap;
  - the outward action is Morta-gated → DENIED until approved.

Runs on its OWN fresh Kernel (it forges a recon engagement and emits findings).
Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import recon, triage
from decima.kernel import Kernel
from decima.hashing import content_id
from decima.weft import ASSERT


def run(_k, line):
    line("\n== RECON (authorized enumeration · rules of engagement · maps to triage) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated

    # An authorized recon engagement: enumerate hosts in *.lab.internal ONLY. The grant's
    # caveats ARE the rules of engagement (scope + Morta floor + SB1 network-denied stub).
    scope = ["*.lab.internal"]
    recon_agent, cap_id = recon.authorize_recon(k, "surface-map", scope, severity="medium")
    cap = k.weave().get(cap_id)
    cav = cap.content["caveats"]
    assert cav["engagement"] == scope and cav["requires_approval"] is True, cav
    assert cav["sandbox"]["network"] is False, cav            # SB1: network-denied stub
    assert cap.content["effect"] == recon.EFFECT, cap.content  # recon effect, not redteam
    line(f"  engagement 'surface-map' scope={scope} · Morta-gated · sandbox(network={cav['sandbox']['network']}) "
         f"[cap {cap_id[:8]}]")

    target = "db01.lab.internal"

    # 1. UNAUTHORIZED principal: an impostor copies the public grant id into its
    #    envelope. The id is not a bearer token — ocap denies on the grantee check.
    impostor = k.keyring.mint("recon-impostor", "agent")
    imp_id = content_id({"agent": "recon-impostor"})
    k.weft.append(k.root.id, ASSERT, {
        "cell": imp_id, "type": "agent",
        "content": {"principal": impostor.id, "objective": "steal a recon sweep",
                    "envelope": [cap_id], "sandbox": True},
    })
    r = recon.enumerate(k, k.weave().get(imp_id), cap_id, target)
    assert "denied" in r, r
    line(f"  unauthorized principal copies the grant id → ✋ denied: {r['denied']}")

    # 2. OUT-of-scope target by the AUTHORIZED holder: refused at the rules of
    #    engagement, BEFORE any invoke is written (the cardinal red-team sin).
    r = recon.enumerate(k, recon_agent, cap_id, "victim.example.com")
    assert "refused" in r, r
    line(f"  authorized holder, OUT-of-scope target → ⊘ refused: {r['refused']}")

    # 3. IN-scope, authorized, but NOT yet approved: Morta gate denies the outward
    #    recon sweep until a human approves it.
    r = recon.enumerate(k, recon_agent, cap_id, target)
    assert "denied" in r and "approval" in r["denied"].lower(), r
    line(f"  in-scope, authorized, NOT approved → ✋ denied: {r['denied']} (Morta gate)")

    # 4. Approve (Morta) → the in-scope, authorized, approved enumeration RUNS as a
    #    deterministic stub, is audited, and maps a surface into DET1-shaped findings.
    k.approve(cap_id)
    r = recon.enumerate(k, recon_agent, cap_id, target)
    assert "findings" in r and r["findings"], r
    assert r["surface"], r                              # a non-empty mapped surface
    w = k.weave()
    for fid in r["findings"]:
        f = w.get(fid)
        # each finding is in DET1's exact shape (detection/rule/severity/source/excerpt)
        assert f.type == "finding" and f.content["rule"] == cap_id, f.content
        assert f.content["source"] == r["asset"], f.content
        prov = w.edges_from(f.id, "found_in")
        assert prov and prov[0]["dst"] == r["asset"], prov   # provenance → the asset
    # audited: a real INVOKE event + a result receipt, signed by the RECON agent's own
    # key (not root / not the orchestrator).
    assert r["signer"] == recon_agent.content["principal"], r
    assert w.get(r["receipt"]) is not None, "no audit receipt on the Weft"
    svcs = ", ".join(f"{s['service']}:{s['port']}" for s in r["surface"])
    line(f"  approved → in-scope enumeration ran (stub) → mapped surface [{svcs}] → "
         f"{len(r['findings'])} finding(s)")
    line(f"    audited: invoke {r['invoke_event'][:8]} + receipt {r['receipt'][:8]} + asset {r['asset'][:8]} "
         f"(signed by recon agent {r['signer'][:8]}) ✓")

    # 5. The recon findings are consumable by the SAME blue-team TRIAGE1 layer as any
    #    detection — with zero new wiring. Enumerate a SECOND in-scope host so the
    #    correlated high findings (mysql/redis exposure) escalate into ONE incident.
    r2 = recon.enumerate(k, recon_agent, cap_id, "web01.lab.internal")
    assert "findings" in r2 and r2["findings"], r2
    incidents = triage.correlate(k, group_by="rule")   # group by the recon engagement
    assert len(incidents) == 1, [k.weave().get(i).content for i in incidents]
    inc = k.weave().get(incidents[0])
    cited = set(triage.includes(k.weave(), inc.id))
    assert cited == set(r["findings"]) | set(r2["findings"]), inc.content
    assert inc.content["severity"] in ("high", "critical"), inc.content
    resp = triage.response_of(k.weave(), inc.id)
    assert resp is not None and resp.content["requires_approval"] is True, resp
    line(f"  triage → {len(cited)} recon findings across 2 hosts CORRELATED by blue-team into "
         f"incident {inc.id[:8]} (sev={inc.content['severity']}); response Morta-gated ✓")
    line("  → recon = a scoped, Morta-gated, sandboxed enumeration capability (RED1's "
         "engagement contract); its surface findings feed the same triage as blue-team detections.")
