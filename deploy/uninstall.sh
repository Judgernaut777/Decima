#!/usr/bin/env bash
# Decima uninstall (handoff §12-13). Removes the SERVICE and the installed launchers,
# and by default PRESERVES all user data (the Weft, artifacts, config, keys, and any
# backups). Destroying canonical state requires an explicit, deliberate flag.
#
#   deploy/uninstall.sh              # stop+disable the service, remove launchers; KEEP data
#   deploy/uninstall.sh --purge      # ALSO delete DECIMA_HOME (irreversible — asks first)
#
# Rationale: an uninstall is an operational action, not a data-destruction action. The
# master seed and the append-only Weft are the user's; losing them silently would be a
# far worse failure than leaving a data directory behind. --purge never removes a backup
# directory that lives OUTSIDE DECIMA_HOME.

set -euo pipefail

DECIMA_HOME="${DECIMA_HOME:-$HOME/.local/share/decima}"
PURGE=0
ASSUME_YES="${ASSUME_YES:-0}"
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

echo "decima uninstall → $DECIMA_HOME"

# 1. Stop + disable the systemd user unit if present.
UNIT="$HOME/.config/systemd/user/decima.service"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now decima.service 2>/dev/null || true
fi
if [ -f "$UNIT" ]; then
  rm -f "$UNIT"
  command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload 2>/dev/null || true
  echo "  removed systemd unit $UNIT"
fi

# 2. Remove the installed launchers (operational binaries — not user data).
rm -f "$DECIMA_HOME/bin/decima-firstrun" "$DECIMA_HOME/bin/decima-shell-server"
rmdir "$DECIMA_HOME/bin" 2>/dev/null || true
echo "  removed installed launchers"

# 3. Data: preserved unless --purge.
if [ "$PURGE" != "1" ]; then
  echo "  user data PRESERVED at $DECIMA_HOME (pass --purge to delete it)"
  echo "decima uninstall complete (data kept)."
  exit 0
fi

echo "  --purge: this DELETES $DECIMA_HOME including the master seed and the Weft."
if [ "$ASSUME_YES" != "1" ]; then
  printf "  type the data directory path to confirm deletion: "
  read -r confirm
  if [ "$confirm" != "$DECIMA_HOME" ]; then
    echo "  confirmation did not match — aborting, data KEPT." >&2
    exit 1
  fi
fi
rm -rf "$DECIMA_HOME"
echo "decima uninstall complete (data purged)."
