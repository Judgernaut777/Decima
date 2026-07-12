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

THE PRODUCT FLOW (workspace lane):
  1. The operator EXPLICITLY grants repository roots by setting
     ``DECIMA_WORKSPACE_ROOTS`` (``os.pathsep``-separated absolute directories) on the
     service. With NO granted root the lane presents as NOT ENABLED: every command and
     reader returns the stable ``NOT_IMPLEMENTED`` (501) envelope with zero durable
     effect — fail closed, exactly the pre-implementation contract shape.
  2. ``CreateWorkspaceRun`` validates the selected root against the grant list, mounts
     a bounded text copy of the repo into an isolated scratch workspace
     (``capabilities.workspace`` — symlinks are never followed, traversal is refused),
     records the DURABLE grant record + the DURABLE run record on the Weft through the
     established kernel paths, and stores the operator's requested bounded change
     (declared literal ``edits`` + a check name from the DECLARED ``CHECKS`` catalogue;
     arbitrary check source from the wire is refused).
  3. ``StartWorkspaceRun`` applies the declared edits to the isolated working tree,
     mints the bounded lease (durable), marks the run RUNNING (durable), and executes
     the declared check ONLY via the existing ``decima.workers`` isolation
     (``Workspace.prepare_worker_run`` + ``execute_prepared_run``): the untrusted check
     runs OUTSIDE the kernel/API process in a jailed, networkless, credential-free
     worker. The pure execution half runs on a helper thread so the single-threaded
     Shell stays responsive; ALL Weft writes stay on the serving thread. Re-driving
     Start on a RUNNING run reconciles a finished worker into durable diff/test
     artifacts + receipt; on a terminal run it replays the recorded outcome with NO
     new effect.
  4. ``CancelWorkspaceRun`` bounds a run: the durable record turns CANCELLED, the
     already-applied edit set is preserved as a reviewable diff artifact (completed
     steps stay), and a late worker result is DISCARDED (never adopted). The jailed
     worker itself is hard-bounded by its wall-clock/CPU budget regardless.
  5. A restart loses only the disposable scratch tree + in-flight thread: the grant,
     run, artifacts, and receipts re-fold from the Weft. A run that was RUNNING when
     the service died is reported ``interrupted`` by the readers and resolves honestly
     to UNKNOWN (outcome unobservable) the next time it is driven.

