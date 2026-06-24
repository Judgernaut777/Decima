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
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

# Confidence is an integer in millionths (WEFT §4/§7: never a float).
FULL_CONFIDENCE = 1_000_000
DEFAULT_SCOPE = "realm:default"
CLAIM = "claim"
EPISODIC = "episodic"
SEMANTIC = "semantic"
PROCEDURAL = "procedural"
DECISION = "decision"
FAILURE = "failure"
MEMORY_TYPES = (CLAIM, EPISODIC, SEMANTIC, PROCEDURAL, DECISION, FAILURE)
MEMORY_ACCESS = "memory_access"

# Memory-as-governance (B4): claims that constrain future action — what's banned,
# what's fragile, what already failed. Governance is its own Cell type (not in the
# recall taxonomy above) so a `governance_check` can consult it directly.
GOVERNANCE = "governance"
BANNED_ACTION = "banned_action"
FRAGILE_FILE = "fragile_file"
FAILED_APPROACH = "failed_approach"
GOVERNANCE_KINDS = (BANNED_ACTION, FRAGILE_FILE, FAILED_APPROACH)
# Default verdict per kind: a ban or a known-failed approach denies; a fragile
# file allows-with-warning (proceed, but with care). Overridable per claim.
_GOVERNANCE_VERDICT = {BANNED_ACTION: "deny", FAILED_APPROACH: "deny", FRAGILE_FILE: "warn"}


def memory_write_allowed(author: str) -> bool:
    """Memory-write policy seam: who may assert a claim. Default: allow. A real
    policy would consult the realm's memory-curator capability."""
    return True


def claim_id(text: str) -> str:
    return content_id({"claim": nfc(text)})


def memory_id(memory_type: str, text: str, scope: str = DEFAULT_SCOPE) -> str:
    return content_id({"memory": memory_type, "scope": nfc(scope), "text": nfc(text)})


def entity_id(name: str) -> str:
    return content_id({"entity": nfc(name)})


def access_id(target: str, query: str, seq: int) -> str:
    return content_id({"memory_access": target, "query": nfc(query), "seq": int(seq)})


def _permissions(instruction_eligible: bool, recallable: bool, citable: bool) -> dict:
    return {
        "recallable": bool(recallable),
        "citable": bool(citable),
        "instruction_eligible": bool(instruction_eligible),
    }


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
        **_permissions(instruction_eligible, recallable, citable),
    })
    assert_edge(weft, author, cid, "supported_by", evidence_src)
    if about is not None:
        eid = entity_id(about)
        assert_content(weft, author, eid, "entity", {"name": nfc(about)})
        assert_edge(weft, author, cid, "about", eid)
    return cid


def remember_memory(weft, author: str, memory_type: str, text: str, evidence_src: str,
                    instruction_eligible: bool, confidence: int = FULL_CONFIDENCE,
                    about: str | None = None, scope: str = DEFAULT_SCOPE,
                    recallable: bool = True, citable: bool = True,
                    **fields) -> str:
    """Assert a typed memory Cell with the same four permission boundaries as
    claims. Typed memories are DATA unless `instruction_eligible` is explicitly
    true, and recall still honors `recallable` and `scope` before matching text."""
    if memory_type not in MEMORY_TYPES or memory_type == CLAIM:
        raise ValueError(f"unknown typed memory type: {memory_type}")
    if not memory_write_allowed(author):
        raise PermissionError(f"{author} may not write memory")
    text = nfc(text)
    cid = memory_id(memory_type, text, scope)
    content = {
        "text": text,
        "confidence": int(confidence),
        "scope": scope,
        **_permissions(instruction_eligible, recallable, citable),
    }
    content.update(fields)
    assert_content(weft, author, cid, memory_type, content)
    assert_edge(weft, author, cid, "supported_by", evidence_src)
    if about is not None:
        eid = entity_id(about)
        assert_content(weft, author, eid, "entity", {"name": nfc(about)})
        assert_edge(weft, author, cid, "about", eid)
    return cid


def remember_episodic(weft, author: str, text: str, evidence_src: str,
                      instruction_eligible: bool = False, **kwargs) -> str:
    return remember_memory(weft, author, EPISODIC, text, evidence_src,
                           instruction_eligible, **kwargs)


