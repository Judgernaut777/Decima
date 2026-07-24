"""Isolated repository workspace — edit, run-in-worker, review a diff, keep artifacts.

The workflow a coding session actually runs:

  1. CREATE a workspace (a bounded host scratch dir the caller owns) and MOUNT a repo
     into it (copy a ``{path: content}`` map). Paths are validated — an absolute path
     or a ``..`` traversal is refused, so a mount can only ever write inside the
     bounded tree.
  2. INSPECT (``list_files`` / ``read_file``) and EDIT (``edit_file``) the working
     tree. The mounted contents are remembered as the BASELINE.
  3. RUN declared commands / tests INSIDE a worker (``decima.workers``): the file map
     is handed to an isolated child that has NO network, NO filesystem outside its
     own jail, NO ssh/git creds, and cannot push/deploy (those are deferred). The
     worker composes the workers adversarial guarantees — a chroot jail means it
     literally cannot read a host path, and it only ever sees the bytes we passed it.
  4. DIFF the working tree against the baseline (``diff``) — a REVIEWABLE unified diff
     produced BEFORE anything is applied. ``apply`` (adopt the working tree as the new
     baseline) is a separate, explicit step, so a change is always reviewable first.
  5. Produce DURABLE artifacts: the diff and the test result are asserted as Cells on
     the canonical Weft (invariant 1). A restart re-folds them from the log — the
     produced diff is never lost, because it is on the log, not in the scratch dir.

The bounded host dir is DISPOSABLE working space, never a canonical store: nothing a
consumer must trust lives there. Its random scratch path is deliberately kept OUT of
recorded Weft content (invariant 6 — no unseeded-random in the log). All authority
flows through a real lease + capability proof + receipt (invariant 3): a worker with
no proof runs nothing.
"""

from __future__ import annotations

import difflib
import os
import tempfile
from dataclasses import dataclass, field

from decima.kernel.hashing import blob_id, content_id, nfc
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.workers.execution import compute_digest, run_worker
from decima.workers.lease import LeaseGuard
from decima.workers.profiles import WORKSPACE as WORKSPACE_PROFILE
from decima.workers.protocol import WorkerRequest

# ── cell types ────────────────────────────────────────────────────────────────
WORKSPACE = "workspace"
DIFF_ARTIFACT = "diff_artifact"
TEST_ARTIFACT = "test_artifact"

# The worker source that RUNS declared checks over the mounted files inside the jail.
# It materializes the file map into its own (jailed) cwd, then execs the caller's
# declared check source and calls its entrypoint with the file map. This is untrusted
# code — which is exactly why it runs ONLY here, in the isolated worker, never in the
# kernel/API process (invariant 7). Pure stdlib; no network, no host access.
_RUNNER_SOURCE = """
def run(files, check_source, check_entrypoint, probe_paths):
    import os
    # Materialize the mounted files into the jail cwd (bounded to the chroot).
    written = []
    for path, content in sorted(files.items()):
        # Same safe-path discipline as Workspace._safe_path: refuse absolute paths
        # and any ".." traversal COMPONENT, but preserve legitimate names that merely
        # contain ".." (e.g. "a..b.py"). A refused path is skipped, never mangled.
        normalized = path.replace("\\\\", "/")
        if os.path.isabs(normalized) or ".." in normalized.split("/"):
            continue
        safe = normalized.lstrip("/")
        directory = os.path.dirname(safe)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(safe, "w", encoding="utf-8") as handle:
            handle.write(content)
        written.append(safe)

    # Adversarial probe: prove the worker cannot read anything outside its workspace.
    # In the chroot jail every host path simply is not present.
    readable_outside = []
    for probe in probe_paths or []:
        try:
            with open(probe, "r", encoding="utf-8", errors="replace") as handle:
                handle.read(16)
            readable_outside.append(probe)
        except OSError:
            pass

    result = {"written": written, "readable_outside": readable_outside,
              "passed": 0, "failed": 0, "detail": None}

    if check_source:
        namespace = {"__name__": "__check__"}
        exec(compile(check_source, "<workspace-check>", "exec"), namespace)
        fn = namespace.get(check_entrypoint)
        if not callable(fn):
            result["detail"] = "check entrypoint %r is not callable" % check_entrypoint
            result["failed"] = 1
        else:
            outcome = fn(files)
            if isinstance(outcome, dict):
                result["passed"] = int(outcome.get("passed", 0))
                result["failed"] = int(outcome.get("failed", 0))
                result["detail"] = outcome.get("detail")
            else:
                result["passed"] = 1 if outcome else 0
                result["failed"] = 0 if outcome else 1
    return result
"""

