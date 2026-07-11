// Cross-scenario security + hygiene asserts.
//
// Covers: a strict CSP header on both the static surface and API replies; unauthenticated
// /api/* -> 401; imported HTML/Markdown cannot execute script; and — across a full nav walk
// of every primary screen — no uncaught console/page errors and no FAILED same-origin
// requests. (The product ships no clickable inline "citations", so "no citation to a
// nonexistent segment" is N/A here and documented as a product gap in the evidence README.)

const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { attachDiagnostics, login, gotoScreen } = require("../helpers");
const { IMPORTED_SCRIPT_HTML } = require("../fixtures/content");

test.describe("Cross-scenario security + hygiene", () => {
  let server;
  test.beforeAll(async () => {
    server = await new DecimaServer().start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
  });

  test("unauth /api/* is 401 and health is public", async ({ request }) => {
    // `request` is an independent context with no session cookie.
    for (const p of ["/api/v1/tasks", "/api/v1/projects", "/api/v1/agents", "/api/v1/notes", "/api/v1/approvals", "/api/v1/activity"]) {
      const r = await request.get(server.baseURL + p);
      expect(r.status(), p + " must be 401 unauthenticated").toBe(401);
    }
    const health = await request.get(server.baseURL + "/api/v1/health");
    expect(health.status()).toBe(200);
  });

  test("strict CSP header is present on static and API responses", async ({ request }) => {
    const staticResp = await request.get(server.baseURL + "/");
    const csp = staticResp.headers()["content-security-policy"];
    expect(csp, "CSP on static surface").toBeTruthy();
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).not.toContain("unsafe-inline");
    expect(csp).not.toContain("unsafe-eval");
    // Defense-in-depth headers.
    expect(staticResp.headers()["x-content-type-options"]).toBe("nosniff");
    expect(staticResp.headers()["x-frame-options"]).toBe("DENY");

    // The API surface also carries the frame/referrer hardening the Shell layers on.
    const apiResp = await request.get(server.baseURL + "/api/v1/health");
    expect(apiResp.headers()["x-frame-options"]).toBe("DENY");
  });

  test("imported HTML/Markdown cannot execute script", async ({ page }) => {
    attachDiagnostics(page, server.baseURL);
    let dialogFired = false;
    page.on("dialog", async (d) => {
      dialogFired = true;
      await d.dismiss();
    });
    await login(page, server);
    await gotoScreen(page, "knowledge", "Knowledge");
    await page.fill("#new-note-text", IMPORTED_SCRIPT_HTML);
    await page.locator("#new-note-eligible").uncheck();
    await page.click(".stacked-form button[type=submit]");
    const card = page.locator(".zone-untrusted", { hasText: "Normal" });
    await expect(card).toHaveCount(1);
    // No script ran and no smuggled element was materialised.
    expect(await page.evaluate(() => Boolean(window.__DECIMA_IMPORT_RAN__))).toBe(false);
    expect(dialogFired).toBe(false);
    await expect(card.locator("script")).toHaveCount(0);
    await expect(card.locator("iframe")).toHaveCount(0);
    // The markdown/HTML shows literally.
    await expect(card.locator(".note-text")).toContainText("<script>");
    await expect(card.locator(".note-text")).toContainText("**markdown**");
  });

  test("no console errors or failed same-origin requests across a full nav walk", async ({
    page,
  }) => {
    const diag = attachDiagnostics(page, server.baseURL);
    await login(page, server);
    const screens = [
      ["today", "Today"],
      ["conversation", "Conversation"],
      ["projects", "Projects"],
      ["knowledge", "Knowledge"],
      ["plans", "Plans"],
      ["approvals", "Approval inbox"],
      ["capabilities", "Capability inspector"],
      ["activity", null],
      ["settings", null],
    ];
    for (const [id, title] of screens) {
      await gotoScreen(page, id, title);
    }
    expect(diag.errors, "console/page errors: " + diag.errors.join(" | ")).toEqual([]);
    expect(diag.requestFailures, diag.requestFailures.join(" | ")).toEqual([]);
    expect(
      diag.badResponses,
      "unexpected >=400 same-origin responses: " + diag.badResponses.join(" | ")
    ).toEqual([]);
  });
});