def remember_semantic(weft, author: str, text: str, evidence_src: str,
                      instruction_eligible: bool = False, **kwargs) -> str:
    return remember_memory(weft, author, SEMANTIC, text, evidence_src,
                           instruction_eligible, **kwargs)


def remember_procedural(weft, author: str, text: str, evidence_src: str,
                        instruction_eligible: bool = False, **kwargs) -> str:
    return remember_memory(weft, author, PROCEDURAL, text, evidence_src,
                           instruction_eligible, **kwargs)


def remember_decision(weft, author: str, text: str, evidence_src: str,
                      instruction_eligible: bool = False, **kwargs) -> str:
    return remember_memory(weft, author, DECISION, text, evidence_src,
                           instruction_eligible, **kwargs)


def remember_failure(weft, author: str, text: str, evidence_src: str,
                     instruction_eligible: bool = False, **kwargs) -> str:
    return remember_memory(weft, author, FAILURE, text, evidence_src,
                           instruction_eligible, **kwargs)


def record_access(weft, author: str, target: str, query: str, weight: int = 1) -> str:
    """Record recall heat in the Weft. Heat is derived from these Cells, not a
    mutable counter hidden outside the log."""
    seq = weft.count() + 1
    cid = access_id(target, query, seq)
    assert_content(weft, author, cid, MEMORY_ACCESS, {
        "target": target,
        "query": nfc(query),
        "weight": int(weight),
        "seq": seq,
    })
    return cid


def access_events(weave, target: str) -> list:
    return [c for c in weave.of_type(MEMORY_ACCESS)
            if c.content.get("target") == target]


def heat(weave, target: str) -> int:
    return sum(int(c.content.get("weight", 1)) for c in access_events(weave, target))


def recency(weave, cell) -> int:
    """A small folded recency signal for ranking.

    Memory authors may provide event_time/valid_time/created_at in the Cell
    content; access events can also refresh ranking without mutating the memory
    Cell itself.
    """
    values = []
    for key in ("event_time", "valid_time", "created_at"):
        try:
            values.append(int(cell.content.get(key, 0)))
        except (TypeError, ValueError):
            pass
    values.extend(int(a.content.get("seq", 0)) for a in access_events(weave, cell.id))
    return max(values or [0])


def recall_with_heat(weft, author: str, weave, query: str, scope: str | None = None,
                     memory_types: tuple[str, ...] | None = None,
                     retriever: Retriever | None = None) -> list:
    """Recall as DATA and append access-signal Cells for the hits."""
    hits = recall(weave, query, scope, memory_types, retriever)
    for cell in hits:
        record_access(weft, author, cell.id, query)
    return hits


def consolidate(weft, author: str, weave, query: str,
                memory_type: str = SEMANTIC, scope: str | None = None,
                retriever: Retriever | None = None) -> str | None:
    """Supersede near-duplicate memories into one Cell, preserving provenance.

    The original Cells are not overwritten or retracted. The consolidated Cell
    carries `supersedes`/`derived_from` and direct evidence links copied from the
    originals, so `why()` can still walk back to the source evidence.
    """
    from decima import retrieval  # local import avoids a module cycle

    engine = retriever or retrieval.LexicalRetriever(
        include_superseded=True, include_duplicates=True)
    hits = engine.search(weave, query, scope, (memory_type,))
    if len(hits) < 2:
        return None

    groups = []
    for cell in hits:
        cell_tokens = retrieval.tokens(retrieval.text_of(cell))
        placed = False
        for group in groups:
            head_tokens = retrieval.tokens(retrieval.text_of(group[0]))
            overlap = len(cell_tokens & head_tokens)
            union = len(cell_tokens | head_tokens) or 1
            if overlap / union >= 0.6:
                group.append(cell)
                placed = True
                break
        if not placed:
            groups.append([cell])
    group = max(groups, key=len)
    if len(group) < 2:
        return None

    primary = max(group, key=lambda c: (c.content.get("confidence", 0), recency(weave, c)))
    sources = []
    for cell in group:
        sources.extend(e["dst"] for e in weave.edges_from(cell.id, "supported_by"))
    text = retrieval.text_of(primary)
    cid = content_id({"memory_consolidation": sorted(c.id for c in group), "text": text})
    assert_content(weft, author, cid, memory_type, {
        "text": text,
        "confidence": max(int(c.content.get("confidence", 0)) for c in group),
        "scope": primary.content.get("scope", DEFAULT_SCOPE),
        **_permissions(bool(primary.content.get("instruction_eligible", False)), True, True),
        "supersedes": [c.id for c in group],
        "derived_from": [c.id for c in group],
        "consolidation": True,
    })
    for cell in group:
        assert_edge(weft, author, cid, "derived_from", cell.id)
    for src in sorted(set(sources)):
        assert_edge(weft, author, cid, "supported_by", src)
    return cid