Trust: diffs, test output, worker output, and edit payloads are UNTRUSTED content —
recorded ``instruction_eligible=False`` and served to the Shell as display text (the
frontend renders them as text nodes only). No push, no deployment, no credential, no
network: ``contracts.WorkspacePolicy`` is structurally networkless and the only
accepted worker profiles are jailed + networkless + namespaces-mandatory.
"""

from __future__ import annotations

import json
import os
import threading

from decima.capabilities import workspace as ws_cap
from decima.kernel.hashing import content_id
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.runtime.cells import RECEIPT
from decima.services.api.contracts import (
    NOT_IMPLEMENTED,
    CommandError,
    WorkspacePolicy,
    WorkspaceRequest,
    WorkspaceRun,
    WorkspaceRunStatus,
)
from decima.workers.lease import LeaseGuard
from decima.workers.profiles import PROFILES

# ── configuration: the operator's explicit repository grants ─────────────────
ENV_ROOTS = "DECIMA_WORKSPACE_ROOTS"

# ── durable cell types this lane records (established assert_content path) ───
RUN = "workspace_run"
GRANT = "workspace_grant"

# ── stable refusal reason codes ───────────────────────────────────────────────
BAD_REQUEST = "BAD_REQUEST"
NOT_FOUND = "NOT_FOUND"
REPO_NOT_GRANTED = "REPO_NOT_GRANTED"
UNDECLARED_CHECK = "UNDECLARED_CHECK"
INVALID_STATE = "INVALID_STATE"

# ── bounds (ints only — invariant 6) ─────────────────────────────────────────
MAX_EDITS = 32
MAX_EDIT_BYTES = 64 * 1024
MAX_MOUNT_FILE_BYTES = 256 * 1024
MAX_TIMEOUT_SECONDS = 120
MAX_DISPLAY_CHARS = 20000

_SKIP_DIRS = frozenset({".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv"})

# The restrictions EVERY workspace run executes under. This is display-truth for the
# Shell and enforcement-truth in code: the policy cannot express network access
# (contracts.WorkspacePolicy), the worker profile must be jailed + networkless, and
# the worker child scrubs its environment / chroots away the host filesystem.
RESTRICTIONS = {
    "network": False,
    "push": False,
    "deploy": False,
    "ssh_agent": False,
    "git_credentials": False,
    "docker_socket": False,
    "home_dir": False,
    "weft_db": False,
    "secret_store": False,
    "scope": "explicit workspace root only",
}

# ── the DECLARED check catalogue ──────────────────────────────────────────────
# StartWorkspaceRun executes ONLY one of these named, reviewed check sources inside
# the isolated worker. Arbitrary check source arriving over the wire is refused
# (UNDECLARED_CHECK) — undeclared command execution has no path here.
_CHECK_PYTHON_TESTS = '''
def check(files):
    ns = {}
    for path in sorted(files):
        name = path.rsplit("/", 1)[-1]
        if path.endswith(".py") and not name.startswith("test_"):
            exec(compile(files[path], path, "exec"), ns)
    passed = 0
    failed = 0
    lines = []
    for path in sorted(files):
        name = path.rsplit("/", 1)[-1]
        if not (path.endswith(".py") and name.startswith("test_")):
            continue
        tns = dict(ns)
        exec(compile(files[path], path, "exec"), tns)
        for fname in sorted(tns):
            fn = tns[fname]
            if fname.startswith("test_") and callable(fn):
                try:
                    fn()
                    passed += 1
                    lines.append(path + "::" + fname + " PASSED")
                except BaseException as exc:
                    failed += 1
                    lines.append(path + "::" + fname + " FAILED: " + repr(exc))
    return {"passed": passed, "failed": failed, "detail": "\\n".join(lines)}
'''

_CHECK_SLOW_LOOP = '''
def check(files):
    total = 0
    for i in range(400):
        for j in range(1000000):
            total += j
    return {"passed": 1, "failed": 0, "detail": "slow loop total=%d" % total}
'''

CHECKS: dict[str, str] = {
    "python_tests": _CHECK_PYTHON_TESTS,
    "slow_loop": _CHECK_SLOW_LOOP,
}

# Containment probes handed to every worker run: the jailed worker PROVES it cannot
# read host paths; a non-empty read-back fails the run closed (never adopted quietly).
_PROBE_PATHS = (
    "/etc/passwd",
    os.path.join(os.path.expanduser("~"), ".ssh", "id_rsa"),
    os.path.join(os.path.expanduser("~"), ".gitconfig"),
)


# ── in-memory lane state (disposable; NEVER canonical) ───────────────────────
class _Attempt:
    """One in-flight worker execution: the helper thread plus its (pure) outcome.

    The thread touches NO canonical store — it only runs ``execute_prepared_run``
    and parks the ``WorkerResponse`` here for the serving thread to reconcile."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.response: object | None = None
        self.error: str = ""

    def done(self) -> bool:
        return self.thread is not None and not self.thread.is_alive()


class _LaneState:
    """Disposable per-service state: live Workspace handles, in-flight attempts, and
    the service-lifetime lease guard (a replayed lease fails closed)."""

    def __init__(self) -> None:
        self.workspaces: dict[str, ws_cap.Workspace] = {}
        self.attempts: dict[str, _Attempt] = {}
        self.lease_guard = LeaseGuard()


def _state(svc: object) -> _LaneState:
    state = getattr(svc, "_workspace_lane_state", None)
    if state is None:
        state = _LaneState()
        svc._workspace_lane_state = state
    return state


# ── grants ────────────────────────────────────────────────────────────────────
def granted_roots() -> list[str]:
    """The operator's explicitly granted repository roots (realpaths, order kept)."""
    raw = os.environ.get(ENV_ROOTS, "") or ""
    roots: list[str] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part or not os.path.isabs(part) or not os.path.isdir(part):
            continue
        real = os.path.realpath(part)
        if real not in roots:
            roots.append(real)
    return roots


