#!/usr/bin/env bash
# Decima local install sketch (handoff §12-13). Fully LOCAL — no network is contacted.
#
# What it does, in order:
#   1. pick a data directory (DECIMA_HOME, default ~/.local/share/decima);
#   2. run first-run provisioning (creates the data layout, mints the box identity +
#      master seed 0600, initializes an empty Weft, writes public default budgets);
#   3. install the systemd USER unit and enable it (kept off by default here — flip
#      INSTALL_SERVICE=1 once decima-server is wired, DEC-044).
#
# This is a SKETCH: it is intentionally conservative (set -euo pipefail, no sudo, no
# curl/wget, no package fetch). Adapt paths to your packaging.

set -euo pipefail

DECIMA_HOME="${DECIMA_HOME:-$HOME/.local/share/decima}"
INSTALL_SERVICE="${INSTALL_SERVICE:-0}"
UNIT_SRC="$(cd "$(dirname "$0")" && pwd)/decima.service"

echo "decima install → $DECIMA_HOME"

# 1 + 2. first-run provisioning (idempotent on the layout; refuses to clobber a seed).
if [ -f "$DECIMA_HOME/keys/master.seed" ]; then
  echo "  identity already present — skipping first-run"
else
  python3 -m decima.services.provision "$DECIMA_HOME"
fi

# 3. systemd user unit (opt-in until the server entry point is wired).
if [ "$INSTALL_SERVICE" = "1" ]; then
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  sed "s#%h/.local/share/decima#$DECIMA_HOME#g" "$UNIT_SRC" > "$UNIT_DIR/decima.service"
  systemctl --user daemon-reload
  systemctl --user enable --now decima.service
  loginctl enable-linger "$USER" || true
  echo "  systemd user service installed + enabled"
else
  echo "  systemd unit NOT installed (set INSTALL_SERVICE=1 once decima-server is wired)"
  echo "  unit template: $UNIT_SRC"
fi

# 4. sanity: run the doctor (keyring-aware; exits non-zero only on a hard failure).
echo "decima install → running doctor"
decima-doctor --base "$DECIMA_HOME" || true

echo "decima install complete."
