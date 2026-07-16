"""Apply the ``adversarial`` marker to every test in this directory.

The marker was registered in pyproject.toml since 0.3 but attached to no test, so CI's
``-m "not adversarial"`` deselected nothing and the "separate adversarial lane" existed
only on paper. Applying it here, at collection time, means no test file in this
directory can forget it.
"""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = __file__.rsplit("/", 1)[0]
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.adversarial)
