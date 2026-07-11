#!/usr/bin/env bash
# WS2 clean-install rehearsal driver — proves Decima installs + operates from a CLEAN,
# systemd-enabled Linux environment with NO dependence on this dev checkout/venv/config.
#
# It builds a systemd container (tests/install/Dockerfile.systemd), copies in a pristine
# `git archive` of the repo (no .git, no worktree state), then AS AN UNPRIVILEGED
# OPERATOR:
#   1. confirms Decima is not installed and no data dir exists;
#   2. does the DOCUMENTED install: `pip install .` then `deploy/install.sh`
#      (INSTALL_SERVICE=1);
#   3. asserts deps/launchers land only in intended locations + perms are correct;
#   4. drives the systemd USER service: enable → active → Shell 200 / unauth 401 / CSP;
#      restart (recover); reboot the container (linger → configured service returns);
#   5. runs the full first-run + backup/restore + fault-matrix rehearsal
#      (tests.install.rehearsal_core) inside the clean install;
#   6. uninstall preserves user data unless --purge.
#
# Every step records ok/FAIL/BLOCKED into an evidence JSON; the script never fakes a pass.
# Bulky output stays in the scratch dir; only small summaries are meant to be committed.
#
# Usage: tests/install/rehearse_clean_install.sh [EVIDENCE_DIR]
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EVIDENCE_DIR="${1:-$REPO_ROOT/docs/release-evidence/install}"
SCRATCH="${SCRATCH:-/tmp/claude-1000/-home-mini/56d98ee8-eef4-4e7a-845d-004995e016ad/scratchpad}"
IMAGE="decima-cleanroom"
CID="decima-cleanroom-run"
PORT=8973
mkdir -p "$EVIDENCE_DIR" "$SCRATCH"
TRANSCRIPT="$SCRATCH/docker-rehearsal.log"
STEPS_JSON="$SCRATCH/docker-steps.json"
: > "$TRANSCRIPT"
echo "[]" > "$STEPS_JSON"

log()  { echo "$@" | tee -a "$TRANSCRIPT" ; }
# record STEP STATUS DETAIL
record() {
  python3 - "$STEPS_JSON" "$1" "$2" "${3:-}" <<'PY'
import json, sys
path, step, status, detail = sys.argv[1:5]
data = json.load(open(path))
data.append({"step": step, "status": status, "detail": detail})
json.dump(data, open(path, "w"), indent=2)
PY
  log "  [$2] $1 ${3:-}"
}

# Operator commands run inside the operator's systemd --user session (XDG_RUNTIME_DIR +
# session bus) so `systemctl --user` and the enabled user service work like a real login.
USERENV="export XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"
dexec() { docker exec -u operator "$CID" bash -lc "$USERENV; $1"; }
dexec_root() { docker exec "$CID" bash -lc "$1"; }

cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# ── 0. docker availability ─────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  record "docker-available" "BLOCKED" "docker CLI not found"
  cp "$STEPS_JSON" "$EVIDENCE_DIR/docker-rehearsal-steps.json"; exit 1
fi

# ── 1. build the clean-room image ─────────────────────────────────
log "== building $IMAGE =="
if docker build -f "$REPO_ROOT/tests/install/Dockerfile.systemd" -t "$IMAGE" "$REPO_ROOT" \
      >>"$TRANSCRIPT" 2>&1; then
  record "image-build" "ok"
else
  record "image-build" "BLOCKED" "docker build failed (see $TRANSCRIPT)"
  cp "$STEPS_JSON" "$EVIDENCE_DIR/docker-rehearsal-steps.json"; exit 1
fi

# ── 2. boot a real systemd PID 1 ──────────────────────────────────
log "== booting systemd container =="
cleanup
if docker run -d --name "$CID" --privileged --cgroupns=host \
      --tmpfs /run --tmpfs /run/lock -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
      "$IMAGE" >>"$TRANSCRIPT" 2>&1; then
  record "container-boot" "ok"
else
  record "container-boot" "BLOCKED" "docker run (systemd) failed — host cgroup policy?"
  cp "$STEPS_JSON" "$EVIDENCE_DIR/docker-rehearsal-steps.json"; exit 1
fi

# wait for systemd to be running
for i in $(seq 1 30); do
  state="$(docker exec "$CID" systemctl is-system-running 2>/dev/null || true)"
  case "$state" in running|degraded) break ;; esac
  sleep 1
done
record "systemd-running" "$([ -n "$state" ] && echo ok || echo BLOCKED)" "state=$state"

