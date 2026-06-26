"""ACCESS1 — accessibility as data: a11y audit findings + output-shaping projections.

A sibling of REVIEW1 (`review.py`). Where a review runs lint-style rules over an
UNTRUSTED code Cell, an *accessibility audit* runs a11y heuristics over a doc/UI
content Cell and the *shaper* derives deterministic, output-shaped projections of it.

THE LAW (mirrors REVIEW1 / the projection-layer thesis):
  - The content under audit is DATA on the Weft — a `content` Cell stored verbatim
    with `instruction_eligible=False`; it is only ever READ (regex/substring/structural
    heuristics), NEVER executed, imported, or treated as an instruction because a rule
    matched. axe-core-style heuristics here are pure text/structure scans.
  - An `a11y_finding` Cell mirrors REVIEW1's `review_finding` shape exactly: `severity`,
    the matched `rule`, a `locus` (1-based line; 0 = document-level), an `excerpt`, and
    a `found_in` provenance edge back to the content Cell — so a11y findings index into
    the same tamper-evident SIEM as review/detection findings.
  - Output-shaping (alt-text, captions, screen-reader / plain-text rendering, captions
    track) is a PROJECTION: a deterministic transform of the content. `shape()` derives,
    it does not mutate the source; the same (content, mode) always yields the same bytes.
  - Ints, never floats (PROFILE.md): the contrast ratio and a11y score are integers.

Heuristics (pure reads over the content's lines — no execution):
  - `missing-alt-text`  — an <img …> with no (or empty) alt="…"            (high)
  - `low-contrast`      — a `contrast: N` marker whose int ratio < 4 (×1)   (high)
  - `missing-label`     — an <input …> / <select …> with no label/aria-label (high)
  - `missing-headings`  — a document with body text but no heading at all   (medium)
  - `vague-link-text`   — a link reading "click here" / "read more" / "here" (low)

Public API only — composes model/weave/hashing; no kernel or other-module edits.
"""
from __future__ import annotations

import re

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

CONTENT = "content"                 # the doc/UI under audit — DATA, instruction-ineligible
A11Y_FINDING = "a11y_finding"       # mirrors REVIEW1's review_finding
FOUND_IN = "found_in"               # a11y_finding → content  (provenance, mirrors REVIEW1)
MIN_CONTRAST = 4                    # WCAG-ish minimum (int) ratio; below this is low-contrast


class Rule:
    """An a11y heuristic. `scan(lines)` yields (locus, excerpt) per hit — a PURE read
    over the content's text. `locus` is 1-based; 0 means document-level (no single line)."""

    def __init__(self, name: str, severity: str, scan):
        self.name, self.severity, self.scan = name, severity, scan


def _excerpt(s: str, limit: int = 80) -> str:
    s = s.strip()
    return s if len(s) <= limit else s[:limit] + "…"


# --- the heuristics: each reads `lines` (list[str]) and never executes them -----------

_IMG = re.compile(r"(?i)<img\b")
_ALT = re.compile(r"""(?i)\balt\s*=\s*['"][^'"]+['"]""")     # non-empty alt
_FIELD = re.compile(r"(?i)<(input|select|textarea)\b")
_LABELLED = re.compile(r"""(?i)\b(aria-label|aria-labelledby|title|id)\s*=\s*['"][^'"]+['"]""")
_HEADING = re.compile(r"(?i)<h[1-6]\b|^#{1,6}\s")
_CONTRAST = re.compile(r"(?i)\bcontrast\s*[:=]\s*(\d+)\b")
_VAGUE = re.compile(r"(?i)>\s*(click here|read more|here|more|link)\s*<"
                    r"|\[(click here|read more|here|more|link)\]")
_TEXT = re.compile(r"\w")


def _scan_missing_alt(lines):
    for i, ln in enumerate(lines, 1):
        if _IMG.search(ln) and not _ALT.search(ln):
            yield i, _excerpt(ln)


