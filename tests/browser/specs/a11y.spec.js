// Accessibility SMOKE — deliberately narrow, not a full WCAG audit (see the evidence
// README, which does not overclaim a11y). It checks a handful of concrete, high-value
// properties on the rendered Shell: keyboard-operable navigation, accessible names on the
// approval controls, status conveyed by text (not colour alone), no duplicate element ids,
// and the presence of headings + landmarks on the primary screens.

const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { login, gotoScreen } = require("../helpers");

async function duplicateIds(page) {
  return page.evaluate(() => {
    const seen = {};
    const dupes = [];
    document.querySelectorAll("[id]").forEach((el) => {
      const id = el.id;
      if (seen[id]) dupes.push(id);
      else seen[id] = true;
    });
    return dupes;
  });
}

test.describe("a11y smoke", () => {
  let server;
  test.beforeAll(async () => {
    server = await new DecimaServer({ seedAgent: true }).start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
  });

  test("nav is keyboard-operable, controls named, status has text, no dup ids, landmarks present", async ({
    page,
  }) => {
    await login(page, server);

    // -- landmarks + heading on the primary screen -----------------------------
    await expect(page.locator("nav[aria-label='Primary']")).toHaveCount(1);
    await expect(page.locator("main")).toHaveCount(1);
    await expect(page.locator("h2#view-title")).not.toHaveText("");

    // -- nav items are real, named, keyboard-operable buttons ------------------
    const navButtons = page.locator(".nav-item");
    const n = await navButtons.count();
    expect(n).toBeGreaterThan(4);
    for (let i = 0; i < n; i++) {
      const btn = navButtons.nth(i);
      expect((await btn.evaluate((e) => e.tagName)).toLowerCase()).toBe("button");
      expect((await btn.innerText()).trim().length, "nav item has an accessible name").toBeGreaterThan(0);
    }
    // Keyboard OPERABILITY: focus the Capabilities nav button and activate with Enter.
    const capBtn = page.locator('.nav-item[data-screen="capabilities"]');
    await capBtn.focus();
    expect(await capBtn.evaluate((e) => e === document.activeElement)).toBe(true);
    await page.keyboard.press("Enter");
    await expect(page.locator("#view-title")).toHaveText("Capability inspector");

    // -- a real gated approval: its action controls have accessible names -------
    const agentCard = page.locator(".card", { hasText: "bounded fixture agent" });
    await agentCard.getByRole("button", { name: "Propose terminate" }).click();
    await gotoScreen(page, "approvals", "Approval inbox");
    for (const name of ["Deny", "Approve once", "Approve with stricter limits"]) {
      await expect(page.locator(".approval-actions").getByRole("button", { name })).toHaveCount(1);
    }
    // Status is conveyed as TEXT (a pill with a label), not colour only.
    const pill = page.locator(".approval-card .pill").first();
    expect((await pill.innerText()).trim().length).toBeGreaterThan(0);

    // -- no duplicate ids on a set of primary screens --------------------------
    for (const [id, title] of [
      ["today", "Today"],
      ["knowledge", "Knowledge"],
      ["approvals", "Approval inbox"],
      ["capabilities", "Capability inspector"],
    ]) {
      await gotoScreen(page, id, title);
      const dupes = await duplicateIds(page);
      expect(dupes, "duplicate ids on " + id + ": " + dupes.join(", ")).toEqual([]);
    }
  });
});