_RUNNER_DIGEST = compute_digest(_RUNNER_SOURCE)


class WorkspaceError(Exception):
    """A workspace operation was refused — bad path, missing file, or failed mount."""


@dataclass
class Workspace:
    """A handle to an isolated repository workspace.

    ``root`` is a DISPOSABLE bounded host dir (not on the Weft). ``baseline`` remembers
    the mounted contents so a reviewable diff can be computed. The durable record of
    the workspace (and its produced artifacts) lives on the canonical Weft."""

    id: str
    name: str
    root: str
    weft: Weft
    author: str
    baseline: dict[str, str] = field(default_factory=dict)

    # -- path safety --------------------------------------------------------
    def _safe_path(self, relpath: str) -> str:
        if not isinstance(relpath, str) or not relpath:
            raise WorkspaceError("a workspace path must be a non-empty string")
        if os.path.isabs(relpath):
            raise WorkspaceError(f"absolute paths are not allowed inside a workspace: {relpath!r}")
        parts = relpath.replace("\\", "/").split("/")
        if ".." in parts:
            raise WorkspaceError(f"path traversal is not allowed: {relpath!r}")
        root = os.path.realpath(self.root)
        full = os.path.realpath(os.path.join(root, relpath))
        if full != root and not full.startswith(root + os.sep):
            raise WorkspaceError(f"path escapes the workspace bound: {relpath!r}")
        return full

    # -- mount / inspect / edit --------------------------------------------
    def mount_repo(self, files: dict[str, str]) -> list[str]:
        """Copy a ``{path: content}`` repo into the bounded tree; record the baseline.

        Every path is validated (no absolute, no ``..``) so a mount can only write
        inside the workspace. Re-mounting resets the baseline to the given files."""
        if not isinstance(files, dict):
            raise WorkspaceError("files must be a {path: content} dict")
        mounted: list[str] = []
        self.baseline = {}
        for path, content in files.items():
            full = self._safe_path(path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as handle:
                handle.write(content)
            self.baseline[path] = content
            mounted.append(path)
        assert_content(
            self.weft,
            self.author,
            self.id,
            WORKSPACE,
            {
                "name": self.name,
                "mounted": True,
                "files": sorted(self.baseline),
                "instruction_eligible": False,
            },
        )
        return sorted(mounted)

    def list_files(self) -> list[str]:
        """Every file currently in the working tree (relative paths, sorted)."""
        root = os.path.realpath(self.root)
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                out.append(os.path.relpath(full, root))
        return sorted(out)

    def read_file(self, path: str) -> str:
        full = self._safe_path(path)
        if not os.path.isfile(full):
            raise WorkspaceError(f"no such file in workspace: {path!r}")
        with open(full, encoding="utf-8") as handle:
            return handle.read()

    def edit_file(self, path: str, content: str) -> None:
        """Write ``content`` to a file in the working tree (a new file is allowed).
        The baseline is untouched, so the change shows up in ``diff``."""
        full = self._safe_path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)

    def working_files(self) -> dict[str, str]:
        """The current working tree as a ``{path: content}`` map (text files only)."""
        out: dict[str, str] = {}
        for rel in self.list_files():
            full = self._safe_path(rel)
            try:
                with open(full, encoding="utf-8") as handle:
                    out[rel] = handle.read()
            except (OSError, UnicodeDecodeError):
                continue
        return out

    # -- reviewable diff ----------------------------------------------------
    def diff(self) -> str:
        """A unified diff of the working tree against the mounted baseline.

        This is produced BEFORE ``apply`` — the change is REVIEWABLE first. Covers
        edited, added, and removed files. Deterministic (sorted paths, no clock)."""
        current = self.working_files()
        paths = sorted(set(self.baseline) | set(current))
        lines: list[str] = []
        for path in paths:
            before = self.baseline.get(path, "")
            after = current.get(path, "")
            if before == after:
                continue
            before_lines = before.splitlines(keepends=True)
            after_lines = after.splitlines(keepends=True)
            if before_lines and not before_lines[-1].endswith("\n"):
                before_lines[-1] += "\n"
            if after_lines and not after_lines[-1].endswith("\n"):
                after_lines[-1] += "\n"
            diff = difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
            lines.extend(diff)
        return "".join(lines)

    def apply(self) -> dict[str, str]:
        """Adopt the working tree as the new baseline — the EXPLICIT, separate step
        after a diff has been reviewed. Returns the new baseline."""
        self.baseline = self.working_files()
        return dict(self.baseline)

    def changed_files(self) -> list[str]:
        """Paths whose current content differs from the mounted baseline (sorted):
        edited, added, and removed files — the reviewable change set."""
        current = self.working_files()
        return sorted(
            path
            for path in set(self.baseline) | set(current)
            if self.baseline.get(path, "") != current.get(path, "")
        )

    # -- run declared commands / tests in an isolated worker ---------------
    def prepare_worker_run(
        self,
        *,
        effect: str = "workspace_check",
        check_source: str = "",
        check_entrypoint: str = "check",
        probe_paths: list[str] | None = None,
    ) -> tuple[WorkerRequest, int]:
        """Mint the durable lease + build the digest-bound ``WorkerRequest`` for one
        run of declared checks. Returns ``(request, now)`` where ``now`` is the logical
        frontier the lease was issued at.

        This is the Weft-touching HALF of :meth:`run_in_worker` (the durable lease is
        asserted through the established ``runtime.cells`` path — invariant 3). The
        pure execution half is :func:`execute_prepared_run`, which touches no canonical
        store, so a caller that must keep all Weft access on one thread (the API's
        single-connection sqlite rule) can prepare here and execute elsewhere."""
        weave = Weave.fold(self.weft)
        frontier = int(weave.frontier_lamport)
        lease_id = cells.create_lease(
            self.weft,
            self.author,
            step_id=self.id,
            worker=f"ws:{self.name}",
            capability_ids=[self.id],
            issued_frontier=frontier,
            expiry=frontier + 1000,
            attempt=1,
            idempotency_key=f"{self.id}:{effect}:{frontier}",
        )
        lease_cell = Weave.fold(self.weft).get(lease_id)
        if lease_cell is None:
            raise WorkspaceError(f"lease {lease_id!r} vanished immediately after being minted")
        lease = dict(lease_cell.content)

        request = WorkerRequest(
            invocation_id=f"{self.id}:{frontier}",
            job_id=self.id,
            effect=effect,
            implementation_digest=_RUNNER_DIGEST,
            arguments={
                "files": self.working_files(),
                "check_source": check_source,
                "check_entrypoint": check_entrypoint,
                "probe_paths": list(probe_paths or []),
            },
            lease=lease,
            capability_proof={"workspace": self.id},  # a proof must be present
        )
        return request, frontier

    def run_in_worker(
        self,
        *,
        effect: str = "workspace_check",
        check_source: str = "",
        check_entrypoint: str = "check",
        probe_paths: list[str] | None = None,
        lease_guard: LeaseGuard | None = None,
        timeout: int = 10,
    ) -> object:
        """Run declared checks over the working tree INSIDE an isolated worker.

        A real lease + capability proof gate the run (invariant 3); the digest-bound
        runner executes in a ``WORKSPACE`` profile worker (no network, chroot jail, no
        creds). ``check_source``/``check_entrypoint`` are the caller's DECLARED check
        (arbitrary untrusted code — which is exactly why it runs only here, confined).
        ``probe_paths`` lets a caller assert the jail: none can be read. Returns the
        ``WorkerResponse``."""
        request, frontier = self.prepare_worker_run(
            effect=effect,
            check_source=check_source,
            check_entrypoint=check_entrypoint,
            probe_paths=probe_paths,
        )
        return execute_prepared_run(
            request,
            now=frontier,
            lease_guard=lease_guard,
            timeout=timeout,
        )

    # -- durable artifacts (on the canonical Weft) -------------------------
    def produce_diff_artifact(self, diff_text: str | None = None) -> str:
        """Assert the current diff as a DURABLE Cell on the Weft and return its id.

        Because the diff lives on the append-only log, a restart re-folds it — the
        produced diff is never lost. Content is DATA (``instruction_eligible=False``);
        the artifact holds no authority."""
        text = self.diff() if diff_text is None else diff_text
        digest = blob_id(text.encode("utf-8"), kind="diff")
        artifact_id = content_id({"diff_artifact": self.id, "digest": digest})
        assert_content(
            self.weft,
            self.author,
            artifact_id,
            DIFF_ARTIFACT,
            {
                "workspace": self.id,
                "workspace_name": self.name,
                "diff": text,
                "digest": digest,
                "applied": False,
                "instruction_eligible": False,
            },
        )
        return artifact_id

    def produce_test_artifact(self, response: object) -> str:
        """Assert a worker's test/command outcome as a DURABLE Cell and return its id.

        Records the observed status + output (no fabrication — a worker's honest
        outcome), so the test result survives a restart as evidence for review."""
        status = getattr(response, "status", "UNKNOWN")
        receipt = getattr(response, "receipt_data", {}) or {}
        output = receipt.get("output")
        digest = blob_id(repr(output).encode("utf-8"), kind="test")
        artifact_id = content_id({"test_artifact": self.id, "status": status, "digest": digest})
        assert_content(
            self.weft,
            self.author,
            artifact_id,
            TEST_ARTIFACT,
            {
                "workspace": self.id,
                "workspace_name": self.name,
                "status": status,
                "output": output,
                "digest": digest,
                "instruction_eligible": False,
            },
        )
        # A durable receipt closes the effect chain (invariant 3).
        cells.record_receipt(
            self.weft,
            self.author,
            step_id=self.id,
            lease_id=self.id,
            idempotency_key=f"{artifact_id}",
            status=status if status in ("SUCCEEDED", "FAILED", "UNKNOWN") else "UNKNOWN",
            output_cell_ids=[artifact_id],
        )
        return artifact_id


