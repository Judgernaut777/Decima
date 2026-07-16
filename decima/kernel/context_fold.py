"""Context-fold — Law 5 (state is a fold) applied to the LLM context window.

The agent/brain loop grows a message history without bound; the LLM's context
window does not. The usual fix is to *summarize* old turns with another LLM call —
but that is non-deterministic (same input → different output), it silently mangles
exact identifiers (a UUID becomes "the user's id"), and it breaks prompt-cache
reuse because the folded prefix is never byte-identical twice.

This module FOLDS the history instead — structurally, with ZERO LLM calls:

  - fold-don't-summarize: old turns are paged out into compact one-line SKELETONS
    (role + a truncated/structural digest), the last `keep_recent` turns stay at
    FULL fidelity. No model is asked to paraphrase anything.
  - zero-LLM: pure stdlib, no network, no inference.
  - deterministic, byte-identical output: identical input → identical bytes. No
    wall-clock, no randomness, no set-iteration leakage (everything is sorted /
    canonically encoded). This is exactly Decima's fold discipline (weave.py):
    a projection is a pure function of its input.
  - exact identifiers preserved: before an old turn is skeletonized we scan it for
    identifier-like tokens (UUIDs, absolute paths, URLs, ports, long hex ids) and
    keep them VERBATIM in a "coordinate closet" (a dict) AND inline in the skeleton,
    so folding never loses an exact coordinate the model may still need to cite.
  - cache-warm anchor: `frozen_prefix` is the canonical serialization of the
    folded (paged-out) prefix — byte-identical across calls for identical inputs, so
    a prompt cache keyed on it stays warm.

This is Law 5 for the context window: the window you send is a *fold* of the
history, recomputed deterministically, never a second lossy source of truth.
Pure stdlib. Deterministic. No Kernel required — operate on plain message dicts.
"""

import re
from typing import Any

from decima.kernel.hashing import canonical, nfc

# ── coordinate-closet extractor ────────────────────────────────────────────────
# Identifier-like tokens we must never lose when a turn is folded. Each pattern is
# matched independently and the union is taken — over-preservation is safe (an exact
# id kept twice costs a few bytes), losing an exact id is not. Sorted+unique at the
# end makes the result deterministic regardless of match order.
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_URL = re.compile(r"\b(?:https?|ftp)://[^\s<>\"')\]]+")
# An absolute path with ≥2 segments (so a lone "/x" — e.g. a URL's path — is not
# grabbed as a false path, while /etc/hosts and /a/b/c are).
_PATH = re.compile(r"(?:/[A-Za-z0-9_.\-]+){2,}/?")
# A bare hex id of 8+ chars (git shas, content-ids, tokens).
_HEX = re.compile(r"\b[0-9a-fA-F]{8,}\b")
# A port: 2–5 digits following a colon (host:port, url:port).
_PORT = re.compile(r"(?<=:)\d{2,5}\b")

_PATTERNS = (_UUID, _URL, _PATH, _HEX, _PORT)


def extract_identifiers(text: object) -> list[str]:
    """The coordinate-closet extractor: every identifier-like token in `text`,
    deterministic — sorted and unique. Recognizes UUIDs, absolute file paths, URLs,
    ports, and long (≥8-char) hex ids. Non-string input yields []."""
    if not isinstance(text, str):
        return []
    found: set[str] = set()
    for pat in _PATTERNS:
        for m in pat.findall(text):
            if m:
                found.add(m)
    return sorted(found)


# ── message → text ─────────────────────────────────────────────────────────────
def _message_text(msg: dict[str, Any]) -> str:
    """The textual payload of a message, tolerant of a `tool_calls`/`tool` shape.
    Deterministic: any structured part is canonically (sorted-key) encoded so the
    same message always yields the same text."""
    if not isinstance(msg, dict):
        return ""
    parts: list[str] = []
    content = msg.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif content is not None:
        parts.append(canonical(content).decode("utf-8"))
    for key in ("tool", "name"):
        v = msg.get(key)
        if isinstance(v, str) and v:
            parts.append(v)
    tc = msg.get("tool_calls")
    if tc is not None:
        parts.append(canonical(tc).decode("utf-8"))
    return " ".join(parts)


