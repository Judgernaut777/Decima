## decima/services/api/ — local backend HTTP API (Phase 8, merge order 5)

A narrow, authenticated, loopback-bound HTTP API where every durable mutation becomes accepted Weft events through the kernel/runtime. Stdlib only (http.server/wsgiref) — no web framework.

### Layout
- `identity.py` — generated local app + operator principals and a deterministic pairing secret (derived from the Keyring master seed; never written to the Weft).
- `auth.py` — `SessionStore`: local-pairing login → session (secure HttpOnly + SameSite=Strict cookie) + CSRF token (double-submit) + a `check_reauth` hook for high-risk approvals. All secret/token comparisons are constant-time.
- `events.py` — `EventBus`: a bounded, disposable buffer of `StreamEvent` (assistant/plan/step/approval/error) with a monotonic logical `seq` cursor; renders SSE frames.
- `commands.py` — `CommandService.execute(command, args)`: a fixed name→handler table (no eval/exec). Commands: CreateNote/UpdateNote/RetractNote, CreateTask/CompleteTask/CreateProject, StartPlan/PausePlan, TerminateAgent, RevokeCapability, ApproveInvocation/DenyInvocation, ImportArtifact/ExportArtifact. Each mutation asserts Cells via `decima.runtime.cells` / `decima.kernel.model` / `decima.kernel.lifecycle`; `execute` computes the produced event ids from the Weft tail.
- `routes.py` — declarative route table: endpoint → command/reader + per-endpoint auth level (public/read/write/reauth).
- `app.py` — `Application`: a WSGI callable. `dispatch(method, path, headers, body, query)` is the deterministic in-process driving surface returning a `Response`; `__call__` adapts to WSGI. Reads served only from disposable projections.
- `server.py` — `build_application(db_path)` assembles Weft + identity + ProjectionDriver + Application; `make_http_server` binds 127.0.0.1 by default and REFUSES a non-loopback bind unless `allow_nonloopback=True` (then warns).

### Endpoints (all under `/api/v1`)
`GET /health` (public), `POST /session/login` (public), `GET /session`, `POST /session/logout`, `GET /{tasks,projects,agents,notes,approvals,activity}`, `GET /stream` (SSE), `POST /{notes,notes/update,notes/retract,tasks,tasks/complete,projects,plans/start,plans/pause,agents/terminate,capabilities/revoke,artifacts/import,artifacts/export,approvals/deny}` (write), `POST /approvals/approve` (reauth).

### Auth flow
1. `POST /session/login` with `{pairing_secret}` → sets `decima_session` cookie + returns `csrf`.
2. Mutations require the cookie + `X-CSRF-Token: <csrf>`.
3. Clearing a Morta gate (`/approvals/approve`) additionally requires `X-Reauth: <pairing_secret>`.

### Gated (high-risk) commands
TerminateAgent / RevokeCapability / ExportArtifact never run inline: submitting one enqueues a pending inbox item and returns `APPROVAL_REQUIRED` (202). The effect runs only after `/approvals/approve` records a decision and re-drives the command.

### Invariants upheld
1 (Weft sole store — web layer never writes storage directly), 2 (projections disposable; deleting+rebuilding the projection store preserves reads), 3 (no ambient authority; gated commands cannot bypass approval), 5 (imported artifacts/untrusted notes are DATA, instruction_eligible=False), 6 (ints + logical cursors, no wall-clock/unseeded-random in recorded content), 7 (no endpoint evaluates arbitrary Python).