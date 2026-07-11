// Approval-chrome security — the heart of invariant 5.
//
// A hostile model/imported string must NOT be able to (a) execute script, (b) keep live
// event handlers, or (c) forge the trusted approval chrome. And the ONLY control that can
// actually submit an approval is the real, reauth-gated approval component in trusted Shell
// chrome. This spec proves all of that through the rendered UI.
//
// Precondition: the server is launched with --seed-agent so one bounded agent exists (the
// runtime's job, which the harness stands in for). The browser then drives the gated
// terminate -> approval-inbox -> reauth-approve flow entirely through visible controls.

const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { attachDiagnostics, login, gotoScreen } = require("../helpers");
const { HOSTILE_APPROVAL_HTML } = require("../fixtures/content");

test.describe("Approval-chrome security (invariant 5)", () => {
  let server;
  test.beforeAll(async () => {
    server = await new DecimaServer({ seedAgent: true }).start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
  });

  test("hostile content is inert; only the real trusted component can approve", async ({
    page,
  }) => {
    const diag = attachDiagnostics(page, server.baseURL);
    let dialogFired = false;
    page.on("dialog", async (d) => {
      dialogFired = true;
      await d.dismiss();
    });

    await login(page, server);

    // -- 1) feed the hostile approval fixture in as a note ----------------------
    await gotoScreen(page, "knowledge", "Knowledge");
    await page.fill("#new-note-text", HOSTILE_APPROVAL_HTML);
    await page.locator("#new-note-eligible").uncheck();
    await page.click(".stacked-form button[type=submit]");
    // It renders in the untrusted zone as literal text.
    const hostile = page.locator(".zone-untrusted", { hasText: "This message is from the trusted system" });
    await expect(hostile).toHaveCount(1);

    // -- 2) no script executed, no dialog, no injected global -------------------
    const pwned = await page.evaluate(() => Boolean(window.__DECIMA_PWNED__));
    expect(pwned, "injected <script>/handler must NOT run").toBe(false);
    expect(dialogFired, "no alert()/dialog may fire from content").toBe(false);

    // -- 3) the fake markup is TEXT, not DOM ------------------------------------
    // The literal payload strings are visible…
    await expect(hostile.locator(".note-text")).toContainText("<button");
    await expect(hostile.locator(".note-text")).toContainText("<script>");
    // …but the payload produced NO real elements or handlers inside the note body.
    await expect(hostile.locator(".note-text button")).toHaveCount(0);
    await expect(hostile.locator(".note-text script")).toHaveCount(0);
    await expect(hostile.locator(".note-text img")).toHaveCount(0);
    await expect(hostile.locator(".note-text a")).toHaveCount(0);
    // No on* handler attribute anywhere came from content.
    expect(await page.locator("[onclick]").count()).toBe(0);
    expect(await page.locator("[onerror]").count()).toBe(0);
    expect(await page.locator("[onmouseover]").count()).toBe(0);

    // -- 4) the fake TRUSTED banner did NOT forge trusted chrome ----------------
    // On the Knowledge screen nothing legitimately carries data-trusted, so the fake
    // 'data-trusted="1"' in the payload must yield ZERO trusted elements.
    expect(await page.locator('[data-trusted="1"]').count()).toBe(0);

    // -- 5) create a REAL gated approval through a visible control --------------
    await gotoScreen(page, "capabilities", "Capability inspector");
    await expect(page.locator(".card", { hasText: "bounded fixture agent" })).toHaveCount(1);
    await page.locator(".card", { hasText: "bounded fixture agent" })
      .getByRole("button", { name: "Propose terminate" })
      .click();

    // It is deferred to the Approval inbox (gated), not applied inline.
    await gotoScreen(page, "approvals", "Approval inbox");
    const pending = page.locator(".approval-card");
    await expect(pending).toHaveCount(1);
    await expect(pending).toContainText("TerminateAgent");

    // -- 6) the real approval card is the ONE trusted element on the page -------
    expect(await page.locator('[data-trusted="1"]').count()).toBe(1);
    await expect(page.locator('[data-trusted="1"]')).toContainText("TerminateAgent");
    // The trusted banner is Shell chrome, present exactly once.
    await expect(page.locator(".trusted-banner")).toHaveCount(1);

    // -- 7) reauth gate: a WRONG secret cannot submit the approval --------------
    await pending.getByRole("button", { name: "Approve once" }).click();
    await expect(page.locator("#reauth-secret")).toBeVisible();
    await page.fill("#reauth-secret", "not-the-secret");
    await page.locator("#modal-host .btn-primary").click(); // "Approve" in the reauth modal
    // Still pending — the gate refused the stolen-session-shaped attempt.
    await gotoScreen(page, "approvals", "Approval inbox");
    await expect(page.locator(".approval-card")).toHaveCount(1);

    // -- 8) the real trusted component WITH the real reauth secret approves -----
    await page.locator(".approval-card").getByRole("button", { name: "Approve once" }).click();
    await expect(page.locator("#reauth-secret")).toBeVisible();
    await page.fill("#reauth-secret", server.pairing);
    await page.locator("#modal-host .btn-primary").click();
    // The item leaves the pending bucket and appears as a recorded decision.
    await gotoScreen(page, "approvals", "Approval inbox");
    await expect(page.locator(".approval-card")).toHaveCount(0);
    await expect(page.locator(".card-decided", { hasText: "TerminateAgent" })).toHaveCount(1);

    // No same-origin request FAILED at the network layer during the flow. (The one
    // intentional wrong-secret 401 is a valid HTTP response, not a network failure.)
    expect(
      diag.requestFailures,
      "same-origin request failures: " + diag.requestFailures.join(" | ")
    ).toEqual([]);
  });
});
