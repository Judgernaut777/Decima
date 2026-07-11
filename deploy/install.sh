#!/usr/bin/env bash
# Decima local install (handoff §12-13). Fully LOCAL — no network is contacted.
#
# What it does, in order:
#   0. verify the interpreter meets the supported Python floor (>= 3.11);
#   1. pick a data directory (DECIMA_HOME, default ~/.local/share/decima);
#   2. run IDEMPOTENT first-run provisioning (creates the data layout, mints the box
#      identity + master seed 0600, initializes an empty Weft, writes public default
#      budgets) — skipped cleanly if an identity already exists;
#   3. install the deploy launchers into <DECIMA_HOME>/bin and, opt-in, the systemd USER
#      unit (INSTALL_SERVICE=1) enabled to start the real loopback Shell.
#
# Conservative by construction: set -euo pipefail, no sudo, no curl/wget, no package
# fetch. Adapt paths to your packaging. Assumes the `decima` package is importable
# (`pip install .` from the repo root, or an installed wheel).

set -euo pipefail

DECIMA_HOME="${DECIMA_HOME:-$HOME/.local/share/decima}"
INSTALL_SERVICE="${INSTALL_SERVICE:-0}"
PYTHON="${PYTHON:-python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT_SRC="$HERE/decima.service"
PY_FLOOR_MAJOR=3
PY_FLOOR_MINOR=11

echo "decima install → $DECIMA_HOME"

# 0. Python floor guard: refuse an unsupported interpreter loudly (do not half-install).
if ! "$PYTHON" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= ($PY_FLOOR_MAJOR, $PY_FLOOR_MINOR) else 1)"; then
  ver="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
  echo "  ERROR: Decima needs Python >= ${PY_FLOOR_MAJOR}.${PY_FLOOR_MINOR}; found $ver ($PYTHON)" >&2
  exit 1
fi

# Confirm the package is importable before doing anything durable.
if ! "$PYTHON" -c "import decima" 2>/dev/null; then
  echo "  ERROR: the 'decima' package is not importable by $PYTHON." >&2
  echo "         install it first (e.g. 'pip install .' from the repo root)." >&2
  exit 1
fi

# 1 + 2. Idempotent first-run provisioning (refuses to clobber an existing seed).
"$PYTHON" "$HERE/decima-firstrun" "$DECIMA_HOME"

# 3a. Install the deploy launchers into the install's own bin/ (the one writable path the
#     hardened unit is allowed, and the location the unit references).
mkdir -p "$DECIMA_HOME/bin"
install -m 0755 "$HERE/decima-firstrun" "$DECIMA_HOME/bin/decima-firstrun"
install -m 0755 "$HERE/decima-shell-server" "$DECIMA_HOME/bin/decima-shell-server"
echo "  launchers installed → $DECIMA_HOME/bin"

# 3b. systemd user unit (opt-in). Substitute the real DECIMA_HOME + launcher paths.
if [ "$INSTALL_SERVICE" = "1" ]; then
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  sed \
    -e "s#%h/.local/share/decima#$DECIMA_HOME#g" \
    "$UNIT_SRC" > "$UNIT_DIR/decima.service"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
    systemctl --user enable --now decima.service
    # `$USER` is not always set (e.g. a non-login shell / `docker exec`); derive it so a
    # missing USER never aborts the install under `set -u`.
    loginctl enable-linger "$(id -un)" 2>/dev/null || true
    echo "  systemd user service installed + enabled"
  else
    echo "  systemd unit written to $UNIT_DIR/decima.service (no systemctl on PATH)"
  fi
else
  echo "  systemd unit NOT installed (set INSTALL_SERVICE=1 to enable the service)"
  echo "  unit template: $UNIT_SRC"
fi

# 4. Sanity: run the doctor (keyring-aware; exits non-zero only on a hard failure).
echo "decima install → running doctor"
if command -v decima-doctor >/dev/null 2>&1; then
  decima-doctor --base "$DECIMA_HOME" || true
else
  "$PYTHON" -m decima.cli.main --help >/dev/null 2>&1 || true
  "$PYTHON" -c "from decima.cli.main import doctor; import sys; sys.exit(doctor(['--base', '$DECIMA_HOME']))" || true
fi

echo "decima install complete."
echo "  data directory: $DECIMA_HOME"
echo "  identity/seed : $DECIMA_HOME/keys/master.seed (0600, never backed up)"
echo "  start the Shell: DECIMA_HOME=$DECIMA_HOME $DECIMA_HOME/bin/decima-shell-server"