# -- retrieval seam ----------------------------------------------------------
class Retriever:
    """The retrieval interface memory recalls through. Swap the implementation
    (vector / graph index) without changing callers."""

    def search(self, weave, query: str, scope: str | None = None,
               memory_types: tuple[str, ...] | None = None) -> list:
        raise NotImplementedError


class SubstringRetriever(Retriever):
    """The prototype default: case-insensitive substring match over memory Cell
    text, honoring the `recallable` permission and an optional `scope` filter."""

    def search(self, weave, query: str, scope: str | None = None,
               memory_types: tuple[str, ...] | None = None) -> list:
        q = nfc(query).lower()
        types = memory_types or (CLAIM,)
        out = []
        for memory_type in types:
            for c in weave.of_type(memory_type):
                if not c.content.get("recallable", True):
                    continue                              # may-recall-as-data gate
                if scope is not None and c.content.get("scope") != scope:
                    continue                              # authorization-first (thin)
                haystack = c.content.get("proposition") or c.content.get("text") or ""
                if q in haystack.lower():
                    out.append(c)
        return out


_DEFAULT_RETRIEVER = SubstringRetriever()


def recall(weave, query: str, scope: str | None = None,
           memory_types: tuple[str, ...] | None = None,
           retriever: Retriever | None = None) -> list:
    """Return matching claim cells as DATA — never as instructions. Honors the
    `recallable` permission and, if given, the `scope` filter. Untrusted claims
    come back too (with `instruction_eligible=False`) for the caller to read."""
    return (retriever or _DEFAULT_RETRIEVER).search(weave, query, scope, memory_types)


def recall_episodic(weave, query: str, scope: str | None = None,
                    retriever: Retriever | None = None) -> list:
    return recall(weave, query, scope, (EPISODIC,), retriever)


def recall_semantic(weave, query: str, scope: str | None = None,
                    retriever: Retriever | None = None) -> list:
    return recall(weave, query, scope, (SEMANTIC,), retriever)


def recall_procedural(weave, query: str, scope: str | None = None,
                      retriever: Retriever | None = None) -> list:
    return recall(weave, query, scope, (PROCEDURAL,), retriever)


def recall_decision(weave, query: str, scope: str | None = None,
                    retriever: Retriever | None = None) -> list:
    return recall(weave, query, scope, (DECISION,), retriever)


def recall_failure(weave, query: str, scope: str | None = None,
                   retriever: Retriever | None = None) -> list:
    return recall(weave, query, scope, (FAILURE,), retriever)


# -- memory-as-governance (B4) ----------------------------------------------
def remember_governance(weft, author: str, kind: str, target: str, reason: str,
                        evidence_src: str, verdict: str | None = None,
                        instruction_eligible: bool = True,
                        confidence: int = FULL_CONFIDENCE, scope: str = DEFAULT_SCOPE,
                        recallable: bool = True, citable: bool = True) -> str:
    """Record a governance claim — what's banned, fragile, or known-failed — as a
    TRUSTED, instruction-eligible memory Cell that `governance_check` consults
    before an action is taken.

    Governance is `instruction_eligible=True` by default because its whole purpose
    is to *influence* behavior — but that is exactly why the trust boundary matters:
    `governance_check` lets ONLY instruction-eligible governance bind, so a claim
    authored from an untrusted source (write it with `instruction_eligible=False`)
    is visible as DATA yet can never silently forbid or permit an action. Same
    recall-vs-instruct law the browser receipt obeys, now guarding governance.

    `target` is the action / file / approach the claim is about; `evidence_src`
    grounds it (a result/receipt/utterance), so a verdict can cite WHY.
    """
    if kind not in GOVERNANCE_KINDS:
        raise ValueError(f"unknown governance kind: {kind}")
    if not memory_write_allowed(author):
        raise PermissionError(f"{author} may not write memory")
    target, reason = nfc(target), nfc(reason)
    verdict = verdict or _GOVERNANCE_VERDICT[kind]
    cid = content_id({"governance": kind, "target": target, "scope": nfc(scope)})
    assert_content(weft, author, cid, GOVERNANCE, {
        "kind": kind,
        "target": target,
        "verdict": verdict,
        "reason": reason,
        "text": reason,          # so text_of()/recall surface something meaningful
        "confidence": int(confidence),
        "scope": scope,
        **_permissions(instruction_eligible, recallable, citable),
    })
    assert_edge(weft, author, cid, "supported_by", evidence_src)
    return cid