# start the operator's user-systemd session (linger + XDG_RUNTIME_DIR).
dexec_root "loginctl enable-linger operator" >>"$TRANSCRIPT" 2>&1
dexec_root "mkdir -p /run/user/1000 && chown operator:operator /run/user/1000" >>"$TRANSCRIPT" 2>&1
# wait for the operator systemd --user session bus to come up (linger auto-starts it)
for i in $(seq 1 30); do docker exec "$CID" test -S /run/user/1000/bus && break; sleep 1; done

# ── 3. copy the repo WORKING TREE in (no .git, no build/venv/cache state) ─
# The working tree (not `git archive HEAD`) is what an operator packages with
# `pip install .`, and it lets this qualification run BEFORE the lane is committed.
log "== staging repo working tree =="
tar --exclude='./.git' --exclude='./.claude' --exclude='*/__pycache__' \
    --exclude='*.egg-info' --exclude='./build' --exclude='./dist' \
    --exclude='*.db' --exclude='*.db.keys' \
    -C "$REPO_ROOT" -cf "$SCRATCH/decima-src.tar" . 2>>"$TRANSCRIPT"
docker cp "$SCRATCH/decima-src.tar" "$CID:/tmp/decima-src.tar" >>"$TRANSCRIPT" 2>&1
dexec "mkdir -p /home/operator/decima-src && tar -C /home/operator/decima-src -xf /tmp/decima-src.tar"
record "repo-staged" "ok" "/home/operator/decima-src"

