"""PURPLE1 — the purple-team loop, AUTOMATED (CAPABILITY_MAP Part C, "detection
engineering … purple loop turns red evasions into FP/TP fixtures").

Red and blue are two halves of one feedback loop, and Decima already has both ends:

  - RED1 (`red.py`) runs an AUTHORIZED, scoped, Morta-gated, sandboxed offensive
    probe and emits a `finding` Cell — its `excerpt` is the attacker-observed signal
    (an evasion / a successful probe);
  - DET1 (`detection.py`) forges a detection that promotes ONLY if it matches every
    TP fixture and no FP — the test-gate IS the rule's unit test;
  - TRIAGE1 (`triage.py`) correlates `finding` Cells into an `incident`.

This module CLOSES red→blue automatically by composing the three PUBLIC APIs (it
edits none of them, nor any core file):

  (a) `harden_from_finding` — take a RED1 finding and turn it into a NEW DET1 fixture:
      add its excerpt as a TP the detection MUST now catch (or an FP it must NOT),
      then RE-FORGE / re-gate the rule via `detection.forge_detection`. A rule too
      narrow to catch the evasion FAILS its re-gate and stays quarantined until
      widened — that failure is the whole point: the red-team evasion just wrote a
      blue-team test the old rule cannot pass.

  (b) `run_loop` — the full cycle end-to-end: a RED1 probe → finding → a DET1 fixture
      that re-gates a rule (narrow fails, widened passes) → a TRIAGE1 incident, with
      a `purple_loop` provenance Cell on the Weft linking the red finding to the
      detection it hardened.

Laws upheld (all inherited from the composed modules + how we wire them):
  - the finding's excerpt is UNTRUSTED data — it is only READ as a fixture string,
    never executed (DET1 only ever `re.search`es it);
  - the offensive end stays authorized/scoped/Morta-gated (RED1's contract is
    unchanged — we call `red.probe`, which enforces it);
  - no ambient authority — every write is signed by a real principal;
  - everything is audited on the Weft with provenance (the `purple_loop` Cell + edges);
  - ints not floats (severities are ranks/strings; counts are ints).
"""
from decima import red, detection, triage, model
from decima.hashing import content_id

# The Cell type recording one closed purple-loop: a red finding → the detection it
# re-gated. Signed provenance, so the loop itself is auditable / time-travelable.
PURPLE_LOOP = "purple_loop"


def _finding_excerpt(k, finding) -> str:
    """The attacker-observed signal carried by a RED1 (or DET1) `finding` Cell — the
    string the blue team must now learn to catch. `finding` may be a Cell or its id."""
    cell = finding if hasattr(finding, "content") else k.weave().get(finding)
    if cell is None or cell.type != "finding":
        raise ValueError(f"not a finding cell: {finding!r}")
    excerpt = cell.content.get("excerpt", "")
    if not excerpt:
        raise ValueError(f"finding {cell.id[:8]} carries no excerpt to learn from")
    return excerpt


def harden_from_finding(k, finding, detection_spec, *, as_fp=False):
    """Turn a RED1 red-team finding into a NEW DET1 fixture and RE-GATE the rule.

    `detection_spec` is the detection to harden, as a dict of `forge_detection`
    kwargs: {name, pattern, severity, tp, fp, field}. The finding's excerpt is
    appended to `tp` (a true-positive the rule MUST now catch) — or to `fp` when
    `as_fp=True` (a false-positive it must NOT catch). The rule is then re-forged via
    `detection.forge_detection`, so it promotes ONLY if it still passes its (now
    larger) fixture set. A rule too narrow for the evasion fails this re-gate and
    stays quarantined — exactly the signal that the rule needs widening.

    Returns (DetectionReport, fixture_str). Composes DET1's public API only.
    """
    fixture = _finding_excerpt(k, finding)
    spec = dict(detection_spec)
    tp = list(spec.get("tp", []))
    fp = list(spec.get("fp", []))
    if as_fp:
        fp.append(fixture)
    else:
        tp.append(fixture)

    report = detection.forge_detection(
        k,
        spec["name"],
        spec["pattern"],
        spec["severity"],
        tp,
        fp,
        field=spec.get("field", "*"),
    )

    # Provenance: record the closed loop on the Weft — this red finding hardened this
    # detection (passed=promoted or not), signed by the SOC author. Untrusted excerpt
    # is stored as DATA. `link` edges give full red→blue traceability.
    fcell = finding if hasattr(finding, "content") else k.weave().get(finding)
    author = k.keyring.mint("purple-eng", "analyst").id
    loop_id = content_id({"purple_loop": fcell.id, "detection": report.det_id,
                          "as_fp": as_fp})
    model.assert_content(k.weft, author, loop_id, PURPLE_LOOP, {
        "finding": fcell.id,
        "detection": report.det_id,
        "detection_name": report.name,
        "fixture": fixture,                 # the red-team signal, as DATA
        "fixture_kind": "fp" if as_fp else "tp",
        "regated_promoted": bool(report.promoted),
    })
    model.assert_edge(k.weft, author, loop_id, "hardened_from", fcell.id)
    model.assert_edge(k.weft, author, loop_id, "regated", report.det_id)
    return report, fixture, loop_id


