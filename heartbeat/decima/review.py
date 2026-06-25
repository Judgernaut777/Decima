"""REVIEW1 — code review as data: lint-style findings on the signed Weft.

A sibling of DET1 (`detection.py`). Where a detection is a test-gated SKILL run over
observation Cells, a *review* is a fixed set of lint-style rules run over a CODE Cell.
The shapes mirror DET1 deliberately: a `review_finding` Cell carries `severity`, the
matched `rule`, a `line`/`loc`, and an `excerpt`, with a `found_in` provenance edge to
the code Cell it was raised against — exactly the `finding`/`found_in` shape `detect()`
emits, so review findings index into the same tamper-evident SIEM.

THE LAW (the whole point): the code under review is UNTRUSTED DATA. It is stored on the
Weft as a `code` Cell with `instruction_eligible=False` (the recall-vs-instruct law from
doc.py / memory.py) and is only ever READ — scanned with regex/substring heuristics. It
is NEVER `eval`'d, `exec`'d, imported, or otherwise executed, and never treated as an
instruction just because a rule matched a line of it.

Rules are pure heuristics (regex / substring) over text — no execution:
  - `eval-call`        — a call to `eval(`  (high)
  - `exec-call`        — a call to `exec(`  (high)
  - `hardcoded-secret` — an inline password/secret/api-key/token assignment (high)
  - `todo-comment`     — a `TODO` / `FIXME` marker (low)
  - `missing-docstring`— a `def`/`class` whose next non-blank line is not a docstring (medium)
  - `overlong-line`    — a line longer than 100 columns (low)

Public API only — composes model/weave/hashing; no kernel or other-module edits.
"""
from __future__ import annotations

import re

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

CODE = "code"
REVIEW_FINDING = "review_finding"
FOUND_IN = "found_in"            # review_finding → code  (provenance, mirrors DET1)
MAX_LINE = 100                   # overlong-line threshold (columns)


class Rule:
    """A lint heuristic. `scan(lines)` yields (line_no, excerpt) per hit — a PURE read
    over the code's text. `line_no` is 1-based; 0 means file-level (no single line)."""

    def __init__(self, name: str, severity: str, scan):
        self.name, self.severity, self.scan = name, severity, scan


def _excerpt(s: str, limit: int = 80) -> str:
    s = s.strip()
    return s if len(s) <= limit else s[:limit] + "…"


# --- the heuristics: each reads `lines` (list[str]) and never executes them ----------

_SECRET = re.compile(
    r"""(?ix) (password|passwd|secret|api[_-]?key|token|access[_-]?key)
        \s* [:=] \s* ['"][^'"]+['"]""",
    re.VERBOSE,
)
_TODO = re.compile(r"(?i)\b(TODO|FIXME)\b")
_DEF = re.compile(r"^\s*(def|class)\s+\w")
_TRIPLE = re.compile(r'''^\s*[rRbBuU]?(?:"""|\'\'\'|"|')''')


def _scan_substr(needle: str):
    def scan(lines):
        for i, ln in enumerate(lines, 1):
            if needle in ln:
                yield i, _excerpt(ln)
    return scan


def _scan_regex(rx: re.Pattern):
    def scan(lines):
        for i, ln in enumerate(lines, 1):
            if rx.search(ln):
                yield i, _excerpt(ln)
    return scan


def _scan_overlong(lines):
    for i, ln in enumerate(lines, 1):
        if len(ln) > MAX_LINE:
            yield i, _excerpt(f"len={len(ln)}: {ln}")


def _scan_missing_docstring(lines):
    """A def/class header whose first non-blank body line is not a string literal."""
    for i, ln in enumerate(lines):
        if _DEF.match(ln):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines) or not _TRIPLE.match(lines[j]):
                yield i + 1, _excerpt(ln)


DEFAULT_RULES = [
    Rule("eval-call", "high", _scan_substr("eval(")),
    Rule("exec-call", "high", _scan_substr("exec(")),
    Rule("hardcoded-secret", "high", _scan_regex(_SECRET)),
    Rule("todo-comment", "low", _scan_regex(_TODO)),
    Rule("missing-docstring", "medium", _scan_missing_docstring),
    Rule("overlong-line", "low", _scan_overlong),
]


def code_id(path: str) -> str:
    """Content-address a code Cell by its path (one stable identity per file)."""
    return content_id({"code": nfc(path)})


def store_code(k, path: str, code: str, *, author: str | None = None) -> str:
    """Write the code under review onto the Weft as a `code` Cell — UNTRUSTED DATA.

    `instruction_eligible=False`, always: code being reviewed is text to read, never
    an order to obey. The body is stored verbatim; nothing here executes it."""
    author = author or k.root.id
    cid = code_id(path)
    assert_content(k.weft, author, cid, CODE, {
        "path": nfc(path),
        "body": code,
        "trusted": False,
        "instruction_eligible": False,   # the law: reviewed code is DATA, never executed
    })
    return cid


def review(k, path: str, code: str, *, rules=None, author: str | None = None) -> list:
    """Store `code` as an untrusted `code` Cell, then run lint-style rules over it and
    emit a `review_finding` Cell per hit, each with a `found_in` provenance edge to the
    code Cell. The code is only READ; a clean file yields no findings. Returns the list
    of finding cell ids."""
    author = author or k.reckoner.id
    cid = store_code(k, path, code, author=author)
    lines = code.split("\n")
    rules = DEFAULT_RULES if rules is None else rules

    findings = []
    for rule in rules:
        for loc, excerpt in rule.scan(lines):
            fid = content_id({"review_finding": cid, "rule": rule.name, "loc": loc})
            assert_content(k.weft, author, fid, REVIEW_FINDING, {
                "rule": rule.name,
                "severity": rule.severity,
                "source": cid,
                "path": nfc(path),
                "line": loc,
                "excerpt": excerpt,
            })
            assert_edge(k.weft, author, fid, FOUND_IN, cid)
            findings.append(fid)
    return findings


def summary(k, path: str) -> dict:
    """The findings for a file, grouped by severity → list of finding Cells (sorted by
    line). Reads the Weave; a file with no findings (or never reviewed) yields {}."""
    cid = code_id(path)
    w = k.weave()
    grouped: dict[str, list] = {}
    for c in w.of_type(REVIEW_FINDING):
        if c.content.get("source") == cid:
            grouped.setdefault(c.content["severity"], []).append(c)
    for sev in grouped:
        grouped[sev].sort(key=lambda c: c.content.get("line", 0))
    return grouped
