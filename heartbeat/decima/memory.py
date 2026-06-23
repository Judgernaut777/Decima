"""Memory / WikiBrain — thin, memory-led, built on the types-as-data model.

Memory is the first hard consumer that hardens the domain model (`model.py`):
a *claim* is a Cell, *evidence* is an EDGE `claim —supported_by→ source`, an
entity link is an EDGE `claim —about→ entity`, and a claim's *provenance* is the
events that asserted it (author + parents, already in the Weft).

Four permissions are kept separate (Codex MEMORY_ARCHITECTURE §5): **may store**
(the `memory_write_allowed` write-gate), **may recall as data** (`recallable`),
**may cite as evidence** (`citable`), and **may use as instruction**
(`instruction_eligible`). The recall-vs-instruct law (same one the browser receipt
obeys): claims from untrusted sources are written `instruction_eligible=False`;
`recall` returns them as DATA; the brain never treats a recalled untrusted claim
as an instruction. Claims also carry a `scope` (thin: a string) — recall can be
filtered by it (authorization-first; full horizon/`authorize`-mediated recall is
deferred).

Retrieval is a pluggable SEAM. The prototype ships a substring `Retriever`; a
real semantic/vector index (Chroma/Milvus, GraphRAG/RAPTOR) wraps in behind the
same interface later — no vector dependency is pulled into the Heartbeat.
Contradiction-resolution, freshness decay, consolidation, and embeddings are
deliberately deferred.
"""
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

# Confidence is an integer in millionths (WEFT §4/§7: never a float).
FULL_CONFIDENCE = 1_000_000
DEFAULT_SCOPE = "realm:default"


def memory_write_allowed(author: str) -> bool:
    """Memory-write policy seam: who may assert a claim. Default: allow. A real
    policy would consult the realm's memory-curator capability."""
    return True


def claim_id(text: str) -> str:
    return content_id({"claim": nfc(text)})


def entity_id(name: str) -> str:
    return content_id({"entity": nfc(name)})


def remember(weft, author: str, claim_text: str, evidence_src: str,
             instruction_eligible: bool, confidence: int = FULL_CONFIDENCE,
             about: str | None = None, scope: str = DEFAULT_SCOPE,
             recallable: bool = True, citable: bool = True) -> str:
    """Assert a claim, its evidence edge, and (optionally) an entity it is about.

    `evidence_src` is the cell id of a result / receipt / utterance that grounds
    the claim. The four permissions (Codex §5): `memory_write_allowed` is the
    *may-store* gate; `recallable` (may recall as data), `citable` (may cite as
    evidence) and `instruction_eligible` (may use as instruction — False for
    anything observed from an untrusted source) are stored on the claim. `scope`
    locates the claim for authorization-first recall. Returns the claim cell id.
    """
    if not memory_write_allowed(author):
        raise PermissionError(f"{author} may not write memory")
    cid = claim_id(claim_text)
    assert_content(weft, author, cid, "claim", {
        "proposition": nfc(claim_text),
        "confidence": int(confidence),
        "scope": scope,
        "recallable": bool(recallable),
        "citable": bool(citable),
        "instruction_eligible": bool(instruction_eligible),
    })
    assert_edge(weft, author, cid, "supported_by", evidence_src)
    if about is not None:
        eid = entity_id(about)
        assert_content(weft, author, eid, "entity", {"name": nfc(about)})
        assert_edge(weft, author, cid, "about", eid)
    return cid


# -- retrieval seam ----------------------------------------------------------
class Retriever:
    """The retrieval interface memory recalls through. Swap the implementation
    (vector / graph index) without changing callers."""

    def search(self, weave, query: str, scope: str | None = None) -> list:
        raise NotImplementedError


class SubstringRetriever(Retriever):
    """The prototype default: type=claim + case-insensitive substring match,
    honoring the `recallable` permission and an optional `scope` filter."""

    def search(self, weave, query: str, scope: str | None = None) -> list:
        q = nfc(query).lower()
        out = []
        for c in weave.of_type("claim"):
            if not c.content.get("recallable", True):
                continue                                  # may-recall-as-data gate
            if scope is not None and c.content.get("scope") != scope:
                continue                                  # authorization-first (thin)
            if q in c.content.get("proposition", "").lower():
                out.append(c)
        return out


_DEFAULT_RETRIEVER = SubstringRetriever()


def recall(weave, query: str, scope: str | None = None,
           retriever: Retriever | None = None) -> list:
    """Return matching claim cells as DATA — never as instructions. Honors the
    `recallable` permission and, if given, the `scope` filter. Untrusted claims
    come back too (with `instruction_eligible=False`) for the caller to read."""
    return (retriever or _DEFAULT_RETRIEVER).search(weave, query, scope)


def why(weave, weft, claim: str) -> dict:
    """Provenance for a claim: its evidence sources (supported_by edges) and the
    events that asserted it (author + parents), walking both."""
    cell = weave.get(claim)
    if cell is None:
        return {"claim": claim, "found": False}
    sources = [e["dst"] for e in weave.edges_from(cell.id, "supported_by")]
    about = [e["dst"] for e in weave.edges_from(cell.id, "about")]
    index = {ev.id: ev for ev in weft.events()}
    events = []
    for eid in cell.provenance:
        ev = index.get(eid)
        if ev:
            events.append({"event": ev.id, "seq": ev.seq,
                           "author": ev.author, "parents": ev.parents})
    return {"claim": cell.id, "found": True,
            "proposition": cell.content.get("proposition"),
            "scope": cell.content.get("scope"),
            "recallable": cell.content.get("recallable"),
            "citable": cell.content.get("citable"),
            "instruction_eligible": cell.content.get("instruction_eligible"),
            "confidence": cell.content.get("confidence"),
            "supported_by": sources, "about": about, "asserted_by": events}
