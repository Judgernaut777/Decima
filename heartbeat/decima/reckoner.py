"""Nona — the Reckoner. The compounding engine ("becomes more advanced the
longer it runs"), made mechanical.

A forged capability is born QUARANTINED. Promotion is gated on EVIDENCE, not a
single check:
  1. a deterministic verifier runs it in the sandbox, and
  2. a static scanner inspects the candidate for risky patterns.
Promotion happens only if the test passes AND the scan is clean. A trusted
principal then ATTESTs the promotion (lifting the quarantine) and grants it;
rollback is a RETRACT. The set of things Decima can do is a fold over the Weft —
and it only ever grows, behind the gate.

`scan` is a quarantined, network-denied stub of the NVIDIA SkillSpector contract
(see specs/NONA_RECKONER.md): it produces *evidence*, never authority. A clean
scan does not prove safety, and findings are combined with the test and Morta
policy — in production also with sandbox execution traces and dependency checks.
"""
import json

from decima.weft import ASSERT, ATTEST
from decima.capability import capability_content
from decima.hashing import content_id
from decima import executor

# Pattern families (a tiny stand-in for SkillSpector's analyzer set).
_RISKY_EFFECTS = {"shell", "network", "exec", "eval"}
_AUDITED_TRANSFORMS = {"upper", "lower", "reverse", "wc"}
_DANGER_TOKENS = ["rm -rf", "curl", "wget", "| sh", "|sh", "eval(", "exec(",
                  "/etc/passwd", "exfil", "nc -", "base64 -d", "ssh "]


def scan(content: dict) -> list[dict]:
    """Static analysis of a candidate capability. Returns findings as evidence."""
    findings = []
    effect = content.get("effect")
    impl = content.get("impl") or {}
    blob = json.dumps({"name": content.get("name"), "effect": effect, "impl": impl}).lower()

    if effect in _RISKY_EFFECTS:
        findings.append({"sev": "high", "rule": "risky-effect",
                         "detail": f"effect {effect!r} requests a privileged capability"})
    if effect == "transform" and impl.get("fn") not in _AUDITED_TRANSFORMS:
        findings.append({"sev": "high", "rule": "unverified-impl",
                         "detail": f"transform fn {impl.get('fn')!r} is outside the audited set"})
    for tok in _DANGER_TOKENS:
        if tok in blob:
            findings.append({"sev": "high", "rule": "dangerous-pattern",
                             "detail": f"matched {tok!r} in the candidate"})
    return findings


class ForgeReport:
    def __init__(self, cap_id, name, promoted, detail, findings=None):
        self.cap_id = cap_id
        self.name = name
        self.promoted = promoted
        self.detail = detail
        self.findings = findings or []

    def __str__(self):
        status = "PROMOTED ✓" if self.promoted else "REJECTED ✗"
        return f"[Nona] forge {self.name!r} {self.cap_id[:8]} → {status} — {self.detail}"


def forge(kernel, name, effect, fn, test_input, expect, command=None) -> ForgeReport:
    root = kernel.root.id
    reckoner = kernel.reckoner.id

    # 1. ASSERT the new capability — born quarantined, sandbox_only. `command` is
    #    an inert payload field used to demonstrate the scanner catching a hidden
    #    payload even when the behavior under test looks benign.
    impl = {"fn": fn}
    if command:
        impl["command"] = command
    content = capability_content(
        name=name, effect=effect, impl=impl,
        caveats={"sandbox_only": True}, quarantined=True,
    )
    cap_id = content_id({"forged": name, "effect": effect, "impl": impl})
    kernel.weft.append(root, ASSERT,
                       {"cell": cap_id, "type": "capability", "content": content})

    # 2. Evidence A — deterministic verifier (sandboxed execution).
    try:
        got = executor.execute(effect, impl, {"text": test_input}).get("out")
        test_pass = (got == expect)
        test_detail = f"ran {test_input!r} → {got!r}, expected {expect!r}"
    except Exception as e:  # noqa: BLE001 - a failed forge is data, not a crash
        test_pass, test_detail = False, f"sandbox error: {e}"

    # 3. Evidence B — static scan (quarantined, network-denied; SkillSpector stub).
    findings = scan(content)
    high = [f for f in findings if f["sev"] == "high"]

    # 4. Gate: promote only if BOTH the test passes and the scan is clean.
    promote = test_pass and not high
    scan_note = "clean" if not high else "; ".join(f["rule"] for f in high)
    detail = f"test:{'pass' if test_pass else 'fail'} ({test_detail})  ·  scan:{scan_note}"

    # 5. ATTEST the outcome. Promotion lifts quarantine; otherwise it just records.
    kernel.weft.append(reckoner, ATTEST,
                       {"target_cell": cap_id, "claim": detail, "promote": promote})
    if promote:
        kernel.grant(cap_id, kernel.decima_agent_id)

    return ForgeReport(cap_id, name, promote, detail, findings)
