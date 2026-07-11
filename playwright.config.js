// Playwright config for the Decima 0.3 browser-rendered Shell qualification (WS1).
//
// The suite drives the REAL trusted Shell (served by tests/browser/serve_fixture.py over a
// real temporary Weft on a loopback port) through visible controls only. Each spec owns its
// own server + temp data dir via tests/browser/serverManager.js, so the durability specs can
// restart the backend and rebuild projections without disturbing other specs.
//
// Determinism on this ARM host: a single worker, no parallelism, no retries. Chromium is
// Playwright's OWN bundled build (npx playwright install chromium) launched with --no-sandbox
// (the host's /usr/bin/chromium raw launch hangs; the bundled headless-shell launches fine).

const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/browser/specs",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/browser-results.json" }],
  ],
  use: {
    headless: true,
    // Same-origin app; baseURL is set per-test from the launcher's advertised port.
    ignoreHTTPSErrors: true,
    launchOptions: {
      args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
    },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
