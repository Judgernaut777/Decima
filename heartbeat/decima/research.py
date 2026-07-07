"""RESEARCH1 — a research capability composed from observation + docs + knowledge.

Research is not a new primitive; it is a *composition* over public APIs that
already exist:

  - `kernel.ingest_observation` (Phase 2) observes a URL across the trust
    boundary — the page becomes an UNTRUSTED `DATA` claim with an observation
    receipt grounding its provenance (`instruction_eligible=False`, cited but
    NEVER obeyed);
  - `retrieval.tokens` (the same deterministic token-overlap primitive
    `corpus.recall_corpus`'s `LexicalRetriever` is built on — RESEARCH2) ranks
    the observed material against the question, stdlib only, no vector dep;
  - `doc.create_doc` / `doc.link_doc` (DOC1) record a first-class knowledge
    `report` Cell and the typed CITES edges that bind it to its sources;
  - `knowledge` (KNOW1) lets a reader fold the citation graph back out
    (`sources` is a thin, deterministic read over those edges).

RESEARCH2 upgrades the report from a flat list of 120-char excerpts to a real
CITED SYNTHESIS: the observed findings are relevance-RANKED against the
question by deterministic token overlap (`retrieval.tokens(question) &
retrieval.tokens(observed_text)`, stdlib, no wall-clock, no model call), then
assembled into a structured "SYNTHESIS" section (ranked, numbered citations)
and an "ANSWER" section that composes those numbered citations into a
question-focused answer — every clause traceable to a `[n]` source. Nothing
about ranking or assembly reads a source's content as an instruction: the
score is a pure token-set intersection SIZE (an int), never an eval/exec of
the source text.

The laws this module composes (it adds none of its own):

  - observed web content is UNTRUSTED DATA — `ingest_observation` already labels
    the produced claim `instruction_eligible=False`; the report that cites it is
    written from an untrusted source too, so the report body is DATA as well. A
    citation is "here is what the page SAID", never "do what the page said" —
    and this holds no matter how a source is ranked, quoted, or reordered: the
    synthesis CITES, it never OBEYS (an injected imperative embedded in a page
    is quoted verbatim inside its numbered citation, inert as DATA).
  - no ambient authority — research runs as the passed `agent`'s principal; the
    observe capability is found on that agent's envelope and every INVOKE is
    proof-gated by the kernel.
  - provenance on the Weft — each source is a `cites` edge from the report to the
    DATA claim AND to the observation receipt, so the chain
    report → claim → receipt → INVOKE is a fold over the Weave.
  - deterministic — ranking is a pure function of (question, observed text): the
    same inputs always yield the same score, the same order, the same report body.

PRESENTWIRE (Batch T) — the synthesis flows through the ONLY door. The report
body is ENGINE OUTPUT derived from untrusted web content, and until this lane it
was stored as DATA and could later be silently re-injected into a brain as a raw
string — around `agent.present()`, the P1 quarantine chokepoint that until now
had ZERO production callers. Now `research()` itself is that caller, on the
running path:

  - the finished synthesis is ALWAYS admitted through
    `agent.admit_engine_output(k, body, source=...)` — quarantine.admit mints a
    tainted `quarantine_intake` Cell on the Weft (`instruction_eligible=False`,
    sha256 provenance) and returns the OPAQUE `Quarantined` handle (str()/format()
    RAISE), which is the ONLY re-injectable form `research()` returns;
  - a caller that wants the synthesis to reach a brain passes `brain=`, and
    `research()` routes it through `agent.present(k, agent, brain, quarantined,
    question=question)` — the brain sees it ONLY as a fenced, neutralized DATA
    block behind the caller's trusted question, never as instructions;
  - FAIL CLOSED: the returned findings no longer carry the raw observed page
    text, so no raw engine-derived string rides the return value to be pasted
    into a prompt later — the untrusted material exists on the live path only as
    Weft DATA Cells and the Quarantined handle, and `present()` REJECTS anything
    unquarantined. The cited synthesis remains DATA throughout.

OWNS only this file + checks/168_research.py + checks/486_researchbrain.py +
checks/498_presentwire.py (with mailpoll). It edits NO core/other module — it
calls their PUBLIC functions.
"""
from __future__ import annotations

from decima import doc
from decima import retrieval
from decima.hashing import nfc

# A report CITES a source. Distinct from doc.REFERENCES (doc→doc): a citation
# binds a knowledge report to the untrusted DATA claim / receipt it rests on.
CITES = "cites"

