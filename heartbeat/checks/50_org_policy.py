"""D3 — learned org policy: prior task outcomes DRIVE the next delegation choice.

`org_score` already folds the task tree into metrics; `org_signal`/`org_policy`
(kernel.py) sharpen that into a per-capability verdict and let it gate delegation.
This check builds a track record where a held capability keeps failing at the
authorization gate, then shows the orchestrator REFUSE to delegate it again —
where the naive path would spawn yet another worker doomed to be denied — while a
capability with a clean record still delegates and completes.

Contract: expose run(k, line). Fail loud (assert) on regression.
"""
from decima.agent import Action, RuleBrain


def run(k, line):
    line("\n== ORG POLICY (learned: recorded outcomes drive the next delegation) ==")

    # A held capability that ALWAYS fails at the gate: requires an approval nobody
    # grants, so every delegated worker that invokes it is denied. (integrate_tool
    # is the public registry path — no kernel edit.)
    k.integrate_tool("fussy", lambda impl, args: {"out": "ok"},
                     caveats={"requires_approval": True})

    # Force the deterministic rule brain for worker decisions so the demo does not
    # depend on a model/network being present; restore it after.
    saved_brain, k.brain = k.brain, RuleBrain()

    def delegate(cap_name, worker, objective):
        act = Action("delegate", tasks=[{"capability": cap_name, "subagent": worker,
                                         "objective": objective, "budget": 5}])
        lines, _ = k._delegate(k.weave().get(k.decima_agent_id), act,
                               depth=1, label="decima", parent_task=None)
        return lines

    def fussy_tasks():
        return [t for t in k.weave().of_type("task")
                if t.content.get("capability") == "fussy"]

    try:
        # 1. Build the track record: two delegations of 'fussy', both denied at the
        #    gate (the workers invoke it; no approval exists).
        for w in ("Probe-1", "Probe-2"):
            for ln in delegate("fussy", w, "fussy: do it"):
                line("  " + ln)
        sig = k.org_signal("fussy")
        line(f"  fussy signal → denied={sig['denied']} completed={sig['completed']} "
             f"distrusted={sig['distrusted']}")
        assert sig["denied"] >= 2 and sig["distrusted"], sig

        # 2. The decision flips by history: policy now refuses 'fussy', still allows
        #    a capability ('shell') with a clean record (completed delegations).
        allow_fussy, why = k.org_policy("fussy")
        allow_shell, _ = k.org_policy("shell")
        line(f"  org_policy('fussy') → {allow_fussy} — {why}")
        line(f"  org_policy('shell') → {allow_shell}")
        assert not allow_fussy and allow_shell, (allow_fussy, allow_shell)

        # 3. Same brief, learned path: delegating 'fussy' AGAIN is refused up front —
        #    no new worker spawned — where the naive path would spawn+deny a 3rd time.
        denied_before = len([t for t in fussy_tasks() if t.content["status"] == "denied"])
        for ln in delegate("fussy", "Probe-3", "fussy: do it"):
            line("  " + ln)
        refused = [t for t in fussy_tasks() if t.content["status"] == "refused"]
        denied = [t for t in fussy_tasks() if t.content["status"] == "denied"]
        line(f"  3rd brief for 'fussy' → status 'refused', not another denial "
             f"(refused={len(refused)} denied={len(denied)}, was {denied_before})")
        assert len(refused) == 1, refused
        assert len(denied) == denied_before, (len(denied), denied_before)   # no new denial
        assert all(not t.content.get("worker") for t in refused)            # no worker spent

        # 4. Contrast: a healthy capability ('shell', still held) delegates and
        #    completes — the policy refuses only what its OWN history distrusts.
        shell_done_before = len([t for t in k.weave().of_type("task")
                                 if t.content.get("capability") == "shell"
                                 and t.content.get("status") == "done"])
        for ln in delegate("shell", "Clock-OK", "date"):
            line("  " + ln)
        shell_done = [t for t in k.weave().of_type("task")
                      if t.content.get("capability") == "shell"
                      and t.content.get("status") == "done"]
        assert len(shell_done) == shell_done_before + 1, (len(shell_done), shell_done_before)
        line(f"  shell still delegates + completes (done tasks: {shell_done_before} → "
             f"{len(shell_done)}) — policy refuses only what its OWN history distrusts")
        line("  → org_score is no longer just measured; it DRIVES the delegation choice.")
    finally:
        k.brain = saved_brain
