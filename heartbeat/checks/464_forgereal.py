"""FORGE-REAL DISCOVERY — forge-if-missing runs the REAL pipeline, not the honest stub.

P3's forge-real loop (intent → codegen → sandboxed test → scan → attested promotion →
versioning) exists as candidate.py / reckoner.py / promotion.py — but discovery's
forge-if-missing used to reach forge.py's honest STUB (stub=True handlers that do
nothing): two parallel forge entry points, the discovery-triggered one fake. `forge`
is now an ADAPTER over `candidate.author_candidate` → `reckoner.evaluate` →
`promotion.promote`, so a caller that reaches self-extension gets a TESTED, SCANNED,
ATTESTED capability — or a refusal (fail closed) — never a decorative stub.

This check proves, offline + deterministically (fresh Kernel, INJECTED deterministic
codegen, no network/clock/key):

  (a) DISCOVERY-FORGE YIELDS A REAL CAPABILITY — a missing capability forged through
      `discover(..., forge=forge_with(<codegen>))` runs candidate→reckoner→promotion:
      the candidate is BORN QUARANTINED (§3 baseline, DRAFT→QUARANTINED provenance),
      EVALUATED (deterministic + hostile-input + property cases, zero high findings,
      an EvaluationResult Cell on the Weft), and PROMOTED through the attested trust
      gate (quarantine lifted by the trusted signer's ATTEST). The result is NOT
      stub=True/forged-stub — and invoking it RUNS its real generated behavior;
      the promoted organ is registered + discoverable (a re-request USEs it);
  (b) A FAILING CANDIDATE IS REFUSED — a candidate whose generated source fails
      evaluation is NOT promoted: `PromotionBlocked` propagates (fail closed), the
      candidate STAYS quarantined on the Weft as evidence, no manifest is registered,
      and there is NO silent stub fallback;
  (c) NO REGRESSION — a matched intent still USEs a real engine (forge stays the last
      resort), and with NO codegen seam at all (offline default), forge degrades to
      the LEGACY HONEST STUB, loudly marked (stub=True, promoted=False,
      fallback="codegen-unavailable") — a truthful placeholder, never passed off as
      a promoted organ.

Mutation-resistance (the load-bearing line): revert `forge` to return the stub (skip
the real pipeline — e.g. make its body `return _forge_stub(...)`) and (a) goes RED:
the "forged" capability comes back stub=True / not promoted, its candidate/evaluation
cells are missing, and its receipt fabricates no real output.

Contract: run(k, line). Fail loud (assert). Owns a fresh, offline Kernel.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.hashing import content_id
from decima import manifest as M
from decima import discovery as D
from decima import forge as F
from decima import candidate as C
from decima import promotion as P
from decima import builtin_manifests as B

THRESHOLD = 200          # an int score bar; real matches clear it, nonexistent goals don't.

# A deterministic BAD codegen: parses fine, declares an entrypoint, but does NOT
# normalize (no collapse, no lowercase) — it fails the deterministic + property stages,
# so the evidence gate must refuse it.
BAD_SOURCE = (
    "def normalize(text):\n"
    "    return str(text)\n"
)


def run(k, line):
    line("\n== FORGE-REAL DISCOVERY (forge-if-missing runs candidate→reckoner→promotion) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    B.register_builtins(kk)                       # a real catalog: forge stays the LAST resort

    def agent():
        # A FRESH decima agent cell before each invoke (spend/lease state advances).
        return kk.weave().get(kk.decima_agent_id)

    # ── (a) DISCOVERY-FORGE YIELDS A REAL CAPABILITY. ──────────────────────────────────
    goal = "quantile lattice phoneme rebalancer"   # matches nothing in the catalog
    name = F.slug(goal)
    before = len(M.registry(kk))
    assert M.get(kk, name) is None, "the forged tool must not exist yet"
    forged = D.discover(kk, goal, threshold=THRESHOLD,
                        forge=F.forge_with(C.fake_normalizer_codegen))
    assert forged["action"] == "forged", f"a nonexistent tool must be FORGED, got {forged}"
    assert forged["name"] == name, forged
    # THE POINT: the forged capability is REAL — never stub=True / a forged-stub.
    assert forged.get("stub") is False and forged.get("promoted") is True, \
        f"discovery-forge returned a STUB, not a promoted real capability: {forged}"
    assert forged.get("fallback") is None, \
        f"with an injected codegen the real pipeline must run (no fallback): {forged}"

    # BORN QUARANTINED — the candidate Cell carries the §3 baseline, and the
    # DRAFT→QUARANTINED transition is provenance (two events), not an edited row.
    ccell = kk.weave().get(forged["candidate"])
    assert ccell is not None and ccell.type == "candidate", \
        "the discovery-forge must author a candidate Cell on the Weft"
    q = ccell.content["quarantine"]
    assert q["sandbox_only"] is True and q["no_outward_effects"] is True \
        and q["network_allow"] == [], f"§3 quarantine baseline missing: {q}"
    assert ccell.content["states"] == ["DRAFT", "QUARANTINED"], ccell.content["states"]

    # EVALUATED — a real EvaluationResult Cell: deterministic + hostile + property all
    # passed, the hostile input was CONTAINED in the sandbox, zero high findings.
    ecell = kk.weave().get(forged["evaluation"])
    assert ecell is not None and ecell.type == "evaluation_result", \
        "the discovery-forge must record an EvaluationResult Cell (evidence)"
    em = ecell.content["aggregate_metrics"]
    assert ecell.content["promote_eligible"] is True, ecell.content["verdict_reason"]
    assert em["deterministic_pass"] == em["deterministic_cases"] >= 1, em
    assert em["hostile_contained"] == em["hostile_cases"] >= 1, em
    assert em["property_pass"] == em["property_cases"] >= 1, em
    assert em["high_findings"] == 0, em

    # PROMOTED + ATTESTED — the trusted signer's promote-ATTEST lifted quarantine
    # through the §7 gate; the cap grants an EDGE to the immutable impl digest.
    cap = kk.weave().get(forged["cap"])
    assert cap.content.get("quarantined") is False, \
        "the forged capability must have its quarantine LIFTED by the attested gate"
    assert cap.content["lifecycle"] == "PROMOTED" and forged["to_state"] == "PROMOTED", forged
    assert cap.content["implementation_digest"] == forged["implementation_digest"], cap.content
    assert any(a["by"] == kk.reckoner.id for a in cap.attestations), \
        "the promotion must be a recorded ATTEST by the trusted tier signer"
    assert any(e["rel"] == "impl_of" and e["dst"] == forged["candidate"]
               for e in cap.edges_out), "no cap → candidate impl provenance edge"

    # IT ACTUALLY RUNS ITS REAL BEHAVIOR — a live, ocap-gated invoke executes the
    # generated code in the sandbox and the receipt carries the REAL output (no stub).
    res = kk.invoke(agent(), forged["cap"], {"text": "  Hello   WORLD  "})
    assert res.get("status") == "SUCCEEDED", f"the promoted organ must be invocable: {res}"
    assert res["ok"]["out"] == "hello world" and res["ok"]["ran"] is True, res["ok"]
    receipt = kk.weave().get(res["result_cell"]).content
    assert receipt.get("stub") is not True and receipt.get("forged") is not True, receipt
    assert receipt.get("out") == "hello world", receipt
    assert receipt.get("effect_class") != F.STUB_EFFECT_CLASS, receipt

    # REGISTERED + DISCOVERABLE — exactly one new manifest; a re-request FINDS it
    # (the second time is a plug-in, not a forge).
    assert len(M.registry(kk)) == before + 1, "forging must register exactly one new manifest"
    mcell = M.get(kk, name)
    assert mcell.content["source"] == "promoted", mcell.content
    assert mcell.content["caveats"].get("stub") is False, mcell.content
    again = D.discover(kk, goal, threshold=THRESHOLD,
                       forge=F.forge_with(C.fake_normalizer_codegen))
    assert again["action"] == "use" and again["name"] == name, again
    assert len(M.registry(kk)) == before + 1, "a re-request must NOT forge a second organ"
    line(f"  discovery-forge → '{name}': born QUARANTINED, evaluated "
         f"(det {em['deterministic_pass']}/{em['deterministic_cases']} · hostile contained · "
         f"property {em['property_pass']}/{em['property_cases']} · 0 high findings), "
         f"ATTESTED+PROMOTED, runs real code ('hello world'), re-request USEs it ✓")

    # ── (b) A FAILING CANDIDATE IS REFUSED — fail closed, no silent stub fallback. ─────
    bad_goal = "isotope glossolalia beacon holography"
    n_before = len(M.registry(kk))
    inv_before = len(kk.weave().invocations)
    try:
        D.discover(kk, bad_goal, threshold=THRESHOLD,
                   forge=F.forge_with(lambda _i: BAD_SOURCE))
        raise AssertionError("a failing candidate was PROMOTED — the evidence gate is open")
    except P.PromotionBlocked:
        pass                                       # refused, fail closed — the only exit
    assert len(M.registry(kk)) == n_before, \
        "a refused forge must register NOTHING (no manifest, no silent stub fallback)"
    assert M.get(kk, F.slug(bad_goal)) is None, "no stub may appear under the refused name"
    assert len(kk.weave().invocations) == inv_before, "a refused forge must run no INVOKE"
    # The refused candidate STAYS quarantined on the Weft — evidence, never authority.
    bad_cell = content_id({"candidate": F.slug(bad_goal),
                           "implementation_digest": C.implementation_digest(BAD_SOURCE),
                           "author": kk.reckoner.id}, kind="cell")
    bcell = kk.weave().get(bad_cell)
    assert bcell is not None and bcell.content["lifecycle"] == "QUARANTINED", \
        "the refused candidate must remain on the Weft, still QUARANTINED"
    assert bcell.content["quarantined"] is True, bcell.content
    line("  failing candidate → PromotionBlocked: nothing registered, nothing invocable, "
         "the candidate stays QUARANTINED on the Weft — fail closed, no stub fallback ✓")

    # ── (c) NO REGRESSION — matched intents still win; the offline stub stays honest. ──
    matched = D.discover(kk, "charge a customer's credit card", threshold=THRESHOLD,
                         forge=F.forge_with(C.fake_normalizer_codegen))
    assert matched["action"] == "use" and matched["name"] == "stripe_rail", \
        f"a matched intent must USE a real engine (forge is the LAST resort): {matched}"
    # With NO codegen seam at all (the offline default), forge degrades to the legacy
    # HONEST stub — loudly marked, never passed off as a promoted organ.
    stub_goal = "xylophone brine cartography"
    stub = D.discover(kk, stub_goal, threshold=THRESHOLD, forge=F.forge)
    assert stub["action"] == "forged" and stub["stub"] is True, stub
    assert stub.get("promoted") is False, "the stub must never claim to be promoted"
    assert stub.get("fallback") == "codegen-unavailable", stub
    srec = kk.invoke(agent(), stub["cap"], {"cost": 0})
    assert srec["status"] == "SUCCEEDED", srec
    sreceipt = kk.weave().get(srec["result_cell"]).content
    assert sreceipt.get("stub") is True and sreceipt.get("out") is None, \
        f"the stub receipt must stay HONEST (stub=True, no fabricated output): {sreceipt}"
    line("  no regression: a matched intent USEs stripe_rail (forge last); with no codegen "
         "seam the offline fallback is the loudly-marked honest stub (stub=True, "
         "promoted=False, fallback=codegen-unavailable) ✓")

    line("  → discovery's forge-if-missing is REAL: a missing capability is authored "
         "quarantined, evaluated in the sandbox, scanned, and PROMOTED through the "
         "attested gate — or refused, fail closed. The stub survives only as an honest, "
         "loudly-marked offline placeholder, never a fake organ.")
