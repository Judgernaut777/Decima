"""LIBRARY TIER (Batch U) — the ~84 domain/security engine packs confer NO
authority merely by existing (specs/LIBRARY_TIER.md is the ruling; this is its
structural, empirical, deterministic proof).

Decima bundles roughly 84 domain/security engine packs under `decima/`
(stripe_rail, tax_engine, kyc, brokerage_engine, esign, comms, payouts,
accounting, shipping, calendar_engine, cloud_storage, exchange, maps_engine,
weather_engine, payroll, insurance_claim, cloud_compute, ecommerce, paging,
ocr_engine, translate_engine, background_check, dns, ads, banking, ride,
crm_engine, ticketing, sms, storage, plus the blue-team packs — detection,
vuln, triage, incident_response, quarantine, purple — and the red-team packs —
red, recon). Each is pure Python: importable, callable only through the ocap
gate, and — the property this check proves — NOT a live capability merely by
being on disk or imported. A pack graduates to a live capability only through
an explicit, Morta-gated operator action: `kernel.integrate_tool`,
`golive.activate_engine`, or a catalog `ApprovalInbox.approve()` (checks
495/496 already prove those seams end-to-end). Import is not on that list.

This check proves it two ways, both load-bearing:

  (1) BOOT SET: a FRESH Kernel (`fresh=True`, empty Weft) mints EXACTLY the
      five always-on kernel/cognitive capabilities in `Kernel._boot`
      (echo/shell/forge/browser.observe/browser.publish) — none of the ~84
      library-tier packs. The installed capability-NAME set at boot is
      compared for EQUALITY against that small set, and separately checked to
      have ZERO overlap with the library-tier pack names.
  (2) IMPORT IS INERT: importing a representative sample of packs — spanning
      FINANCIAL/LEGAL/IDENTITY/READ archetypes AND the blue/red security packs
      — must leave `kk.weft.count()` and the installed-capability-name set
      BYTE-IDENTICAL, before vs. after. This is the load-bearing proof: import
      confers no authority.
  (3) THE SHARPER CASE: a few packs (`red.py`, `recon.py`, …) DO register a
      handler in the process-wide `executor` dispatch table at module level —
      a real, narrow import-time side effect on PROCESS state, not Weft state.
      This check does not look away from that: it imports two such packs,
      confirms the registry DID gain their effect names, and then proves the
      registration alone buys NOTHING in BOTH refusal modes:
        (3a) a bare effect name with NO Weft cell refuses with "no such
             capability" — `capability.authorize`'s cap-is-None short-circuit;
        (3b) the SHARPER claim (specs/LIBRARY_TIER.md §2: "no Weft-backed,
             GRANTED capability") — a REAL, Weft-backed capability CELL is
             minted for the registered effect but left UNGRANTED; invoking it
             is STILL refused, this time PAST the cap-is-None branch, at the
             envelope/grantee ocap gate ("no grant in envelope"). A populated
             dispatch-table entry AND an existing cap cell together are still
             unreachable without a GRANT in the caller's envelope.

MUTATION → RED (the property this guards): if any library-tier pack were
changed to call `kernel._assert_cap`/`kernel.grant`/`kernel.integrate_tool` (or
append any ASSERT) at MODULE level — i.e., ambient self-installation on import
— then (1)'s boot-set equality or (2)'s before/after equality would break
immediately: the capability-name set would gain an entry that wasn't there
before, or the Weft count would move on a bare `import`. Either assert goes
loud and red. (Verified by hand while drafting this check: temporarily adding
a module-level `kk_dummy_assert()`-style Weft append to an imported pack's
top level flips both assertions in (2) red; reverted before landing — no pack
is left mutated by this file.)

Contract: run(k, line). Fail loud (assert). Owns a FRESH, offline Kernel over
its own tmp db — never touches the shared `k` the other checks built, so this
check's before/after comparisons cannot be polluted by anything another
section installed on `k`. No wall-clock; the Weft's own lamport-ordered count
and content-addressed capability names are the only measurements taken.
"""
import importlib
import os
import tempfile

from decima import executor
from decima.kernel import Kernel

# The ENTIRE always-on kernel/cognitive capability set a fresh boot mints
# (Kernel._boot, kernel.py) — nothing else exists until an operator acts.
BOOT_CAPS = frozenset({"echo", "shell", "forge", "browser.observe", "browser.publish"})

