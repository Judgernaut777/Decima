"""TRACING1 — observability over the signed Weft: one trace = one INVOKE causal chain.

Where AUDIT1 (audit.py) draws a *compliance* lens and TIMELINE1 (timeline.py) the
*human activity* lens, this module draws the *causal* lens: given one event — canonically
an INVOKE that requested an effect — reconstruct the SPAN TREE of everything that
causally descends from it (the receipt the kernel asserted, and the downstream events
those produced), following the event `parents` DAG (WEFT §2: each event names its causal
parents) plus the explicit back-references the kernel writes (a result cell's `content.of`
names the INVOKE it is the receipt of). That is exactly a distributed trace: a root span
(the INVOKE), child spans (its receipt + downstream asserts), each attributed to the
signing author and the capability that authorized it.

It is a pure, read-only consumer of the PUBLIC read APIs:
  - `k.weft.events()` — yields events in causal (seq) order, recomputing each event's
    content id and verifying the author's signature ON READ (weft.py §events;
    specs/WEFT_PROTOCOL.md §8). Reaching the end of the iterator is itself the
    tamper-evidence: a single altered byte raises `WeftError`, so a trace built from it
    fails loud rather than presenting a tampered history as a clean span tree.
  - `k.keyring.name_of(...)` — a human name for a principal id (best-effort).

It edits no core file and never appends to the Weft: the Weft stays append-only and its
tamper-evidence is untouched. Ints, not floats. Deterministic: a trace is a pure function
of the folded event set + the chosen root, so recomputing it yields the identical tree
(same spans, same order, same depths). Provenance is preserved end to end: every span
carries the signing author, the authorizing capability, and the event id.
"""
from decima.weft import WeftError, INVOKE, ASSERT, RETRACT, ATTEST


# -- human-facing vocabulary -------------------------------------------------
_VERB_WORD = {
    ASSERT: "asserted",
    RETRACT: "retracted",
    INVOKE: "invoked",
    ATTEST: "attested",
}


def _name(k, principal_id: str) -> str:
    """Human name for a principal id, via the keyring (falls back to the id)."""
    try:
        n = k.keyring.name_of(principal_id)
    except Exception:
        n = None
    return n or principal_id


def _read_events(k):
    """Read every event from the Weft, VERIFYING id + signature on the way (Law 1/4).

    Returns (events_by_id, ordered_events, ok, error). `events()` raises WeftError the
    instant a content id or signature does not recompute, so reaching the end of the
    iterator is itself the tamper-evidence proof: the whole history re-derived cleanly.
    On tamper we fail closed — empty maps, ok=False — never a half-built trace."""
    try:
        ordered = list(k.weft.events())            # verifies id + sig per event
        return {ev.id: ev for ev in ordered}, ordered, True, None
    except WeftError as e:                           # tampered history — fail closed
        return {}, [], False, str(e)


def _explicit_cause(ev) -> str | None:
    """The event id this event explicitly names as its cause, beyond the parents DAG.

    The kernel writes a result cell (an EffectReceipt) whose `content.of` is the INVOKE
    event it is the receipt of (kernel.invoke). That is a CAUSAL edge the trace must
    honor even though, on a forked log, the receipt's `parents` might not literally be
    the INVOKE. Returns the referenced event id, or None."""
    b = ev.body if isinstance(ev.body, dict) else {}
    content = b.get("content")
    if isinstance(content, dict):
        of = content.get("of")
        if isinstance(of, str) and of:
            return of
    return None


def _span(ev, parent: str | None, depth: int) -> dict:
    """One span = one event in the causal tree, attributed and placed.

    `parent` is the event id this span hangs under in the reconstructed tree (None for
    the root); `depth` is its distance from the root. Provenance is preserved: author +
    the authorizing capability + the event id all travel on the span."""
    return {
        "event": ev.id,
        "seq": ev.seq,
        "verb": ev.verb,
        "verb_word": _VERB_WORD.get(ev.verb, ev.verb.lower()),
        "author": ev.author,
        "author_name": None,            # filled by the caller (needs the kernel)
        "authorized_by": ev.authorized,  # capability cell id, or None
        "parent": parent,
        "depth": depth,
    }