def _require_enabled() -> list[str]:
    """The lane is enabled only by an explicit operator grant. With no granted root
    it presents as not enabled — the stable 501 envelope, zero durable effect."""
    roots = granted_roots()
    if not roots:
        raise CommandError(
            NOT_IMPLEMENTED,
            "workspace lane is not enabled: no granted repository roots "
            f"(set {ENV_ROOTS} to grant explicit repository roots)",
            http_status=501,
        )
    return roots


def _resolve_granted_root(repo_root: object) -> str:
    roots = _require_enabled()
    if not isinstance(repo_root, str) or not repo_root:
        raise CommandError(BAD_REQUEST, "missing or invalid field 'repo_root'")
    real = os.path.realpath(repo_root)
    if real not in roots:
        raise CommandError(
            REPO_NOT_GRANTED,
            f"repository root {repo_root!r} is not granted — grant it explicitly "
            f"via {ENV_ROOTS}",
            http_status=403,
        )
    if not os.path.isdir(real):
        raise CommandError(NOT_FOUND, f"granted root {repo_root!r} does not exist", 404)
    return real


# ── deterministic validation helpers (fail closed) ────────────────────────────
def _require_str(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise CommandError(BAD_REQUEST, f"missing or invalid field {key!r}")
    return value


def _validate_policy(policy: WorkspacePolicy) -> None:
    profile = PROFILES.get(policy.profile)
    if profile is None:
        raise CommandError(BAD_REQUEST, f"unknown worker profile {policy.profile!r}")
    if profile.network or not profile.filesystem_jail or not profile.namespaces_mandatory:
        raise CommandError(
            BAD_REQUEST,
            f"worker profile {policy.profile!r} is not permitted for a workspace run: "
            "a workspace worker must be jailed, networkless, and namespace-mandatory",
        )
    if not (1 <= policy.timeout_seconds <= MAX_TIMEOUT_SECONDS):
        raise CommandError(
            BAD_REQUEST,
            f"timeout_seconds must be within 1..{MAX_TIMEOUT_SECONDS}",
        )
    if not (1 <= policy.max_files <= 4096):
        raise CommandError(BAD_REQUEST, "max_files must be within 1..4096")


def _validate_check(args: dict) -> str:
    for forbidden in ("check_source", "check_entrypoint", "entrypoint", "command"):
        if forbidden in args:
            raise CommandError(
                UNDECLARED_CHECK,
                f"field {forbidden!r} is refused: a workspace run executes only a "
                "check DECLARED in the service catalogue, never wire-supplied code",
            )
    check = args.get("check", "python_tests")
    if not isinstance(check, str) or check not in CHECKS:
        raise CommandError(
            UNDECLARED_CHECK,
            f"unknown check {check!r} — declared checks: {sorted(CHECKS)}",
        )
    return check


def _validate_edits(value: object) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CommandError(BAD_REQUEST, "edits must be a list of {path, content} objects")
    if len(value) > MAX_EDITS:
        raise CommandError(BAD_REQUEST, f"at most {MAX_EDITS} edits per run")
    edits: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise CommandError(BAD_REQUEST, "each edit must be a {path, content} object")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not path:
            raise CommandError(BAD_REQUEST, "edit path must be a non-empty string")
        if "\x00" in path:
            raise CommandError(
                BAD_REQUEST,
                f"edit path {path!r} is refused: NUL bytes are never part of a "
                "workspace path",
            )
        parts = path.replace("\\", "/").split("/")
        if os.path.isabs(path) or any(part in ("", ".", "..") for part in parts):
            raise CommandError(
                BAD_REQUEST,
                f"edit path {path!r} is refused: paths are relative file paths inside "
                "the workspace root — never absolute, never traversing, never a "
                "directory-resolving segment ('.', '', '..')",
            )
        if not isinstance(content, str):
            raise CommandError(BAD_REQUEST, "edit content must be a string")
        if len(content.encode("utf-8")) > MAX_EDIT_BYTES:
            raise CommandError(BAD_REQUEST, f"edit content exceeds {MAX_EDIT_BYTES} bytes")
        edits.append({"path": path, "content": content})
    return edits


def _scan_repo(root: str, max_files: int) -> dict[str, str]:
    """A bounded TEXT copy of the granted root. Symlinks are never followed (a
    symlink escape simply is not mounted); binary/oversized files are skipped;
    exceeding ``max_files`` fails closed."""
    files: dict[str, str] = {}
    real_root = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(real_root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d))
        )
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            real_full = os.path.realpath(full)
            if real_full != real_root and not real_full.startswith(real_root + os.sep):
                continue  # belt+braces: nothing outside the granted root is mounted
            try:
                if os.path.getsize(full) > MAX_MOUNT_FILE_BYTES:
                    continue
                with open(full, encoding="utf-8") as handle:
                    text = handle.read()
            except (OSError, UnicodeDecodeError):
                continue  # unreadable/binary → not part of the text workspace
            files[os.path.relpath(full, real_root)] = text
            if len(files) > max_files:
                raise CommandError(
                    BAD_REQUEST,
                    f"repository exceeds the policy bound of {max_files} files",
                )
    return files


