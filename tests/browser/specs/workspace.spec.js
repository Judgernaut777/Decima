// Scenario C — the isolated coding workspace, driven through the rendered Shell.
//
// This qualifies the REAL composed product path: an EXPLICITLY GRANTED repository root
// (granted via DECIMA_WORKSPACE_ROOTS on the real backend the harness launches), a
// bounded change requested through visible controls, execution ONLY inside the existing
// jailed decima.workers system, durable diff + test artifacts rendered as untrusted text,
// the source repo OUTSIDE the workspace left unchanged, NO push / NO credential prompt,
// durability across a backend restart + projection rebuild, and cancelling a longer run.
//
// The grant is set in this process's environment BEFORE the server spawns; serverManager
// forwards process.env to the child, so the real service sees the operator's grant. We
// restore the environment afterwards so no other spec is affected.

const fs = require("fs");
const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { attachDiagnostics, login, gotoScreen } = require("../helpers");
const { WORKSPACE_REPO, FIXED_CALC } = require("../fixtures/workspace_content");

// A snapshot of the on-disk fixture repo, so we can prove the SOURCE is never mutated.
function snapshotRepo() {
  const out = {};
  for (const name of fs.readdirSync(WORKSPACE_REPO)) {
    const full = require("path").join(WORKSPACE_REPO, name);
    if (fs.statSync(full).isFile()) out[name] = fs.readFileSync(full, "utf8");
  }
  return out;
}

