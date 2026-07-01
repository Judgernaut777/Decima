"""FORGE-IF-MISSING — Nona grows an organ only when no existing tool fits.

The user's rule: "find a tool that fits what you want to do, and build it if it doesn't
exist." Discovery (plug-in-or-forge) does the finding; this lane does the BUILDING. When
the registry (and any research seam) miss for an intent, Nona (the FORGE Fate) synthesizes
a `capability_manifest`, registers it (now discoverable), and wires a REAL, INVOCABLE
handler via `kernel.integrate_tool` — a stub that is HONEST about being a placeholder. It
proves the full round-trip:

  0. a catalog of the ~29 real bundled engines is registered (register_builtins + the
     engines' own register_manifest), so discovery has something real to find first;
  1. an intent that MATCHES a real engine returns that REAL capability — NOT a forged one
     (forge is the last resort, never the first move);
  2. an intent for a NONEXISTENT tool FORGES a stub → it is registered, now discoverable,
     and INVOCABLE, and its receipt clearly marks it a stub (honest placeholder);
  3. a re-request for the SAME intent FINDS the now-forged tool instead of forging again
     (the second time is a use, not a forge);
  4. object-capability is respected — the forged capability is authorize()'d like any
     other: it grants nothing extra, and Morta can revoke it (the next INVOKE fails closed).

Contract: run(k, line). Fail loud via assert. Owns a fresh, offline Kernel.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import discovery as D
from decima import forge as F
from decima import builtin_manifests as B
from decima import banking, crm_engine, ride, ticketing


def run(k, line):
    line("\n== FORGE-IF-MISSING (Nona grows an organ only when no tool fits) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    def agent():
        # A FRESH decima agent cell before each invoke (spend/lease state advances).
        return kk.weave().get(kk.decima_agent_id)

    THRESHOLD = 200          # an int score bar; real matches clear it, nonexistent goals don't.

    # 0. Register the catalog of REAL engines so discovery can find one before forging. ─
    B.register_builtins(kk)                       # the ~25 bundled engines, descriptive
    for eng in (banking, crm_engine, ride, ticketing):
        eng.register_manifest(kk)                 # engines that self-register their manifest
    catalog = {c.content["name"] for c in M.registry(kk)}
    assert len(catalog) >= 29, f"expected a catalog of >= 29 real engines, got {len(catalog)}"
    assert {"stripe_rail", "comms", "banking", "crm"} <= catalog, sorted(catalog)
    line(f"  registered a catalog of {len(catalog)} REAL engines (discovery searches these first) ✓")

    # 1. A MATCHED intent returns a REAL engine — NOT a forged one. ────────────────────
    matched = D.discover(kk, "charge a customer's credit card", threshold=THRESHOLD,
                         forge=F.forge)
    assert matched["action"] == "use", f"a matched intent must USE a real engine, got {matched}"
    assert matched["name"] == "stripe_rail", matched
    assert isinstance(matched["score"], int) and matched["score"] >= THRESHOLD, matched
    assert kk.weave().get(matched["manifest"]).content["source"] == "builtin", matched
    line(f"  matched intent → USE real engine '{matched['name']}' (score={matched['score']}, "
         f"source=builtin) — forge NOT invoked ✓")

    # 2. A NONEXISTENT tool is FORGED → registered, discoverable, INVOCABLE, honest stub. ─
    goal = "xylophone tuning submarine periscope calibration"
    before = len(M.registry(kk))
    assert M.get(kk, F.slug(goal)) is None, "the forged tool must not exist yet"
    forged = D.discover(kk, goal, threshold=THRESHOLD, forge=F.forge)
    assert forged["action"] == "forged", f"a nonexistent tool must be FORGED, got {forged}"
    name = forged["name"]
    assert name == F.slug(goal) and forged["stub"] is True, forged
    assert forged["archetype"] == "COMPUTE", forged      # no effect-verb in the goal → COMPUTE

    # It is now REGISTERED + DISCOVERABLE (the registry grew by exactly one).
    assert len(M.registry(kk)) == before + 1, "forging must register exactly one new manifest"
    mcell = M.get(kk, name)
    assert mcell is not None and mcell.content["source"] == "forged", mcell
    assert mcell.content["effect_class"] == F.STUB_EFFECT_CLASS, mcell.content
    assert mcell.content["caveats"].get("stub") is True, mcell.content

    # It is INVOCABLE — a real, live capability wired via kernel.integrate_tool.
    ok = kk.invoke(agent(), forged["cap"], {"cost": 0})
    assert ok["status"] == "SUCCEEDED", f"the forged capability must be invocable, got {ok}"
    receipt = kk.weave().get(ok["result_cell"]).content
    # The receipt is HONEST that this is a stub placeholder (never a fabricated outcome).
    assert receipt.get("stub") is True and receipt.get("forged") is True, receipt
    assert receipt.get("effect_class") == F.STUB_EFFECT_CLASS, receipt
    assert "stub" in receipt.get("note", "").lower(), receipt
    line(f"  nonexistent tool → FORGED '{name}': registered + discoverable + INVOCABLE; "
         f"its receipt is honestly marked a stub ✓")

    # 3. A RE-REQUEST for the same intent FINDS the forged tool — no second forge. ──────
    again = D.discover(kk, goal, threshold=THRESHOLD, forge=F.forge)
    assert again["action"] == "use", f"a re-request must USE the forged tool, got {again}"
    assert again["name"] == name and again["score"] >= THRESHOLD, again
    assert len(M.registry(kk)) == before + 1, "a re-request must NOT forge a second tool"
    line(f"  re-request same intent → USE '{again['name']}' (score={again['score']}) — "
         f"found, not re-forged ✓")

    # 4. OBJECT-CAPABILITY respected — the forged cap is authorize()'d like any other. ──
    # It runs only because Decima was granted it (integrate_tool); Morta can revoke it.
    envelope = agent().content.get("envelope", [])
    assert forged["cap"] in envelope, "the forged capability must sit in Decima's envelope (granted)"
    kk.revoke(forged["cap"])                              # Morta gates/revokes it like any other
    denied = kk.invoke(agent(), forged["cap"], {"cost": 0})
    assert denied.get("denied"), f"a revoked forged cap must fail closed, got {denied}"
    line("  ocap respected: the forged cap is granted + authorize()'d, and Morta's revoke "
         "makes the next INVOKE fail closed ✓")

    line("  → find a tool that fits; build it if it doesn't exist. Nona forges a real, "
         "gated, honest stub as a LAST resort — discoverable, invocable, and revocable "
         "like every other capability.")
