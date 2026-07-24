"""Architecture guard: no INVOKE effect is forged outside the authorized invoke seam.

INVOKE is the one Weft verb that enacts an effect in the world through a capability.
The product's contract is that every INVOKE is written only by the kernel's authorized
invoke seam — the path that first runs ``capability.verify_proof`` / ``authorize_detail``
and clears the durable human-approval gate (``decima/kernel/inbox.py``). No API, service,
runtime, projection, shell, or worker module may append a raw INVOKE event directly: a
bare ``weft.append(author, INVOKE, ...)`` would land an effect on the Log while bypassing
that ocap spine entirely (P1.3).

This test FAILS THE BUILD if any product module OUTSIDE ``decima/kernel/`` (the TCB, the
only place the single authorized, ``verify_proof``-gated invoke seam belongs) hands the
INVOKE verb to the Weft append seam. The invariant holds today — this pins it so it can
never silently regress. It is a static import/AST guard in the spirit of
``tests/architecture/test_import_boundaries.py``; it adds no authority path and touches
no content-addressed bytes.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PRODUCT = _REPO_ROOT / "decima"
_KERNEL = _PRODUCT / "kernel"

# The effect-enacting verb (decima/kernel/weft.py). Legitimate only through the kernel's
# authorized invoke seam; forged anywhere else it bypasses capability.verify_proof.
_INVOKE = "INVOKE"

# The Weft mutation seam whose 2nd positional (or ``verb=``) argument is the event verb.
# A list's ``.append`` takes a single positional, so it can never occupy that slot — this
# guard therefore cannot mistake an ordinary list append for a Weft INVOKE write.
_APPEND_ATTR = "append"


def _product_files_outside_kernel() -> list[pathlib.Path]:
    """Every product .py file except those in the kernel package (the TCB)."""
    return sorted(p for p in _PRODUCT.rglob("*.py") if _KERNEL not in p.parents)


def _arg_is_invoke(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Name):
        return node.id == _INVOKE
    if isinstance(node, ast.Constant):
        return node.value == _INVOKE
    return False


def _raw_invoke_appends(path: pathlib.Path) -> list[int]:
    """Line numbers of any ``<x>.append(author, INVOKE, ...)`` / ``verb=INVOKE`` call."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != _APPEND_ATTR:
            continue
        # weft.append(author_pid, verb, body, ...) — verb is positional index 1 or verb=.
        verb_arg: ast.expr | None = node.args[1] if len(node.args) > 1 else None
        for kw in node.keywords:
            if kw.arg == "verb":
                verb_arg = kw.value
        if _arg_is_invoke(verb_arg):
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize(
    "path",
    _product_files_outside_kernel(),
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_no_raw_invoke_append_outside_kernel(path: pathlib.Path) -> None:
    hits = _raw_invoke_appends(path)
    assert not hits, (
        f"{path.relative_to(_REPO_ROOT)} appends a raw INVOKE event at line(s) {hits}: an "
        f"INVOKE effect must be written only through the kernel's authorized invoke seam "
        f"(capability.verify_proof + the inbox.py approval gate), never a direct "
        f".append(..., INVOKE, ...) that bypasses authorization (P1.3)"
    )


def test_services_never_append_invoke() -> None:
    """The API/dispatch boundary (decima/services/**) holds the Weft handle; assert it
    never uses it to append a raw INVOKE — the explicit target of P1.3."""
    services = _PRODUCT / "services"
    offenders: dict[str, list[int]] = {}
    for p in sorted(services.rglob("*.py")):
        lines = _raw_invoke_appends(p)
        if lines:
            offenders[str(p.relative_to(_REPO_ROOT))] = lines
    assert not offenders, (
        f"service modules forge a raw INVOKE: {offenders}; every gated effect must route "
        f"through the ocap spine (capability.verify_proof/authorize_detail) and the "
        f"durable approval gate in decima/kernel/inbox.py, never a bare weft.append INVOKE"
    )


def test_detector_flags_a_raw_invoke_append(tmp_path: pathlib.Path) -> None:
    """Self-check: the guard actually recognizes the pattern it forbids (so a broken
    detector cannot make the invariant pass vacuously), and never flags a list append."""
    sample = tmp_path / "sample.py"

    sample.write_text("weft.append(author, INVOKE, {'x': 1})\n", encoding="utf-8")
    assert _raw_invoke_appends(sample) == [1]

    sample.write_text('weft.append(author, verb="INVOKE", body={})\n', encoding="utf-8")
    assert _raw_invoke_appends(sample) == [1]

    sample.write_text("weft.append(author, ASSERT, body)\n", encoding="utf-8")
    assert _raw_invoke_appends(sample) == []

    # an ordinary list append of a value named INVOKE is never mistaken for a Weft write
    sample.write_text("collected.append(INVOKE)\n", encoding="utf-8")
    assert _raw_invoke_appends(sample) == []
