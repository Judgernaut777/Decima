"""PURPLE1 — the purple-team loop, AUTOMATED (CAPABILITY_MAP Part C, the flagship's
native fit: "purple loop turns red evasions into FP/TP fixtures").

PURPLE1 composes three existing modules — it edits none of them:
  RED1   : an authorized, scoped, Morta-gated, sandboxed probe → a `finding` Cell.
  DET1   : a detection promotes only if it passes its TP/FP fixtures (the unit test).
  TRIAGE1: findings correlate into an `incident`.

This check proves, fail-loud, that PURPLE1 closes red→blue automatically:
  - a RED1 red-team finding (an evasion) becomes a NEW DET1 fixture (a TP the rule
    must now catch) that RE-GATES the rule: a NARROW rule that misses the evasion is
    REJECTED (stays quarantined) until WIDENED — the purple loop's teeth;
  - the red-team findings CORRELATE into ONE TRIAGE1 incident with a Morta-gated
    response;
  - the whole cycle is on the Weft with provenance (a `purple_loop` Cell whose
    `hardened_from` edge points back at the red finding).

Runs on its OWN fresh Kernel (it forges offensive caps, detections, and findings).
Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import purple, triage
from decima.kernel import Kernel


def run(_k, line):
    line("\n== PURPLE LOOP (red finding → re-gated detection → incident · automated) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated

    # Drive the whole loop: RED1 probe → finding → DET1 re-gate (narrow fails, wide
    # passes) → TRIAGE1 incident. Each step composes a module's PUBLIC API only.
    res = purple.run_loop(k)
    w = k.weave()

    # --- RED1: authorized probes produced red-team findings (the evasions) --------
    assert len(res["findings"]) >= 2, res["findings"]
    for fid in res["findings"]:
        f = w.get(fid)
        assert f.type == "finding" and f.content["rule"] == res["cap_id"], f.content
    line(f"  RED1 → {len(res['findings'])} authorized, Morta-gated, sandboxed probes "
         f"emitted findings; evasion signal = {res['evasion']!r}")

    # --- DET1 re-gate (a): the NARROW rule MISSES the evasion → REJECTED -----------
    narrow = res["narrow"]
    assert not narrow.promoted, ("narrow rule should fail the re-gate", narrow.gate)
    assert res["fixture"] in narrow.gate["tp_missed"], narrow.gate
    # a rejected rule stays quarantined and cannot raise findings
    assert detect_is_empty(k, narrow.det_id), "a quarantined rule must not fire"
    line(f"  DET1 re-gate (a): evasion added as a TP → NARROW rule {str(narrow)}")
    line(f"    → rejected (missed {narrow.gate['tp_missed']}); quarantined rule raised 0 findings ✓")

    # --- DET1 re-gate (b): the WIDENED rule catches it → PROMOTED ------------------
    wide = res["wide"]
    assert wide.promoted, ("widened rule should pass the re-gate", wide.gate)
    line(f"  DET1 re-gate (b): same fixture, WIDENED rule {str(wide)} → promoted ✓")

    # --- the purple loop is on the Weft with provenance (red finding → detection) --
    all_loops = purple.loops(w)
    assert len(all_loops) == 2, [l.content for l in all_loops]      # narrow + wide
    wl = w.get(res["wide_loop"])
    assert wl.type == purple.PURPLE_LOOP and wl.content["regated_promoted"] is True
    assert wl.content["fixture"] == res["fixture"] and wl.content["fixture_kind"] == "tp"
    hardened = purple.hardened_from(w, wl.id)
    assert hardened == [res["findings"][0]], hardened                # edge → the red finding
    line(f"  purple-loop Cell {wl.id[:8]} on the Weft: detection {wl.content['detection'][:8]} "
         f"hardened_from→ red finding {hardened[0][:8]} (provenance ✓)")

    # --- TRIAGE1: the red findings correlate into ONE incident with a response -----
    inc_id = res["incident"]
    assert inc_id is not None, ("red findings did not correlate into an incident", res["incidents"])
    inc = w.get(inc_id)
    cited = set(triage.includes(w, inc.id))
    assert cited == set(res["findings"]), (cited, res["findings"])
    assert inc.content["severity"] in ("high", "critical"), inc.content
    resp = triage.response_of(w, inc.id)
    assert resp is not None and resp.content["requires_approval"] is True, resp
    line(f"  TRIAGE1 → {len(cited)} red findings correlated into incident {inc.id[:8]} "
         f"(sev={inc.content['severity']}); response Morta-gated ✓")

    line("  → PURPLE1: a red-team evasion auto-writes a blue-team test that re-gates the "
         "rule (narrow→rejected, widened→promoted); findings triage to one incident; all on the Weft.")


def detect_is_empty(k, det_id) -> bool:
    """A quarantined (rejected) detection raises no findings over any type — proven by
    DET1's own `detect`, which refuses an unpromoted rule."""
    from decima import detection
    # apply over the asset Cells RED1 created; a rejected rule must return [].
    return detection.detect(k, det_id, "asset") == []