# A representative sample of the ~84 library-tier packs (specs/LIBRARY_TIER.md
# §4): financial / legal / identity / read archetypes plus blue+red security.
SAMPLE_MODULES = [
    "decima.stripe_rail",        # FINANCIAL, Morta-gated
    "decima.tax_engine",         # COMPUTE / READ
    "decima.kyc",                # COMPUTE / IDENTITY
    "decima.weather_engine",     # COMPUTE / READ
    "decima.ocr_engine",         # COMPUTE / READ
    "decima.background_check",   # COMPUTE / COMPLIANCE
    "decima.dns",                # INFRA, Morta-gated
    "decima.ads",                # FINANCIAL, Morta-gated
    "decima.red",                # blue/red: offensive-engagement pack
    "decima.recon",              # blue/red: recon/enumeration pack
    "decima.detection",          # blue/red: detection-as-code pack
    "decima.vuln",                # blue/red: vulnerability-tracking pack
]

# Library-tier pack/engine NAMES that must never surface as an installed
# capability's `name` merely from being imported (BUILTINS ∪ the wider
# catalog; specs/LIBRARY_TIER.md §4 has the full enumeration by category).
LIBRARY_TIER_NAMES = frozenset({
    "stripe_rail", "payouts", "brokerage_engine", "exchange", "payroll",
    "shipping", "cloud_compute", "ecommerce", "ads", "comms", "paging", "sms",
    "esign", "insurance_claim", "dns", "oidc", "calendar_engine",
    "tax_engine", "kyc", "background_check", "accounting", "maps_engine",
    "weather_engine", "cloud_storage", "ocr_engine", "translate_engine",
    "banking", "ride", "crm_engine", "ticketing", "storage",
})


