# Decima — verified installation & operation procedure

_WS2 release-qualification lane. This is the DOCUMENTED procedure that the clean-install
rehearsal (`tests/install/`) executes and proves. Documented == tested: every command
below is exercised either socket-free in the pytest gate
(`tests/install/test_clean_install_rehearsal.py`) or end to end in a systemd-enabled
clean-room container (`tests/install/rehearse_clean_install.sh`)._

Decima is a **fully local, loopback-only** agent operating layer. Installation contacts
**no network**; the running Shell binds `127.0.0.1` and refuses a non-loopback bind
without an explicit override. The single secret an install owns is the master seed
(`keys/master.seed`, `0600`); it is never written to config, never copied into a backup,
and never emitted by diagnostics.

## Requirements

| Requirement | Value |
|---|---|
| OS | Linux (systemd for the optional service) |
| Python | **>= 3.11** (the installer refuses an older interpreter) |
| Runtime dependency | PyNaCl (`pynacl>=1.5`); everything else is the stdlib |
| Privileges | none — installs entirely under `$HOME`, no root, no sudo |
| Network | none, at install or run |

## 1. Install the package

From a release artifact (wheel) or the repo root:

```
pip install .            # or: pip install decima-<version>-py3-none-any.whl
```

This installs the `decima` package **and its Shell frontend assets** (the trusted
HTML/CSS/JS under `decima/shell/frontend/`, shipped as package data — without them the
Shell would serve its root as 404). It also puts the operations console scripts on PATH:
`decima-doctor`, `decima-backup`, `decima-restore`, `decima-rebuild`.

## 2. First-run provisioning + install

```
DECIMA_HOME=~/.local/share/decima \
INSTALL_SERVICE=1 \
deploy/install.sh
```

`deploy/install.sh`, in order:

1. **checks the Python floor** (>= 3.11) and that `decima` is importable — refusing loudly
   rather than half-installing;
2. runs **idempotent first-run** (`deploy/decima-firstrun`): creates the data layout,
   mints the box identity + master seed (`0600`), initializes an empty canonical Weft, and
   writes public default budgets. Re-running is safe — it never re-mints or clobbers an
   existing identity;
3. installs the deploy launchers into `$DECIMA_HOME/bin/`;
4. with `INSTALL_SERVICE=1`, installs + enables the **systemd user service** (below);
5. runs `decima-doctor` as a sanity check.

### Data layout (`$DECIMA_HOME`)

```
weft/         the Weft — the SOLE canonical store (weft.db)
artifacts/    content-addressed blobs referenced from the Weft
checkpoints/  signed integrity commitments (disposable evidence)
config/       PUBLIC config only (budgets, identity fingerprint) — NO secrets
projections/  DISPOSABLE read-models, rebuildable from the fold
logs/         operational logs (disposable; only redacted tails ever leave the box)
keys/         SECRET — master.seed (0700 dir, 0600 file). NEVER backed up.
bin/          installed deploy launchers
```

## 3. Run the Shell

Under systemd (recommended):

```
systemctl --user status decima.service      # active
```

Or directly:

```
DECIMA_HOME=~/.local/share/decima ~/.local/share/decima/bin/decima-shell-server
```

The Shell then serves on `http://127.0.0.1:8973/` (configurable via `DECIMA_PORT`):

- `GET /` → **200**, the trusted frontend, with a **strict same-origin CSP**
  (`default-src 'self'`, no `unsafe-inline`/`unsafe-eval`), `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY`;
- `GET /api/v1/...` unauthenticated → **401** (a browser session is minted by presenting
  the local pairing secret printed at startup);
- `GET /api/v1/health` → 200 (the only public endpoint).

> **Service entry point note.** The `decima-server` console script is still a stub
> (DEC-044/DEC-084). Until it graduates, the systemd unit runs the real Shell host via
> `deploy/decima-shell-server` (a thin launcher over `decima.shell.serve.serve`). When
> `decima-server` is wired, point `ExecStart` back at it and drop the two deploy
> launchers.

## 4. Diagnostics

```
decima-doctor --base ~/.local/share/decima            # human report
decima-doctor --base ~/.local/share/decima --json     # structured
decima-doctor --base ~/.local/share/decima --export   # SCRUBBED support bundle
```

`doctor` is a pure read (asserts nothing). It verifies Weft integrity (full fold),
checkpoint consistency, artifact digests, disk headroom, and unresolved effects. Overall
status is the worst check; a non-`fail` status is required for release. The `--export`
bundle is safe to hand to a maintainer: it reads `keys/` **never**, emits no raw Weft
payloads or artifact bytes, and runs every included log line through a secret redactor.

## 5. Backup & restore

```
decima-backup  --base ~/.local/share/decima --dest /path/to/backup
decima-restore --dest /path/to/backup --base ~/.local/share/decima-restored \
               --identity ~/.local/share/decima          # seed custody
```

A backup captures the **canonical event log itself** plus the durable byte-artifacts
(`artifacts/`, `checkpoints/`, `config/`) with an integrity root, so tampering is
detected offline *before* a restore. Projections are excluded (rebuildable) and **keys
are excluded** (a plaintext secret in a backup would be a second leak site).

Because the seed is not in the backup, a restore needs `--identity` pointing at your key
custody (the seed that authored the log). Restore verifies the backup, preserves a
rollback copy of any existing base, **replays every event through the kernel's acceptance
gate**, restores the artifacts (re-checking each digest), and confirms the folded
`state_root` equals the one the backup certified — failing closed on any mismatch. After
restore, re-place your custodied `keys/master.seed` into the restored base and run
`decima-rebuild` to repopulate the disposable projections.

## 6. Uninstall

```
deploy/uninstall.sh            # stop+disable service, remove launchers; PRESERVE data
deploy/uninstall.sh --purge    # ALSO delete DECIMA_HOME (asks for confirmation)
```

Uninstall is an operational action, not a data-destruction action: user data (Weft,
config, keys) and any backups outside `$DECIMA_HOME` are preserved unless you pass the
explicit `--purge` flag.

## Fault behavior (each explicit + recoverable)

| Fault | Behavior |
|---|---|
| Unsupported Python (< 3.11) | `install.sh` refuses loudly, installs nothing |
| Package not importable | `install.sh` refuses with a `pip install .` hint |
| Second install / restart | first-run is idempotent — never re-mints identity |
| Occupied port | the Shell server raises `OSError` (never silently shares) |
| Non-loopback bind | refused unless `allow_nonloopback` is set deliberately |
| Corrupt backup | `backup_verify` fails; `restore` refuses before touching the base |
| Missing identity on backup/restore | CLI fails closed (exit 1) with a clear message |
| Restore into a non-empty base | prior data moved aside to a `.rollback` copy, never deleted |
| No model provider configured | defaults to the deterministic provider; no live egress |

## Reproduce the qualification

```
# fast, socket-free, part of the normal gate
PYTHONPATH="$TESTENV:$PWD" python3 -m pytest tests/install/ -q

# full clean-room: systemd container, real pip install, service lifecycle, reboot
tests/install/rehearse_clean_install.sh docs/release-evidence/install
```

Evidence summaries are written under `docs/release-evidence/install/`.
