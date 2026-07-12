"""The route table — endpoints mapped to commands + per-endpoint authorization (Phase 8).

Every endpoint is declared here with its authorization LEVEL, so the mapping from an
HTTP surface to an authority path is explicit and auditable (invariant 3 — no ambient
authority; even a Shell user with broad LOCAL authority reaches an effect only through a
named command). Levels escalate:

  * ``public`` — no session (health, login);
  * ``read``   — a valid session cookie (disposable projection reads);
  * ``write``  — session + a matching CSRF token (a durable mutation command);
  * ``reauth`` — session + CSRF + a fresh reauth (clearing a Morta approval gate).

A gated command (TerminateAgent / RevokeCapability / ExportArtifact) is submitted at a
``write`` endpoint but still cannot bypass approval — the command service defers it to
the inbox regardless of the transport. The ``reauth`` level guards the human APPROVAL
that clears the gate. Paths are exact; ids travel in the JSON body, never in the path,
so there is no path-injection surface.
"""

from __future__ import annotations

from dataclasses import dataclass

PUBLIC = "public"
READ = "read"
WRITE = "write"
REAUTH = "reauth"

# route kinds
SPECIAL = "special"   # handled inline by the app (auth, health, stream)
READER = "reader"     # a disposable projection read
COMMAND = "command"   # a durable command-service mutation


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    auth: str
    kind: str
    target: str


ROUTES: tuple[Route, ...] = (
    # -- public -------------------------------------------------------------
    Route("GET", "/api/v1/health", PUBLIC, SPECIAL, "health"),
    Route("POST", "/api/v1/session/login", PUBLIC, SPECIAL, "login"),
    # -- session ------------------------------------------------------------
    Route("GET", "/api/v1/session", READ, SPECIAL, "session_info"),
    Route("POST", "/api/v1/session/logout", WRITE, SPECIAL, "logout"),
    # -- disposable reads ---------------------------------------------------
    Route("GET", "/api/v1/tasks", READ, READER, "tasks"),
    Route("GET", "/api/v1/projects", READ, READER, "projects"),
    Route("GET", "/api/v1/agents", READ, READER, "agents"),
    Route("GET", "/api/v1/notes", READ, READER, "notes"),
    Route("GET", "/api/v1/approvals", READ, READER, "approvals"),
    Route("GET", "/api/v1/activity", READ, READER, "activity"),
    Route("GET", "/api/v1/stream", READ, SPECIAL, "stream"),
    # -- knowledge mutations ------------------------------------------------
    Route("POST", "/api/v1/notes", WRITE, COMMAND, "CreateNote"),
    Route("POST", "/api/v1/notes/update", WRITE, COMMAND, "UpdateNote"),
    Route("POST", "/api/v1/notes/retract", WRITE, COMMAND, "RetractNote"),
    # -- runtime mutations --------------------------------------------------
    Route("POST", "/api/v1/tasks", WRITE, COMMAND, "CreateTask"),
    Route("POST", "/api/v1/tasks/complete", WRITE, COMMAND, "CompleteTask"),
    Route("POST", "/api/v1/projects", WRITE, COMMAND, "CreateProject"),
    Route("POST", "/api/v1/plans/start", WRITE, COMMAND, "StartPlan"),
    Route("POST", "/api/v1/plans/pause", WRITE, COMMAND, "PausePlan"),
    # -- gated proposals (deferred to approval by the command service) ------
    Route("POST", "/api/v1/agents/terminate", WRITE, COMMAND, "TerminateAgent"),
    Route("POST", "/api/v1/capabilities/revoke", WRITE, COMMAND, "RevokeCapability"),
    Route("POST", "/api/v1/artifacts/import", WRITE, COMMAND, "ImportArtifact"),
    Route("POST", "/api/v1/artifacts/export", WRITE, COMMAND, "ExportArtifact"),
    # -- approval decisions -------------------------------------------------
    Route("POST", "/api/v1/approvals/deny", WRITE, COMMAND, "DenyInvocation"),
    Route("POST", "/api/v1/approvals/approve", REAUTH, COMMAND, "ApproveInvocation"),
    # ======================================================================
    # Path-A product lanes (0.3). These routes are the FROZEN shared contract
    # (see services/api/contracts.py); each command/reader is a one-line
    # delegation into the owning lane's service module. Ids travel in the JSON
    # body (commands) or the query string (detail readers), never in the path.
    # -- grounded Q&A (qa lane → qa_service.py) -----------------------------
    Route("GET", "/api/v1/questions", READ, READER, "question_runs"),
    Route("GET", "/api/v1/questions/detail", READ, READER, "question_run"),
    Route("POST", "/api/v1/questions/ask", WRITE, COMMAND, "AskGroundedQuestion"),
    # -- coding workspace (workspace lane → workspace_service.py) -----------
    Route("GET", "/api/v1/workspaces", READ, READER, "workspace_runs"),
    Route("GET", "/api/v1/workspaces/detail", READ, READER, "workspace_run"),
    Route("POST", "/api/v1/workspaces", WRITE, COMMAND, "CreateWorkspaceRun"),
    Route("POST", "/api/v1/workspaces/start", WRITE, COMMAND, "StartWorkspaceRun"),
    Route("POST", "/api/v1/workspaces/cancel", WRITE, COMMAND, "CancelWorkspaceRun"),
    # -- model-planned agents (planning lane → plan_service.py) -------------
    # StartPlan/PausePlan (above) stay as-is; these compose with them.
    Route("GET", "/api/v1/plans/proposals", READ, READER, "plan_proposals"),
    Route("GET", "/api/v1/agents/runs", READ, READER, "agent_run_summaries"),
    Route("POST", "/api/v1/plans/propose", WRITE, COMMAND, "RequestPlanProposal"),
    Route("POST", "/api/v1/plans/accept", WRITE, COMMAND, "AcceptPlanProposal"),
    Route("POST", "/api/v1/plans/execute", WRITE, COMMAND, "StartPlanExecution"),
    Route("POST", "/api/v1/plans/resume", WRITE, COMMAND, "ResumePlan"),
    Route("POST", "/api/v1/plans/cancel", WRITE, COMMAND, "CancelPlan"),
)

_BY_KEY: dict[tuple[str, str], Route] = {(r.method, r.path): r for r in ROUTES}
_PATHS: frozenset[str] = frozenset(r.path for r in ROUTES)


def match(method: str, path: str) -> Route | None:
    """The route for a method+path, or None. A known path with the wrong method is a
    distinct case the caller reports as 405."""
    return _BY_KEY.get((method, path))


def path_known(path: str) -> bool:
    return path in _PATHS