def execute_prepared_run(
    request: WorkerRequest,
    *,
    now: int,
    lease_guard: LeaseGuard | None = None,
    timeout: int = 10,
    limits: dict[str, int] | None = None,
) -> object:
    """Execute a prepared workspace worker run — the PURE half of ``run_in_worker``.

    Touches NO canonical store: it only dispatches the digest-bound runner into the
    isolated ``WORKSPACE``-profile worker (``decima.workers`` — jailed, networkless,
    credential-free, fail closed). The lease inside ``request`` is still validated and
    consumed by ``run_worker`` (expired/replayed ⇒ ``LeaseError``, nothing runs)."""
    guard = lease_guard if lease_guard is not None else LeaseGuard()
    return run_worker(
        request,
        _RUNNER_SOURCE,
        "run",
        now=now,
        profile=WORKSPACE_PROFILE,
        lease_guard=guard,
        timeout=timeout,
        limits=limits,
    )


def create_workspace(
    weft: Weft,
    author: str,
    *,
    name: str,
    root: str | None = None,
    discriminator: str = "",
) -> Workspace:
    """Create an isolated workspace: a bounded host scratch tree + a durable Weft
    record. The scratch path is deliberately NOT recorded on the log (invariant 6).

    ``discriminator`` (optional, deterministic — e.g. the caller's current Weft head)
    scopes the durable workspace identity: two workspaces created with the SAME name
    but different discriminators are DISTINCT cells, so one run's mount can never
    overwrite another run's recorded file list and their content-addressed artifacts
    (which include the workspace id) never cross-link."""
    key: dict[str, str] = {"workspace": nfc(name)}
    if discriminator:
        key["at"] = str(discriminator)
    ws_id = content_id(key)
    base = root or tempfile.mkdtemp(prefix="decima-ws-")
    tree = os.path.join(base, "tree")
    os.makedirs(tree, exist_ok=True)
    assert_content(
        weft,
        author,
        ws_id,
        WORKSPACE,
        {"name": nfc(name), "mounted": False, "instruction_eligible": False},
    )
    return Workspace(id=ws_id, name=nfc(name), root=tree, weft=weft, author=author)


def get_diff_artifact(weft: Weft, artifact_id: str) -> dict | None:
    """Read a durable diff artifact back from the fold — the seam a post-restart
    reviewer uses. Returns the artifact content, or ``None`` if absent/retracted."""
    cell = Weave.fold(weft).get(artifact_id)
    if cell is None or cell.retracted or cell.type != DIFF_ARTIFACT:
        return None
    return dict(cell.content)
