"""OR1 — the Orientation lens ("the Big O"): the agent interprets a request through
the user's values + governance + horizon BEFORE it decides.

Proves:
  - an Orientation is assembled from profile (trusted preferences) + governance
    (B4 rules) + the agent horizon — and an UNTRUSTED preference is excluded;
  - a request conflicting with a governance rule is caught AT ORIENT-TIME, with the
    rule cited as evidence, and the brain refuses it (no INVOKE);
  - a stated preference changes the chosen action.

Runs on its OWN fresh Kernel: this check lays down governance + a steering
preference, which would otherwise pollute the shared kernel for other checks (and
`smoke.py` discovers checks by lexical filename order, so '100' runs early). Pattern
matches checks/74/80/94. Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import memory, orientation, reckoner, model
from decima.kernel import Kernel
from decima.hashing import content_id


def run(_k, line):
    line("\n== ORIENTATION LENS (values + governance + horizon, consulted before decide) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    wf, author = k.weft, k.human.id
    src = k.weave().of_type("agent")[-1].id        # any existing cell to ground evidence

    # --- profile + governance laid down on the Weave -------------------------
    orientation.set_preference(wf, author, "prefer-cap", "greet", src)      # trusted → binds
    orientation.set_preference(wf, author, "tone", "reckless", src,         # untrusted → must NOT bind
                               instruction_eligible=False)
    ban = memory.remember_governance(wf, author, memory.BANNED_ACTION, target="rm -rf",
                                     reason="rm -rf wipes the box — never run it",
                                     evidence_src=src)
    reckoner.forge(k, "greet", "transform", "upper", "hi", "HI")            # the preferred cap, now held

    # --- (1) orientation assembled from profile + governance + horizon -------
    # a synthetic agent carrying a horizon, to show horizon is part of the lens
    ag_id = content_id({"orient_agent": "scout"})
    model.assert_content(wf, author, ag_id, "agent",
                         {"principal": "scout", "envelope": [], "horizon": "realm:default"})
    o = orientation.orient(k.weave(), k.weave().get(ag_id), "summarize the news")
    assert o.value("prefer-cap") == "greet" and "tone" not in o.values, o.values
    assert o.horizon == "realm:default" and o.allow and not o.blocked
    line(f"  oriented: values={sorted(o.values)} (untrusted 'tone' excluded) · "
         f"horizon={o.horizon!r} · verdict={o.governance['verdict']} · fast_path={o.fast_path}")

    # --- (2) a conflicting request is caught at orient-time, rule cited ------
    decima = k.weave().get(k.decima_agent_id)
    oc = orientation.orient(k.weave(), decima, "shell: rm -rf /tmp/data")
    assert oc.blocked and oc.evidence, oc
    cited = oc.evidence[0]
    assert cited["kind"] == memory.BANNED_ACTION
    assert ban in [e["governance"] for e in oc.evidence]
    line(f"  conflict caught at ORIENT-time: blocked={oc.blocked}; "
         f"rule cited → {cited['kind']}: {cited['reason']!r}")
    # driven through a real turn, the brain refuses (no INVOKE) and cites the rule
    out = k.say("shell: rm -rf /tmp/data")
    assert any("✋" in ln for ln in out) and not any("[shell]" in ln for ln in out), out
    line(f"  brain refused before deciding: {[l for l in out if '✋' in l][-1].strip()}")

    # --- (3) a preference changes the chosen action -------------------------
    # an utterance matching no capability pattern → orientation steers to 'greet'
    out2 = k.say("salutations everyone")
    assert any("[greet]" in ln for ln in out2), out2
    line(f"  preference steered the fallback: {[l for l in out2 if '[greet]' in l][-1].strip()}")

    # --- (4) the untrusted preference is DATA, never binding ----------------
    assert "tone" not in orientation.preferences(k.weave())
    line("  untrusted 'tone' preference stays DATA, never orients — recall-vs-instruct holds ✓")
