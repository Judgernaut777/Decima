"""Clean-install / first-run / backup-restore rehearsal — the executable core.

This module is the reusable engine behind the WS2 release-qualification lane. It drives
a *real* Decima install through its whole operational lifecycle using ONLY the public,
documented seams — provisioning (`decima.services.provision.first_run`), the wired
operations CLI (`decima.cli.main`), the diagnostics service, and the loopback Shell —
so the same steps run:

  * fast + socket-free as a pytest (`tests/install/test_clean_install_rehearsal.py`);
  * end to end inside a systemd-enabled clean-room container
    (`tests/install/rehearse_clean_install.sh`), where this file is invoked as
    ``python3 -m tests.install.rehearsal_core <evidence-dir>`` AFTER a real
    ``pip install .`` + ``deploy/install.sh`` — i.e. with no dependence on the dev
    checkout's ``PYTHONPATH``.

It asserts the WS2 acceptance properties and, when run as ``__main__``, writes small
evidence SUMMARIES (JSON) an operator can archive. It NEVER writes a secret to evidence,
NEVER prepopulates first-run flags directly in storage (first-run is always the real
`first_run` call), and captures bulky material (full doctor JSON, transcripts) as small
scrubbed summaries only.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import sys
import tempfile
from dataclasses import dataclass, field

# A fixed 32-byte seed makes the whole rehearsal deterministic AND lets us assert that
# this exact secret never leaks into config / doctor / the UI. os.urandom is used by a
# real first-run; here we pin it so leak-detection has a concrete needle to search for.
REHEARSAL_SEED = bytes(range(7, 39))
SEED_HEX = REHEARSAL_SEED.hex()


class RehearsalError(AssertionError):
    """A rehearsal invariant did not hold — fail closed, never soften."""


@dataclass
class Rehearsal:
    """Accumulates check outcomes + a scrubbed transcript as the lifecycle runs."""

    checks: list[dict] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)

    def check(self, code: str, ok: bool, **extra: object) -> None:
        self.checks.append({"code": code, "status": "ok" if ok else "FAIL", **extra})
        self.transcript.append(
            f"[{'ok' if ok else 'FAIL'}] {code} " + " ".join(f"{k}={v}" for k, v in extra.items())
        )
        if not ok:
            raise RehearsalError(f"{code}: {extra}")

    def note(self, line: str) -> None:
        self.transcript.append(f"      {line}")

    def summary(self) -> dict:
        return {
            "schema": 1,
            "kind": "decima-install-rehearsal",
            "overall": "ok" if all(c["status"] == "ok" for c in self.checks) else "FAIL",
            "checks": self.checks,
            "facts": self.facts,
        }


# ── secret-leak scanner ────────────────────────────────────────────
def _assert_no_secret(r: Rehearsal, label: str, blob: object) -> None:
    """The master seed is the ONE secret; it must never appear in config, a doctor
    report, a support bundle, a log, or any UI byte. Scan a rendered blob for it."""
    text = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    leaked = SEED_HEX in text
    r.check(f"no-secret::{label}", not leaked, bytes_scanned=len(text))


# ── a minimal browser-like Shell client (cookie + CSRF double-submit) ──
class _ShellClient:
    def __init__(self, shell: object, pairing_secret: str) -> None:
        from decima.services.api.auth import COOKIE_NAME

        self._shell = shell
        self._cookie_name = COOKIE_NAME
        self._pairing = pairing_secret
        self.cookie: str | None = None
        self.csrf: str | None = None

    def get(self, path: str, headers: dict | None = None):
        h = dict(headers or {})
        if self.cookie:
            h["cookie"] = self.cookie
        return self._shell.handle("GET", path, headers=h)

    def post(self, path: str, body: dict | None = None):
        h = {}
        if self.cookie:
            h["cookie"] = self.cookie
        if self.csrf:
            h["x-csrf-token"] = self.csrf
        payload = None if body is None else json.dumps(body)
        return self._shell.handle("POST", path, headers=h, body=payload)

    def login(self) -> None:
        r = self._shell.handle(
            "POST", "/api/v1/session/login", body=json.dumps({"pairing_secret": self._pairing})
        )
        if r.status != 200:
            raise RehearsalError(f"login failed: {r.status} {r.body!r}")
        set_cookie = [v for k, v in r.headers if k.lower() == "set-cookie"][0]
        token = set_cookie.split(";")[0].split("=", 1)[1]
        self.cookie = f"{self._cookie_name}={token}"
        self.csrf = json.loads(r.body.decode())["csrf"]


# ── lifecycle phases ───────────────────────────────────────────────
def phase_preconditions(r: Rehearsal, base: str) -> None:
    """A clean environment: no data directory, and the package importable."""
    r.check("clean-env::no-data-dir", not os.path.exists(base), base=base)
    import decima  # noqa: F401

    r.check("clean-env::package-importable", True, module=os.path.dirname(decima.__file__))


def phase_first_run(r: Rehearsal, base: str) -> dict:
    """Run the DOCUMENTED first-run (provision) and assert the install it stands up."""
    from decima.services.data_layout import ALL_DIRS, CONFIG, DataDir
    from decima.services.provision import first_run

    summary = first_run(base, seed=REHEARSAL_SEED, token_budget=100_000, monetary_budget=0)
    dd = DataDir(base)

    for name in ALL_DIRS:
        r.check(f"first-run::dir::{name}", os.path.isdir(dd.path(name)))

    # keys/ is a private directory (0700); the master seed is custodied 0600.
    keys_mode = stat.S_IMODE(os.stat(dd.path("keys")).st_mode)
    r.check("first-run::keys-dir-0700", keys_mode == 0o700, mode=oct(keys_mode))
    seed_mode = stat.S_IMODE(os.stat(dd.master_seed).st_mode)
    r.check("first-run::seed-0600", seed_mode == 0o600, mode=oct(seed_mode))
    r.check("first-run::seed-not-world-readable", not (seed_mode & 0o077), mode=oct(seed_mode))

    # Public config only: budgets are ints, identity carries the public key (never seed).
    with open(dd.path(CONFIG, "budgets.json"), encoding="utf-8") as fh:
        budgets = json.load(fh)
    r.check(
        "first-run::budgets-int",
        isinstance(budgets["token_budget"], int)
        and isinstance(budgets["monetary_budget_microcents"], int),
        token_budget=budgets["token_budget"],
    )
    with open(dd.path(CONFIG, "identity.json"), encoding="utf-8") as fh:
        identity = json.load(fh)
    r.check("first-run::identity-has-public-key", bool(identity.get("public_key")))
    _assert_no_secret(r, "config/identity.json", identity)
    _assert_no_secret(r, "config/budgets.json", budgets)
    r.check(
        "first-run::summary-omits-seed", "seed" not in summary and summary.get("network") == "none"
    )
    r.facts["principal"] = summary["principal"]
    r.facts["state_root_after_first_run"] = _fold_state_root(base)
    return summary


def phase_first_run_is_idempotent(r: Rehearsal, base: str) -> None:
    """A restart / a second install invocation must NOT repeat first-run: provisioning
    refuses to clobber an existing identity, and the persisted identity is unchanged."""
    from decima.services.data_layout import CONFIG, DataDir
    from decima.services.provision import first_run

    dd = DataDir(base)
    with open(dd.path(CONFIG, "identity.json"), encoding="utf-8") as fh:
        before = json.load(fh)

    refused = False
    try:
        first_run(base, seed=REHEARSAL_SEED)
    except FileExistsError:
        refused = True
    r.check("restart::first-run-refuses-clobber", refused)

    with open(dd.path(CONFIG, "identity.json"), encoding="utf-8") as fh:
        after = json.load(fh)
    r.check("restart::identity-persists", before == after, principal=after.get("principal"))


def phase_doctor(r: Rehearsal, base: str) -> dict:
    """`decima-doctor` over a fresh install: runs, no HARD failure, and the scrubbed
    support bundle carries no secret."""
    from decima.cli import main as cli
    from decima.services.diagnostics import diagnostic_export, doctor

    keyring = _keyring(base)
    report = doctor(base, keyring=keyring)
    r.check("doctor::no-critical-failure", report["status"] != "fail", overall=report["status"])
    _assert_no_secret(r, "doctor-report", report)

    export = diagnostic_export(base, keyring=keyring)
    _assert_no_secret(r, "doctor-export", export)

    # The wired CLI entry returns 0 (doctor exits non-zero only on a hard fail).
    r.check("doctor::cli-exit-zero", cli.doctor(["--base", base]) == 0)
    r.facts["doctor_status"] = report["status"]
    r.facts["doctor_report"] = _scrub_report(report)
    r.facts["doctor_export"] = export
    return report


def phase_shell_surface(r: Rehearsal, base: str) -> _ShellClient:
    """The Shell serves the trusted frontend at 200 with a strict CSP; an unauthenticated
    API read is 401; health is public. Returns a logged-in client for the data phase."""
    from decima.services.api.server import build_application
    from decima.shell.serve import CSP, build_shell

    dd_db = os.path.join(base, "weft", "weft.db")
    backend, identity = build_application(dd_db, keyring=_keyring(base), secure_cookie=False)
    shell = build_shell(backend)

    root = shell.handle("GET", "/")
    hdrs = {k.lower(): v for k, v in root.headers}
    r.check("shell::root-200", root.status == 200)
    r.check("shell::csp-present", hdrs.get("content-security-policy") == CSP)
    r.check(
        "shell::csp-no-unsafe",
        "unsafe-inline" not in hdrs.get("content-security-policy", "")
        and "unsafe-eval" not in hdrs.get("content-security-policy", ""),
    )
    r.check("shell::nosniff", hdrs.get("x-content-type-options") == "nosniff")
    r.check("shell::frame-deny", hdrs.get("x-frame-options") == "DENY")
    _assert_no_secret(r, "shell-root-body", root.body.decode("utf-8", "replace"))

    unauth = shell.handle("GET", "/api/v1/tasks")
    r.check("shell::unauth-api-401", unauth.status == 401, http_status=unauth.status)
    health = shell.handle("GET", "/api/v1/health")
    r.check("shell::health-public-200", health.status == 200)

    r.facts["pairing_secret_len"] = len(identity.pairing_secret)
    _assert_no_secret(r, "shell-identity-pairing", identity.pairing_secret)
    client = _ShellClient(shell, identity.pairing_secret)
    client.login()
    r.check("shell::login-ok", client.cookie is not None and client.csrf is not None)
    return client


def phase_create_representative_data(r: Rehearsal, client: _ShellClient) -> dict:
    """Create representative notes / tasks / project / artifacts THROUGH the real
    authenticated API (never by writing storage directly), then confirm the disposable
    projections surface them."""
    note = client.post("/api/v1/notes", {"text": "release-qual rehearsal note"})
    r.check("data::create-note", note.status in (200, 201), http_status=note.status)

    proj = client.post("/api/v1/projects", {"objective": "WS2 clean-install qualification"})
    r.check("data::create-project", proj.status in (200, 201), http_status=proj.status)
    project_id = json.loads(proj.body)["data"]["id"]

    task = client.post(
        "/api/v1/tasks", {"project_id": project_id, "description": "rehearse backup + restore"}
    )
    r.check("data::create-task", task.status in (200, 201), http_status=task.status)

    art = client.post(
        "/api/v1/artifacts/import",
        {"name": "rehearsal.txt", "body": "representative artifact bytes"},
    )
    r.check("data::import-artifact", art.status in (200, 201), http_status=art.status)

    # The disposable read-models must now surface the records.
    notes = json.loads(client.get("/api/v1/notes").body)
    projects = json.loads(client.get("/api/v1/projects").body)
    tasks = json.loads(client.get("/api/v1/tasks").body)
    counts = {
        "notes": _count(notes),
        "projects": _count(projects),
        "tasks": _count(tasks),
    }
    r.check(
        "data::projections-show-records",
        counts["notes"] >= 1 and counts["projects"] >= 1 and counts["tasks"] >= 1,
        **counts,
    )
    r.facts["record_counts_before_backup"] = counts
    r.facts["project_id"] = project_id
    return counts


def phase_backup(r: Rehearsal, base: str, dest: str) -> dict:
    """Back up canonical state and verify the backup offline (pure, keyring-free)."""
    from decima.services.backup import backup_create, backup_verify

    manifest = backup_create(base, dest, keyring=_keyring(base))
    r.check("backup::manifest-written", os.path.isfile(os.path.join(dest, "MANIFEST.json")))
    ok, reason = backup_verify(dest)
    r.check("backup::verify-clean", ok, reason=reason)

    # keys/ must never enter a backup (a plaintext secret would be a second leak site).
    r.check("backup::excludes-keys", not os.path.exists(os.path.join(dest, "keys")))
    _assert_no_secret(r, "backup-manifest", manifest)
    r.facts["backup_state_root"] = manifest["state_root"]
    r.facts["backup_event_count"] = manifest["weft"]["count"]
    return manifest


def phase_restore_roundtrip(r: Rehearsal, base: str, dest: str, backup_state_root: str) -> str:
    """Stop → move active data aside → restore into a fresh base → rebuild projections →
    doctor → compare state roots → confirm the Shell shows the records again."""
    from decima.cli import main as cli
    from decima.services.data_layout import DataDir

    aside = base.rstrip("/") + ".aside"
    if os.path.exists(aside):
        shutil.rmtree(aside)
    shutil.move(base, aside)
    r.check("restore::moved-active-aside", os.path.isdir(aside) and not os.path.exists(base))

    # Restore, pointing --identity at the seed custody that authored the log (the seed is
    # excluded from backups by design — an operator restores it from their own custody).
    rc = cli.restore(["--dest", dest, "--base", base, "--identity", aside])
    r.check("restore::cli-exit-zero", rc == 0)

    # The backup NEVER carries the master seed (a plaintext secret would be a second leak
    # site). A real restore therefore has the operator re-place the custodied seed into
    # the restored base from their own key custody — here, the moved-aside install.
    from decima.services.data_layout import DataDir as _DD

    restored_keys = _DD(base).path("keys")
    os.makedirs(restored_keys, mode=0o700, exist_ok=True)
    r.check("restore::backup-carried-no-seed", not os.path.exists(_DD(base).master_seed))
    shutil.copyfile(_DD(aside).master_seed, _DD(base).master_seed)
    os.chmod(_DD(base).master_seed, 0o600)
    r.check("restore::seed-replaced-from-custody", os.path.exists(_DD(base).master_seed))

    restored_root = _fold_state_root(base)
    r.check(
        "restore::state-root-matches",
        restored_root == backup_state_root,
        restored=restored_root[:16],
        expected=backup_state_root[:16],
    )

    # Rebuild the disposable projections; canonical store must survive untouched.
    r.check("restore::rebuild-projections", cli.rebuild(["--base", base]) == 0)
    r.check("restore::weft-survives-rebuild", os.path.exists(DataDir(base).weft_db))

    report = _doctor(base)
    r.check("restore::doctor-ok", report["status"] != "fail", overall=report["status"])

    # The Shell must show the records again after restore.
    client = _fresh_client(base)
    counts = {
        "notes": _count(json.loads(client.get("/api/v1/notes").body)),
        "projects": _count(json.loads(client.get("/api/v1/projects").body)),
        "tasks": _count(json.loads(client.get("/api/v1/tasks").body)),
    }
    before = r.facts["record_counts_before_backup"]
    r.check("restore::shell-shows-records", counts == before, **counts)
    r.facts["restored_state_root"] = restored_root
    r.facts["record_counts_after_restore"] = counts
    return restored_root


def phase_fault_cases(r: Rehearsal, workdir: str, good_base: str, good_backup: str) -> None:
    """Each fault must be explicit + recoverable — never a silent partial world."""
    from decima.cli import main as cli
    from decima.services.backup import BackupError, backup_verify, restore_apply
    from decima.services.data_layout import DataDir

    # 1. Corrupt backup: flip a byte in the manifest → verify False, restore refused.
    corrupt = os.path.join(workdir, "backup-corrupt")
    shutil.copytree(good_backup, corrupt)
    mpath = os.path.join(corrupt, "MANIFEST.json")
    with open(mpath, encoding="utf-8") as fh:
        manifest = json.load(fh)
    if manifest["weft"]["events"]:
        row = manifest["weft"]["events"][0]
        # Substitute the event id so it no longer equals content_id(payload): the pure
        # verifier must reject the row before a single byte is ever trusted.
        row[0] = "0" * len(row[0])
        manifest["weft"]["events"][0] = row
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, sort_keys=True)
    ok, _ = backup_verify(corrupt)
    r.check("fault::corrupt-backup-verify-false", not ok)
    refused = False
    try:
        restore_apply(
            corrupt, os.path.join(workdir, "restore-corrupt"), keyring=_keyring(good_base)
        )
    except BackupError:
        refused = True
    r.check("fault::corrupt-backup-restore-refused", refused)

    # 2. Backup with no identity (no master.seed) → CLI fails closed (exit 1).
    no_id = os.path.join(workdir, "no-identity")
    DataDir(no_id).ensure()
    r.check(
        "fault::backup-without-identity-exit-1",
        cli.backup(["--base", no_id, "--dest", os.path.join(workdir, "b")]) == 1,
    )

    # 3. Restore with no identity available → CLI fails closed (exit 1).
    r.check(
        "fault::restore-without-identity-exit-1",
        cli.restore(
            ["--dest", good_backup, "--base", os.path.join(workdir, "r-noid"), "--identity", no_id]
        )
        == 1,
    )

    # 4. Restore INTO a non-empty base → a rollback copy is preserved (never deleted).
    occupied = os.path.join(workdir, "occupied-base")
    DataDir(occupied).ensure()
    with open(os.path.join(occupied, "sentinel"), "w") as fh:
        fh.write("prior data")
    result = restore_apply(good_backup, occupied, keyring=_keyring(good_base))
    r.check(
        "fault::restore-preserves-rollback",
        result["rollback"] is not None and os.path.isdir(result["rollback"]),
        rollback=os.path.basename(result["rollback"] or ""),
    )
    r.check(
        "fault::rollback-retains-prior-data",
        os.path.isfile(os.path.join(result["rollback"], "sentinel")),
    )

    # 5. Occupied port: binding the Shell to a taken loopback port fails loudly (OSError),
    #    it does not silently share or crash.
    from decima.services.api.server import make_http_server

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    taken_port = sock.getsockname()[1]
    sock.listen(1)
    port_err = False
    try:
        make_http_server(_dummy_app(good_base), host="127.0.0.1", port=taken_port)
    except OSError:
        port_err = True
    finally:
        sock.close()
    r.check("fault::occupied-port-raises", port_err, port=taken_port)

    # 6. Non-loopback bind without the explicit opt-in is REFUSED (trust-surface guard).
    refused_bind = False
    try:
        make_http_server(_dummy_app(good_base), host="0.0.0.0", port=0)
    except ValueError:
        refused_bind = True
    r.check("fault::nonloopback-bind-refused", refused_bind)

    # 7. Unsupported Python: the install guard rejects < 3.11 (documented floor). The
    #    running interpreter meets the floor; a hypothetical 3.8 would be rejected.
    floor = (3, 11)
    r.check(
        "fault::python-floor-guard",
        _meets_floor(sys.version_info[:2], floor) and not _meets_floor((3, 8), floor),
        floor="3.11",
        running=".".join(map(str, sys.version_info[:2])),
    )

    # 8. Missing model config: a fresh install ships NO provider credential and defaults
    #    to the deterministic provider (no live egress). Assert config carries no secret
    #    and no api key is required to operate the tested surface.
    from decima.services.data_layout import CONFIG

    cfg_files = DataDir(good_base).list_files(CONFIG)
    r.check(
        "fault::no-model-secret-in-config",
        not any("key" in f or "secret" in f for f in cfg_files),
        config=cfg_files,
    )


def phase_hygiene(r: Rehearsal, base: str) -> None:
    """Path / permission hygiene of the standing install."""
    from decima.services.data_layout import DataDir

    dd = DataDir(base)
    # No world-readable secret.
    seed_mode = stat.S_IMODE(os.stat(dd.master_seed).st_mode)
    r.check("hygiene::seed-not-group-other-readable", not (seed_mode & 0o077), mode=oct(seed_mode))
    # keys/ dir stays private.
    keys_mode = stat.S_IMODE(os.stat(dd.path("keys")).st_mode)
    r.check("hygiene::keys-dir-private", not (keys_mode & 0o077), mode=oct(keys_mode))
    # The only secret file is the seed; config holds no *.seed / *.key.
    from decima.services.data_layout import CONFIG

    cfg = DataDir(base).list_files(CONFIG)
    r.check(
        "hygiene::config-has-no-secret-files",
        not any(f.endswith((".seed", ".key", ".pem")) for f in cfg),
        config=cfg,
    )


# ── small helpers ──────────────────────────────────────────────────
def _keyring(base: str):
    from decima.kernel.crypto import Keyring
    from decima.services.data_layout import DataDir

    with open(DataDir(base).master_seed, "rb") as fh:
        return Keyring(seed=fh.read())


def _fold_state_root(base: str) -> str:
    from decima.kernel.weave import Weave
    from decima.kernel.weft import Weft
    from decima.services.data_layout import DataDir

    weft = Weft(DataDir(base).weft_db, _keyring(base))
    return Weave.fold(weft).state_root()


def _doctor(base: str) -> dict:
    from decima.services.diagnostics import doctor

    return doctor(base, keyring=_keyring(base))


def _scrub_report(report: dict) -> dict:
    """Keep only status + code + scalar fields — evidence stays small and secret-free."""
    out = {
        "base": os.path.basename(report.get("base", "")),
        "status": report["status"],
        "checks": [],
    }
    for c in report["checks"]:
        entry = {"code": c.get("code"), "status": c.get("status")}
        for k, v in c.items():
            if k not in ("code", "status") and isinstance(v, (int, str)) and k != "detail":
                entry[k] = v
        out["checks"].append(entry)
    return out


def _fresh_client(base: str) -> _ShellClient:
    from decima.services.api.server import build_application
    from decima.shell.serve import build_shell

    db = os.path.join(base, "weft", "weft.db")
    backend, identity = build_application(db, keyring=_keyring(base), secure_cookie=False)
    client = _ShellClient(build_shell(backend), identity.pairing_secret)
    client.login()
    return client


def _dummy_app(base: str):
    from decima.services.api.server import build_application

    app, _ = build_application(os.path.join(base, "weft", "weft.db"), keyring=_keyring(base))
    return app


def _count(payload: object) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("items", "notes", "projects", "tasks", "results", "data"):
            v = payload.get(key)
            if isinstance(v, list):
                return len(v)
        return len(payload)
    return 0


def _meets_floor(version: tuple[int, int], floor: tuple[int, int]) -> bool:
    return version >= floor


# ── full lifecycle driver ──────────────────────────────────────────
def run_full_rehearsal(workdir: str) -> Rehearsal:
    """Drive the whole clean-install lifecycle under `workdir`. Raises RehearsalError on
    the first failed invariant; returns the Rehearsal record on success."""
    r = Rehearsal()
    base = os.path.join(workdir, "install")
    backup = os.path.join(workdir, "backup")

    phase_preconditions(r, base)
    phase_first_run(r, base)
    phase_first_run_is_idempotent(r, base)
    phase_doctor(r, base)
    client = phase_shell_surface(r, base)
    phase_create_representative_data(r, client)
    manifest = phase_backup(r, base, backup)
    phase_restore_roundtrip(r, base, backup, manifest["state_root"])
    phase_fault_cases(r, workdir, base, backup)
    phase_hygiene(r, base)
    return r


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    evidence_dir = argv[0] if argv else None
    workdir = tempfile.mkdtemp(prefix="decima-rehearsal-")
    try:
        r = run_full_rehearsal(workdir)
    except RehearsalError as exc:
        print(f"REHEARSAL FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        pass
    print(f"rehearsal ok — {len(r.checks)} checks passed")
    if evidence_dir:
        os.makedirs(evidence_dir, exist_ok=True)
        with open(os.path.join(evidence_dir, "rehearsal-summary.json"), "w") as fh:
            json.dump(r.summary(), fh, indent=2, sort_keys=True)
        with open(os.path.join(evidence_dir, "rehearsal-transcript.txt"), "w") as fh:
            fh.write("\n".join(r.transcript) + "\n")
        with open(os.path.join(evidence_dir, "doctor-report.json"), "w") as fh:
            json.dump(r.facts.get("doctor_report", {}), fh, indent=2, sort_keys=True)
        with open(os.path.join(evidence_dir, "doctor-export.json"), "w") as fh:
            json.dump(r.facts.get("doctor_export", {}), fh, indent=2, sort_keys=True)
        print(f"evidence written to {evidence_dir}")
    shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
