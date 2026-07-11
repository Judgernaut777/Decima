"""Secure-by-construction: the Shell's own JS never evals or injects untrusted markup.

Grep every frontend script for the forbidden sinks. The renderer builds DOM via
createElement + textContent (dom.js), so there is no path that turns a model/imported
string into markup or code:

  * no eval( / new Function(          — no dynamic code execution
  * no .innerHTML= / .outerHTML=      — no markup injection sink
  * no document.write                 — no markup injection sink
  * no insertAdjacentHTML             — no markup injection sink
  * no on*="..." inline handlers      — handlers are attached in code only
"""

from __future__ import annotations

import re

import pytest

from tests.shell.conftest import FRONTEND

JS_FILES = sorted(FRONTEND.rglob("*.js"))

FORBIDDEN = [
    (re.compile(r"\beval\s*\("), "eval("),
    (re.compile(r"new\s+Function\s*\("), "new Function("),
    (re.compile(r"\.innerHTML\s*="), ".innerHTML="),
    (re.compile(r"\.outerHTML\s*="), ".outerHTML="),
    (re.compile(r"document\.write\s*\("), "document.write("),
    (re.compile(r"insertAdjacentHTML"), "insertAdjacentHTML"),
    (re.compile(r"\bsetTimeout\s*\(\s*['\"]"), "setTimeout(string)"),
]


def test_js_files_present():
    assert JS_FILES, "no frontend JS files found"


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_no_forbidden_sink(path):
    src = path.read_text(encoding="utf-8")
    hits = [name for rx, name in FORBIDDEN if rx.search(src)]
    assert not hits, f"{path.name} contains forbidden sink(s): {hits}"


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_no_inline_event_handler_strings(path):
    # No 'onclick="..."'-style attributes assembled in JS; handlers use addEventListener.
    src = path.read_text(encoding="utf-8")
    assert not re.search(r'(?<![A-Za-z])on[a-z]+\s*=\s*["\']', src), (
        f"{path.name} builds inline handler"
    )


def test_html_has_no_inline_handlers_or_scripts():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'\son\w+\s*=\s*["\']', html), "index.html has an inline handler"
    # Every <script> must have a src (no inline script blocks — CSP forbids them anyway).
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag, f"inline <script> block not allowed: {tag}"
