"""RESEARCH1 — a research capability composed from observation + docs + knowledge.

Research is not a new primitive; it is a *composition* over public APIs that
already exist:

  - `kernel.ingest_observation` (Phase 2) observes a URL across the trust
    boundary — the page becomes an UNTRUSTED `DATA` claim with an observation
    receipt grounding its provenance (`instruction_eligible=False`, cited but
    NEVER obeyed);
  - `doc.create_doc` / `doc.link_doc` (DOC1) record a first-class knowledge
    `report` Cell and the typed CITES edges that bind it to its sources;
  - `knowledge` (KNOW1) lets a reader fold the citation graph back out
    (`sources` is a thin, deterministic read over those edges).

The laws this module composes (it adds none of its own):

  - observed web content is UNTRUSTED DATA — `ingest_observation` already labels
    the produced claim `instruction_eligible=False`; the report that cites it is
    written from an untrusted source too, so the report body is DATA as well. A
    citation is "here is what the page SAID", never "do what the page said".
  - no ambient authority — research runs as the passed `agent`'s principal; the
    observe capability is found on that agent's envelope and every INVOKE is
    proof-gated by the kernel.
  - provenance on the Weft — each source is a `cites` edge from the report to the
    DATA claim AND to the observation receipt, so the chain
    report → claim → receipt → INVOKE is a fold over the Weave.

OWNS only this file + checks/168_research.py. It edits NO core/other module — it
calls their PUBLIC functions.
"""
from __future__ import annotations

from decima import doc
from decima.hashing import nfc

# A report CITES a source. Distinct from doc.REFERENCES (doc→doc): a citation
# binds a knowledge report to the untrusted DATA claim / receipt it rests on.
CITES = "cites"


def research(k, agent, question: str, urls: list[str]) -> dict:
    """Research `question` over `urls` → a `report` knowledge doc citing each source.

    For each URL: observe it via `kernel.ingest_observation` (untrusted → a DATA
    claim with an observation receipt; the page is NEVER obeyed). Collect the
    findings into a `report` document (created from an untrusted source, so the
    report body is DATA) that CITES each observed claim AND its receipt with a
    typed `cites` edge — provenance on the Weft.

    Returns {"report": report_cell_id, "findings": [...]} where each finding is
    {url, claim, receipt, instruction_eligible} — claim is None when the page was
    disposed as noise/archived rather than remembered.
    """
    agent = _agent_cell(k, agent)
    question = nfc(question)

    findings: list[dict] = []
    lines: list[str] = []
    for url in urls:
        obs = k.ingest_observation(agent, url)
        if "denied" in obs:
            raise PermissionError(f"observe denied for {url!r}: {obs['denied']}")
        # The observed page is untrusted DATA — the kernel already enforced this.
        assert obs.get("instruction_eligible") is False, \
            "observed web content MUST be untrusted DATA, never instruction-eligible"
        findings.append({
            "url": url,
            "claim": obs.get("claim"),
            "receipt": obs["receipt"],
            "instruction_eligible": obs["instruction_eligible"],
        })
        excerpt = str(obs.get("observed", "")).replace("\n", " ")[:120]
        lines.append(f"  • {url}\n    {excerpt}")

    # The report is knowledge synthesized over UNTRUSTED observations → its source
    # is untrusted, so create_doc stores its body as DATA (instruction_eligible
    # False, by the trust law) — a report about the web is to be read, not obeyed.
    body = (f"Research report on: {question}\n\n"
            f"Synthesized from {len(urls)} observed source(s) (untrusted web "
            f"content — cited as evidence, never obeyed):\n"
            + "\n".join(lines))
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

    return {"report": report, "findings": findings}


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