def _digest(text: str, *, width: int = 60) -> str:
    """A compact structural digest: whitespace collapsed, truncated to `width` with
    an ellipsis + the original char count so the skeleton is one bounded line."""
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    return f"{flat[:width]}… (+{len(flat) - width}c)"


def _skeleton(msg: dict[str, Any]) -> dict[str, Any]:
    """Page an old turn out into a compact one-line skeleton — role + digest, with
    every exact identifier kept INLINE (⟨ids: …⟩) so no coordinate is lost even in
    the folded message body itself. Deterministic (ids are sorted)."""
    role = str(msg.get("role", "?")) if isinstance(msg, dict) else "?"
    text = nfc(_message_text(msg))
    ids = extract_identifiers(text)
    body = _digest(text)
    if ids:
        body = f"{body} ⟨ids: {' '.join(ids)}⟩"
    return {"role": role, "content": f"{role}: {body}", "folded": True}


def _fold_at(
    history: list[dict[str, Any]], keep: int
) -> tuple[list[dict[str, Any]], dict[str, list[int]], list[dict[str, Any]]]:
    """Fold `history` keeping the last `keep` turns full and skeletonizing the rest.
    Returns (messages, coordinates, prefix_skeletons). Pure — no side effects."""
    n = len(history)
    keep = max(0, min(keep, n))
    split = n - keep
    prefix = [_skeleton(history[i]) for i in range(split)]
    kept = [dict(history[i]) for i in range(split, n)]
    # Coordinate closet: identifier → sorted list of the folded-message indices it
    # appeared in. Built from the (sorted) folded range in index order → deterministic.
    coordinates: dict[str, list[int]] = {}
    for i in range(split):
        for ident in extract_identifiers(nfc(_message_text(history[i]))):
            coordinates.setdefault(ident, [])
            if i not in coordinates[ident]:
                coordinates[ident].append(i)
    return prefix + kept, coordinates, prefix


def fold(
    history: list[dict[str, Any]], *, keep_recent: int, budget: int | None = None
) -> dict[str, Any]:
    """Fold a message history: keep the last `keep_recent` turns at FULL fidelity and
    skeletonize everything older into one compact line each (fold, don't summarize —
    zero LLM). Exact identifiers in the folded turns are preserved verbatim, both in a
    "coordinate closet" (`coordinates`) and inline in each skeleton.

    If `budget` (a max total char count of the produced messages) is given and the
    fold still exceeds it, MORE recent turns are folded (keep_recent is reduced,
    deterministically) until it fits or nothing is left to fold.

    Returns {"messages": [...], "stats": {...}, "coordinates": {...}}. `stats` are
    ints only: input_count, folded_count, kept_count, char_before, char_after.
    Deterministic: identical input → byte-identical output."""
    n = len(history)
    char_before = sum(len(_message_text(m)) for m in history)
    keep = max(0, min(keep_recent, n))
    messages, coordinates, _ = _fold_at(history, keep)
    char_after = sum(len(_message_text(m)) for m in messages)
    while budget is not None and char_after > budget and keep > 0:
        keep -= 1
        messages, coordinates, _ = _fold_at(history, keep)
        char_after = sum(len(_message_text(m)) for m in messages)
    return {
        "messages": messages,
        "coordinates": coordinates,
        "stats": {
            "input_count": int(n),
            "folded_count": int(n - keep),
            "kept_count": int(keep),
            "char_before": int(char_before),
            "char_after": int(char_after),
        },
    }


def frozen_prefix(history: list[dict[str, Any]], *, keep_recent: int) -> str:
    """The cache-warm anchor: the deterministic, canonical serialization of the folded
    (paged-out) prefix — the skeletons of every turn older than the last `keep_recent`,
    plus their coordinate closet. Byte-identical for identical inputs (so a prompt
    cache keyed on it stays warm across calls). Uses sorted-key canonical JSON."""
    n = len(history)
    keep = max(0, min(keep_recent, n))
    _, coordinates, prefix = _fold_at(history, keep)
    return canonical({"prefix": prefix, "coordinates": coordinates}).decode("utf-8")
