"""FORGE-REAL DEFAULT — PRODUCTION discover() reaches the REAL pipeline, unwired.

Cycle 60 (check 464) proved `forge.forge` routes a discovery-triggered forge through
the REAL candidate → reckoner → promotion pipeline — but only for callers that WIRE
it (`discover(..., forge=forge_with(...))`). The two PRODUCTION discover() call sites
(kernel.say's live discovery hook at kernel.py, agent.suggest_capabilities at
agent.py) pass NO forge=, so production self-extension still stopped at the bare
{"action": "forge"} toy signal: a proven library with no production caller — the
recurring Batch-S failure. The fix lands AT THE SEAM (discovery.py only, non-core,
both sites inherit it): `discover()` now DEFAULTS its forge seam to
`discovery.default_forge`, an adapter over the real `forge.forge` pipeline, with an
injectable default-codegen binding (`bind_default_codegen`) so an offline harness can
drive the REAL default path deterministically.

This check proves, offline + deterministically (fresh Kernel, a deterministic codegen
bound through the DEFAULT seam — no network, no clock, no key):

  (a) PRODUCTION DISCOVER REACHES THE REAL PIPELINE (load-bearing): `discover()`
      called EXACTLY the production way — goal + int threshold, NO forge= — for a
      goal that misses the whole catalog returns a FORGED capability that is REAL:
      stub=False, promoted=True, its candidate Cell BORN QUARANTINED
      (DRAFT→QUARANTINED, §3 baseline), a real EvaluationResult Cell as evidence, the
      cap ATTESTED by the trusted tier signer with quarantine LIFTED — and an
      ocap-gated invoke RUNS its real generated behavior (no fabricated stub
      receipt). The promoted organ is registered + discoverable: a re-request (still
      production-shaped) USEs it instead of forging twice.
  (b) TEST OVERRIDE PRESERVED: an explicit forge= still overrides the default — the
      injected seam is called with (k, goal) and its descriptor returned verbatim;
      the default pipeline does NOT also run.
  (c) FAIL CLOSED: a candidate whose generated source fails evaluation is REFUSED
      through the DEFAULT path — PromotionBlocked propagates, nothing is registered,
      nothing is invocable, no silent stub appears under the refused name.
  (d) NO REGRESSION: with NO codegen bound (the offline production default,
      `candidate.model_codegen` failing closed), the default falls back to the
      legacy bare {"action":"forge"} signal — byte-identical across calls and
      writing NOTHING to the Weft — and a matched intent still USEs a real engine
      (forge stays the last resort).

Mutation-resistance (the load-bearing line): in `discovery.discover`, revert the
default — replace `forge = default_forge` with the old bare-signal return (or route
the default at a stub) — and (a) goes RED: production-shaped discover comes back
{"action":"forge"} (or stub=True), no candidate/evaluation cells land, and nothing
promoted ever runs.

Contract: run(k, line). Fail loud (assert). Owns a fresh, offline Kernel.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import discovery as D
from decima import forge as F
from decima import candidate as C
from decima import promotion as P
from decima import builtin_manifests as B

# A deterministic BAD codegen: parses fine, declares an entrypoint, but does NOT
# normalize — it fails the deterministic + property stages, so the gate must refuse.
BAD_SOURCE = (
    "def normalize(text):\n"
    "    return str(text)\n"
)


def run(k, line):
    line("\n== FORGE-REAL DEFAULT (production discover() reaches candidate→reckoner→promotion) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    B.register_builtins(kk)                       # a real catalog: forge stays the LAST resort

    def agent():
        # A FRESH decima agent cell before each invoke (spend/lease state advances).
        return kk.weave().get(kk.decima_agent_id)

    # ── (a) PRODUCTION DISCOVER (no forge=) runs the REAL pipeline. ─────────────────────
    prev = D.bind_default_codegen(C.fake_normalizer_codegen)   # deterministic, offline
    try:
        goal = "chromatic sigil entropy weaver"    # matches nothing in the catalog
        name = F.slug(goal)
        assert M.get(kk, name) is None, "the forged tool must not exist yet"
        before = len(M.registry(kk))
        # EXACTLY the production call shape (kernel.say / agent.suggest_capabilities):
        # goal + int threshold — NO forge=, NO embedder, NO research.
        forged = D.discover(kk, goal, threshold=kk.DISCOVERY_THRESHOLD)
        assert forged["action"] == "forged", \
            f"production discover must reach the forge DEFAULT, got {forged}"
        assert forged["name"] == name, forged
        # THE POINT: the DEFAULT path is REAL — never stub=True / a decorative toy.
        assert forged.get("stub") is False and forged.get("promoted") is True, \
            f"the production default forged a STUB, not a promoted real capability: {forged}"
        assert forged.get("fallback") is None, \
            f"with a bound codegen the real pipeline must run (no fallback): {forged}"

        # BORN QUARANTINED — §3 baseline + DRAFT→QUARANTINED provenance on the Weft.
        ccell = kk.weave().get(forged["candidate"])
        assert ccell is not None and ccell.type == "candidate", \
            "the default forge must author a candidate Cell on the Weft"
        q = ccell.content["quarantine"]
        assert q["sandbox_only"] is True and q["no_outward_effects"] is True \
            and q["network_allow"] == [], f"§3 quarantine baseline missing: {q}"
        assert ccell.content["states"] == ["DRAFT", "QUARANTINED"], ccell.content["states"]

        # EVALUATED — a real EvaluationResult Cell (evidence), promote-eligible.
        ecell = kk.weave().get(forged["evaluation"])
        assert ecell is not None and ecell.type == "evaluation_result", \
            "the default forge must record an EvaluationResult Cell (evidence)"
        em = ecell.content["aggregate_metrics"]
        assert ecell.content["promote_eligible"] is True, ecell.content["verdict_reason"]
        assert em["deterministic_pass"] == em["deterministic_cases"] >= 1, em
        assert em["hostile_contained"] == em["hostile_cases"] >= 1, em
        assert em["high_findings"] == 0, em

        # ATTESTED + PROMOTED — quarantine lifted by the trusted signer through §7.
        cap = kk.weave().get(forged["cap"])
        assert cap.content.get("quarantined") is False, \
            "the forged capability must have its quarantine LIFTED by the attested gate"
        assert cap.content["lifecycle"] == "PROMOTED" and forged["to_state"] == "PROMOTED", forged
        assert any(a["by"] == kk.reckoner.id for a in cap.attestations), \
            "the promotion must be a recorded ATTEST by the trusted tier signer"

        # IT RUNS ITS REAL BEHAVIOR — an ocap-gated invoke executes the generated code
        # and the receipt carries the REAL output (never a fabricated stub receipt).
        res = kk.invoke(agent(), forged["cap"], {"text": "  Hello   WORLD  "})
        assert res.get("status") == "SUCCEEDED", f"the promoted organ must be invocable: {res}"
        assert res["ok"]["out"] == "hello world" and res["ok"]["ran"] is True, res["ok"]
        receipt = kk.weave().get(res["result_cell"]).content
        assert receipt.get("stub") is not True and receipt.get("out") == "hello world", receipt
        assert receipt.get("effect_class") != F.STUB_EFFECT_CLASS, receipt

        # REGISTERED + DISCOVERABLE — a production-shaped re-request USEs it (found,
        # not re-forged: the second time is a plug-in, not a forge).
        assert len(M.registry(kk)) == before + 1, "forging must register exactly one manifest"
        assert M.get(kk, name).content["source"] == "promoted", M.get(kk, name).content
        again = D.discover(kk, goal, threshold=kk.DISCOVERY_THRESHOLD)
        assert again["action"] == "use" and again["name"] == name, again
        assert len(M.registry(kk)) == before + 1, "a re-request must NOT forge a second organ"
        line(f"  production-shaped discover (NO forge=) → '{name}': born QUARANTINED, "
             f"evaluated (det {em['deterministic_pass']}/{em['deterministic_cases']} · "
             f"hostile contained · 0 high findings), ATTESTED+PROMOTED, runs real code "
             f"('hello world'); re-request USEs it ✓")

        # ── (c) FAIL CLOSED through the DEFAULT path — refused, never silently stubbed.
        D.bind_default_codegen(lambda _i: BAD_SOURCE)
        bad_goal = "palindrome nebula chant estimator"
        n_before = len(M.registry(kk))
        inv_before = len(kk.weave().invocations)
        try:
            D.discover(kk, bad_goal, threshold=kk.DISCOVERY_THRESHOLD)   # production shape
            raise AssertionError("a failing candidate was PROMOTED through the default seam")
        except P.PromotionBlocked:
            pass                                    # refused, fail closed — the only exit
        assert len(M.registry(kk)) == n_before, \
            "a refused default forge must register NOTHING (no silent stub fallback)"
        assert M.get(kk, F.slug(bad_goal)) is None, "no stub may appear under the refused name"
        assert len(kk.weave().invocations) == inv_before, "a refused forge must run no INVOKE"
        line("  failing candidate through the DEFAULT → PromotionBlocked: nothing "
             "registered, nothing invocable — fail closed, no stub fallback ✓")

        # ── (b) TEST OVERRIDE PRESERVED — an explicit forge= beats the default. ────────
        D.bind_default_codegen(C.fake_normalizer_codegen)   # a default that WOULD promote
        marker = {"action": "forged", "name": "override_marker", "via": "explicit-seam"}
        seen = []

        def _override(k_, g):
            seen.append((k_ is kk, g))
            return dict(marker)

        o_goal = "cerulean fjord haiku metronome"
        n_before = len(M.registry(kk))
        got = D.discover(kk, o_goal, threshold=kk.DISCOVERY_THRESHOLD, forge=_override)
        assert got == marker, f"an explicit forge= must override the default: {got}"
        assert seen == [(True, o_goal)], f"the injected seam must be called (k, goal): {seen}"
        assert M.get(kk, F.slug(o_goal)) is None and len(M.registry(kk)) == n_before, \
            "with an explicit forge= the DEFAULT pipeline must NOT also run"
        line("  explicit forge= still overrides: the injected seam ran (k, goal) and the "
             "default pipeline stayed out of it — the test seam is preserved ✓")
    finally:
        D.bind_default_codegen(prev)                # ALWAYS restore the production default

    # ── (d) NO REGRESSION — unbound (offline production): honest bare signal, no Weft
    #        writes, byte-identical; matched intents still USE a real engine. ───────────
    junk = "photosynthesis of chloroplasts in mesophyll"
    ev_before = sum(1 for _ in kk.weft.events())
    sig = D.discover(kk, junk, threshold=kk.DISCOVERY_THRESHOLD)
    assert sig == {"action": "forge", "goal": junk,
                   "reason": "no existing capability matches"}, \
        f"offline (no codegen reachable) the default must fall back to the bare signal: {sig}"
    assert D.discover(kk, junk, threshold=kk.DISCOVERY_THRESHOLD) == sig, \
        "the offline fallback must be deterministic (byte-identical across calls)"
    assert sum(1 for _ in kk.weft.events()) == ev_before, \
        "the offline honest fallback must write NOTHING to the Weft"
    used = D.discover(kk, "charge a customer's credit card", threshold=kk.DISCOVERY_THRESHOLD)
    assert used["action"] == "use" and used["name"] == "stripe_rail", \
        f"a matched intent must USE a real engine (forge is the LAST resort): {used}"
    line("  no regression: offline/unbound the default is the honest bare signal "
         "(deterministic, Weft-untouched); a matched intent still USEs stripe_rail ✓")

    line("  → the forge-real DEFAULT is live: production discover() — kernel.say and "
         "agent.suggest_capabilities, which pass no forge= — now reaches the REAL "
         "candidate→reckoner→promotion pipeline at the seam: promoted or refused, "
         "fail closed, never a decorative stub.")