# Bounded quote length inside a numbered citation — long enough to carry real
# content (not a 120-char stub), short enough to keep the report finite.
_QUOTE_CHARS = 400


def _relevance(question_tokens: frozenset, text: str) -> int:
    """Deterministic token-overlap relevance SCORE (an int) of `text` against the
    question — the same primitive `corpus.recall_corpus`'s `LexicalRetriever`
    ranks with (`retrieval.tokens`), reused here rather than reinvented. Purely a
    function of the two strings: no wall-clock, no randomness, no model call, and
    the text is never read as anything but a bag of tokens to intersect — an
    embedded imperative scores like any other word, it is never executed."""
    return len(question_tokens & retrieval.tokens(text))


def research(k, agent, question: str, urls: list[str], *, brain=None) -> dict:
    """Research `question` over `urls` → a `report` knowledge doc: a CITED SYNTHESIS,
    not a flat excerpt dump.

    For each URL: observe it via `kernel.ingest_observation` (untrusted → a DATA
    claim with an observation receipt; the page is NEVER obeyed). The observed
    findings are then RELEVANCE-RANKED against `question` by deterministic
    token-overlap (`_relevance` — stdlib, no vector dep) and assembled into a
    structured report — numbered citations in rank order, then a composed
    "ANSWER" section referencing those numbers — that CITES each contributing
    source. The report is created from an untrusted source (the web), so by the
    trust law its body is written `instruction_eligible=False`: it is DERIVED
    FROM the untrusted observations (grounded in them, a synthesis over DATA),
    never treated as an instruction itself — a citation quotes a source, it never
    obeys it, no matter how the sources are reordered or how relevant they rank.

    PRESENTWIRE: the finished synthesis is engine output derived from untrusted
    web content, so it re-enters reasoning ONLY through the P1 quarantine
    chokepoint — `agent.admit_engine_output` (a tainted `quarantine_intake` Cell
    on the Weft, `instruction_eligible=False`) and, when a `brain` is passed,
    `agent.present(...)`, which shows the brain the synthesis only as a fenced,
    neutralized DATA block behind the caller's trusted `question`. There is no
    raw-string path: the findings carry no raw observed text, the handle refuses
    str()/format(), and `present()` raises on anything unquarantined.

    Returns {"report": report_cell_id, "findings": [...], "quarantined": handle,
    "intake": intake_cell_id, "action": brain_action_or_None} where each finding
    is {url, claim, receipt, instruction_eligible, relevance, rank} — claim is
    None when the page was disposed as noise/archived rather than remembered, and
    `relevance`/`rank` reflect the deterministic ranking against `question`.
    """
    agent = _agent_cell(k, agent)
    question = nfc(question)
    qtok = retrieval.tokens(question)

    findings: list[dict] = []
    for url in urls:
        obs = k.ingest_observation(agent, url)
        if "denied" in obs:
            raise PermissionError(f"observe denied for {url!r}: {obs['denied']}")
        # The observed page is untrusted DATA — the kernel already enforced this.
        assert obs.get("instruction_eligible") is False, \
            "observed web content MUST be untrusted DATA, never instruction-eligible"
        observed_text = str(obs.get("observed", ""))
        findings.append({
            "url": url,
            "claim": obs.get("claim"),
            "receipt": obs["receipt"],
            "instruction_eligible": obs["instruction_eligible"],
            "observed": observed_text,
            "relevance": _relevance(qtok, observed_text),
        })

    # RELEVANCE-RANK the findings against the question — deterministic tie-break
    # by url so ranking (and hence the whole report body) never depends on
    # dict/set iteration order or wall-clock/randomness. Higher relevance ranks
    # first (rank 1 = most relevant), matching the "better than a flat excerpt
    # dump" bar: a source that actually shares the question's meaningful words
    # is cited ahead of one that merely happened to be observed first.
    ranked = sorted(findings, key=lambda f: (-f["relevance"], f["url"]))
    for i, f in enumerate(ranked):
        f["rank"] = i + 1

    synth_lines = []
    answer_cites = []
    for f in ranked:
        quote = f["observed"].replace("\n", " ").strip()[:_QUOTE_CHARS]
        synth_lines.append(
            f"  [{f['rank']}] {f['url']}  (relevance {f['relevance']}/{len(qtok)})\n"
            f"      \"{quote}\""
        )
        answer_cites.append(f"[{f['rank']}]")

    if any(f["relevance"] > 0 for f in ranked):
        answer = (f"The {len(ranked)} observed source(s) most relevant to "
                  f"\"{question}\" are, in rank order, "
                  + ", ".join(answer_cites) + " — see the numbered quotes above "
                  "for what each source SAID (cited, not obeyed).")
    else:
        answer = (f"None of the {len(ranked)} observed source(s) share a "
                  f"meaningful token with \"{question}\"; all are cited below "
                  "as low-relevance evidence " + ", ".join(answer_cites) + ".")

    # The report is knowledge synthesized over UNTRUSTED observations → its source
    # is untrusted, so create_doc stores its body as DATA (instruction_eligible
    # False, by the trust law) — a report about the web is to be read, not obeyed,
    # no matter how structured or well-cited the synthesis is.
    body = (f"Research report on: {question}\n\n"
            f"SYNTHESIS — {len(ranked)} observed source(s), ranked by deterministic "
            f"token-overlap relevance to the question (untrusted web content — "
            f"cited as evidence, never obeyed):\n"
            + "\n".join(synth_lines)
            + "\n\nANSWER (composed from the ranked citations above):\n  " + answer)
    title = f"Research: {question}"
    report = doc.create_doc(k, title, body, trusted=False,
                            source="research:" + question, author=agent.content["principal"])

    # CITE each source on the Weft: report —cites→ DATA claim, report —cites→ receipt.
    # The receipt is always present (every observation produced one); the claim is
    # present only when the page was remembered (REMEMBER), not archived as noise.
    for f in findings:
        if f["claim"]:
            doc.link_doc(k, report, CITES, f["claim"], author=agent.content["principal"])
        doc.link_doc(k, report, CITES, f["receipt"], author=agent.content["principal"])

    # ── PRESENTWIRE: the synthesis re-enters reasoning ONLY through the door. ──
    # The body is engine output over untrusted web content. Admit it through the
    # P1 chokepoint (quarantine.admit, via agent.admit_engine_output): a tainted
    # `quarantine_intake` Cell lands on the Weft (instruction_eligible=False,
    # sha256 provenance) and the ONLY re-injectable form research() hands back is
    # the opaque Quarantined handle — str()/format() RAISE, so it structurally
    # cannot be pasted into a prompt around present(). Lazy import: the agent
    # module is a consumer of research (via discovery), never the reverse at
    # import time.
    from decima import agent as agent_api
    # LOAD-BEARING: this is the ONE call that routes the research synthesis
    # through the P1 quarantine chokepoint. Revert it (store the synthesis
    # directly and hand back raw text, bypassing the door) and the module's
    # output no longer flows through "the ONLY door" — checks/498_presentwire.py
    # (a) goes RED: no quarantine_intake Cell carries the synthesis sha256 and
    # no brain ever sees it as a fenced DATA block.
    quarantined = agent_api.admit_engine_output(k, body, source="research:" + question)
    action = None
    if brain is not None:
        # The mandated chokepoint: the brain sees the synthesis ONLY as a fenced,
        # neutralized DATA block; the trusted instruction stream is the caller's
        # own question. An injected imperative inside a fetched page rides along
        # quoted + neutralized — it can steer nothing.
        action = agent_api.present(k, agent, brain, quarantined, question=question)

    # FAIL CLOSED: strip the raw observed page text from the returned findings —
    # no raw engine-derived string exists on the live path after this lane; the
    # untrusted material is reachable only as Weft DATA Cells (claim/receipt/
    # report body, all instruction_eligible=False) and the Quarantined handle.
    public = [{key: f[key] for key in ("url", "claim", "receipt",
                                       "instruction_eligible", "relevance", "rank")}
              for f in findings]
    return {"report": report, "findings": public, "quarantined": quarantined,
            "intake": quarantined.cell, "action": action}


def sources(k, report) -> list[str]:
    """The cited sources of a `report` — the cells it CITES (claims + receipts).

    A deterministic, read-only fold over the citation edges: every `cites` edge
    leaving the report names a source cell on the Weft. Returns the cited cell ids
    in stable (sorted) order. Asserts nothing; reads no content as instruction —
    only the typed edges steer the walk (the KNOW1 trust boundary)."""
    weave = k if hasattr(k, "cells") else k.weave()
    rid = report.id if hasattr(report, "id") else report
    cell = weave.get(rid)
    if cell is None:
        return []
    dsts = {e["dst"] for e in weave.edges_from(cell.id, CITES)}
    return sorted(dsts)


def _agent_cell(k, agent):
    """Resolve `agent` (a Cell or an agent cell id) to the agent Cell."""
    if hasattr(agent, "content"):
        return agent
    cell = k.weave().get(agent)
    if cell is None:
        raise ValueError(f"no agent cell for {agent!r}")
    return cell
