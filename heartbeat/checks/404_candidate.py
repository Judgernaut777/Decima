"""EXTENSION CANDIDATE — authoring an organ is content-addressed DATA, born quarantined.

Stage A of the forge-real loop (NONA_RECKONER §1/§3/§4). Nona authors a candidate organ
from an intent via an INJECTED, deterministic codegen fake — no network, no key, no live
model. This lane is an adversarial detector for the guarantees later stages stand on:

  (0) the codegen SEAM fails CLOSED live: the default `model_codegen` cannot reach a
      model offline — only an injected fake authors source here;
  (a) authoring yields a `candidate` Cell on the Weft, BORN QUARANTINED — lifecycle
      QUARANTINED with the §3 baseline (sandbox_only · no_outward_effects · network_allow([]))
      — and the DRAFT→QUARANTINED transition is provenance (two events), not an edited row;
  (b) CONTENT-ADDRESSED (Law 4): implementation_digest == content_id(source) and changes
      IFF the source changes — identical source ⇒ identical digest, one different byte ⇒
      a different digest;
  (c) the EvaluationSuite is a versioned Cell on the Weft (§4): a content id, an int
      version, int thresholds/repetitions, and >=1 hostile-input adversary;
  (d) the generated source is DATA — recorded verbatim, NEVER executed or trusted:
      source that would RAISE at import is stored inertly, and authoring grants nothing
      (no envelope grant, no invoke, no live handler).

Deterministic + offline: fresh Kernel, injected fake codegen, no network/clock/key.
Contract: run(k, line). Fail loud (assert).
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.hashing import content_id, nfc
from decima import candidate as C


def run(k, line):
    line("\n== EXTENSION CANDIDATE (Nona authors a content-addressed, quarantined organ) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    def agent():
        return kk.weave().get(kk.decima_agent_id)

    # 0. THE SEAM FAILS CLOSED LIVE — the default codegen cannot reach a model offline. ──
    try:
        C.model_codegen("normalize some text")
        raise AssertionError("model_codegen ran live offline — the egress seam is not gated")
    except C.CodegenUnavailable:
        pass
    line("  codegen seam: default model_codegen fails closed offline (inject a fake) ✓")

    intent = "normalize user text: collapse whitespace and lowercase"
    env_before = list(agent().content.get("envelope", []))
    invokes_before = len(kk.weave().invocations)

    # 1. AUTHOR from intent via the injected deterministic fake. ────────────────────────
    cand = C.author_candidate(kk, intent, C.fake_normalizer_codegen)
    w = kk.weave()
    cell = w.get(cand["cell"])
    assert cell is not None and cell.type == "candidate", "no candidate Cell on the Weft"

    # (a) BORN QUARANTINED — lifecycle + the §3 baseline caveats. ───────────────────────
    assert cell.content["lifecycle"] == "QUARANTINED", \
        f"candidate must be born QUARANTINED, got {cell.content.get('lifecycle')!r}"
    q = cell.content["quarantine"]
    assert q["sandbox_only"] is True, "quarantine baseline: sandbox_only must be True"
    assert q["no_outward_effects"] is True, "quarantine baseline: no_outward_effects must be True"
    assert q["network_allow"] == [], "quarantine baseline: network_allow must be []"
    assert cell.content["manifest"]["caveats"].get("sandbox_only") is True, \
        "the candidate's manifest must also carry sandbox_only"
    # The DRAFT→QUARANTINED transition is provenance (two events), not an edited row.
    assert cell.content["states"] == ["DRAFT", "QUARANTINED"], cell.content["states"]
    assert len(cell.provenance) == 2, \
        f"expected DRAFT then QUARANTINED as two events, got {len(cell.provenance)}"
    line("  born QUARANTINED: sandbox_only · no_outward_effects · network_allow([]); "
         "DRAFT→QUARANTINED is two events on the Weft ✓")

    # (b) CONTENT-ADDRESSED (Law 4): digest == content_id(source), and tracks the source. ─
    src = cand["source_blobs"]
    assert isinstance(src, str), "source_blobs must be text (generated source is DATA)"
    assert cand["implementation_digest"] == content_id(nfc(src)), \
        "implementation_digest is NOT the content-address of the source (Law 4 broken)"
    assert cell.content["implementation_digest"] == cand["implementation_digest"], \
        "the folded candidate's digest disagrees with the authored digest"

    # identical source ⇒ identical digest (idempotent, same cell); different source ⇒
    # a different digest (the immutable handle a promotion will grant an edge to).
    same = C.author_candidate(kk, intent, C.fake_normalizer_codegen)
    assert same["implementation_digest"] == cand["implementation_digest"], \
        "identical source produced a different implementation_digest"
    assert same["cell"] == cand["cell"], "identical authoring must be the same content-addressed cell"

    def mutated_codegen(_intent):
        return C.NORMALIZER_SOURCE + "# an extra byte changes the build\n"
    other = C.author_candidate(kk, intent, mutated_codegen, name="normalize_v2")
    assert other["implementation_digest"] != cand["implementation_digest"], \
        "changing the source did NOT change the digest — the build is not content-addressed"
    assert other["cell"] != cand["cell"], "a different build must be a different candidate cell"
    line("  content-addressed: implementation_digest == content_id(source); identical "
         "source ⇒ same digest+cell, one byte different ⇒ different digest+cell ✓")

    # (c) EVALUATIONSUITE is a versioned Cell on the Weft (§4). ─────────────────────────
    suite_cell = w.get(cand["suite"])
    assert suite_cell is not None and suite_cell.type == "evaluation_suite", \
        "the EvaluationSuite must be a real Cell on the Weft"
    s = suite_cell.content
    assert isinstance(s["version"], int) and not isinstance(s["version"], bool), \
        "the suite must carry an int version"
    assert suite_cell.id == content_id(
        {"evaluation_suite": cand["name"], "version": s["version"], "candidate": cand["cell"]},
        kind="cell"), "the suite is not content-addressed by (name, version, candidate)"
    for key in ("pass_rate_pct", "hostile_contained", "max_high_findings"):
        v = s["thresholds"][key]
        assert isinstance(v, int) and not isinstance(v, bool), \
            f"threshold {key} must be an int (no floats in signed content), got {v!r}"
    assert isinstance(s["repetitions"], int), "repetitions must be an int"
    assert len(s["adversaries"]) >= 1, "the suite must carry >= 1 hostile-input adversary"
    # seeds are ints throughout the plan (§3: seeded tests where possible).
    for case in s["cases"] + s["adversaries"]:
        assert isinstance(case["seed"], int) and not isinstance(case["seed"], bool), \
            f"every case seed must be an int, got {case.get('seed')!r}"
    # a provenance edge candidate → suite ties them on the graph.
    assert any(e["rel"] == "evaluated_by" and e["dst"] == cand["suite"]
               for e in cell.edges_out), "no candidate → suite provenance edge"
    line(f"  EvaluationSuite: versioned Cell (v{s['version']}), int thresholds+repetitions, "
         f"{len(s['adversaries'])} adversary, provenance edge ✓")

    # (d) THE SOURCE IS DATA — never executed or trusted. ──────────────────────────────
    # Source that RAISES at import is stored inertly: if authoring ever exec/compiled it,
    # this would blow up. That it doesn't proves the generated code is treated as DATA.
    booby = ("raise RuntimeError('generated source must never run at authoring time')\n"
             "def normalize(text):\n    return text\n")

    def booby_codegen(_intent):
        return booby
    danger = C.author_candidate(kk, intent, booby_codegen, name="booby")
    assert danger["source_blobs"] == booby, "source-as-data must be recorded verbatim"
    assert danger["implementation_digest"] == content_id(nfc(booby)), \
        "even hostile source is content-addressed as inert DATA"
    dcell = kk.weave().get(danger["cell"])
    assert dcell.content["source_is_data"] is True and dcell.content["quarantined"] is True, \
        "the candidate must record its source as quarantined DATA"

    # Authoring GRANTS NOTHING: no new envelope grant, no invoke, no live handler.
    env_after = list(agent().content.get("envelope", []))
    assert env_after == env_before, \
        "authoring a candidate changed Decima's envelope — it must confer no grant"
    assert len(kk.weave().invocations) == invokes_before, \
        "authoring a candidate wrote an INVOKE — it must run nothing"
    line("  source-as-DATA: import-raising source is stored inertly (never exec'd); "
         "authoring confers no grant, runs no invoke ✓")

    line("  → Nona authors an organ as content-addressed, quarantined DATA — an immutable "
         "impl digest + a versioned EvaluationSuite the Reckoner will judge, granting nothing.")