# -- trace: reconstruct the causal span tree ---------------------------------
def trace(k, root_event: str) -> dict:
    """Reconstruct the causal SPAN TREE rooted at `root_event` (canonically an INVOKE,
    but any event id works). Walk FORWARD from the root: an event is a child span iff it
    names the root — or another span already in the tree — among its causal predecessors,
    where "predecessor" is the union of the event `parents` (WEFT §2 DAG) and the explicit
    `content.of` back-reference the kernel writes for a receipt.

    The walk is over events in causal (seq) order, so a parent span is always placed
    before its children — the first predecessor already in the tree becomes the span's
    parent, giving a deterministic tree (independent of how the log was stored). Each span
    is `{event, seq, verb, verb_word, author, author_name, authorized_by, parent, depth}`.

    `verifiable` is True iff the whole history re-derived cleanly on read; on tamper it is
    False, `error` says where, and `spans` is empty — a trace never presents a tampered
    log as a clean chain. A root that is not on the Weft yields an empty trace (found=False)."""
    by_id, ordered, ok, err = _read_events(k)

    root = by_id.get(root_event)
    spans: list[dict] = []
    if root is not None:
        # depth/parent of every event id already admitted into the tree.
        placed = {root.id: (None, 0)}                # event id -> (parent_id, depth)
        spans.append(_span(root, None, 0))
        for ev in ordered:                           # causal (seq) order: parents precede children
            if ev.id == root.id or ev.id in placed:
                continue
            preds = list(ev.parents)
            cause = _explicit_cause(ev)
            if cause is not None:
                preds.append(cause)
            # The span hangs under the FIRST predecessor already in the tree — in causal
            # order that is the nearest admitted ancestor, the natural tree parent.
            parent_id = next((p for p in preds if p in placed), None)
            if parent_id is None:
                continue                             # not causally downstream of the root
            depth = placed[parent_id][1] + 1
            placed[ev.id] = (parent_id, depth)
            spans.append(_span(ev, parent_id, depth))

    for s in spans:                                  # attribute (needs the kernel)
        s["author_name"] = _name(k, s["author"])

    return {
        "root": root_event,
        "found": root is not None,
        "spans": spans,
        "count": len(spans),
        "verb": root.verb if root is not None else None,
        "verifiable": ok,
        "error": err,
    }


# -- spans: flat ordered spans of a trace ------------------------------------
def spans(k, trace_result: dict) -> list[dict]:
    """The flat, ordered spans of a `trace(...)` result — the same causal (seq) order the
    tree was built in (root first, each child after its parent). A thin accessor so a
    consumer can iterate the chain without re-walking the DAG; deterministic by construction."""
    return list(trace_result.get("spans", []))


# -- root_cause: walk back to the originating cause --------------------------
def root_cause(k, event: str) -> dict:
    """Walk BACKWARD from `event` along the causal predecessors (event `parents` ⊕ the
    explicit `content.of` receipt back-reference) to the ORIGINATING cause — the earliest
    event with no predecessor still on the Weft. Returns the chain from the originating
    cause DOWN to `event` (`chain`, ordered cause-first), and `cause` (its first element).

    Deterministic: at each step it follows the predecessor that appears earliest in causal
    order (the nearest true ancestor), so the walk-back is a pure function of the folded
    log. Tamper-evident: on a tampered read it fails closed (empty chain, verifiable=False)."""
    by_id, _ordered, ok, err = _read_events(k)

    chain: list[dict] = []
    if event in by_id:
        cur = event
        seen = set()
        steps = []
        while cur is not None and cur not in seen:
            seen.add(cur)
            ev = by_id.get(cur)
            if ev is None:
                break
            steps.append(ev)
            preds = list(ev.parents)
            cause = _explicit_cause(ev)
            if cause is not None:
                preds.append(cause)
            # Among predecessors actually on the Weft, follow the earliest in causal
            # order — the nearest real ancestor — so the walk-back is deterministic.
            live = [by_id[p] for p in preds if p in by_id]
            cur = min(live, key=lambda e: e.seq).id if live else None
        steps.reverse()                              # originating cause first
        for ev in steps:
            chain.append({
                "event": ev.id,
                "seq": ev.seq,
                "verb": ev.verb,
                "verb_word": _VERB_WORD.get(ev.verb, ev.verb.lower()),
                "author": ev.author,
                "author_name": _name(k, ev.author),
                "authorized_by": ev.authorized,
            })

    return {
        "event": event,
        "found": event in by_id,
        "cause": chain[0] if chain else None,
        "chain": chain,
        "depth": len(chain),
        "verifiable": ok,
        "error": err,
    }


