"""Journal / diary — PRIVATE entries as DATA, never instruction, never recalled.

A journal entry is the most personal thing the agent can hold: a private,
reflective note that must (a) never be obeyed as an instruction and (b) never
leak into a general recall. We mirror HEALTH1's triple-layer privacy
(Codex MEMORY_ARCHITECTURE §5; CAPABILITY_MAP Part B) so the guarantee is
STRUCTURAL, three ways at once:

  - **own Cell type.** Each entry is a `journal_entry` Cell — a type that is NOT
    in memory's recall taxonomy (CLAIM/EPISODIC/.../GOVERNANCE), so a general
    `memory.recall(...)` over those types cannot even consider it.
  - **private scope.** Every entry carries a per-author private `scope`
    (`journal:private:<author>`); `entries` filters by it, so an out-of-scope
    read returns nothing.
  - **non-recallable + not instruction-eligible.** `recallable=False` and
    `instruction_eligible=False` are stamped on the Cell — even a retriever that
    did look at `journal_entry` Cells would skip them, and the brain may never
    act on a diary entry as a command.

Provenance lives on the Weft: each entry asserts a `supported_by` evidence edge
to the author's own utterance/identity that grounded it (WEFT §4/Law 4). `reflect`
folds the recorded entries into a deterministic INT digest (counts by mood/tag,
entry count, recent) — a read-only fold (WEFT §4/§7: ints, never a float), not a
new authority.

This module OWNS only heartbeat/decima/journal.py and composes the PUBLIC model
API (`model.assert_content`/`assert_edge`); it adds no kernel code.
"""
from __future__ import annotations

from decima import model
from decima.hashing import content_id, nfc

JOURNAL_ENTRY = "journal_entry"
# A private scope keyed to the author. General recall is scoped to a realm
# (e.g. "realm:default"); a journal scope never collides with it, so even a
# scope-blind query for general memory cannot name this scope by accident.
SCOPE_PREFIX = "journal:private"


def journal_scope(author: str) -> str:
    """The private scope an author's entries live in — never a general realm scope."""
    return f"{SCOPE_PREFIX}:{nfc(author)}"


def _entry_id(author: str, text: str, seq: int) -> str:
    return content_id({"journal": nfc(author), "text": nfc(text), "seq": int(seq)})


def entry(k, text: str, *, mood: str | None = None, tags=None,
          author: str | None = None, evidence_src: str | None = None) -> str:
    """Record one private journal entry and return its Cell id.

    The entry is stamped `instruction_eligible=False` and `recallable=False` in a
    private `scope`, so it is DATA that general recall cannot surface and the brain
    can never obey. `mood` is an optional label; `tags` an optional list of labels —
    both normalized to NFC. A `supported_by` edge grounds it on the Weft
    (provenance); `evidence_src` defaults to the author's own utterance/identity
    when no external receipt is given.
    """
    if not isinstance(text, str):
        raise TypeError("journal entry text must be a str")
    text = nfc(text)
    author = author or k.decima_agent_id
    scope = journal_scope(author)
    mood = nfc(mood) if mood is not None else None
    norm_tags = sorted({nfc(t) for t in tags}) if tags else []
    seq = k.weft.count() + 1                    # deterministic, log-positioned id
    cid = _entry_id(author, text, seq)
    content = {
        "text": text,
        "mood": mood,
        "tags": norm_tags,
        "scope": scope,
        "seq": seq,
        # the four permissions (Codex §5): DATA only — never an instruction,
        # never surfaced by general recall.
        "instruction_eligible": False,
        "recallable": False,
        "citable": False,
    }
    model.assert_content(k.weft, author, cid, JOURNAL_ENTRY, content)
    # provenance on the Weft: ground the entry in evidence (Law 4).
    model.assert_edge(k.weft, author, cid, "supported_by", evidence_src or author)
    return cid


def _entries(k, author: str | None = None) -> list:
    """All journal Cells for `author`, scope-filtered, in record (seq) order."""
    author = author or k.decima_agent_id
    scope = journal_scope(author)
    out = [c for c in k.weave().of_type(JOURNAL_ENTRY)
           if c.content.get("scope") == scope]      # authorization-first filter
    return sorted(out, key=lambda c: int(c.content.get("seq", 0)))


def entries(k, author: str | None = None) -> list:
    """The recorded entries as DATA dicts (scope-filtered, in order)."""
    return [{
        "id": c.id,
        "text": c.content["text"],
        "mood": c.content.get("mood"),
        "tags": list(c.content.get("tags", [])),
        "scope": c.content["scope"],
    } for c in _entries(k, author)]


def reflect(k, author: str | None = None) -> dict:
    """A deterministic INT digest over the entries: counts by mood/tag, the entry
    count, and the recent entries' texts.

    `by_mood` / `by_tag` map each label to an int count; `count` is the number of
    entries; `recent` lists the texts of the last few entries (newest last). All
    figures are ints — a read-only fold (WEFT §4/§7: never a float).
    """
    cells = _entries(k, author)
    by_mood: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    for c in cells:
        mood = c.content.get("mood")
        if mood is not None:
            by_mood[mood] = by_mood.get(mood, 0) + 1
        for t in c.content.get("tags", []):
            by_tag[t] = by_tag.get(t, 0) + 1
    recent = [c.content["text"] for c in cells[-3:]]
    return {
        "count": len(cells),
        "by_mood": by_mood,
        "by_tag": by_tag,
        "moods": len(by_mood),
        "tags": len(by_tag),
        "recent": recent,
    }
