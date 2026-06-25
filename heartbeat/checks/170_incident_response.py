"""IR1 — incident-response playbooks: compose triage + planning + projects.

Proves (all on the Weft, fail-loud):
  - DET1 findings → a TRIAGE1 incident → a remediation PLAYBOOK: an ORDERED plan
    (isolate → investigate → remediate → verify) opened as a PROJECT of ptasks,
    both linked back to the incident with `responds_to` provenance;
  - advancing a step marks the plan step done AND moves its kanban ptask to done,
    so `status` updates (open → done) in lock-step;
  - a HIGHER-severity incident yields MORE and DIFFERENT steps than a low one;
  - the response is structure only — no effect runs; executing a step stays
    Morta-gated elsewhere.

Runs on its OWN fresh Kernel (forges detections, emits findings). Contract:
run(k, line). Fail loud.
"""
import os
import tempfile

from decima import detection, triage, model, planning, projects, incident_response as ir
from decima.kernel import Kernel
from decima.hashing import content_id


def _seed_incident(k, hosts, *, pattern, name, severity, tp, fp):
    """Forge a promoted detection, drop `hosts` observations, emit findings, and
    correlate them into ONE TRIAGE1 incident. Returns the incident Cell."""
    det = detection.forge_detection(k, name, pattern, severity, tp, fp, field="text")
    assert det.promoted, det.gate
    for tag, text in hosts.items():
        model.assert_content(k.weft, k.root.id, content_id({"obs": tag}), "observation",
                             {"text": text})
    found = detection.detect(k, det.det_id, "observation")
    assert len(found) == len(hosts), (len(found), len(hosts))
    before = {i.id for i in triage.incidents(k.weave())}
    triage.correlate(k)
    new = [i for i in triage.incidents(k.weave()) if i.id not in before]
    assert len(new) == 1, [i.content for i in new]
    return new[0]


def run(_k, line):
    line("\n== INCIDENT RESPONSE (incident → playbook plan → project of ptasks) — IR1 ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    w = lambda: k.weave()

    # ---- a LOW-severity incident: 2 medium findings correlate ---------------
    lo_inc = _seed_incident(
        k, {"web-1": "deprecated-tls handshake", "web-2": "deprecated-tls handshake"},
        pattern=r"deprecated-tls", name="weak-tls", severity="medium",
        tp=["deprecated-tls handshake"], fp=["modern tls 1.3"])
    line(f"  seeded {lo_inc.content['severity']} incident {lo_inc.id[:8]} "
         f"({lo_inc.content['finding_count']} findings) ✓")

    # respond → an ORDERED playbook plan opened as a project of ptasks.
    lo = ir.respond(k, lo_inc.id)
    lo_plan, lo_proj = lo["plan"], lo["project"]
    assert w().get(lo_plan).type == planning.PLAN
    assert w().get(lo_proj).type == projects.PROJECT
    # one ptask per plan step, each keyed by its step id, all of type ptask.
    assert len(lo["ptasks"]) == len(lo["steps"]) > 0
    for tid in lo["ptasks"]:
        assert w().get(tid).type == "ptask", tid
    # the playbook phases are present and ordered isolate → … → verify.
    lo_objs = [w().get(s).content["objective"] for s in lo["steps"]]
    assert lo_objs[0].startswith("isolate") and lo_objs[-1].startswith("verify"), lo_objs
    line(f"  respond → plan {lo_plan[:8]} of {len(lo['steps'])} ordered steps "
         f"(isolate→…→verify) + project {lo_proj[:8]} of {len(lo['ptasks'])} ptasks ✓")

    # provenance: BOTH plan and project link back to the incident via responds_to.
    assert ir.plan_of(k, lo_inc.id) == lo_plan
    assert ir.project_of(k, lo_inc.id) == lo_proj
    back = {e["src"] for e in w().edges_to(lo_inc.id, ir.RESPONDS_TO)}
    assert {lo_plan, lo_proj} <= back, back
    line("  plan + project both linked back to the incident (responds_to on the Weft) ✓")

    # ---- advancing steps updates status (open → done) in lock-step ----------
    st0 = ir.status(k, lo_inc.id)
    assert st0["done"] == 0 and st0["open"] == st0["total"] and not st0["complete"], st0
    # walk the topological order; each advance marks the step done AND moves the
    # kanban ptask to done, and unlocks exactly the next step on the frontier.
    ready0 = {b["step"] for b in st0["ready"]}
    assert len(ready0) == 1, ready0          # only the first step is delegable
    done_so_far = 0
    for sid in lo["steps"]:
        st = ir.advance(k, lo_inc.id, sid)
        done_so_far += 1
        assert st["done"] == done_so_far, (st["done"], done_so_far)
        # the matching board ptask is now in the done column.
        tid = projects.ptask_id(lo_proj, sid)
        assert any(x["ptask"] == tid for x in st["board"]["done"]), st["board"]
    final = ir.status(k, lo_inc.id)
    assert final["complete"] and final["done"] == final["total"] and final["open"] == 0, final
    line(f"  advanced all {final['total']} steps → board done; status complete ✓")

    # ---- a HIGHER-severity incident yields MORE + DIFFERENT steps -----------
    hi_inc = _seed_incident(
        k, {"db-1": "ransomware note dropped", "db-2": "ransomware note dropped",
            "db-3": "ransomware note dropped"},
        pattern=r"ransomware", name="ransomware", severity="critical",
        tp=["ransomware note dropped"], fp=["routine backup"])
    assert hi_inc.content["severity"] == "critical", hi_inc.content
    hi = ir.respond(k, hi_inc.id)
    hi_objs = [w().get(s).content["objective"] for s in hi["steps"]]
    assert len(hi["steps"]) > len(lo["steps"]), (len(hi["steps"]), len(lo["steps"]))
    # the deeper playbook adds containment/forensics/hardening the low one lacked.
    hi_keys = {w().get(s).content["key"] for s in hi["steps"]}
    lo_keys = {w().get(s).content["key"] for s in lo["steps"]}
    assert {"quarantine", "forensics", "harden"} <= hi_keys, hi_keys
    assert {"quarantine", "forensics", "harden"}.isdisjoint(lo_keys), lo_keys
    line(f"  critical incident → {len(hi['steps'])}-step playbook "
         f"(adds quarantine+forensics+harden) vs {len(lo['steps'])}-step low playbook ✓")

    # ---- no effect ran: it is structure (proposals), not execution ----------
    # every step's `capability` is a STRING hint that grants nothing; the project
    # is a board, not an INVOKE. (Executing a step is Morta-gated elsewhere.)
    for s in hi["steps"]:
        cap = w().get(s).content["capability"]
        assert isinstance(cap, str) and cap, cap
    # the high incident still has all steps open (we only PLANNED it).
    hist = ir.status(k, hi_inc.id)
    assert hist["done"] == 0 and hist["open"] == len(hi["steps"]) and not hist["complete"]
    line("  response is structure only — steps are proposals (capability hints), "
         "executing any stays Morta-gated; nothing invoked ✓")

    line("  → IR1 composes triage(read)+planning+projects into incident playbooks, "
         "all signed Cells/edges on the Weft.")
