# WS2 — clean-install / first-run / backup-restore evidence

Release-qualification evidence for the WS2 lane. Two independent executions of the same
documented lifecycle back these files; both are reproducible (commands below).

## Files

| File | What it is |
|---|---|
| `environment-manifest.json` | Host + clean-room environment the rehearsal ran in |
| `rehearsal-summary.json` | In-process (socket-free) rehearsal: every WS2 check + outcome |
| `rehearsal-transcript.txt` | Human-readable transcript of the same run |
| `doctor-report.json` | `decima-doctor` structured report over the fresh install (scrubbed) |
| `doctor-export.json` | `decima-doctor --export` scrubbed support bundle (proof: no secret) |
| `docker-rehearsal-steps.json` | Clean-room (systemd container) per-step ok/FAIL/BLOCKED ledger |
| `docker-rehearsal-tail.log` | Tail of the clean-room transcript |
| `container-rehearsal-summary.json` | The in-container run of the same lifecycle rehearsal |

## The two executions

1. **In-process (socket-free), part of the pytest gate.** `tests.install.rehearsal_core`
   drives first-run, doctor, the Shell surface (200 / unauth-401 / strict-CSP),
   representative data through the authenticated API, backup → move-aside → restore
   state-root round-trip, and the fault matrix. Reproduce:

   ```
   PYTHONPATH="$TESTENV:$PWD" python3 -m pytest tests/install/ -q
   PYTHONPATH="$TESTENV:$PWD" python3 -m tests.install.rehearsal_core docs/release-evidence/install
   ```

2. **Clean-room, systemd-enabled Docker container.** A fresh `debian:bookworm-slim` with a
   real systemd PID 1 and an unprivileged `operator` account — NO dev checkout, venv, or
   config. It does the documented `pip install .` + `deploy/install.sh` (INSTALL_SERVICE=1),
   drives the systemd USER service (enable → active → Shell 200 / 401 / CSP → restart →
   container reboot → service returns), runs the full lifecycle rehearsal, and proves
   uninstall preserves data unless `--purge`. Reproduce:

   ```
   tests/install/rehearse_clean_install.sh docs/release-evidence/install
   ```

## Defects found + fixed in this lane (documented == tested)

- **Shell frontend was not packaged.** A real `pip install .` shipped no
  `decima/shell/frontend/*`, so `GET /` served 404 — the trusted UI was entirely absent.
  Fixed by adding `[tool.setuptools.package-data]` for the frontend (pyproject).
- **Service unit failed to start as a user service (`218/CAPABILITIES`).** Hardening
  directives that drop capability-bounding-set entries can't be applied by an unprivileged
  `systemd --user` manager. Reduced to the user-service-enforceable subset; the deeper
  directives are retained as documented guidance for a privileged/system deployment.
- **`StartLimitIntervalSec` was in `[Service]`** (silently ignored) — moved to `[Unit]`.
- **`ExecStartPre` re-ran provision on every boot** and would fail on the second boot
  (provision refuses to clobber an existing seed). Replaced with an idempotent first-run
  wrapper (`deploy/decima-firstrun`).
- **`decima-server` is a DEC-044 stub** (exits non-zero). The unit now runs the real Shell
  host via `deploy/decima-shell-server`; when `decima-server` graduates, point `ExecStart`
  back at it.
- **`install.sh` aborted on unset `$USER`** under `set -u` — derive it via `id -un`.

## Honest scope notes

- Host systemd is degraded / has no user instance, so the service lifecycle is proven in
  the container, not on the host. If Docker/systemd-in-container is unavailable on a given
  operator's box, the `docker-rehearsal-steps.json` records the exact BLOCKED step and its
  reproduce command; the in-process rehearsal still fully covers everything except the
  systemd-manager and reboot mechanics.