def governance_check(weave, target: str, scope: str | None = None,
                     retriever: "Retriever | None" = None) -> dict:
    """Consult governance memory for `target`; return a verdict with provenance.

    Aggregation over matching claims: any `deny` → deny (allow=False); else any
    `warn` → allow-with-warning; else allow (nothing on record). ONLY trusted
    (instruction-eligible) governance binds — untrusted matches are returned under
    `ignored_untrusted` as DATA but never affect the verdict. The returned
    `evidence` carries each binding claim's kind/reason and its `supported_by`
    sources, so a denial can show the prior evidence that earned it.

    Decima auto-consulting this *before it delegates* (gate every action through
    governance) is the kernel wiring — a later **core** cycle; this lane provides
    the query and the verdict.
    """
    from decima import retrieval  # local import avoids a module cycle

    engine = retriever or retrieval.GovernanceRetriever()
    hits = engine.search(weave, target, scope, (GOVERNANCE,))
    binding, ignored, evidence, verdicts = [], [], [], set()
    for c in hits:
        if not c.content.get("instruction_eligible"):
            ignored.append(c.id)                  # visible as DATA, never governs
            continue
        binding.append(c)
        verdicts.add(c.content.get("verdict", "warn"))
        evidence.append({
            "governance": c.id,
            "kind": c.content.get("kind"),
            "target": c.content.get("target"),
            "verdict": c.content.get("verdict"),
            "reason": c.content.get("reason") or c.content.get("text"),
            "supported_by": [e["dst"] for e in weave.edges_from(c.id, "supported_by")],
        })
    if "deny" in verdicts:
        verdict, allow = "deny", False
    elif "warn" in verdicts:
        verdict, allow = "warn", True
    else:
        verdict, allow = "allow", True
    return {"target": nfc(target), "allow": allow, "verdict": verdict,
            "reason": _governance_reason(verdict, evidence, target),
            "evidence": evidence, "ignored_untrusted": ignored}


def _governance_reason(verdict: str, evidence: list, target: str) -> str:
    if not evidence:
        return f"no governance on record for {target!r}"
    head = {"deny": "DENY", "warn": "ALLOW (warn)"}.get(verdict, "ALLOW")
    parts = "; ".join(f"{e['kind']}: {e['reason']}" for e in evidence)
    return f"{head} {target!r} — {parts}"


def why(weave, weft, claim: str) -> dict:
    """Provenance for a claim: its evidence sources (supported_by edges) and the
    events that asserted it (author + parents), walking both."""
    cell = weave.get(claim)
    if cell is None:
        return {"claim": claim, "found": False}
    sources = [e["dst"] for e in weave.edges_from(cell.id, "supported_by")]
    about = [e["dst"] for e in weave.edges_from(cell.id, "about")]
    derived_from = [e["dst"] for e in weave.edges_from(cell.id, "derived_from")]
    index = {ev.id: ev for ev in weft.events()}
    events = []
    for eid in cell.provenance:
        ev = index.get(eid)
        if ev:
            events.append({"event": ev.id, "seq": ev.seq,
                           "author": ev.author, "parents": ev.parents})
    return {"claim": cell.id, "found": True,
            "proposition": cell.content.get("proposition") or cell.content.get("text"),
            "type": cell.type,
            "scope": cell.content.get("scope"),
            "recallable": cell.content.get("recallable"),
            "citable": cell.content.get("citable"),
            "instruction_eligible": cell.content.get("instruction_eligible"),
            "confidence": cell.content.get("confidence"),
            "supported_by": sources, "about": about,
            "derived_from": derived_from, "asserted_by": events}
