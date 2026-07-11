// Scenario B — project + plan lifecycle + capability/agent inspector, through the UI.
//
// HONEST SCOPE NOTE: the shipped 0.3 Shell does NOT drive a model to GENERATE a plan, does
// not surface an "accept plan" step, and has no runtime that spawns an agent hierarchy from
// the UI (see docs/release-evidence/browser/README.md, "Product gaps"). What it DOES expose
// and this spec qualifies: create a project (a Plan) through a visible form; its durable
// appearance; the plan START/PAUSE lifecycle controls and their reflected status; the
// capability/agent INSPECTOR disclosing a bounded agent's objective / principal / budgets /
// deadline / status; and that a gated terminate/revoke PROPOSAL is deferred to the trusted
// Approval inbox rather than applied inline. The agent used here is a harness precondition
// (--seed-agent) standing in for the runtime.

const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { attachDiagnostics, login, gotoScreen } = require("../helpers");

test.describe("Scenario B: project + plan lifecycle + inspector", () => {
  let server;
  test.beforeAll(async () => {
    server = await new DecimaServer({ seedAgent: true }).start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
  });

  test("create project (durable), start/pause plan, inspect bounded agent, gated proposal defers", async ({
    page,
  }) => {
    const diag = attachDiagnostics(page, server.baseURL);
    await login(page, server);

    const OBJECTIVE = "Ship the 0.3 daily driver";

    // -- create a project via the visible form ---------------------------------
    await gotoScreen(page, "projects", "Projects");
    await page.fill("#new-project-objective", OBJECTIVE);
    await page.click(".inline-form button[type=submit]");
    await expect(page.locator(".card", { hasText: OBJECTIVE })).toHaveCount(1);

    // durable across a browser refresh
    await page.reload();
    await expect(page.locator("#app")).toBeVisible();
    await gotoScreen(page, "projects", "Projects");
    await expect(page.locator(".card", { hasText: OBJECTIVE })).toHaveCount(1);

    // -- plan lifecycle: START then PAUSE, status reflected --------------------
    await gotoScreen(page, "plans", "Plans");
    const planCard = page.locator(".card", { hasText: OBJECTIVE });
    await expect(planCard).toHaveCount(1);
    await planCard.getByRole("button", { name: "Start" }).click();
    await expect(
      page.locator(".card", { hasText: OBJECTIVE }).locator(".pill")
    ).toHaveText(/ACTIVE/i);
    await page.locator(".card", { hasText: OBJECTIVE }).getByRole("button", { name: "Pause" }).click();
    await expect(
      page.locator(".card", { hasText: OBJECTIVE }).locator(".pill")
    ).toHaveText(/PAUSED/i);

    // durable across a backend restart + projection rebuild
    await server.restart();
    await login(page, server);
    await gotoScreen(page, "plans", "Plans");
    await expect(
      page.locator(".card", { hasText: OBJECTIVE }).locator(".pill")
    ).toHaveText(/PAUSED/i);

    // -- capability / agent inspector discloses the bounded agent --------------
    await gotoScreen(page, "capabilities", "Capability inspector");
    const agentCard = page.locator(".card", { hasText: "bounded fixture agent" });
    await expect(agentCard).toHaveCount(1);
    // objective / principal / budgets / deadline / status are all disclosed as text. The
    // objective is the card heading; principal + budgets + deadline are labelled fields.
    await expect(agentCard.locator(".row-head strong")).toContainText("bounded fixture agent");
    await expect(agentCard).toContainText("Principal");
    await expect(agentCard).toContainText("Token budget");
    await expect(agentCard).toContainText("1000"); // the seeded token budget
    await expect(agentCard).toContainText("Monetary budget");
    await expect(agentCard).toContainText("Deadline");
    await expect(agentCard.locator(".pill")).toHaveText(/CREATED/i); // status pill

    // -- a gated proposal is DEFERRED to the trusted inbox, not applied inline ---
    await agentCard.locator("input.input").fill("cap-does-not-matter");
    await agentCard.getByRole("button", { name: "Propose revoke" }).click();
    await gotoScreen(page, "approvals", "Approval inbox");
    await expect(page.locator(".approval-card", { hasText: "RevokeCapability" })).toHaveCount(1);

    expect(diag.errors, "console/page errors: " + diag.errors.join(" | ")).toEqual([]);
    expect(diag.requestFailures, diag.requestFailures.join(" | ")).toEqual([]);
  });
});
