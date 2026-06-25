"""HOME1 — Morta-gated home/devices (IoT) rail: a device action is an OUTWARD,
possibly-irreversible effect, so it is approval-gated, sandboxed, audited as an
EffectReceipt, and its state is tracked on the Weft.

Runs on its OWN fresh Kernel (it registers a device effect and forges a capability),
so it stays out of the shared kernel's state. Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import home, executor, audit
from decima.kernel import Kernel


def run(_k, line):
    line("\n== HOME / DEVICES (OUTWARD effect · Morta · sandbox · state on the Weft) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    cap_id = home.install_rail(k)
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- register a device; its state lives on the Weft --------------------
    did = home.register_device(k, "front-door", "lock", state="locked")
    dev = home.device(k, "front-door")
    assert dev is not None and dev.type == "device" and dev.id == did
    assert dev.content["state"] == "locked" and dev.content["kind"] == "lock"
    assert home.is_sensitive("unlock"), "unlock must be a Morta-gated sensitive action"
    line(f"  registered device front-door (lock) — state={dev.content['state']} on the Weft")

    # ---- (1) Morta: a sensitive action (unlock) is DENIED until approved ----
    r0 = home.act(k, decima(), cap_id, dev, "unlock")
    assert "denied" in r0 and "approval" in r0["denied"].lower(), r0
    assert home.device(k, "front-door").content["state"] == "locked"   # nothing moved
    line(f"  pre-approval: unlock DENIED — {r0['denied']}")

    k.approve(cap_id)                                                   # human/Morta approves
    line("  (a human approves the device capability — Morta gate)")

    # ---- (2) approved → the action runs, device state UPDATED + audited ----
    r1 = home.act(k, decima(), cap_id, dev, "unlock")
    assert r1["status"] == executor.SUCCEEDED and not r1.get("denied"), r1
    assert r1["state"] == "unlocked", r1
    receipt = k.weave().get(r1["result_cell"])
    assert receipt.content["effect_class"] == home.EffectClass
    assert receipt.content["status"] == executor.SUCCEEDED
    after = home.device(k, "front-door")
    assert after.content["state"] == "unlocked", after.content     # state tracked on Weft
    line(f"  approved: unlock → receipt {r1['result_cell'][:8]} "
         f"(class={receipt.content['effect_class']}); device state locked→unlocked ✓")

    # the device cell's history is on the signed Weft (audited)
    trail = audit.audit_trail(k, did)
    assert trail["verifiable"] and trail["count"] >= 2, trail        # register + state update
    line(f"  audit: {trail['count']} verified events touch the device cell "
         f"(verifiable={trail['verifiable']}) ✓")

    # ---- (3) a non-sensitive action runs once approved; a bad action fails --
    light = home.device(k, home.register_device(k, "lamp", "light", state="off"))
    r2 = home.act(k, decima(), cap_id, light, "on")
    assert r2["status"] == executor.SUCCEEDED and r2["state"] == "on", r2
    assert home.device(k, "lamp").content["state"] == "on"
    line(f"  lamp (light): on → state={r2['state']} ✓")

    rbad = home.act(k, decima(), cap_id, light, "explode")            # invalid for a light
    assert "denied" in rbad and rbad["status"] == executor.FAILED, rbad
    assert home.device(k, "lamp").content["state"] == "on"           # unchanged — no-effect
    line(f"  invalid action 'explode' on a light → FAILED no-effect, state unchanged ✓")
