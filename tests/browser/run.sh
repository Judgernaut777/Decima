#!/usr/bin/env bash
# Run the WS1 browser-rendered Shell qualification from a clean checkout.
#
# It installs the Node/Playwright dev deps + Playwright's OWN bundled Chromium (the product
# has no JS build dependency — this is test-only tooling), launches the REAL Decima backend +
# trusted Shell on an ephemeral loopback port over a fresh temp Weft, drives the rendered UI
# in headless Chromium, and asserts. See docs/release-evidence/browser/README.md.
#
# Env:
#   TESTENV   path to the pytest/site-packages env used for PYTHONPATH (so the launcher can
#             import decima). Defaults to the repo checkout only; set it if decima's deps live
#             elsewhere. The launcher itself needs only the stdlib + the decima package.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

: "${TESTENV:=$REPO_ROOT}"
export TESTENV

echo "== [1/3] install Node dev deps (@playwright/test) =="
npm install --no-audit --no-fund

echo "== [2/3] install Playwright's bundled Chromium =="
# --no-sandbox is applied at launch (playwright.config.js); the ARM host's /usr/bin/chromium
# raw launch hangs, so we rely on Playwright's own headless-shell build.
npx playwright install chromium

echo "== [3/3] run the qualification suite =="
npx playwright test --config playwright.config.js "$@"
