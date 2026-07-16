# ruff: noqa: F821 — add/mul are injected by the workspace check runner from calc.py.
def test_add():
    assert add(2, 3) == 5  # type: ignore[name-defined]  # add injected at runtime by the workspace check runner


def test_mul():
    assert mul(2, 3) == 6  # type: ignore[name-defined]  # mul injected at runtime by the workspace check runner
