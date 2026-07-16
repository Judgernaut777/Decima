// Planning lane — model-planned durable agents through the rendered Shell.
//
// Drives the REAL composed product path through VISIBLE controls only: type an
// objective → the model's proposal appears (visibly marked untrusted) → inspect
// steps/dependencies/budgets/capabilities/approvals → accept (the human decision) →
// durable steps appear → start → agent states update → pause (server-enforced: no new
// work) → resume → inspect the agent hierarchy → run to completion (receipt-confirmed)
// → refresh + full backend restart → the plan and its history return. The objective
// carries hostile markup which must stay inert text everywhere it is rendered.

const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { attachDiagnostics, login, gotoScreen } = require("../helpers");

// Hostile-but-plannable objective: renders as TEXT (never markup/handlers). It avoids
// the validator's executable-content blocklist on purpose — the point is that even an
// ACCEPTED plan renders hostile strings inert.
const OBJECTIVE = 'Prepare the quarterly report <img src=x onerror="window.__pwned=1"> now';

test.describe("Planning: model proposal → human acceptance → durable bounded agents", () => {
  let server;
  test.beforeAll(async () => {
    server = await new DecimaServer().start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
  });

  test("full plan lifecycle through visible controls, durable across restart", async ({ page }) => {
    const diag = attachDiagnostics(page, server.baseURL);
    await login(page, server);
    await gotoScreen(page, "plans", "Plans");

    // -- objective → model proposal, visibly marked untrusted -----------------
    await page.fill("#plan-objective", OBJECTIVE);
    await page.click('button[data-action="propose"]');
    const proposal = page.locator(".proposal-card");
    await expect(proposal).toHaveCount(1);
    await expect(proposal.locator(".zone-model .zone-label")).toHaveText(
      /model proposal \(untrusted\)/i
    );
    // hostile objective is inert TEXT: no injected element, no executed handler
    await expect(proposal).toContainText('<img src=x onerror="window.__pwned=1">');
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
    expect(await proposal.locator("img").count()).toBe(0);

    // -- inspect steps, dependencies, budgets, capabilities, approvals --------
    await expect(proposal.locator(".prop-step")).toHaveCount(4);
    await expect(proposal.locator(".prop-step").nth(1)).toContainText("needs s1");
    await expect(proposal).toContainText("Model budget");
    await expect(proposal).toContainText("4096 tokens");
    await expect(proposal).toContainText("Execution budget");
    await expect(proposal).toContainText("local:derive");
    await expect(proposal).toContainText("Expected approvals");
    // the recorded routing decision (selected model + policy) is disclosed
    await expect(proposal).toContainText("deterministic-offline");
    await expect(proposal).toContainText("Routing policy");
    // proposing alone minted NO durable plan
    await expect(page.locator(".plan-card")).toHaveCount(0);

    // -- accept: the human decision mints the durable plan --------------------
    await proposal.locator('button[data-action="accept"]').click();
    const plan = page.locator(".plan-card");
    await expect(plan).toHaveCount(1);
    await expect(plan.locator(".step-list li")).toHaveCount(4);
    await expect(plan.locator(".pill").first()).toHaveText(/DRAFT/i);
    await expect(page.locator(".proposal-card .pill").first()).toHaveText(/ACCEPTED/i);

    // the agent hierarchy is visible with objective/model/budget/capabilities
    const agents = plan.locator(".agent-card");
    await expect(agents).toHaveCount(3); // coordinator + researcher + builder
    const builder = plan.locator(".agent-card", { hasText: "builder:" });
    await expect(builder).toContainText("Token budget");
    await expect(builder).toContainText("2048");
    await expect(builder).toContainText("local:derive");
    await expect(builder).toContainText("deterministic-offline");
    await expect(builder.locator(".pill")).toHaveText(/CREATED/i);

    // -- start execution: one bounded pass; agent states update ---------------
    await plan.locator('button[data-action="start"]').click();
    await expect(page.locator(".plan-card .pill").first()).toHaveText(/ACTIVE/i);
    await expect(
      page.locator(".plan-card .step-list li", { hasText: "Ingest the reference material" })
    ).toContainText("receipt-confirmed");

    // -- pause: server-enforced, advancing dispatches NOTHING new --------------
    await page.locator('.plan-card button[data-action="pause"]').click();
    await expect(page.locator(".plan-card .pill").first()).toHaveText(/PAUSED/i);
    await page.locator('.plan-card button[data-action="advance"]').click();
    await expect(page.locator(".plan-card .pill").first()).toHaveText(/PAUSED/i);
    // still exactly ONE receipt-confirmed step — no new work while paused
    await expect(page.locator(".plan-card .step-receipt")).toHaveCount(1);

    // -- resume and run the rest to receipt-confirmed completion --------------
    // The composed plan is a serial 4-step chain (s1→s2→s3→s4), one dispatch per pass.
    await page.locator('.plan-card button[data-action="resume"]').click();
    await expect(page.locator(".plan-card .step-receipt")).toHaveCount(2);
    // researcher owns s1+s2; both are receipt-confirmed now
    const researcher = page.locator(".agent-card", { hasText: "researcher:" });
    await expect(researcher.locator(".pill")).toHaveText(/COMPLETED/i);
    await page.locator('.plan-card button[data-action="advance"]').click();
    await expect(page.locator(".plan-card .step-receipt")).toHaveCount(3);
    await page.locator('.plan-card button[data-action="advance"]').click();
    await expect(page.locator(".plan-card .pill").first()).toHaveText(/COMPLETED/i);
    await expect(page.locator(".plan-card .step-receipt")).toHaveCount(4);
    await expect(
      page.locator('.agent-card', { hasText: "coordinate:" }).locator(".pill")
    ).toHaveText(/COMPLETED/i);

    // -- durable across a browser refresh --------------------------------------
    await page.reload();
    await expect(page.locator("#app")).toBeVisible();
    await gotoScreen(page, "plans", "Plans");
    await expect(page.locator(".plan-card .pill").first()).toHaveText(/COMPLETED/i);
    await expect(page.locator(".proposal-card .pill").first()).toHaveText(/ACCEPTED/i);

    // -- durable across a FULL backend restart (fold + projection rebuild) ----
    await server.restart();
    await login(page, server);
    await gotoScreen(page, "plans", "Plans");
    const revived = page.locator(".plan-card");
    await expect(revived).toHaveCount(1);
    await expect(revived.locator(".pill").first()).toHaveText(/COMPLETED/i);
    await expect(revived.locator(".step-receipt")).toHaveCount(4);
    await expect(page.locator(".proposal-card")).toHaveCount(1);
    await expect(page.locator(".agent-card")).toHaveCount(3);
    // hostile objective STILL inert after the round-trip through the Weft
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
    expect(await revived.locator("img").count()).toBe(0);

    expect(diag.errors, "console/page errors: " + diag.errors.join(" | ")).toEqual([]);
    expect(diag.requestFailures, diag.requestFailures.join(" | ")).toEqual([]);
    expect(diag.badResponses, diag.badResponses.join(" | ")).toEqual([]);
  });
});
