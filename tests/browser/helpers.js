// helpers.js — shared browser-driving helpers for the WS1 qualification specs.
//
// Everything here drives the rendered UI through VISIBLE controls (typing into inputs,
// clicking buttons, submitting forms) and observes the DOM the operator sees. Nothing here
// writes to SQLite, injects projections, or calls the command service directly.

const { expect } = require("@playwright/test");

// Attach console + network collectors to a page. Returns { errors, requestFailures,
// badResponses } arrays that a test can assert are empty. Same-origin only for the request
// checks (cross-origin is blocked by CSP by design and never attempted by the Shell).
function attachDiagnostics(page, baseURL) {
  const errors = [];
  const requestFailures = [];
  const badResponses = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    // Chromium logs "Failed to load resource: … status of NNN" for ANY >=400 subresource,
    // including the app's own benign, expected session probe (GET /session returns 401 when
    // no session cookie exists yet, e.g. on first load before pairing). That HTTP-status
    // noise is covered by the response/badResponses check below with an explicit allowlist;
    // it is NOT an uncaught JS error, so keep it out of the `errors` (JS-error) channel.
    if (/Failed to load resource/i.test(text)) return;
    errors.push(text);
  });
  page.on("pageerror", (err) => {
    errors.push("pageerror: " + (err && err.message));
  });
  page.on("requestfailed", (req) => {
    const url = req.url();
    if (url.startsWith(baseURL)) {
      const f = req.failure();
      requestFailures.push(url + " :: " + (f && f.errorText));
    }
  });
  page.on("response", (resp) => {
    const url = resp.url();
    if (!url.startsWith(baseURL) || resp.status() < 400) return;
    // Allowlist the one benign case: the app's boot-time session probe returns 401 before
    // the browser is paired. Every other >=400 same-origin response is recorded for tests.
    if (resp.status() === 401 && /\/api\/v1\/session(\?|$)/.test(url)) return;
    badResponses.push(resp.status() + " " + url);
  });
  return { errors, requestFailures, badResponses };
}

// Log in through the real pairing gate (visible password field + submit button).
async function login(page, server) {
  await page.goto(server.baseURL + "/");
  const gate = page.locator("#gate");
  await expect(gate).toBeVisible();
  await page.fill("#gate-secret", server.pairing);
  await page.click("#gate-form button[type=submit]");
  await expect(page.locator("#app")).toBeVisible();
  await expect(page.locator("#gate")).toBeHidden();
}

// Navigate to a screen by its sidebar nav button (data-screen=<id>) and wait for its title.
async function gotoScreen(page, screenId, title) {
  await page.click(`.nav-item[data-screen="${screenId}"]`);
  if (title) {
    await expect(page.locator("#view-title")).toHaveText(title);
  }
  // Let the screen's async withData() settle (the "Loading…" node is removed on render).
  await expect(page.locator("#view .loading")).toHaveCount(0);
}

module.exports = { attachDiagnostics, login, gotoScreen };
