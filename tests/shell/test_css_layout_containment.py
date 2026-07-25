"""The Shell chrome must be width-bounded: no identifier can widen the layout.

Weft v0.1 ids are 56-character base32 tokens (``prn_``/``cap_``/``cell_``/``evt_`` +
BLAKE3-256) with NO break opportunity, and they are rendered as monospace text. Before the
re-freeze a principal id was 16 hex characters and fitted the 232px sidebar unaided; at 56
characters it does not, so any element that can hold one MUST be able to shrink or break
below its min-content width. Where it cannot, the intrinsic width propagates outward - flex
row -> sidebar -> grid track -> document - and the document scrolls horizontally at a mobile
viewport (the visual_a11y lane's overflow assertion) while spilling over the main column at
desktop width.

These are static assertions over the real stylesheet, so the invariant is guarded by the
normal pytest gate without needing a browser; the browser lane (tests/browser/specs/
visual_a11y.spec.js) measures the resulting geometry for real.
"""

from __future__ import annotations

import re

import pytest

from decima.kernel.hashing import blob_id
from tests.shell.conftest import FRONTEND

CSS_TEXT = (FRONTEND / "app.css").read_text(encoding="utf-8")

# A monospace glyph advance at font-size 11px, rounded up the way FreeType hinting does on
# Linux (DejaVu Sans Mono is 0.602em -> 6.63px -> 7px integer advance). Used only to decide
# whether a real id can possibly fit the sidebar; never to assert an exact pixel width.
MONO_ADVANCE_PX_AT_11 = 7
# .sidebar is a 232px grid track: minus 12px padding either side, minus the sign-out control
# (~70px) and the 8px flex gap, this is what is left for the principal id.
PRINCIPAL_BUDGET_PX = 232 - 24 - 70 - 8


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.DOTALL)


def _rules(css: str) -> list[tuple[str, dict[str, str]]]:
    """Every style rule as ``(selector_list, declarations)``, @media blocks included.

    A tiny brace scanner rather than a regex: the stylesheet nests style rules inside
    ``@media`` blocks, which a flat ``[^{}]+\\{[^{}]*\\}`` pattern mis-parses.
    """
    out: list[tuple[str, dict[str, str]]] = []
    text = _strip_comments(css)
    prelude = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "{":
            selector = " ".join(prelude.split())
            prelude = ""
            if selector.startswith("@"):
                i += 1  # descend into the at-rule; its inner rules are collected as usual
                continue
            depth, start = 1, i + 1
            i += 1
            while i < len(text) and depth:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            body = text[start : i - 1]
            decls: dict[str, str] = {}
            for part in body.split(";"):
                if ":" not in part:
                    continue
                prop, _, value = part.partition(":")
                decls[prop.strip().lower()] = " ".join(value.split()).lower()
            out.append((selector, decls))
            continue
        if ch == "}":
            prelude = ""
        else:
            prelude += ch
        i += 1
    return out


RULES = _rules(CSS_TEXT)


def _declared(selector: str, prop: str) -> list[str]:
    """Every value declared for ``prop`` by a rule naming ``selector`` in its list."""
    values = []
    for sel_list, decls in RULES:
        parts = [s.strip() for s in sel_list.split(",")]
        if selector in parts and prop in decls:
            values.append(decls[prop])
    return values


def _tracks(value: str) -> list[str]:
    """Split a grid-template-columns value into tracks, keeping minmax()/repeat() whole."""
    tracks, depth, buf = [], 0, ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == " " and depth == 0:
            if buf:
                tracks.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        tracks.append(buf)
    return tracks


def test_stylesheet_parses():
    assert len(RULES) > 40, "CSS scanner found almost no rules - the parser is wrong"
    assert _declared(".app", "grid-template-columns"), "no .app grid declaration found"


@pytest.mark.parametrize("value", _declared(".app", "grid-template-columns"))
def test_app_grid_tracks_cannot_be_widened_by_content(value):
    """Every .app track must have an explicit minimum.

    A bare ``1fr`` is ``minmax(auto, 1fr)``: its minimum is the widest item's min-content
    width, so a single 56-char id makes the track - and the document - wider than the
    viewport. ``minmax(0, 1fr)`` (or a fixed length) lets the viewport decide instead.

    Scoped to ``.app`` on purpose: it is the one grid whose tracks span the viewport. The
    ``.fields`` grid is exempt because its ``dd`` breaks inside tokens (``word-break``), so
    its ``1fr`` minimum collapses to a single glyph.
    """
    for track in _tracks(value):
        assert track not in {"1fr", "auto", "min-content", "max-content"}, (
            f"unbounded grid track {track!r} in `.app {{ grid-template-columns: {value} }}` - "
            "content can widen it past the viewport; use minmax(0, 1fr)"
        )


def test_sidebar_never_imposes_its_min_content_on_the_track():
    assert "0" in _declared(".sidebar", "min-width"), (
        ".sidebar must declare min-width: 0 - as a grid item its automatic minimum size is "
        "its min-content width, which an id-bearing row can push past the viewport"
    )


def test_principal_truncates_instead_of_growing_the_row():
    for prop, expected in (
        ("min-width", "0"),
        ("overflow", "hidden"),
        ("text-overflow", "ellipsis"),
        ("white-space", "nowrap"),
    ):
        assert expected in _declared(".principal", prop), (
            f".principal must declare {prop}: {expected} - without min-width:0 the flex item "
            "refuses to shrink below the full id width and the ellipsis never applies"
        )


@pytest.mark.parametrize("selector", [".tl-author", ".tl-auth", ".approval-effect"])
def test_id_bearing_text_may_break_inside_a_token(selector):
    """These render API-supplied id-shaped tokens outside the wrapping ``.fields`` grid.

    ``overflow-wrap: anywhere`` and not ``break-word``: only ``anywhere`` also shrinks the
    element's min-content contribution, which is what keeps the token out of the enclosing
    flex/grid minimums.
    """
    assert "anywhere" in _declared(selector, "overflow-wrap"), (
        f"{selector} renders unbreakable id text and must declare overflow-wrap: anywhere"
    )


@pytest.mark.parametrize("selector", ["html", "body", ":root", ".app", "html, body"])
def test_overflow_is_never_merely_masked(selector):
    """Guard the guard: hiding horizontal overflow would silence the visual_a11y assertion
    (``documentElement.scrollWidth``) while the content stayed unreachable off-screen."""
    for prop in ("overflow", "overflow-x"):
        for value in _declared(selector, prop):
            assert value.split()[0] not in {"hidden", "clip"}, (
                f"{selector} sets {prop}: {value} - that masks horizontal overflow instead of "
                "containing it; fix the element that is too wide"
            )


def test_real_principal_id_cannot_fit_the_sidebar_unaided():
    """The protocol-side reason the truncation above is load-bearing, stated in numbers.

    Conditional on purpose: if ids ever get shorter than the sidebar budget the containment
    is merely belt-and-braces, and this test should not start failing for that.
    """
    pid = blob_id(b"operator", kind="principal")
    if len(pid) * MONO_ADVANCE_PX_AT_11 > PRINCIPAL_BUDGET_PX:
        assert "0" in _declared(".principal", "min-width"), (
            f"a real principal id is {len(pid)} chars (~{len(pid) * MONO_ADVANCE_PX_AT_11}px "
            f"at 11px monospace) against a ~{PRINCIPAL_BUDGET_PX}px budget: it MUST truncate"
        )
