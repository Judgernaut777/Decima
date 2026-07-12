"""Keep pytest OUT of the mounted workspace fixture repo.

``workspace_repo/`` is a deterministic repository the Scenario C browser spec mounts
into an isolated coding workspace; its ``test_calc.py`` is executed by the workspace
check runner (which injects ``add``/``mul`` from ``calc.py``), NOT by pytest. Collecting
it here would fail with NameError, so it is excluded from pytest collection.
"""

collect_ignore_glob = ["workspace_repo/*"]