def _display_text(value: object, cap: int = MAX_DISPLAY_CHARS) -> str:
    """Shell-bound copy of untrusted text: control characters (except newline/tab)
    are stripped and length is capped. Inert DATA — the frontend renders it as a
    text node only."""
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, sort_keys=True)
        except (TypeError, ValueError):
            text = repr(value)
    text = "".join(ch for ch in text if ch >= " " or ch in "\n\t")
    if len(text) > cap:
        text = text[:cap] + "\n… [truncated]"
    return text


# ── fold reads ────────────────────────────────────────────────────────────────
def _weave(weft: object) -> Weave:
    return Weave.fold(weft)


def _run_cell(weave: Weave, run_id: str) -> object | None:
    cell = weave.get(run_id)
    if cell is None or cell.retracted or cell.type != RUN:
        return None
    return cell


def _receipt_for_artifact(weave: Weave, artifact_id: str) -> object | None:
    for cell in weave.of_type(RECEIPT):
        if cell.content.get("idempotency_key") == artifact_id:
            return cell
    return None


def _run_view(cell: object, state: _LaneState | None) -> dict:
    c = cell.content
    run = WorkspaceRun(
        id=cell.id,
        workspace_id=c.get("workspace_id", ""),
        name=c.get("name", ""),
        status=c.get("status", WorkspaceRunStatus.UNKNOWN),
        artifact_ids=tuple(c.get("artifact_ids", [])),
        receipt_id=c.get("receipt_id", ""),
        created_frontier=int(c.get("created_frontier", 0)),
    )
    live = state is not None and cell.id in state.attempts
    view = run.as_dict()
    view.update({
        "objective": _display_text(c.get("objective", ""), 400),
        "repo_root": c.get("repo_root", ""),
        "grant_id": c.get("grant_id", ""),
        "check": c.get("check", ""),
        "policy": dict(c.get("policy", {})),
        "restrictions": dict(RESTRICTIONS),
        "changed_files": [_display_text(p, 400) for p in c.get("changed_files", [])],
        "mounted_files": [_display_text(p, 400) for p in c.get("mounted_files", [])],
        "detail": _display_text(c.get("detail", ""), 2000),
        "passed": int(c.get("passed", 0)),
        "failed": int(c.get("failed", 0)),
        "interrupted": bool(
            c.get("status") == WorkspaceRunStatus.RUNNING and not live
        ),
    })
    return view


# ── durable transitions (serving thread only; established kernel paths) ──────
def _assert_run(svc: object, run_id: str, content: dict) -> None:
    assert_content(svc.weft, svc.app, run_id, RUN, content)