def _scan_low_contrast(lines):
    for i, ln in enumerate(lines, 1):
        m = _CONTRAST.search(ln)
        if m and int(m.group(1)) < MIN_CONTRAST:
            yield i, _excerpt(f"ratio={int(m.group(1))} (<{MIN_CONTRAST}): {ln}")


def _scan_missing_label(lines):
    for i, ln in enumerate(lines, 1):
        if _FIELD.search(ln) and not _LABELLED.search(ln):
            yield i, _excerpt(ln)


def _scan_vague_link(lines):
    for i, ln in enumerate(lines, 1):
        if _VAGUE.search(ln):
            yield i, _excerpt(ln)


def _scan_missing_headings(lines):
    """Document-level: body text exists but no heading anywhere ⇒ one locus-0 finding."""
    has_text = any(_TEXT.search(ln) for ln in lines)
    has_heading = any(_HEADING.search(ln) for ln in lines)
    if has_text and not has_heading:
        yield 0, "document has body text but no headings"


DEFAULT_RULES = [
    Rule("missing-alt-text", "high", _scan_missing_alt),
    Rule("low-contrast", "high", _scan_low_contrast),
    Rule("missing-label", "high", _scan_missing_label),
    Rule("missing-headings", "medium", _scan_missing_headings),
    Rule("vague-link-text", "low", _scan_vague_link),
]

# Severity weights for the integer a11y score (all ints, no floats).
_WEIGHT = {"high": 10, "medium": 4, "low": 1}
MAX_SCORE = 100


def content_id_for(locus: str) -> str:
    """Content-address a doc/UI Cell by its locus/path (one stable identity per doc)."""
    return content_id({"a11y_content": nfc(locus)})


def store_content(k, locus: str, content: str, *, author: str | None = None) -> str:
    """Write the doc/UI under audit onto the Weft as a `content` Cell — DATA.

    `instruction_eligible=False`, always: content being audited or shaped is text to
    read and project, never an order to obey. Stored verbatim; nothing here executes it."""
    author = author or k.root.id
    cid = content_id_for(locus)
    assert_content(k.weft, author, cid, CONTENT, {
        "locus": nfc(locus),
        "body": content,
        "trusted": False,
        "instruction_eligible": False,   # the law: audited/shaped content is DATA
    })
    return cid


def audit(k, locus: str, content: str, *, rules=None, author: str | None = None) -> list:
    """Store `content` as a DATA Cell, run a11y heuristics over it, and emit an
    `a11y_finding` Cell per hit — each with a `found_in` provenance edge to the content
    Cell, mirroring REVIEW1's finding shape. The content is only READ; a clean doc yields
    no findings. Returns the list of finding cell ids."""
    author = author or k.reckoner.id
    cid = store_content(k, locus, content, author=author)
    lines = content.split("\n")
    rules = DEFAULT_RULES if rules is None else rules

    findings = []
    for rule in rules:
        for locus_no, excerpt in rule.scan(lines):
            fid = content_id({"a11y_finding": cid, "rule": rule.name, "locus": locus_no})
            assert_content(k.weft, author, fid, A11Y_FINDING, {
                "rule": rule.name,
                "severity": rule.severity,
                "source": cid,
                "locus": locus_no,
                "path": nfc(locus),
                "excerpt": excerpt,
            })
            assert_edge(k.weft, author, fid, FOUND_IN, cid)
            findings.append(fid)
    return findings


def summary(k, locus: str) -> dict:
    """The a11y findings for a doc, grouped by severity → list of finding Cells (sorted by
    locus). Reads the Weave; a doc with no findings (or never audited) yields {}."""
    cid = content_id_for(locus)
    w = k.weave()
    grouped: dict[str, list] = {}
    for c in w.of_type(A11Y_FINDING):
        if c.content.get("source") == cid:
            grouped.setdefault(c.content["severity"], []).append(c)
    for sev in grouped:
        grouped[sev].sort(key=lambda c: c.content.get("locus", 0))
    return grouped


# --- output-shaping: deterministic projections over the content -----------------------

