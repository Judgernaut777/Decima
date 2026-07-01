"""ENGAGEMENT / SESSION — a scoped, ROE-governed unit of orchestrated work.

From the Method platform ("sessions orchestrated at scale; clear rules of engagement
dictate when user input is needed; structured data provides context"): an engagement
is a scoped session carrying structured CONTEXT (data) + a Rules-of-Engagement policy;
the ROE DICTATES when the human decides, per action. This check proves:

  - an act whose effect_class is READ → the engagement ACTS (runs it, here via an
    injected `run` stub);
  - an act whose effect_class is FINANCIAL → PENDING_APPROVAL — NOT run (the run stub
    is asserted to have NOT fired): ROE said a human must decide;
  - an act on capability "exploit" → REFUSED (out of scope, never run);
  - `approve_action` on the pending one → a human approves → now ACTED (runs);
  - `status` folds the action counts by outcome — the session summary;
  - the structured context is carried verbatim on the engagement Cell (as DATA);
  - ROE GRANTS NO AUTHORITY: a "proceed" verdict that invokes a Morta-gated capability
    is STILL denied by the kernel's authorize/Morta until the capability is approved —
    ROE is policy (when the human decides); the ocap gate is independent.

Contract: run(k, line). Fail loud. Owns a fresh, offline Kernel.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import engagement as E
from decima import roe as R
from decima import manifest as M


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== ENGAGEMENT / SESSION (scoped, ROE-governed orchestrated work) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # An ROE: reads proceed, money needs a human, exploit is out of scope, else approve.
    pol = R.roe_policy("engagement-1", [
        {"match": {"effect_class": "READ"}, "verdict": "proceed", "reason": "reads are safe"},
        {"match": {"effect_class": "FINANCIAL"}, "verdict": "approve", "reason": "money moves need a human"},
        {"match": {"capability": "exploit"}, "verdict": "refuse", "reason": "out of scope"},
    ], default="approve")
    rid = R.register(kk, pol)

    # A run stub that records whether/what it was called with — so we can prove a
    # pending action is NOT run.
    calls = []
    def run_stub(_k, action):
        calls.append(action)
        return {"ok": {"out": f"ran {action['capability']}"}}

    # Open an engagement with structured CONTEXT (pure data) + the ROE.
    ctx = {"scope": "acme-prod", "ticket": 42, "hosts": ["h1", "h2"]}
    eng = E.open_engagement(kk, "assess acme", roe=rid, context=ctx)

    # 1. READ → acted (runs via the injected run stub). ────────────────────────────────
    r_read = E.act(kk, eng, {"capability": "reader", "effect_class": "READ"}, run=run_stub)
    assert r_read["status"] == "acted", r_read
    assert r_read["result"]["ok"]["out"] == "ran reader", r_read
    assert len(calls) == 1, "READ/proceed must actually run the action"
    line("  READ action → ROE 'proceed' → engagement ACTED (ran via run stub) ✓")

    # 2. FINANCIAL → pending_approval, NOT run (the run stub must NOT fire). ─────────────
    r_fin = E.act(kk, eng, {"capability": "wire", "effect_class": "FINANCIAL"}, run=run_stub)
    assert r_fin["status"] == "pending_approval", r_fin
    assert len(calls) == 1, "FINANCIAL/approve must NOT run — a human must decide first"
    pending_idx = r_fin["idx"]
    line("  FINANCIAL action → ROE 'approve' → PENDING (run stub NOT called — human decides) ✓")

    # 3. capability 'exploit' → refused (never run). ────────────────────────────────────
    r_x = E.act(kk, eng, {"capability": "exploit"}, run=run_stub)
    assert r_x["status"] == "refused" and r_x["reason"] == "out of scope", r_x
    assert len(calls) == 1, "a refused action must never run"
    line("  'exploit' action → ROE 'refuse' → REFUSED (out of scope, never run) ✓")

    # 4. approve_action on the pending FINANCIAL → a human approves → now acted. ─────────
    r_appr = E.approve_action(kk, eng, pending_idx, run=run_stub)
    assert r_appr["status"] == "acted", r_appr
    assert len(calls) == 2 and calls[1]["capability"] == "wire", "approval must now run it"
    line("  approve_action(pending) → human approves → now ACTED (runs the held action) ✓")

    # 5. status folds the counts — the session summary. ─────────────────────────────────
    s = E.status(kk, eng)
    assert (s["acted"], s["pending_approval"], s["refused"], s["total"]) == (2, 0, 1, 3), s
    line(f"  status folds outcomes: acted={s['acted']} pending={s['pending_approval']} "
         f"refused={s['refused']} (total {s['total']}) ✓")

    # 6. structured context is carried verbatim on the engagement Cell (as DATA). ────────
    cell = kk.weave().get(eng)
    assert cell.type == E.ENGAGEMENT and cell.content["context"] == ctx, cell.content
    assert s["context"] == ctx, "status surfaces the structured context"
    line("  structured context carried on the engagement Cell (treated as data) ✓")

    # 7. ROE grants NO authority: a 'proceed' on a Morta-gated capability is STILL denied
    #    by the kernel until the capability is approved — authorize gates independently. ─
    gated = M.capability_manifest("gated.read", archetype="EFFECT", effect_class="READ",
                                  caveats={"requires_approval": True})
    _, gcap = M.install(kk, gated, lambda _impl, args: {"out": "sensitive-read"})
    eng2 = E.open_engagement(kk, "gated probe", roe=rid, context={"scope": "gated"})
    # effect_class READ ⇒ ROE says proceed; the engagement invokes for real (no run stub).
    r_gate = E.act(kk, eng2, {"capability": gcap, "effect_class": "READ"},
                   agent_cell=_decima(kk))
    assert r_gate["status"] == "acted", r_gate            # ROE let it proceed…
    assert "denied" in r_gate["result"] and "approval" in r_gate["result"]["denied"], \
        "a proceed verdict must NOT bypass Morta — the kernel still gates the invoke"
    # Now a human approves the capability at the kernel (Morta) → the same act runs.
    kk.approve(gcap)
    r_gate2 = E.act(kk, eng2, {"capability": gcap, "effect_class": "READ"},
                    agent_cell=_decima(kk))
    assert "ok" in r_gate2["result"] and r_gate2["result"]["ok"]["out"] == "sensitive-read", r_gate2
    line("  ROE 'proceed' grants no authority: Morta-gated invoke denied until approved, "
         "then runs — authorize/Morta gate independently of ROE ✓")

    line("  → an engagement is a scoped, ROE-governed session with structured context: "
         "policy dictates when the human decides, composing with (never replacing) the "
         "ocap gate.")