def _finalize(svc: object, cell: object, attempt: _Attempt) -> None:
    """Adopt a finished worker execution into durable artifacts + receipt + terminal
    status. Runs on the serving thread inside a command handler (invariant 1)."""
    state = _state(svc)
    run_id = cell.id
    content = dict(cell.content)
    ws = state.workspaces.get(run_id)
    response = attempt.response

    if ws is None or response is None:
        content["status"] = WorkspaceRunStatus.FAILED
        content["detail"] = _display_text(
            attempt.error or "worker execution produced no observable response", 2000
        )
        _assert_run(svc, run_id, content)
        svc.bus.emit("workspace.run_failed", id=run_id, status=content["status"])
        return

    diff_text = ws.diff()
    diff_id = ws.produce_diff_artifact(diff_text)
    test_id = ws.produce_test_artifact(response)
    receipt = _receipt_for_artifact(_weave(svc.weft), test_id)

    output = (getattr(response, "receipt_data", {}) or {}).get("output") or {}
    if not isinstance(output, dict):
        output = {"detail": output}
    passed = int(output.get("passed", 0) or 0)
    failed = int(output.get("failed", 0) or 0)
    readable_outside = list(output.get("readable_outside", []) or [])

    worker_status = getattr(response, "status", "UNKNOWN")
    if readable_outside:
        # The containment probe read a host path — fail the run closed, loudly.
        status = WorkspaceRunStatus.FAILED
        detail = "containment probe breached the jail: " + ", ".join(
            _display_text(p, 200) for p in readable_outside
        )
    elif worker_status == "SUCCEEDED" and failed == 0:
        status = WorkspaceRunStatus.SUCCEEDED
        detail = output.get("detail") or ""
    elif worker_status == "UNKNOWN":
        status = WorkspaceRunStatus.UNKNOWN
        detail = "worker outcome unobservable (budget exceeded or killed mid-effect)"
    else:
        status = WorkspaceRunStatus.FAILED
        detail = output.get("detail") or (
            (getattr(response, "diagnostics", {}) or {})
            .get("worker_diagnostics", {})
            .get("error", "")
        )

    content.update({
        "status": status,
        "artifact_ids": [diff_id, test_id],
        "receipt_id": receipt.id if receipt is not None else "",
        "changed_files": ws.changed_files(),
        "detail": _display_text(detail, 4000),
        "passed": passed,
        "failed": failed,
        "finished_frontier": int(_weave(svc.weft).frontier_lamport),
    })
    _assert_run(svc, run_id, content)

    event = {
        WorkspaceRunStatus.SUCCEEDED: "workspace.run_succeeded",
        WorkspaceRunStatus.UNKNOWN: "workspace.run_failed",
    }.get(status, "workspace.run_failed")
    svc.bus.emit(event, id=run_id, status=status)
    svc.bus.emit("artifact.produced", id=diff_id, run=run_id, kind=ws_cap.DIFF_ARTIFACT)
    svc.bus.emit("artifact.produced", id=test_id, run=run_id, kind=ws_cap.TEST_ARTIFACT)


def _reconcile(svc: object, run_id: str) -> None:
    """Fold a finished (or discarded) in-memory attempt into durable state. A run
    that is no longer RUNNING never adopts a late result — it is dropped."""
    state = _state(svc)
    attempt = state.attempts.get(run_id)
    if attempt is None or not attempt.done():
        return
    del state.attempts[run_id]
    cell = _run_cell(_weave(svc.weft), run_id)
    if cell is None or cell.content.get("status") != WorkspaceRunStatus.RUNNING:
        return  # cancelled/terminal meanwhile: the late result is discarded
    _finalize(svc, cell, attempt)


def _resolve_interrupted(svc: object, cell: object) -> None:
    """A run recorded RUNNING with no live attempt in this process was interrupted
    (service restart). Resolve honestly: completed durable steps stay; the outcome
    of the in-flight worker is UNKNOWN — never fabricated."""
    content = dict(cell.content)
    content["status"] = WorkspaceRunStatus.UNKNOWN
    content["detail"] = _display_text(
        "run interrupted by a service restart; the worker outcome is unobservable", 2000
    )
    _assert_run(svc, cell.id, content)
    svc.bus.emit("workspace.run_failed", id=cell.id, status=WorkspaceRunStatus.UNKNOWN)


