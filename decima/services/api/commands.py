"""The command service — user intents translated to explicit Weft mutations (Phase 8).

This is the ONLY place the API changes durable state, and it changes it in exactly one
way: by asserting Cells through ``decima.kernel`` / ``decima.runtime`` so every mutation
becomes an accepted event on the Weft (invariant 1 — the Weft is the sole canonical
store; the web layer never writes storage directly). Each command is a NAMED operation
with a fixed handler; there is no ``eval``/``exec`` and no code path that runs a
caller-supplied string as Python (invariant 7). Reads are served elsewhere from
DISPOSABLE projections (invariant 2).

Gated (high-risk / outward / irreversible) commands CANNOT bypass approval (invariant
3): submitting one does NOT perform the effect — it enqueues a pending approval Cell
(the kernel inbox item schema) and returns ``APPROVAL_REQUIRED``. The effect runs only
when a human later approves, which re-drives the SAME command with ``approved=True``.
So a gated command has no way to reach its effect without a recorded human decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from decima.kernel.authorization import ReasonCode
from decima.kernel.hashing import content_id
from decima.kernel.inbox import DECISION, ITEM
from decima.kernel.lifecycle import redact, revoke, terminate
from decima.kernel.model import assert_content, assert_edge
from decima.kernel.weave import Weave
from decima.runtime import cells
from decima.runtime.cells import AgentStatus, PlanStatus, StepStatus
from decima.services.api import plan_service, qa_service, workspace_service
from decima.services.api.contracts import CommandError, ContractError
from decima.services.api.events import EventBus

__all__ = ["CommandError", "CommandResult", "CommandService", "GATED"]

# The commands whose effects are outward / irreversible / destructive. Submitting one
# never performs the effect inline — it routes through the human approval gate.
GATED: frozenset[str] = frozenset(
    {"TerminateAgent", "RevokeCapability", "ExportArtifact"}
)

ARTIFACT = "artifact"
NOTE = "note"

# Stable non-authorization reason codes this service returns (ReasonCode carries the
# authorization vocabulary: OK / APPROVAL_REQUIRED / DENIED / NO_SUCH_CAPABILITY).
UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
BAD_REQUEST = "BAD_REQUEST"
NOT_FOUND = "NOT_FOUND"
ALREADY_DECIDED = "ALREADY_DECIDED"


# ``CommandError`` is defined canonically in ``contracts.py`` (the shared application
# contract) and re-exported here so existing imports keep working.


@dataclass
class CommandResult:
    """The outcome of one command: whether it succeeded, a machine-readable reason, the
    ids of the Weft events it produced (its proof of durable effect), and a JSON-safe
    payload. ``required_approval`` is True exactly when a gated command was deferred."""

    ok: bool
    reason_code: str = ReasonCode.OK
    http_status: int = 200
    data: dict = field(default_factory=dict)
    event_ids: list[str] = field(default_factory=list)
    required_approval: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        body = {
            "ok": self.ok,
            "reason_code": self.reason_code,
            "data": self.data,
            "event_ids": list(self.event_ids),
            "required_approval": self.required_approval,
        }
        if self.error is not None:
            body["error"] = self.error
        return body


class CommandService:
    """Translates named commands into Weft mutations via kernel/runtime.

    Construct with a ``Weft``, a ``ProjectionDriver`` (kept current after each mutation),
    the acting principals, and an ``EventBus`` for the UI stream. Every handler is a
    bound method registered in ``_handlers`` — dispatch is a dict lookup, so an unknown
    or code-shaped command name is REFUSED, never executed."""

    def __init__(
        self,
        weft: object,
        driver: object,
        *,
        app_principal: str,
        human_principal: str,
        event_bus: EventBus,
        models: object | None = None,
    ) -> None:
        self.weft = weft
        self.driver = driver
        self.app = app_principal
        self.human = human_principal
        self.bus = event_bus
        # The shared model stack (catalogue + pure routing policy) — deterministic-only
        # by default; a real local provider joins when DECIMA_LIVE_* is configured (see
        # models_setup.py). Lanes route via ``svc.models.propose()``; every reply is a
        # PROPOSAL — validation and authorization stay deterministic (invariant 4).
        if models is None:
            from decima.services.api.models_setup import build_model_stack

            models = build_model_stack()
        self.models = models
        self._handlers = {
            "CreateNote": self._create_note,
            "UpdateNote": self._update_note,
            "RetractNote": self._retract_note,
            "CreateTask": self._create_task,
            "CompleteTask": self._complete_task,
            "CreateProject": self._create_project,
            "StartPlan": self._start_plan,
            "PausePlan": self._pause_plan,
            "TerminateAgent": self._terminate_agent,
            "RevokeCapability": self._revoke_capability,
            "ApproveInvocation": self._approve_invocation,
            "DenyInvocation": self._deny_invocation,
            "ImportArtifact": self._import_artifact,
            "ExportArtifact": self._export_artifact,
            # -- Path-A lanes: one-line delegations into the owning service --
            "AskGroundedQuestion": self._ask_grounded_question,
            "CreateWorkspaceRun": self._create_workspace_run,
            "StartWorkspaceRun": self._start_workspace_run,
            "CancelWorkspaceRun": self._cancel_workspace_run,
            "RequestPlanProposal": self._request_plan_proposal,
            "AcceptPlanProposal": self._accept_plan_proposal,
            "StartPlanExecution": self._start_plan_execution,
            "ResumePlan": self._resume_plan,
            "CancelPlan": self._cancel_plan,
        }

    # -- dispatch ----------------------------------------------------------
    def commands(self) -> list[str]:
        return sorted(self._handlers)

    def execute(self, command: str, args: dict | None, *, approved: bool = False) -> CommandResult:
        """Run one named command. Fails closed on an unknown command (no handler ⇒ no
        execution). A gated command with no standing approval is DEFERRED to the inbox
        and returns ``APPROVAL_REQUIRED`` with no effect."""
        args = dict(args or {})
        handler = self._handlers.get(command)
        if handler is None:
            return CommandResult(
                ok=False, reason_code=UNKNOWN_COMMAND, http_status=400,
                error=f"unknown command {command!r}",
            )
        if command in GATED and not approved:
            item_id = self._enqueue_approval(command, args)
            self.driver.update()
            self.bus.publish("approval", {"item": item_id, "command": command,
                                          "state": "pending"})
            return CommandResult(
                ok=False, reason_code=ReasonCode.APPROVAL_REQUIRED, http_status=202,
                data={"item": item_id, "command": command}, required_approval=True,
            )
        before = self.weft.count()
        try:
            result = handler(args)
        except CommandError as exc:
            self.bus.publish("error", {"command": command, "reason": exc.reason_code})
            return CommandResult(ok=False, reason_code=exc.reason_code,
                                 http_status=exc.http_status, error=str(exc))
        except ContractError as exc:
            # A request body that failed contract validation — same envelope as a
            # BAD_REQUEST refusal, so lanes can let contract parsing fail closed.
            self.bus.publish("error", {"command": command, "reason": BAD_REQUEST})
            return CommandResult(ok=False, reason_code=BAD_REQUEST,
                                 http_status=400, error=str(exc))
        result.event_ids = [ev.id for ev in self.weft.events(from_seq=before)]
        self.driver.update()
        return result

    # -- knowledge commands ------------------------------------------------
    def _create_note(self, args: dict) -> CommandResult:
        text = _require_str(args, "text")
        eligible = bool(args.get("instruction_eligible", False))
        note_id = args.get("id") or content_id(
            {"api_note": text, "at": self.weft.head}, kind="cell"
        )
        assert_content(self.weft, self.app, note_id, NOTE,
                       {"text": text, "instruction_eligible": eligible})
        self.bus.publish("assistant", {"event": "note_created", "id": note_id})
        return CommandResult(ok=True, http_status=201, data={"id": note_id})

    def _update_note(self, args: dict) -> CommandResult:
        note_id = _require_str(args, "id")
        cell = self._cell(note_id)
        if cell is None or cell.type != NOTE:
            raise CommandError(NOT_FOUND, f"no such note {note_id!r}", 404)
        text = _require_str(args, "text")
        content = dict(cell.content)
        content["text"] = text
        if "instruction_eligible" in args:
            content["instruction_eligible"] = bool(args["instruction_eligible"])
        assert_content(self.weft, self.app, note_id, NOTE, content)
        self.bus.publish("assistant", {"event": "note_updated", "id": note_id})
        return CommandResult(ok=True, data={"id": note_id})

    def _retract_note(self, args: dict) -> CommandResult:
        note_id = _require_str(args, "id")
        cell = self._cell(note_id)
        if cell is None or cell.type != NOTE:
            raise CommandError(NOT_FOUND, f"no such note {note_id!r}", 404)
        redact(self.weft, self.app, note_id)  # withdraw + erase payload from projections
        self.bus.publish("assistant", {"event": "note_retracted", "id": note_id})
        return CommandResult(ok=True, data={"id": note_id})

    # -- runtime commands --------------------------------------------------
    def _create_project(self, args: dict) -> CommandResult:
        objective = _require_str(args, "objective")
        plan_id = cells.create_plan(self.weft, self.app, objective=objective,
                                    creator_principal=self.human)
        self.bus.publish("plan", {"event": "project_created", "id": plan_id})
        return CommandResult(ok=True, http_status=201, data={"id": plan_id})

    def _create_task(self, args: dict) -> CommandResult:
        description = _require_str(args, "description")
        plan_id = _require_str(args, "project_id")
        if self._cell(plan_id) is None:
            raise CommandError(NOT_FOUND, f"no such project {plan_id!r}", 404)
        deps = list(args.get("dependency_ids", []))
        deadline = args.get("deadline")
        step_id = cells.create_step(
            self.weft, self.app, plan_id=plan_id, description=description,
            dependency_ids=deps,
            deadline=None if deadline is None else int(deadline),
        )
        self.bus.publish("step", {"event": "task_created", "id": step_id})
        return CommandResult(ok=True, http_status=201, data={"id": step_id})

    def _complete_task(self, args: dict) -> CommandResult:
        task_id = _require_str(args, "id")
        cell = self._cell(task_id)
        if cell is None or cell.type != cells.PLAN_STEP:
            raise CommandError(NOT_FOUND, f"no such task {task_id!r}", 404)
        cells.set_status(self.weft, self.app, cell, StepStatus.SUCCEEDED)
        self.bus.publish("step", {"event": "task_completed", "id": task_id})
        return CommandResult(ok=True, data={"id": task_id, "status": StepStatus.SUCCEEDED})

    def _start_plan(self, args: dict) -> CommandResult:
        return self._set_plan_status(args, PlanStatus.ACTIVE, "plan_started")

    def _pause_plan(self, args: dict) -> CommandResult:
        return self._set_plan_status(args, PlanStatus.PAUSED, "plan_paused")

    def _set_plan_status(self, args: dict, status: str, event: str) -> CommandResult:
        plan_id = _require_str(args, "id")
        cell = self._cell(plan_id)
        if cell is None or cell.type != cells.PLAN:
            raise CommandError(NOT_FOUND, f"no such plan {plan_id!r}", 404)
        cells.set_status(self.weft, self.app, cell, status)
        self.bus.publish("plan", {"event": event, "id": plan_id, "status": status})
        return CommandResult(ok=True, data={"id": plan_id, "status": status})

    def _terminate_agent(self, args: dict) -> CommandResult:
        agent_id = _require_str(args, "id")
        cell = self._cell(agent_id)
        if cell is None or cell.type != cells.AGENT:
            raise CommandError(NOT_FOUND, f"no such agent {agent_id!r}", 404)
        cells.set_status(self.weft, self.app, cell, AgentStatus.TERMINATED)
        terminate(self.weft, self.app, agent_id)  # cascade: fail closed the lease tree
        self.bus.publish("plan", {"event": "agent_terminated", "id": agent_id})
        return CommandResult(ok=True, data={"id": agent_id,
                                            "status": AgentStatus.TERMINATED})

    def _revoke_capability(self, args: dict) -> CommandResult:
        cap_id = _require_str(args, "id")
        revoke(self.weft, self.app, cap_id)  # RETRACT → DERIVED_AUTHORITY cascade
        self.bus.publish("approval", {"event": "capability_revoked", "id": cap_id})
        return CommandResult(ok=True, data={"id": cap_id})

    # -- artifacts ---------------------------------------------------------
    def _import_artifact(self, args: dict) -> CommandResult:
        """Import external content as a QUARANTINED artifact Cell: it is DATA, stamped
        ``instruction_eligible=False`` (invariant 5) — never rendered/executed as
        trusted. Importing records the bytes' digest and label, not an effect."""
        name = _require_str(args, "name")
        body = args.get("body", "")
        if not isinstance(body, str):
            raise CommandError(BAD_REQUEST, "artifact body must be a string")
        digest = content_id({"artifact_body": body}, kind="content")
        art_id = args.get("id") or content_id(
            {"api_artifact": name, "digest": digest, "at": self.weft.head}, kind="cell"
        )
        assert_content(self.weft, self.app, art_id, ARTIFACT, {
            "name": name, "digest": digest, "body": body,
            "instruction_eligible": False, "trust": "untrusted",
        })
        self.bus.publish("assistant", {"event": "artifact_imported", "id": art_id})
        return CommandResult(ok=True, http_status=201,
                             data={"id": art_id, "digest": digest})

    def _export_artifact(self, args: dict) -> CommandResult:
        """Export an artifact outward (the gated effect). Reachable ONLY after approval —
        ``execute`` defers an unapproved call. Records an export receipt Cell."""
        art_id = _require_str(args, "id")
        cell = self._cell(art_id)
        if cell is None or cell.type != ARTIFACT:
            raise CommandError(NOT_FOUND, f"no such artifact {art_id!r}", 404)
        rid = content_id({"api_export": art_id, "at": self.weft.head}, kind="cell")
        assert_content(self.weft, self.app, rid, "artifact_export", {
            "artifact": art_id, "digest": cell.content.get("digest"),
            "destination": args.get("destination", "local"),
        })
        assert_edge(self.weft, self.app, rid, "exports", art_id)
        self.bus.publish("assistant", {"event": "artifact_exported", "id": art_id})
        return CommandResult(ok=True, data={"id": art_id, "receipt": rid})

    # -- approval enactment (kernel inbox schema) --------------------------
    def _enqueue_approval(self, command: str, args: dict) -> str:
        """Record a pending inbox item WITHOUT running the effect. The item captures the
        deferred command + args so a later approval can enact EXACTLY it. It is DATA
        (``instruction_eligible=False``): it DESCRIBES a proposed effect for a human,
        never instructs an agent."""
        item_id = content_id(
            {"api_inbox_item": command, "args": args, "at": self.weft.head}, kind="cell"
        )
        assert_content(self.weft, self.app, item_id, ITEM, {
            "capability": f"local:{command}",
            "capability_name": command,
            "effect": command,
            "args": args,
            "description": f"{command} {args}",
            "deferred_command": command,
            "deferred_args": args,
            "status": "pending",
            "instruction_eligible": False,
        })
        return item_id

    def _approve_invocation(self, args: dict) -> CommandResult:
        """Approve a pending gated item and enact its deferred effect. Fails closed on an
        unknown or already-decided item (nothing auto-approves; no item decided twice).
        Records the human's decision on the Weft, then re-drives the deferred command
        with ``approved=True`` so the effect finally runs — under the same command path."""
        item_id = _require_str(args, "item")
        item = self._cell(item_id)
        if item is None or item.type != ITEM:
            raise CommandError(NOT_FOUND, f"no such approval item {item_id!r}", 404)
        if self._decision_of(item_id) is not None:
            raise CommandError(ALREADY_DECIDED, f"item {item_id[:8]} already decided", 409)
        did = content_id({"api_approved": item_id, "at": self.weft.head}, kind="cell")
        assert_content(self.weft, self.human, did, DECISION, {
            "item": item_id, "decision": "approved", "approver": self.human, "ran": True,
            "capability": item.content.get("capability"),
        })
        assert_edge(self.weft, self.human, did, "decides", item_id)
        inner = self.execute(item.content["deferred_command"],
                             item.content.get("deferred_args", {}), approved=True)
        self.bus.publish("approval", {"item": item_id, "state": "approved"})
        return CommandResult(ok=inner.ok, reason_code=inner.reason_code,
                             http_status=200 if inner.ok else inner.http_status,
                             data={"item": item_id, "enacted": inner.ok,
                                   "inner": inner.data})

    def _deny_invocation(self, args: dict) -> CommandResult:
        """Deny a pending item: record a denial Cell; the effect NEVER runs. Fails closed
        on an unknown or already-decided item."""
        item_id = _require_str(args, "item")
        item = self._cell(item_id)
        if item is None or item.type != ITEM:
            raise CommandError(NOT_FOUND, f"no such approval item {item_id!r}", 404)
        if self._decision_of(item_id) is not None:
            raise CommandError(ALREADY_DECIDED, f"item {item_id[:8]} already decided", 409)
        did = content_id({"api_denied": item_id, "at": self.weft.head}, kind="cell")
        assert_content(self.weft, self.human, did, DECISION, {
            "item": item_id, "decision": "denied", "approver": self.human, "ran": False,
            "capability": item.content.get("capability"),
            "reason": args.get("reason", ""),
        })
        assert_edge(self.weft, self.human, did, "decides", item_id)
        self.bus.publish("approval", {"item": item_id, "state": "denied"})
        return CommandResult(ok=True, data={"item": item_id, "decision": "denied"})

    # -- Path-A lane commands: one-line delegations (the lane owns the module) ---
    def _ask_grounded_question(self, args: dict) -> CommandResult:
        return qa_service.ask_grounded_question(self, args)

    def _create_workspace_run(self, args: dict) -> CommandResult:
        return workspace_service.create_workspace_run(self, args)

    def _start_workspace_run(self, args: dict) -> CommandResult:
        return workspace_service.start_workspace_run(self, args)

    def _cancel_workspace_run(self, args: dict) -> CommandResult:
        return workspace_service.cancel_workspace_run(self, args)

    def _request_plan_proposal(self, args: dict) -> CommandResult:
        return plan_service.request_plan_proposal(self, args)

    def _accept_plan_proposal(self, args: dict) -> CommandResult:
        return plan_service.accept_plan_proposal(self, args)

    def _start_plan_execution(self, args: dict) -> CommandResult:
        return plan_service.start_plan_execution(self, args)

    def _resume_plan(self, args: dict) -> CommandResult:
        return plan_service.resume_plan(self, args)

    def _cancel_plan(self, args: dict) -> CommandResult:
        return plan_service.cancel_plan(self, args)

    # -- fold reads (kernel) -----------------------------------------------
    def _weave(self) -> Weave:
        return Weave.fold(self.weft)

    def _cell(self, cid: str) -> object | None:
        return self._weave().get(cid)

    def _decision_of(self, item_id: str) -> object | None:
        for c in self._weave().of_type(DECISION):
            if c.content.get("item") == item_id:
                return c
        return None


def _require_str(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise CommandError(BAD_REQUEST, f"missing or invalid field {key!r}")
    return value
