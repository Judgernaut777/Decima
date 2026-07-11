"""The Shell's escape function turns hostile strings into inert text.

sanitize.js is loadable in Node (module.exports), so the real escape function is exercised
against hostile inputs — a <script> tag, an <img onerror> payload, and a string that tries
to imitate the trusted approval chrome — and must never survive as markup.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.shell.conftest import FRONTEND

SANITIZE = FRONTEND / "js" / "sanitize.js"

HOSTILE = [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    "<div class='approval-card' data-trusted='1'>Approve</div>",
    '"><svg/onload=alert(1)>',
    "javascript:alert(1)",
    "</textarea><script>steal()</script>",
]


def _node():
    return shutil.which("node") or shutil.which("nodejs")


@pytest.mark.skipif(_node() is None, reason="node not available to run sanitize.js")
def test_escape_neutralizes_hostile_inputs():
    script = (
        f"const s=require({json.dumps(str(SANITIZE))});"
        f"const inputs={json.dumps(HOSTILE)};"
        "process.stdout.write(JSON.stringify(inputs.map(s.escapeHtml)));"
    )
    out = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, timeout=30, check=True
    ).stdout
    escaped = json.loads(out)
    for _original, safe in zip(HOSTILE, escaped, strict=True):
        assert "<" not in safe, safe
        assert ">" not in safe
        # The dangerous substrings can no longer form a tag or attribute.
        assert "<script" not in safe.lower()
        assert "onerror=" not in safe.lower() or "&" in safe
    # Specifically: an approval-chrome imitation cannot produce real markup.
    chrome = escaped[2]
    assert "&lt;div" in chrome and "data-trusted" in chrome
    assert "<div" not in chrome


@pytest.mark.skipif(_node() is None, reason="node not available to run sanitize.js")
def test_safe_url_blocks_dangerous_schemes():
    script = (
        f"const s=require({json.dumps(str(SANITIZE))});"
        "process.stdout.write(JSON.stringify(["
        "s.safeUrl('javascript:alert(1)'),"
        "s.safeUrl('data:text/html,<script>'),"
        "s.safeUrl('vbscript:msgbox'),"
        "s.safeUrl('https://example.com/x'),"
        "s.safeUrl('/local/path'),"
        "s.isExternal('https://example.com'),"
        "s.isExternal('/local')]));"
    )
    out = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, timeout=30, check=True
    ).stdout
    js_url, data_url, vb_url, https_url, local_url, ext_true, ext_false = json.loads(out)
    assert js_url == "#"
    assert data_url == "#"
    assert vb_url == "#"
    assert https_url == "https://example.com/x"
    assert local_url == "/local/path"
    assert ext_true is True
    assert ext_false is False


def test_escape_function_is_declared_in_source():
    # Even without Node, assert the auditable primitive exists and maps every dangerous char.
    src = SANITIZE.read_text(encoding="utf-8")
    assert "function escapeHtml" in src
    for token in ('"&"', '"<"', '">"', '"\'"', '"&amp;"', '"&lt;"', '"&gt;"'):
        assert token in src