def _remount(svc: object, cell: object) -> ws_cap.Workspace:
    """Rebuild the DISPOSABLE scratch workspace for a CREATED run (e.g. after a
    restart): re-validate the grant and re-mount the granted root. The durable run
    record is untouched — the scratch tree is working space, never canonical."""
    c = cell.content
    root = _resolve_granted_root(c.get("repo_root"))
    policy = WorkspacePolicy.from_args(dict(c.get("policy", {})))
    files = _scan_repo(root, policy.max_files)
    ws = ws_cap.create_workspace(
        svc.weft, svc.app, name=c.get("name", "workspace"),
        discriminator=str(c.get("workspace_at", "")),
    )
    ws.mount_repo(files)
    return ws


# ── commands ──────────────────────────────────────────────────────────────────
def create_workspace_run(svc: object, args: dict) -> object:
    """Create an isolated workspace run: durable grant record + durable run record +
    a bounded scratch tree mounted from the EXPLICITLY GRANTED repository root."""
    from decima.services.api.commands import CommandResult

    _require_enabled()
    req = WorkspaceRequest.from_args(args)          # ContractError ⇒ 400, fail closed
    _validate_policy(req.policy)
    check = _validate_check(args)
    edits = _validate_edits(args.get("edits"))
    root = _resolve_granted_root(args.get("repo_root"))
    files = _scan_repo(root, req.policy.max_files)

    # -- durable mutations (established kernel paths; only after all validation) --
    grant_id = content_id({"workspace_grant": root}, kind="cell")
    assert_content(svc.weft, svc.app, grant_id, GRANT, {
        "root": root,
        "restrictions": dict(RESTRICTIONS),
        "instruction_eligible": False,
    })
    # The workspace identity is scoped per run (name + the Weft head at creation, the
    # same discriminator style run_id uses): two runs sharing a name get DISTINCT
    # workspace cells, distinct artifact ids, and distinct receipts.
    ws_at = str(svc.weft.head or "")
    ws = ws_cap.create_workspace(svc.weft, svc.app, name=req.name, discriminator=ws_at)
    ws.mount_repo(files)

    run_id = content_id(
        {"workspace_run": req.name, "root": root, "at": svc.weft.head}, kind="cell"
    )
    content = {
        "workspace_id": ws.id,
        "workspace_at": ws_at,                        # deterministic identity scope
        "grant_id": grant_id,
        "name": req.name,
        "objective": req.objective,
        "repo_root": root,
        "check": check,
        "policy": req.policy.as_dict(),
        "edits": edits,                               # untrusted DATA (bounded above)
        "mounted_files": sorted(files),
        "status": WorkspaceRunStatus.CREATED,
        "created_frontier": int(_weave(svc.weft).frontier_lamport),
        "artifact_ids": [],
        "receipt_id": "",
        "changed_files": [],
        "detail": "",
        "passed": 0,
        "failed": 0,
        "instruction_eligible": False,
    }
    _assert_run(svc, run_id, content)
    _state(svc).workspaces[run_id] = ws
    svc.bus.emit("workspace.created", id=run_id, workspace=ws.id)
    cell = _run_cell(_weave(svc.weft), run_id)
    return CommandResult(ok=True, http_status=201, data=_run_view(cell, _state(svc)))