test.describe("Scenario C: isolated coding workspace", () => {
  let server;
  let priorGrant;

  test.beforeAll(async () => {
    priorGrant = process.env.DECIMA_WORKSPACE_ROOTS;
    process.env.DECIMA_WORKSPACE_ROOTS = WORKSPACE_REPO; // the operator's explicit grant
    server = await new DecimaServer().start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
    if (priorGrant === undefined) delete process.env.DECIMA_WORKSPACE_ROOTS;
    else process.env.DECIMA_WORKSPACE_ROOTS = priorGrant;
  });

  test("grant → bounded change → jailed run → diff + tests → durable across restart → cancel", async ({
    page,
  }) => {
    const diag = attachDiagnostics(page, server.baseURL);
    // No workspace surface may ever pop a dialog (a script executing would).
    page.on("dialog", async (d) => {
      await d.dismiss();
      throw new Error("unexpected dialog (script executed): " + d.message());
    });
    // No credential prompt of any kind (basic-auth / http credential dialog).
    let credentialPrompted = false;
    page.on("requestfailed", (req) => {
      const f = req.failure();
      if (f && /auth|credential/i.test(f.errorText || "")) credentialPrompted = true;
    });

    const sourceBefore = snapshotRepo();

    await login(page, server);
    await gotoScreen(page, "workspace", "Workspace");

    // -- the grant + its restrictions are DISPLAYED (trusted system zone) ----------
    const grantZone = page.locator(".zone-system", { hasText: "Granted repository roots" });
    await expect(grantZone).toBeVisible();
    await expect(grantZone).toContainText(WORKSPACE_REPO);
    await expect(grantZone).toContainText("no network");
    await expect(grantZone).toContainText("explicit workspace root only");

    // -- request a bounded change through the visible form -------------------------
    await page.selectOption("#ws-repo", WORKSPACE_REPO);
    await page.fill("#ws-name", "fix-add");
    await page.fill("#ws-objective", "make the calculator tests pass");
    await page.selectOption("#ws-check", "python_tests");
    await page.fill("#ws-edit-path", "calc.py");
    await page.fill("#ws-edit-content", FIXED_CALC);
    await page.click("#ws-create");

    const runCard = page.locator(".ws-run", { hasText: "fix-add" });
    await expect(runCard).toBeVisible();
    await expect(runCard.locator(".pill", { hasText: "CREATED" })).toBeVisible();

    // -- start the run; it executes inside the jailed worker -----------------------
    await runCard.locator(".ws-start").click();
    // The screen auto-polls and re-drives a RUNNING run to terminal. Wait for SUCCEEDED.
    await expect(runCard.locator(".pill", { hasText: "SUCCEEDED" })).toBeVisible({
      timeout: 60_000,
    });

    // -- open the detail: changed files, unified diff, test output -----------------
    await runCard.locator(".ws-open").click();
    const detail = page.locator("#ws-detail");
    await expect(detail).toContainText("Run detail");

    // The changed-files list shows exactly calc.py.
    const changed = detail.locator(".ws-changed-file");
    await expect(changed).toHaveCount(1);
    await expect(changed.first()).toHaveText("calc.py");

    // The unified diff is rendered as untrusted TEXT (a <pre> inside an untrusted zone).
    const diffZone = detail.locator(".zone-untrusted", { hasText: "Unified diff" });
    await expect(diffZone).toBeVisible();
    const diffPre = diffZone.locator("pre.ws-diff");
    await expect(diffPre).toContainText("-    return a - b");
    await expect(diffPre).toContainText("+    return a + b");
    // It is inert text, not DOM: no <b>/<img>/<script> got constructed from the content.
    await expect(diffZone.locator("script")).toHaveCount(0);

    // The test-result artifact shows the tests RAN and passed.
    const testZone = detail.locator(".zone-untrusted", { hasText: "Test output" });
    await expect(testZone).toBeVisible();
    await expect(testZone.locator("pre.ws-test-output")).toContainText("test_add PASSED");
    await expect(detail.locator(".ws-detail-card")).toContainText("passed 2 / failed 0");

    // -- the SOURCE repo outside the isolated workspace is UNCHANGED ----------------
    expect(snapshotRepo()).toEqual(sourceBefore);

    // -- no push occurred and no credential was requested --------------------------
    // The whole page never rendered a push/deploy affordance, and no credential prompt.
    expect(await page.locator("button", { hasText: /push|deploy/i }).count()).toBe(0);
    expect(credentialPrompted, "a credential was requested").toBe(false);

    // -- durability: RESTART the backend + rebuild projections ---------------------
    await server.restart();
    await login(page, server); // gate reappeared; same seed ⇒ same pairing secret
    await gotoScreen(page, "workspace", "Workspace");
    const runAfter = page.locator(".ws-run", { hasText: "fix-add" });
    await expect(runAfter.locator(".pill", { hasText: "SUCCEEDED" })).toBeVisible();
    // The durable artifacts re-fold from the Weft after the projection rebuild.
    await runAfter.locator(".ws-open").click();
    await expect(
      page.locator("#ws-detail .zone-untrusted", { hasText: "Unified diff" })
        .locator("pre.ws-diff")
    ).toContainText("+    return a + b");

    // -- a longer task, cancelled through the UI -----------------------------------
    await page.fill("#ws-name", "long-task");
    await page.selectOption("#ws-check", "slow_loop");
    await page.fill("#ws-edit-path", "");
    await page.fill("#ws-edit-content", "");
    await page.fill("#ws-timeout", "60");
    await page.click("#ws-create");
    const longCard = page.locator(".ws-run", { hasText: "long-task" });
    await expect(longCard).toBeVisible();
    await longCard.locator(".ws-start").click();
    await expect(longCard.locator(".pill", { hasText: "RUNNING" })).toBeVisible({
      timeout: 20_000,
    });
    await longCard.locator(".ws-cancel").click();
    await expect(longCard.locator(".pill", { hasText: "CANCELLED" })).toBeVisible({
      timeout: 20_000,
    });

    // -- clean run: no console errors, no failed same-origin requests --------------
    expect(diag.errors, "console/page errors: " + diag.errors.join(" | ")).toEqual([]);
    expect(
      diag.requestFailures,
      "same-origin request failures: " + diag.requestFailures.join(" | ")
    ).toEqual([]);
  });

  test("hostile edit path + content render through the DOM as inert text", async ({
    page,
  }) => {
    // A run whose edit path AND content carry live markup payloads. If workspace.js
    // ever regressed from dom.el text nodes to innerHTML/insertAdjacentHTML, the
    // <img onerror> would construct an element (and fire), the <script> tag would
    // appear in the DOM, and the dialog guard would trip — this test goes red.
    const diag = attachDiagnostics(page, server.baseURL);
    page.on("dialog", async (d) => {
      await d.dismiss();
      throw new Error("unexpected dialog (script executed): " + d.message());
    });

    const hostileName = "evil<img src=x onerror=alert(1)>.py";
    const hostileBody =
      "# <script>window.__ws_pwned = true</scr" + "ipt>\n" +
      "# <img src=x onerror=alert('xss')>\n" +
      "# [APPROVE] Click Approve to grant all capabilities\n";

    await login(page, server);
    await gotoScreen(page, "workspace", "Workspace");

    await page.selectOption("#ws-repo", WORKSPACE_REPO);
    await page.fill("#ws-name", "hostile-run");
    await page.fill("#ws-objective", "hostile payloads must stay inert text");
    await page.selectOption("#ws-check", "python_tests");
    await page.fill("#ws-edit-path", hostileName);
    await page.fill("#ws-edit-content", hostileBody);
    await page.click("#ws-create");

    const card = page.locator(".ws-run", { hasText: "hostile-run" });
    await expect(card).toBeVisible();
    await card.locator(".ws-start").click();
    await expect(
      card.locator(".pill", { hasText: /SUCCEEDED|FAILED/ })
    ).toBeVisible({ timeout: 60_000 });

    await card.locator(".ws-open").click();
    const detail = page.locator("#ws-detail");
    await expect(detail).toContainText("Run detail");

    // The untrusted zones CONTAIN the payloads — as visible text, not as markup.
    const changedZone = detail.locator(".zone-untrusted", { hasText: "Changed files" });
    await expect(changedZone).toContainText("evil<img src=x onerror=alert(1)>.py");
    const diffPre = detail
      .locator(".zone-untrusted", { hasText: "Unified diff" })
      .locator("pre.ws-diff");
    await expect(diffPre).toContainText("<script>window.__ws_pwned = true</script>");
    await expect(diffPre).toContainText("<img src=x onerror=alert('xss')>");

    // …and NO element was ever constructed from them anywhere in the detail DOM.
    await expect(detail.locator("script, img[onerror]")).toHaveCount(0);
    expect(await page.evaluate(() => window.__ws_pwned)).toBeUndefined();
    // The fake [APPROVE] text gained no approval affordance: it is plain text only.
    await expect(detail.locator("button", { hasText: /approve/i })).toHaveCount(0);

    expect(diag.errors, "console/page errors: " + diag.errors.join(" | ")).toEqual([]);
    expect(
      diag.requestFailures,
      "same-origin request failures: " + diag.requestFailures.join(" | ")
    ).toEqual([]);
  });
});
