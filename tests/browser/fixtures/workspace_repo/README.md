# Workspace fixture repo

A tiny deterministic repository the Scenario C browser qualification mounts into an
isolated coding workspace. `calc.py` ships a deliberate bug (`add` subtracts); the
declared `python_tests` check fails until the bounded change replaces `calc.py` with a
correct implementation, at which point both tests pass. Nothing here is ever pushed,
deployed, or given a credential — the workspace has no outward path.