def start_workspace_run(svc: object, args: dict) -> object:
    """Drive a run forward. CREATED ⇒ apply the declared edits and execute the
    declared check in the isolated worker (bounded lease, RUNNING recorded durably).
    RUNNING ⇒ reconcile a finished worker into durable artifacts, or resolve an
    interrupted run honestly. Terminal ⇒ replay the recorded outcome, NO new effect."""
    from decima.services.api.commands import CommandResult

    _require_enabled()
    run_id = _require_str(args, "id")
    state = _state(svc)
    _reconcile(svc, run_id)

    cell = _run_cell(_weave(svc.weft), run_id)
    if cell is None:
        raise CommandError(NOT_FOUND, f"no such workspace run {run_id!r}", 404)
    status = cell.content.get("status")

    if status in WorkspaceRunStatus.TERMINAL or status == WorkspaceRunStatus.UNKNOWN:
        return CommandResult(ok=True, data=_run_view(cell, state))

    if status == WorkspaceRunStatus.RUNNING:
        if run_id not in state.attempts:
            _resolve_interrupted(svc, cell)
            cell = _run_cell(_weave(svc.weft), run_id)
        return CommandResult(ok=True, data=_run_view(cell, state))

    # -- CREATED: apply the declared bounded change, then execute in the worker ----
    ws = state.workspaces.get(run_id)
    if ws is None:
        ws = _remount(svc, cell)
        state.workspaces[run_id] = ws
    try:
        for edit in cell.content.get("edits", []):
            ws.edit_file(edit["path"], edit["content"])
    except (ws_cap.WorkspaceError, OSError, ValueError) as exc:
        # Fail closed INSIDE the stable envelope: a hostile recorded path (NUL byte,
        # directory-resolving segment, filesystem refusal) must never surface as a raw
        # 500 or wedge the run — the operator sees a deterministic 400 and can Cancel.
        raise CommandError(BAD_REQUEST, f"edit refused: {exc}") from exc

    policy = WorkspacePolicy.from_args(dict(cell.content.get("policy", {})))
    check = cell.content.get("check", "python_tests")
    if check not in CHECKS:
        raise CommandError(UNDECLARED_CHECK, f"recorded check {check!r} is not declared")

    request, now = ws.prepare_worker_run(
        effect=f"workspace_check:{check}",
        check_source=CHECKS[check],
        check_entrypoint="check",
        probe_paths=list(_PROBE_PATHS),
    )

    content = dict(cell.content)
    content["status"] = WorkspaceRunStatus.RUNNING
    content["started_frontier"] = int(now)
    _assert_run(svc, run_id, content)
    svc.bus.emit("workspace.run_started", id=run_id, workspace=ws.id)

    timeout = int(policy.timeout_seconds)
    limits = {"cpu_seconds": max(5, timeout)}
    attempt = _Attempt()

    def _execute() -> None:
        try:
            attempt.response = ws_cap.execute_prepared_run(
                request, now=now, lease_guard=state.lease_guard,
                timeout=timeout, limits=limits,
            )
        except Exception as exc:  # dispatch refusal (isolation/lease/digest) — honest
            attempt.error = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=_execute, daemon=True, name=f"ws-run-{run_id[:8]}")
    attempt.thread = thread
    state.attempts[run_id] = attempt
    thread.start()

    cell = _run_cell(_weave(svc.weft), run_id)
    return CommandResult(ok=True, data=_run_view(cell, state))


def cancel_workspace_run(svc: object, args: dict) -> object:
    """Cancel a run (terminal CANCELLED, durable). Completed steps stay: the applied
    edit set is preserved as a reviewable diff artifact; the worker's late result is
    discarded and the lease is never renewed."""
    from decima.services.api.commands import CommandResult

    _require_enabled()
    run_id = _require_str(args, "id")
    state = _state(svc)
    _reconcile(svc, run_id)

    cell = _run_cell(_weave(svc.weft), run_id)
    if cell is None:
        raise CommandError(NOT_FOUND, f"no such workspace run {run_id!r}", 404)
    status = cell.content.get("status")
    if status in WorkspaceRunStatus.TERMINAL or status == WorkspaceRunStatus.UNKNOWN:
        raise CommandError(
            INVALID_STATE, f"run {run_id[:8]} is already terminal ({status})", 409
        )

    content = dict(cell.content)
    artifact_ids = list(content.get("artifact_ids", []))
    ws = state.workspaces.get(run_id)
    if ws is not None:
        diff_text = ws.diff()
        if diff_text:
            diff_id = ws.produce_diff_artifact(diff_text)
            if diff_id not in artifact_ids:
                artifact_ids.append(diff_id)
            svc.bus.emit(
                "artifact.produced", id=diff_id, run=run_id, kind=ws_cap.DIFF_ARTIFACT
            )
        content["changed_files"] = ws.changed_files()

    content["status"] = WorkspaceRunStatus.CANCELLED
    content["artifact_ids"] = artifact_ids
    content["detail"] = _display_text(
        "cancelled by the operator; the in-flight worker outcome was not adopted", 2000
    )
    _assert_run(svc, run_id, content)
    svc.bus.emit("workspace.run_cancelled", id=run_id, status=WorkspaceRunStatus.CANCELLED)
    cell = _run_cell(_weave(svc.weft), run_id)
    return CommandResult(ok=True, data=_run_view(cell, state))


