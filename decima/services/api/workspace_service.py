"""Coding workspace service — OWNED BY THE WORKSPACE LANE (Path A).

This module is the ONLY backend file the workspace lane edits (besides its own screen
``js/screens/workspace.js``, its tests, and workspace capability glue). The shared
contracts live in ``contracts.py``; the routes/commands/events are already wired:

  commands  CreateWorkspaceRun  → :func:`create_workspace_run`
            StartWorkspaceRun   → :func:`start_workspace_run`
            CancelWorkspaceRun  → :func:`cancel_workspace_run`
  readers   GET /api/v1/workspaces              → :func:`list_workspace_runs`
            GET /api/v1/workspaces/detail?id=…  → :func:`get_workspace_run`
  events    ``workspace.* / artifact.*`` via ``svc.bus.emit``

Implementation rules (the lane's obligations):
  * Execution happens ONLY inside the EXISTING worker system — compose
    ``decima.capabilities.workspace`` (mount → edit → ``run_in_worker`` → diff →
    artifacts). Untrusted code NEVER runs in the kernel/API process (invariant 7);
    the runner stays implementation-digest-bound; a real lease + capability proof
    gates every run (invariant 3).
  * ``contracts.WorkspacePolicy`` structurally cannot grant network/credentials —
    keep it that way: no push, no deploy, no outward effect from a workspace run.
  * Diffs, test output, and worker output are UNTRUSTED content
    (``instruction_eligible=False``); durable artifacts go on the Weft via the
    existing capability paths (invariant 1); readers are pure fold/projection reads.
  * Return ``CommandResult`` from commands, ``{"items": [...]}`` dicts from readers.
"""

from __future__ import annotations

from decima.services.api.contracts import NOT_IMPLEMENTED, CommandError


def create_workspace_run(svc: object, args: dict) -> object:
    """Create an isolated workspace (durable record + bounded scratch tree).

    OWNER: workspace lane. Parse with ``contracts.WorkspaceRequest.from_args``,
    compose ``capabilities.workspace.create_workspace``, emit ``workspace.created``,
    and return a ``contracts.WorkspaceRun.as_dict()`` payload."""
    raise CommandError(
        NOT_IMPLEMENTED, "CreateWorkspaceRun is not implemented yet (workspace lane)",
        http_status=501,
    )


def start_workspace_run(svc: object, args: dict) -> object:
    """Run declared checks over a workspace's tree INSIDE an isolated worker.

    OWNER: workspace lane. Compose ``Workspace.run_in_worker`` (existing worker
    system; jailed, no network, no creds) + ``produce_diff_artifact`` /
    ``produce_test_artifact``; emit ``workspace.run_started`` then a terminal
    ``workspace.run_*`` and ``artifact.produced`` with ids only."""
    raise CommandError(
        NOT_IMPLEMENTED, "StartWorkspaceRun is not implemented yet (workspace lane)",
        http_status=501,
    )


def cancel_workspace_run(svc: object, args: dict) -> object:
    """Cancel a workspace run (terminal CANCELLED; the lease is not renewed).

    OWNER: workspace lane. Emit ``workspace.run_cancelled``."""
    raise CommandError(
        NOT_IMPLEMENTED, "CancelWorkspaceRun is not implemented yet (workspace lane)",
        http_status=501,
    )


def list_workspace_runs(app: object, query: dict) -> dict:
    """Reader: recorded workspace runs (``contracts.WorkspaceRun`` shapes), newest
    first — ``{"items": [...]}``.

    OWNER: workspace lane."""
    raise CommandError(
        NOT_IMPLEMENTED, "workspace runs reader is not implemented yet (workspace lane)",
        http_status=501,
    )


def get_workspace_run(app: object, query: dict) -> dict:
    """Reader: one workspace run by ``?id=…`` with its ``WorkspaceArtifact`` list.

    OWNER: workspace lane. Unknown id ⇒ ``CommandError(NOT_FOUND, http_status=404)``."""
    raise CommandError(
        NOT_IMPLEMENTED, "workspace run detail is not implemented yet (workspace lane)",
        http_status=501,
    )


# Reader dispatch (target name in routes.py → callable). The app consults this table;
# the workspace lane replaces stub bodies above, never the table keys.
READERS = {
    "workspace_runs": list_workspace_runs,
    "workspace_run": get_workspace_run,
}