def run(k, line):
    line("\n== LIBRARY TIER (Batch U) — packs are inert until an operator flips them ==")
    # Owns a FRESH kernel over its own tmp db — the shared `k` is left alone.
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # ── (1) BOOT SET — the fresh-boot capability set is the SMALL kernel/
    # cognitive set, never the ~84 library-tier packs. ─────────────────────────
    boot_names = frozenset(
        c.content.get("name") for c in kk.weave().of_type("capability"))
    assert boot_names == BOOT_CAPS, (
        f"fresh boot installed {sorted(boot_names)}, expected EXACTLY the "
        f"always-on kernel/cognitive set {sorted(BOOT_CAPS)} — a library-tier "
        f"pack got installed at boot with no operator action (ambient authority)"
    )
    assert not (boot_names & LIBRARY_TIER_NAMES), \
        f"boot installed library-tier capabilities: {sorted(boot_names & LIBRARY_TIER_NAMES)}"
    line(f"  fresh boot installs EXACTLY {sorted(BOOT_CAPS)} — none of the "
         f"~84 library-tier engine packs are live at boot ✓")

    # ── (2) IMPORT IS INERT (load-bearing) — the Weft and the installed-
    # capability-name set are BYTE-IDENTICAL before vs. after importing a
    # representative sample spanning every archetype + blue/red security. ─────
    n0 = kk.weft.count()
    caps0 = frozenset(c.content.get("name") for c in kk.weave().of_type("capability"))
    for mod in SAMPLE_MODULES:
        importlib.import_module(mod)
    n1 = kk.weft.count()
    caps1 = frozenset(c.content.get("name") for c in kk.weave().of_type("capability"))
    assert n1 == n0, (
        f"importing {SAMPLE_MODULES} appended {n1 - n0} event(s) to the Weft "
        f"(count {n0} -> {n1}) — a library-tier pack must be INERT on import "
        f"(MUTATION -> RED: a module-level Weft ASSERT/self-registration in "
        f"any of these packs would move this count)"
    )
    assert caps1 == caps0, (
        f"importing {SAMPLE_MODULES} changed the installed capability set "
        f"({sorted(caps0)} -> {sorted(caps1)}) — a pack must NOT self-install; "
        f"only integrate_tool/activate_engine/a catalog approval may install a "
        f"capability, and none of those ran here"
    )
    assert not (caps1 & LIBRARY_TIER_NAMES), \
        f"a library-tier pack self-installed as a capability: {sorted(caps1 & LIBRARY_TIER_NAMES)}"
    line(f"  imported {len(SAMPLE_MODULES)} packs (financial/legal/identity/"
         f"read archetypes + blue+red security) — Weft count UNCHANGED "
         f"({n0}), installed capability set UNCHANGED ({sorted(caps0)}) ✓")

    # ── (3) THE SHARPER CASE — a couple of these packs DO register a handler
    # in the process-wide `executor` dispatch table at import (decima.red ->
    # "redteam", decima.recon -> "recon"). That is real, but it is PROCESS
    # state, not Weft state — and it still confers NO authority. Proven in the
    # two distinct refusal modes specs/LIBRARY_TIER.md §2 rests on:
    #   (3a) NO Weft cell at all — a bare effect NAME is refused outright, the
    #        `capability.authorize` cap-is-None short-circuit;
    #   (3b) the SHARPER claim — a real, WEFT-BACKED capability cell for the
    #        registered effect that is simply UNGRANTED is STILL refused, this
    #        time PAST the cap-is-None short-circuit, at the envelope/grantee
    #        ocap check. A registered handler + an existing cap CELL is not
    #        authority; only a GRANTED capability in the caller's envelope is.
    registered = set(executor.registered())
    assert {"redteam", "recon"} <= registered, (
        f"expected decima.red / decima.recon to have registered their stub "
        f"effect handlers ('redteam'/'recon') in the process-wide executor "
        f"table at import (a known, narrow, benign side effect) — got "
        f"{sorted(registered)}; if the pack's effect-name constant changed, "
        f"update this check, don't loosen it"
    )

    # (3a) NO Weft cell — a bare effect NAME is refused as "no such capability".
    agent = kk.weave().get(kk.decima_agent_id)
    denied = kk.invoke(agent, "redteam", {"target": "example.com"})
    assert denied == {"denied": "no such capability"}, (
        f"a bare effect NAME with a registered executor handler but NO Weft "
        f"capability must be refused as 'no such capability' — proving a "
        f"registered handler is NOT authority: got {denied}"
    )
    assert kk.weft.count() == n1, "the refused bare-name invoke must write nothing to the Weft"

    # (3b) THE SHARPER CLAIM (specs/LIBRARY_TIER.md §2: "no Weft-backed, GRANTED
    # capability"). Mint a REAL Weft capability CELL for the same registered
    # "redteam" effect — but DO NOT grant it into any envelope. The cell now
    # genuinely exists (so a refusal can no longer be the cap-is-None
    # short-circuit), yet invoking it is STILL refused, at the envelope/grantee
    # ocap check — a Weft-backed but UNGRANTED capability is not authority.
    probe_id = kk._assert_cap("lib_tier_probe.redteam", "redteam",
                              caveats={"engagement": "lib-tier-probe"})
    assert kk.weave().get(probe_id) is not None, (
        "the probe capability CELL must genuinely exist on the Weft — a later "
        "refusal must be the envelope/grantee gate, not the cap-is-None branch")
    n_probe = kk.weft.count()                       # count AFTER the cap assertion
    agent = kk.weave().get(kk.decima_agent_id)
    assert probe_id not in agent.content.get("envelope", []), (
        "the probe capability must be UNGRANTED — absent from the orchestrator's "
        "envelope — for this to test the ungranted-authority refusal")
    denied_backed = kk.invoke(agent, probe_id, {"target": "example.com"})
    assert denied_backed == {"denied": "no grant in envelope (no ambient authority)"}, (
        f"a WEFT-BACKED but UNGRANTED capability for a registered effect must be "
        f"refused at the envelope/grantee ocap check (NOT the cap-is-None "
        f"short-circuit, since the cell exists) — proving a registered handler "
        f"AND an existing cap cell together are still not authority without a "
        f"GRANT: got {denied_backed}. (Granting probe_id into the orchestrator's "
        f"envelope would flip this refusal — the load-bearing line.)"
    )
    assert kk.weft.count() == n_probe, "the refused ungranted invoke must write nothing to the Weft"
    line("  the sharper case: decima.red / decima.recon DO register a stub "
         "handler in the process-wide executor table at import (in-process, "
         "off the Weft). (3a) kernel.invoke still refuses the bare 'redteam' "
         "effect NAME with 'no such capability'; (3b) even a REAL, Weft-backed "
         "capability cell for that effect, left UNGRANTED, is refused at the "
         "envelope/grantee gate ('no grant in envelope') — reaching PAST the "
         "cap-is-None short-circuit. A registered handler with no WEFT-BACKED, "
         "GRANTED capability is unreachable. Import != authority, even here ✓")

    line("  -> LIBRARY-TIER RULING HOLDS: a fresh boot's installed capability "
         "set contains none of the ~84 library-tier packs; importing any of "
         "them (financial/legal/identity/read rails and blue/red security "
         "packs alike) leaves the Weft and the capability set byte-identical; "
         "a pack becomes a live capability ONLY through an explicit, Morta-"
         "gated operator action (integrate_tool / activate_engine / a catalog "
         "approval) — never at import (specs/LIBRARY_TIER.md).")
