"""Nona — the Reckoner. The compounding engine ("becomes more advanced the
longer it runs"), made mechanical.

A forged capability is born QUARANTINED. The Reckoner runs it against a
deterministic verifier in a sandbox. Only on pass does a trusted principal
ATTEST a promotion (which lifts the quarantine) and grant it. Promotion is a
signature; rollback is a RETRACT. The set of things Decima can do is itself a
fold over the Weft — and it only ever grows.
"""
from decima.weft import ASSERT, ATTEST
from decima.weave import Weave
from decima.capability import capability_content
from decima.hashing import content_id
from decima import executor


class ForgeReport:
    def __init__(self, cap_id, name, passed, detail):
        self.cap_id = cap_id
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = "PROMOTED ✓" if self.passed else "REJECTED ✗"
        return f"[Nona] forge {self.name!r} {self.cap_id[:8]} → {status} — {self.detail}"


def forge(kernel, name, effect, fn, test_input, expect) -> ForgeReport:
    root = kernel.root.id
    reckoner = kernel.reckoner.id

    # 1. ASSERT the new capability — born quarantined, sandbox_only.
    impl = {"fn": fn}
    content = capability_content(
        name=name, effect=effect, impl=impl,
        caveats={"sandbox_only": True}, quarantined=True,
    )
    cap_id = content_id({"forged": name, "effect": effect, "impl": impl})
    kernel.weft.append(root, ASSERT,
                       {"cell": cap_id, "type": "capability", "content": content})

    # 2. Verify in the sandbox with a DETERMINISTIC check (the cheap-verifier pattern).
    try:
        result = executor.execute(effect, impl, {"text": test_input})
        got = result.get("out")
        passed = (got == expect)
        detail = f"ran {test_input!r} → {got!r}, expected {expect!r}"
    except Exception as e:  # noqa: BLE001 - a failed forge is data, not a crash
        passed, detail = False, f"sandbox error: {e}"

    # 3. ATTEST the outcome. Promotion lifts quarantine; failure just records it.
    kernel.weft.append(
        reckoner, ATTEST,
        {"target_cell": cap_id, "claim": detail, "promote": passed},
    )

    # 4. On pass, grant it to the orchestrator (granting = asserting an edge).
    if passed:
        kernel.grant(cap_id, kernel.decima_agent_id)

    return ForgeReport(cap_id, name, passed, detail)