# ── readers (pure reads over the Weft fold — disposable by construction) ─────
def list_workspace_runs(app: object, query: dict) -> dict:
    """Reader: recorded workspace runs (``contracts.WorkspaceRun`` shapes plus lane
    display fields), newest first, with the grant list + declared check catalogue."""
    roots = _require_enabled()
    weave = _weave(app.weft)
    state = _state(app.commands)

    recorded: dict[str, dict] = {}
    for cell in weave.of_type(GRANT):
        if not cell.retracted:
            recorded[cell.content.get("root", "")] = {"id": cell.id}
    grants = [
        {
            "root": root,
            "recorded": root in recorded,
            "grant_id": recorded.get(root, {}).get("id", ""),
            "restrictions": dict(RESTRICTIONS),
        }
        for root in roots
    ]

    runs = [
        _run_view(cell, state)
        for cell in weave.of_type(RUN)
        if not cell.retracted
    ]
    runs.sort(key=lambda r: (-int(r.get("created_frontier", 0)), r.get("id", "")))
    return {
        "items": runs,
        "grants": grants,
        "checks": sorted(CHECKS),
        "policy_defaults": WorkspacePolicy().as_dict(),
    }


def get_workspace_run(app: object, query: dict) -> dict:
    """Reader: one workspace run by ``?id=…`` with its artifacts (diff text and test
    output as sanitized display text), receipt, grant, and mounted scope."""
    _require_enabled()
    run_id = query.get("id")
    if not isinstance(run_id, str) or not run_id:
        raise CommandError(BAD_REQUEST, "missing query parameter 'id'")
    weave = _weave(app.weft)
    cell = _run_cell(weave, run_id)
    if cell is None:
        raise CommandError(NOT_FOUND, f"no such workspace run {run_id!r}", 404)

    artifacts = []
    for aid in cell.content.get("artifact_ids", []):
        acell = weave.get(aid)
        if acell is None or acell.retracted:
            continue
        ac = acell.content
        entry = {
            "id": acell.id,
            "workspace_id": ac.get("workspace", ""),
            "kind": acell.type,
            "digest": ac.get("digest", ""),
            "status": ac.get("status", ""),
            "applied": bool(ac.get("applied", False)),
            "untrusted": True,
        }
        if acell.type == ws_cap.DIFF_ARTIFACT:
            entry["diff"] = _display_text(ac.get("diff", ""))
        elif acell.type == ws_cap.TEST_ARTIFACT:
            output = ac.get("output")
            if isinstance(output, dict):
                entry["passed"] = int(output.get("passed", 0) or 0)
                entry["failed"] = int(output.get("failed", 0) or 0)
                entry["output"] = _display_text(output.get("detail", ""))
                entry["readable_outside"] = [
                    _display_text(p, 200)
                    for p in (output.get("readable_outside") or [])
                ]
            else:
                entry["output"] = _display_text(output)
        artifacts.append(entry)

    receipt = None
    rid = cell.content.get("receipt_id", "")
    if rid:
        rcell = weave.get(rid)
        if rcell is not None and not rcell.retracted:
            receipt = {
                "id": rcell.id,
                "status": rcell.content.get("status", ""),
                "output_cell_ids": list(rcell.content.get("output_cell_ids", [])),
            }

    grant = None
    gid = cell.content.get("grant_id", "")
    if gid:
        gcell = weave.get(gid)
        if gcell is not None and not gcell.retracted:
            grant = {
                "id": gcell.id,
                "root": gcell.content.get("root", ""),
                "restrictions": dict(gcell.content.get("restrictions", {})),
            }

    return {
        "run": _run_view(cell, _state(app.commands)),
        "artifacts": artifacts,
        "receipt": receipt,
        "grant": grant,
    }


# Reader dispatch (target name in routes.py → callable). The app consults this table;
# the workspace lane replaces stub bodies above, never the table keys.
READERS = {
    "workspace_runs": list_workspace_runs,
    "workspace_run": get_workspace_run,
}
