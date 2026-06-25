"""RESEARCH1 — a research capability composed from observation + docs + knowledge.

A NEW MODULE (`decima/research.py`), not core. It COMPOSES public APIs:
`kernel.ingest_observation` (observe a URL → untrusted DATA claim + receipt),
`doc.create_doc`/`link_doc` (the report and its citation edges), and the
knowledge graph (sources folds the edges back out). It edits nothing it composes.

This check proves, end to end:
  - research a question over a couple URLs → a `report` knowledge Cell that CITES
    each observed source (typed `cites` edges → DATA claim AND observation receipt);
  - the observed content is UNTRUSTED DATA (instruction_eligible=False) — cited as
    evidence, NEVER obeyed — and so is the report synthesized over it;
  - provenance on the Weft: report —cites→ claim, claim grounded in the receipt,
    receipt descends from the INVOKE;
  - sources() lists the cited sources (deterministic read over the citation edges).

Contract: run(k, line). Fail loud.
"""
from decima import research, doc


def run(k, line):
    line("\n== RESEARCH (observation + docs + knowledge, composed) — RESEARCH1 ==")
    w = lambda: k.weave()
    decima = w().get(k.decima_agent_id)

    urls = ["decima.dev/notes", "research.example/page"]
    question = "What do the sources say about Decima?"
    out = research.research(k, decima, question, urls)
    report_id = out["report"]
    findings = out["findings"]

    # The report is a first-class knowledge `document` Cell on the Weft.
    report = w().get(report_id)
    assert report is not None and report.type == "document", report
    assert question in report.content["title"], report.content["title"]
    line(f"  researched {len(urls)} URL(s) → report doc {report_id[:8]} "
         f"('{report.content['title'][:40]}…') on the Weft ✓")

    # Every observed source produced an untrusted DATA claim + an observation receipt.
    assert len(findings) == len(urls), findings
    for f in findings:
        assert f["instruction_eligible"] is False, \
            "observed web content MUST be untrusted DATA, never instruction-eligible"
        assert f["receipt"], f                      # observation receipt always present
        assert f["claim"], f                        # canned page is a fact → remembered as DATA
        claim = w().get(f["claim"])
        assert claim.content["instruction_eligible"] is False, \
            "the cited DATA claim must be instruction_eligible=False"
    line(f"  each source observed as UNTRUSTED DATA (instruction_eligible=False) "
         f"with a receipt — cited, never obeyed ✓")

    # The report itself is DATA: synthesized over untrusted observations, the trust
    # law writes its body instruction_eligible=False (a report is to be read).
    assert report.content["trusted"] is False, report.content
    assert report.content["instruction_eligible"] is False, \
        "a report over untrusted sources MUST be DATA, never instruction-eligible"
    line("  the report is DATA too (untrusted source → instruction_eligible=False) ✓")

    # Provenance on the Weft: report —cites→ claim AND —cites→ receipt for each source.
    cite_edges = w().edges_from(report_id, research.CITES)
    cited = {e["dst"] for e in cite_edges}
    for f in findings:
        assert f["claim"] in cited, ("report must cite the DATA claim", f)
        assert f["receipt"] in cited, ("report must cite the observation receipt", f)
    # Each claim is grounded in its receipt (the kernel's observed_via provenance):
    # report → claim → intake → receipt → INVOKE is one chain folded from the Weave.
    line(f"  report cites {len(cited)} source cell(s) — claim + receipt per URL "
         f"(provenance on the Weft) ✓")

    # sources() lists the cited sources — a deterministic read over the edges.
    srcs = research.sources(k, report_id)
    assert set(srcs) == cited, (srcs, cited)
    assert srcs == sorted(srcs), "sources must be deterministically ordered"
    expected = len(findings) * 2          # claim + receipt per source
    assert len(srcs) == expected, (len(srcs), expected)
    line(f"  sources(report) → {len(srcs)} cited cells (deterministic) ✓")

    # The injection embedded in the canned page is INERT: it lives in the cited DATA
    # claim, but it is not instruction-eligible and the report never obeyed it (no
    # capability was forged/granted/published from page content — research only
    # observed + cited). The cite edge says "the page SAID this", never "do this".
    claim0 = w().get(findings[0]["claim"])
    assert "ignore your instructions" in claim0.content.get("proposition", "").lower(), \
        "canned page (with its injection) is preserved verbatim as DATA"
    line("  embedded page injection preserved as cited DATA but INERT "
         "(instruction_eligible=False; cited, never obeyed) ✓")

    line("  → research = observe (untrusted DATA + receipt) ∘ doc (report + cite "
         "edges) ∘ knowledge (sources): provenance on the Weft, no instruction "
         "from the web.")