_ALT_VAL = re.compile(r"""(?i)\balt\s*=\s*['"]([^'"]*)['"]""")
_TAG = re.compile(r"<[^>]+>")
_SRC = re.compile(r"""(?i)\bsrc\s*=\s*['"]([^'"]+)['"]""")


def _basename(src: str) -> str:
    name = src.rstrip("/").split("/")[-1]
    return name.split(".")[0].replace("-", " ").replace("_", " ").strip() or "image"


def _alt_text_projection(content: str) -> str:
    """Deterministically backfill alt-text on <img> tags that lack it, derived from the
    src filename. Existing non-empty alt is preserved verbatim."""
    out = []
    for ln in content.split("\n"):
        if _IMG.search(ln) and not _ALT.search(ln):
            m = _SRC.search(ln)
            derived = _basename(m.group(1)) if m else "image"
            ln = re.sub(r"(?i)(<img\b)", rf'\1 alt="{derived}"', ln, count=1)
        out.append(ln)
    return "\n".join(out)


def _screen_reader_projection(content: str) -> str:
    """A plain-text / screen-reader rendering: strip tags, surface each image as its
    alt-text (or a derived description), drop the rest of the markup. Deterministic."""
    out = []
    for ln in content.split("\n"):
        if _IMG.search(ln):
            m = _ALT_VAL.search(ln)
            if m and m.group(1).strip():
                out.append(f"[image: {m.group(1).strip()}]")
            else:
                s = _SRC.search(ln)
                out.append(f"[image: {_basename(s.group(1)) if s else 'image'}]")
            continue
        text = _TAG.sub("", ln).strip()
        if text:
            out.append(text)
    return "\n".join(out)


def _captions_projection(content: str) -> str:
    """A minimal captions track: one cue per non-empty text line of the plain rendering,
    numbered deterministically. (A WebVTT-ish skeleton; integer cue indices.)"""
    plain = [ln for ln in _screen_reader_projection(content).split("\n") if ln.strip()]
    cues = ["WEBVTT", ""]
    for i, ln in enumerate(plain, 1):
        cues.append(str(i))
        cues.append(ln)
        cues.append("")
    return "\n".join(cues).rstrip("\n")


_MODES = {
    "alt-text": _alt_text_projection,
    "screen-reader": _screen_reader_projection,
    "captions": _captions_projection,
}


def shape(k, locus: str, content: str, *, mode: str, author: str | None = None) -> dict:
    """Output-shaping PROJECTION (the projection-layer law): deterministically transform
    `content` into an accessible rendering and store the result as a derived `content`
    Cell carrying a `found_in` provenance edge back to the source. Does NOT mutate the
    source. `mode` ∈ {alt-text, screen-reader, captions}. Returns
    {mode, projection, cell, source}; the same (content, mode) always yields identical
    `projection` bytes."""
    if mode not in _MODES:
        raise ValueError(f"unknown shaping mode {mode!r}; expected one of {sorted(_MODES)}")
    author = author or k.reckoner.id
    src = store_content(k, locus, content, author=author)
    projection = _MODES[mode](content)

    pid = content_id({"a11y_projection": src, "mode": mode})
    assert_content(k.weft, author, pid, CONTENT, {
        "locus": nfc(f"{locus}#{mode}"),
        "body": projection,
        "mode": mode,
        "source": src,
        "shaped": True,
        "trusted": False,
        "instruction_eligible": False,   # a derived projection is still DATA
    })
    assert_edge(k.weft, author, pid, FOUND_IN, src)
    return {"mode": mode, "projection": projection, "cell": pid, "source": src}


def score(k, locus: str, content: str, *, author: str | None = None) -> int:
    """An INTEGER a11y score in [0, 100] for `content`: start at 100 and subtract a
    severity-weighted penalty per finding, clamped at 0. A clean doc scores 100.
    Deterministic (audit is a pure read); ints only, never floats."""
    findings = audit(k, locus, content, author=author)
    w = k.weave()
    penalty = sum(_WEIGHT.get(w.get(f).content["severity"], 1) for f in findings)
    return max(0, MAX_SCORE - penalty)