# -- structured_log: recent events as structured log records -----------------
# A coarse severity for each verb, the way an observability backend would level a span:
# an outward effect (INVOKE) is NOTICE (something happened in the world), a withdrawal
# (RETRACT) is WARNING (state was taken back), and the rest are INFO.
_LEVEL = {INVOKE: "NOTICE", RETRACT: "WARNING", ASSERT: "INFO", ATTEST: "INFO"}


def _cell_of(ev) -> str | None:
    """The cell id this event acted on, read from the body by verb shape — the `cell` an
    ASSERT/RETRACT named, the `cap` an INVOKE exercised, the `target_cell` an ATTEST
    witnessed. None when the event references no single cell (e.g. an edge-only assert)."""
    b = ev.body if isinstance(ev.body, dict) else {}
    for key in ("cell", "target_cell", "cap", "src"):
        v = b.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def structured_log(k, *, last: int | None = None) -> dict:
    """The recent Weft events as STRUCTURED LOG RECORDS — the flat, machine-parseable feed
    an observability backend ingests. Each record carries `level` (severity by verb),
    `seq`, `verb`, `author` + `author_name`, `cell` (the cell/effect touched), `authorized_by`
    (the capability that permitted it), and `event` (the provenance handle), newest LAST in
    causal (seq) order. `last=N` keeps the N most recent records (None = all).

    Carries the same tamper-evidence as the rest of the module: a clean read is
    `verifiable=True`; a tampered read fails closed with empty records."""
    _by_id, ordered, ok, err = _read_events(k)
    records = []
    for ev in ordered:                               # causal (seq) order
        records.append({
            "level": _LEVEL.get(ev.verb, "INFO"),
            "seq": ev.seq,
            "verb": ev.verb,
            "author": ev.author,
            "author_name": _name(k, ev.author),
            "cell": _cell_of(ev),
            "authorized_by": ev.authorized,
            "event": ev.id,
        })
    if last is not None:
        records = records[-last:]                    # the N most recent, newest last
    return {
        "records": records,
        "count": len(records),
        "last": last,
        "verifiable": ok,
        "error": err,
    }


# -- human-readable summaries ------------------------------------------------
def summary(report: dict) -> list[str]:
    """Render a trace / root_cause / structured_log dict to human lines."""
    if "spans" in report:                            # a trace
        out = [f"trace of {report['root'][:8]} ({report['verb'] or '—'}) — "
               f"{report['count']} span(s), verifiable={report['verifiable']}"]
        for s in report["spans"]:
            cap = s["authorized_by"][:8] if s["authorized_by"] else "—"
            out.append(f"  {'  ' * s['depth']}e{s['seq']:<3} {s['verb_word']:<9} "
                       f"by {s['author_name']:<10} via {cap}  prov={s['event'][:8]}")
        return out
    if "chain" in report:                            # a root_cause
        out = [f"root cause of {report['event'][:8]} — chain of {report['depth']} "
               f"(cause first), verifiable={report['verifiable']}"]
        for i, s in enumerate(report["chain"]):
            arrow = "→ " if i else "  "
            out.append(f"  {arrow}e{s['seq']:<3} {s['verb_word']:<9} by {s['author_name']:<10} "
                       f"prov={s['event'][:8]}")
        return out
    # a structured_log
    out = [f"structured log — {report['count']} record(s), "
           f"verifiable={report['verifiable']}"]
    for r in report["records"]:
        cap = r["authorized_by"][:8] if r["authorized_by"] else "—"
        cell = r["cell"][:10] if r["cell"] else "—"
        out.append(f"  [{r['level']:<7}] e{r['seq']:<3} {r['verb']:<7} "
                   f"by {r['author_name']:<10} cell={cell} via {cap}")
    return out