def loops(weave) -> list:
    """Read-side projection: every recorded purple-loop Cell on the Weft."""
    return weave.of_type(PURPLE_LOOP)


def hardened_from(weave, loop_id) -> list:
    """The finding(s) a given purple-loop hardened a detection from (provenance)."""
    return [e["dst"] for e in weave.edges_from(loop_id, "hardened_from")]


def run_loop(k, *, engagement="evasion-probe", scope=("*.lab.internal",),
             technique="evasion", targets=("db01.lab.internal", "web01.lab.internal"),
             narrow_pattern=r"^port-scan@",
             wide_pattern=r"^[\w-]+@\S+: stub-observed exposure",
             severity="high"):
    """Tie the whole purple loop together, end to end, composing RED1 → DET1 → TRIAGE1.

    Steps:
      1. RED1: stand up an authorized engagement, approve it (Morta), and probe the
         in-scope `targets` — each emits a `finding` Cell (the red-team evasions).
      2. DET1 (`harden_from_finding`): feed the FIRST finding's excerpt to a NARROW
         detection as a new TP. The narrow rule (it only knows the OLD technique
         signature) cannot catch the new evasion → it FAILS its re-gate and stays
         quarantined (the loop's teeth).
      3. DET1 again: a WIDER rule with the same new fixture PASSES — the red finding
         became a blue test the improved rule must (and does) satisfy.
      4. TRIAGE1: correlate the red findings into ONE incident (grouped by the
         engagement rule), with a Morta-gated response.

    Returns a dict of everything proved, all of it Weft-resident with provenance.
    """
    # 1. RED1 — authorized, scoped, Morta-gated, sandboxed offensive probes.
    red_agent, cap_id = red.authorize_engagement(
        k, engagement, list(scope), technique=technique, severity=severity)
    k.approve(cap_id)                              # Morta go/no-go
    findings = []
    for t in targets:
        r = red.probe(k, red_agent, cap_id, t)
        assert "finding" in r, ("red probe did not yield a finding", r)
        findings.append(r["finding"])
    assert findings, "no red-team findings produced"

    # The red-team-observed evasion we will teach the blue team to catch.
    evasion = _finding_excerpt(k, findings[0])
    base_spec = {"name": "redteam-evasion-rule", "severity": severity,
                 "tp": [], "fp": [], "field": "excerpt"}

    # 2. The NARROW rule fails its re-gate once the evasion is a required TP.
    narrow_spec = dict(base_spec, name="redteam-evasion-narrow", pattern=narrow_pattern)
    narrow_rep, fixture, narrow_loop = harden_from_finding(k, findings[0], narrow_spec)

    # 3. The WIDENED rule, same fixture, passes its re-gate.
    wide_spec = dict(base_spec, name="redteam-evasion-wide", pattern=wide_pattern)
    wide_rep, _, wide_loop = harden_from_finding(k, findings[0], wide_spec)

    # 4. TRIAGE1 — correlate the red findings into an incident (grouped by engagement).
    incidents = triage.correlate(k)
    rule_incident = None
    for iid in incidents:
        if cap_id == k.weave().get(iid).content.get("key"):
            rule_incident = iid
            break

    return {
        "cap_id": cap_id,
        "findings": findings,
        "evasion": evasion,
        "fixture": fixture,
        "narrow": narrow_rep,
        "narrow_loop": narrow_loop,
        "wide": wide_rep,
        "wide_loop": wide_loop,
        "incident": rule_incident,
        "incidents": incidents,
    }
