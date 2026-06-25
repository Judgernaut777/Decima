"""DEVOPS1 — a CI/CD pipeline as a PLAN1 acyclic DAG (build→test→deploy) whose DEPLOY
is a Morta-gated, sandboxed, audited effect.

Proves: the pipeline is an acyclic stage DAG; build+test run as sandboxed stubs; the
deploy stage is DENIED until approved (Morta), then DEPLOYED with an EffectReceipt on
the Weft; status reflects each stage. Runs on its OWN fresh Kernel (it forges a DEPLOY
capability and registers effects) so it stays out of the shared kernel's state.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import devops, planning, executor
from decima.kernel import Kernel


def run(_k, line):
    line("\n== CI/CD PIPELINE (PLAN1 DAG · build/test sandboxed · DEPLOY Morta-gated · audited) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    decima = lambda: k.weave().get(k.decima_agent_id)

    caps = devops.install_pipeline_effects(k)

    # ---- (1) define an ACYCLIC build→test→deploy pipeline on the Weft -------
    p = devops.define_pipeline(k, "checkout-svc")
    topo = planning.topological_order(k, p["plan"])             # raises if cyclic
    keys = [k.weave().get(s).content["key"] for s in topo]
    assert keys == [devops.BUILD, devops.TEST, devops.DEPLOY_STAGE], keys
    plan_cell = k.weave().get(p["plan"])
    assert plan_cell.content.get("scope") == devops.PIPELINE
    line(f"  pipeline {p['plan'][:8]} = acyclic DAG {' → '.join(keys)} (on the Weft) ✓")

    # a cyclic pipeline is REJECTED at define time (fail closed) ----
    try:
        devops.define_pipeline(k, "bad", steps=[
            {"key": "a", "objective": "a", "depends_on": ["b"]},
            {"key": "b", "objective": "b", "depends_on": ["a"]},
        ])
        raise AssertionError("a cyclic pipeline must be rejected")
    except ValueError as e:
        assert "cyclic" in str(e).lower(), e
    line("  cyclic pipeline → REJECTED at define time (fail closed) ✓")

    # ---- (2) build + test run as SANDBOXED stubs --------------------------
    rb = devops.run_stage(k, decima(), p, devops.BUILD, caps)
    assert rb["status"] == devops.PASSED and rb["artifact"], rb
    rb_receipt = k.weave().get(rb["receipt"])
    assert rb_receipt.content["status"] == executor.SUCCEEDED
    rt = devops.run_stage(k, decima(), p, devops.TEST, caps)
    assert rt["status"] == devops.PASSED, rt
    line(f"  build → {rb['status']} (artifact {rb['artifact']}, sandboxed); "
         f"test → {rt['status']} (sandboxed) ✓")

    # ---- (3) deploy is DENIED until Morta approves ------------------------
    d0 = devops.run_stage(k, decima(), p, devops.DEPLOY_STAGE, caps)
    assert d0["status"] == devops.DENIED and "approval" in d0["denied"].lower(), d0
    deploy_step = k.weave().get(p["steps"][devops.DEPLOY_STAGE])
    assert deploy_step.content["status"] == planning.PENDING                 # nothing deployed
    fin = [c for c in k.weave().of_type(devops.RESULT)
           if c.content.get("effect_class") == devops.DEPLOY
           and c.content.get("status") == executor.SUCCEEDED]
    assert len(fin) == 0, fin                                                # no deploy effect ran
    line(f"  pre-approval: deploy DENIED — {d0['denied']} (step still pending, nothing shipped) ✓")

    k.approve(caps[devops.DEPLOY_STAGE])                                     # human/Morta approves
    line("  (a human approves the DEPLOY capability — Morta gate)")

    # ---- (4) approved deploy runs (stub), AUDITED on the Weft -------------
    d1 = devops.run_stage(k, decima(), p, devops.DEPLOY_STAGE, caps)
    assert d1["status"] == devops.DEPLOYED, d1
    receipt = k.weave().get(d1["receipt"])
    assert receipt.content["effect_class"] == devops.DEPLOY
    assert receipt.content["status"] == executor.SUCCEEDED
    assert receipt.content["artifact"] == rb["artifact"]                     # shipped the build artifact
    assert k.weave().get(p["steps"][devops.DEPLOY_STAGE]).content["status"] == planning.DONE
    line(f"  approved: deploy → {d1['status']} → receipt {d1['receipt'][:8]} "
         f"(class={receipt.content['effect_class']}, target={d1['target']}, audited) ✓")

    # ---- (5) status reflects each stage on the Weft -----------------------
    st = devops.status(k, p)
    by = {s["stage"]: s["status"] for s in st["stages"]}
    assert by == {devops.BUILD: devops.PASSED, devops.TEST: devops.PASSED,
                  devops.DEPLOY_STAGE: devops.DEPLOYED}, by
    assert st["complete"] and st["deployed"], st
    line(f"  status: {by[devops.BUILD]}/{by[devops.TEST]}/{by[devops.DEPLOY_STAGE]} "
         f"(complete={st['complete']}, deployed={st['deployed']}) ✓")
