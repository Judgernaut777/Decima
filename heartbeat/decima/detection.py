"""DET1 — detection-as-code: Nona forges test-gated detections.

The cybersecurity flagship's cheapest win, built on Decima's own primitives. A
detection is a FORGED SKILL (`reckoner.py` pattern): a pattern/IOC matcher born
QUARANTINED, promoted only if it passes its own unit test — the **TP fixtures it MUST
match** and the **FP fixtures it must NOT**. Promotion is the same evidence-gated
`ATTEST` lift Nona uses for any capability, so a detection is a `capability`
(`effect="detect"`) and shows up as a forged skill in the Constellation (INS1).

A promoted detection applied to data Cells emits `finding` Cells with provenance on the
signed Weft — which doubles as a tamper-evident SIEM. Matched content is only ever READ
(untrusted data, never obeyed). The **purple-team loop**: a red-team evasion becomes a new
fixture (a TP the rule must now catch, or an FP it must not) that RE-GATES the rule — an
over-narrow or over-broad rule fails the gate and stays quarantined until improved.

Applying a detection here is via `detect()`; wiring it as an INVOKE-able effect is a later
step. Public API only (weft / capability / model / weave) — no kernel edits.
"""
import json
import re

from decima.weft import ASSERT, ATTEST
from decima.capability import capability_content
from decima.hashing import content_id
from decima import model


class DetectionReport:
    def __init__(self, det_id, name, promoted, detail, gate):
        self.det_id, self.name, self.promoted = det_id, name, promoted
        self.detail, self.gate = detail, gate

    def __str__(self):
        s = "PROMOTED ✓" if self.promoted else "REJECTED ✗"
        return f"[Nona] detection {self.name!r} {self.det_id[:8]} → {s} — {self.detail}"


def _text(content: dict, field: str) -> str:
    """The string a detection reads from a Cell: one field, or the whole content
    canonicalized when field == '*'. (A fixture string is already this extracted text.)"""
    if field == "*":
        return json.dumps(content, sort_keys=True)
    v = content.get(field, "")
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True)


def _match(pattern: str, text: str):
    """Return (matched, excerpt). A regex search over already-extracted text. Untrusted
    input is only READ — never executed, never treated as an instruction."""
    m = re.search(pattern, text)
    return (True, text[max(0, m.start() - 12): m.end() + 12]) if m else (False, "")


def forge_detection(k, name, pattern, severity, tp, fp, field="*") -> DetectionReport:
    """Forge a detection, test-gated by its fixtures. Born quarantined; promoted (the
    quarantine lifted via ATTEST) only if it matches EVERY true-positive and NO
    false-positive. An invalid regex fails closed."""
    root, reckoner = k.root.id, k.reckoner.id
    impl = {"kind": "detection", "pattern": pattern, "field": field, "severity": severity}
    det_id = content_id({"detection": name, "pattern": pattern})
    content = capability_content(name=name, effect="detect", impl=impl,
                                 caveats={"sandbox_only": True}, quarantined=True)
    k.weft.append(root, ASSERT, {"cell": det_id, "type": "capability", "content": content})

    try:
        tp_missed = [s for s in tp if not _match(pattern, s)[0]]
        fp_hit = [s for s in fp if _match(pattern, s)[0]]
        passed = not tp_missed and not fp_hit
        gate = {"tp_missed": tp_missed, "fp_hit": fp_hit}
        detail = (f"tp:{len(tp) - len(tp_missed)}/{len(tp)} "
                  f"fp_clean:{len(fp) - len(fp_hit)}/{len(fp)}"
                  + (f" · missed {tp_missed}" if tp_missed else "")
                  + (f" · false-positive on {fp_hit}" if fp_hit else ""))
    except re.error as e:  # noqa: BLE001 — a bad rule is data, not a crash
        passed, gate, detail = False, {"error": str(e)}, f"invalid regex: {e}"

    # Evidence-gated promotion, exactly like Nona: ATTEST records the gate result and,
    # when it passes, lifts the quarantine (weave handles promote for type 'capability').
    k.weft.append(reckoner, ATTEST,
                  {"target_cell": det_id, "claim": f"detection gate: {detail}", "promote": passed})
    if passed:
        k.grant(det_id, k.decima_agent_id)   # a promoted detection is a held skill
    return DetectionReport(det_id, name, passed, detail, gate)


def detect(k, det_id, over_type) -> list:
    """Apply a PROMOTED detection over every Cell of `over_type`; emit a `finding` Cell
    (with a `found_in` provenance edge) per match. A quarantined/unpromoted detection is
    refused (returns []), so an un-gated rule can never raise a finding."""
    w = k.weave()
    det = w.get(det_id)
    if det is None or det.content.get("quarantined"):
        return []
    impl = det.content["impl"]
    pattern, field, sev, name = impl["pattern"], impl["field"], impl["severity"], det.content["name"]
    findings = []
    for cell in w.of_type(over_type):
        matched, excerpt = _match(pattern, _text(cell.content, field))
        if matched:
            fid = content_id({"finding": det_id, "in": cell.id})
            model.assert_content(k.weft, k.reckoner.id, fid, "finding", {
                "detection": name, "rule": det_id, "severity": sev,
                "source": cell.id, "excerpt": excerpt})
            model.assert_edge(k.weft, k.reckoner.id, fid, "found_in", cell.id)
            findings.append(fid)
    return findings
