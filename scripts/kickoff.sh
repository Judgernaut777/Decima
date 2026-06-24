#!/usr/bin/env bash
# Bootstrap a Decima build lane for one instance: sync main, branch, and confirm
# the §11 oracle is green BEFORE any edits — so a red baseline never gets blamed
# on your change.
#
# Usage: scripts/kickoff.sh <clone-dir> <branch>
#   scripts/kickoff.sh ~/decima-claude claude/a1-merge-design
#   scripts/kickoff.sh ~/decima-codex  codex/b1-memory-taxonomy
set -euo pipefail

CLONE="${1:?usage: kickoff.sh <clone-dir> <branch>}"
BRANCH="${2:?usage: kickoff.sh <clone-dir> <branch>}"

cd "$CLONE"

# 1. Clean tree required — `git pull --rebase` refuses a dirty tree.
if [ -n "$(git status --porcelain)" ]; then
  echo "✋ working tree not clean in $CLONE — commit or stash first." >&2
  git status --short >&2
  exit 1
fi

# 2. Sync main, then branch (reuse the branch if it already exists).
git checkout main
git pull --rebase
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git checkout "$BRANCH"
else
  git checkout -b "$BRANCH"
fi

# 3. Baseline oracle must be green before you touch anything.
echo "== baseline smoke (must be green before you start) =="
log="$(mktemp)"
if ( cd heartbeat && python3 smoke.py >"$log" 2>&1 ); then
  tail -1 "$log"
else
  echo "✗ baseline smoke FAILED — do not start; investigate." >&2
  tail -8 "$log" >&2
  exit 1
fi

cat <<EOF

✓ lane ready: $CLONE on $BRANCH — baseline green.
  read your brief in docs/BACKLOG.md (and scripts/ASSIGNMENTS.md)
  loop: edit → (cd heartbeat && python3 smoke.py) → commit small →
        git pull --rebase → git push → open a PR into main
  rule: stay in your lane. If you need a file outside it, STOP and ask the
        owner — do not edit another instance's files.
EOF