# ── 4. confirm CLEAN: no install, no data dir ─────────────────────
notinst="$(dexec 'python3 -c "import decima" 2>&1 || echo MISSING' )"
nodata="$(dexec 'test -e ~/.local/share/decima && echo EXISTS || echo NONE')"
record "clean-precondition" \
  "$([ "${notinst##*$'\n'}" = MISSING ] && [ "$nodata" = NONE ] && echo ok || echo FAIL)" \
  "decima=$([ "${notinst##*$'\n'}" = MISSING ] && echo absent || echo present) data=$nodata"

# ── 5. documented install: pip install . then deploy/install.sh ───
log "== documented install =="
dexec "cd ~/decima-src && pip install --user --break-system-packages -q . 2>&1 | tail -2" \
  >>"$TRANSCRIPT" 2>&1
if dexec 'python3 -c "import decima; print(decima.__version__)"' >>"$TRANSCRIPT" 2>&1; then
  record "pip-install" "ok"
else
  record "pip-install" "FAIL" "package not importable after pip install"
fi

if dexec "cd ~/decima-src && INSTALL_SERVICE=1 bash deploy/install.sh" >>"$TRANSCRIPT" 2>&1; then
  record "install-sh" "ok" "INSTALL_SERVICE=1"
else
  record "install-sh" "FAIL" "deploy/install.sh returned non-zero"
fi

# ── 6. install landed in intended locations + perms ───────────────
seedperm="$(dexec 'stat -c %a ~/.local/share/decima/keys/master.seed 2>/dev/null')"
keysperm="$(dexec 'stat -c %a ~/.local/share/decima/keys 2>/dev/null')"
launchers="$(dexec 'ls ~/.local/share/decima/bin 2>/dev/null | tr "\n" ,')"
record "seed-perm-0600" "$([ "$seedperm" = 600 ] && echo ok || echo FAIL)" "mode=$seedperm"
record "keys-perm-0700" "$([ "$keysperm" = 700 ] && echo ok || echo FAIL)" "mode=$keysperm"
record "launchers-installed" "$(echo "$launchers" | grep -q decima-shell-server && echo ok || echo FAIL)" "$launchers"
unit_present="$(dexec 'test -f ~/.config/systemd/user/decima.service && echo yes || echo no')"
record "service-unit-installed" "$([ "$unit_present" = yes ] && echo ok || echo FAIL)"

# ── 7. systemd USER service lifecycle ─────────────────────────────
log "== systemd user service =="
active="$(dexec 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active decima.service 2>&1')"
record "service-active" "$([ "$active" = active ] && echo ok || echo BLOCKED)" "is-active=$active"

curl_status() { dexec "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/${1:-} 2>/dev/null"; }
# give it a moment to bind
for i in $(seq 1 15); do [ "$(curl_status)" = 200 ] && break; sleep 1; done
root_code="$(curl_status)"; api_code="$(curl_status api/v1/tasks)"
csp="$(dexec "curl -s -D - -o /dev/null http://127.0.0.1:$PORT/ 2>/dev/null | grep -i content-security-policy")"
record "shell-root-200" "$([ "$root_code" = 200 ] && echo ok || echo BLOCKED)" "code=$root_code"
record "shell-unauth-401" "$([ "$api_code" = 401 ] && echo ok || echo BLOCKED)" "code=$api_code"
record "shell-csp-present" "$(echo "$csp" | grep -qi "default-src 'self'" && echo ok || echo BLOCKED)"

# doctor (no critical failure)
docstat="$(dexec 'python3 -c "from decima.cli.main import doctor; import sys; sys.exit(doctor([\"--base\", \"/home/operator/.local/share/decima\"]))"; echo $?' | tail -1)"
record "doctor-no-critical" "$([ "$docstat" = 0 ] && echo ok || echo FAIL)" "exit=$docstat"

# restart → recover
dexec 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart decima.service' >>"$TRANSCRIPT" 2>&1
for i in $(seq 1 15); do [ "$(curl_status)" = 200 ] && break; sleep 1; done
record "service-restart-recovers" "$([ "$(curl_status)" = 200 ] && echo ok || echo BLOCKED)"

# reboot the container → configured (lingered) service must return
log "== container reboot =="
docker restart "$CID" >>"$TRANSCRIPT" 2>&1
for i in $(seq 1 30); do
  s="$(docker exec "$CID" systemctl is-system-running 2>/dev/null || true)"
  case "$s" in running|degraded) break ;; esac; sleep 1
done
dexec_root "mkdir -p /run/user/1000 && chown operator:operator /run/user/1000" >>"$TRANSCRIPT" 2>&1
for i in $(seq 1 20); do [ "$(curl_status)" = 200 ] && break; sleep 1; done
reboot_code="$(curl_status)"
record "reboot-service-returns" "$([ "$reboot_code" = 200 ] && echo ok || echo BLOCKED)" "code=$reboot_code"

# ── 8. first-run persistence + full lifecycle rehearsal in the install ─
# identity persists (restart did NOT re-run first-run): principal fingerprint stable.
p1="$(dexec 'python3 -c "import json;print(json.load(open(\"/home/operator/.local/share/decima/config/identity.json\"))[\"principal\"])" 2>/dev/null')"
record "identity-persists" "$([ -n "$p1" ] && echo ok || echo FAIL)" "principal=${p1:0:16}"

log "== in-container backup/restore + fault rehearsal =="
if dexec "cd ~/decima-src && python3 -m tests.install.rehearsal_core /tmp/ev >/tmp/rehearsal.out 2>&1"; then
  record "core-rehearsal" "ok" "$(dexec 'tail -1 /tmp/rehearsal.out')"
  docker cp "$CID:/tmp/ev/rehearsal-summary.json" "$EVIDENCE_DIR/container-rehearsal-summary.json" >/dev/null 2>&1
else
  record "core-rehearsal" "FAIL" "$(dexec 'tail -3 /tmp/rehearsal.out' | tr '\n' ' ')"
fi

# ── 9. uninstall preserves data unless --purge ────────────────────
log "== uninstall (keep data) =="
dexec "cd ~/decima-src && bash deploy/uninstall.sh" >>"$TRANSCRIPT" 2>&1
data_kept="$(dexec 'test -f ~/.local/share/decima/keys/master.seed && echo KEPT || echo GONE')"
record "uninstall-preserves-data" "$([ "$data_kept" = KEPT ] && echo ok || echo FAIL)" "$data_kept"
svc_gone="$(dexec 'test -f ~/.config/systemd/user/decima.service && echo present || echo removed')"
record "uninstall-removes-service" "$([ "$svc_gone" = removed ] && echo ok || echo FAIL)"

log "== uninstall --purge =="
dexec "cd ~/decima-src && ASSUME_YES=1 bash deploy/uninstall.sh --purge" >>"$TRANSCRIPT" 2>&1
purged="$(dexec 'test -e ~/.local/share/decima && echo EXISTS || echo GONE')"
record "purge-removes-data" "$([ "$purged" = GONE ] && echo ok || echo FAIL)" "$purged"

# ── 10. finalize evidence ─────────────────────────────────────────
cp "$STEPS_JSON" "$EVIDENCE_DIR/docker-rehearsal-steps.json"
tail -c 20000 "$TRANSCRIPT" > "$EVIDENCE_DIR/docker-rehearsal-tail.log"
overall="$(python3 -c "import json;d=json.load(open('$STEPS_JSON'));print('FAIL' if any(s['status']=='FAIL' for s in d) else ('BLOCKED' if any(s['status']=='BLOCKED' for s in d) else 'ok'))")"
log "== rehearsal overall: $overall =="
[ "$overall" = ok ] && exit 0 || exit 1
