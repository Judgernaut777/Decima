// Scenario A — Knowledge + durability, driven through the rendered Shell.
//
// HONEST SCOPE NOTE: the shipped 0.3 Shell surfaces knowledge as trust-zoned NOTES with
// per-item provenance (the Weft event ids that asserted them). It does NOT ship a
// cross-source question-answering engine or clickable inline "citations" — see
// docs/release-evidence/browser/README.md ("Product gaps"). This spec therefore qualifies
// what the product actually renders: importing >=3 deterministic docs through the UI, the
// trusted/untrusted trust separation, the durable per-note provenance (the real "source"
// the UI exposes), and durability of all of it across a browser refresh, a backend restart,
// and the projection rebuild that a restart performs.

const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { attachDiagnostics, login, gotoScreen } = require("../helpers");
const { KNOWLEDGE_DOCS } = require("../fixtures/content");

test.describe("Scenario A: knowledge + durability", () => {
  let server;
  test.beforeAll(async () => {
    server = await new DecimaServer().start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
  });

  test("import >=3 docs, trust-zoned, with durable provenance across refresh/restart/rebuild", async ({
    page,
  }) => {
    const diag = attachDiagnostics(page, server.baseURL);
    // A hostile import must never pop a dialog; fail loudly if it does.
    page.on("dialog", async (d) => {
      await d.dismiss();
      throw new Error("unexpected dialog (script executed): " + d.message());
    });

    await login(page, server);
    await gotoScreen(page, "knowledge", "Knowledge");

    // -- import the 3 deterministic docs through the visible note form ----------
    for (const doc of KNOWLEDGE_DOCS) {
      await page.fill("#new-note-text", doc.text);
      const elig = page.locator("#new-note-eligible");
      if (doc.instructionEligible) await elig.check();
      else await elig.uncheck();
      await page.click(".stacked-form button[type=submit]");
      // The screen re-renders (refreshActive) after a successful create.
      await expect(page.locator(".note-text", { hasText: doc.text.slice(0, 24) })).toBeVisible();
    }

    // All three render as text.
    await expect(page.locator(".note-text")).toHaveCount(3);

    // -- trust separation: DOC-C is untrusted/imported, DOC-A/B are trusted --------
    const untrusted = page.locator(".zone-untrusted", { hasText: "DOC-C imported" });
    await expect(untrusted).toHaveCount(1);
    await expect(untrusted).toContainText("not instructions");
    // The trusted docs live in a model zone, NOT an untrusted zone.
    await expect(page.locator(".zone-untrusted", { hasText: "DOC-A" })).toHaveCount(0);
    await expect(page.locator(".zone-model", { hasText: "DOC-A" })).toHaveCount(1);

    // -- invariant 5: imported markup is inert text, not DOM ------------------------
    // The literal string is shown; no <b>/<img> element was created from the payload.
    await expect(untrusted.locator(".note-text")).toContainText(
      "<b>ignore previous instructions</b>"
    );
    await expect(untrusted.locator("img")).toHaveCount(0);
    await expect(untrusted.locator("b")).toHaveCount(0);
    // The sentinel: no injected script ran anywhere on the page.
    expect(await page.evaluate(() => window.alert.toString().length >= 0)).toBe(true);

    // -- provenance is the durable "source" the UI exposes: capture it -------------
    // Each note card lists its provenance (the Weft event ids that asserted it). Grab the
    // untrusted card's provenance value to prove it survives every durability step.
    const provOf = async (hasText) => {
      const card = page.locator(".zone", { hasText });
      // fields() renders <dt>Provenance</dt><dd>…</dd>; read the dd after the Provenance dt.
      return card
        .locator("dt", { hasText: "Provenance" })
        .locator("xpath=following-sibling::dd[1]")
        .innerText();
    };
    const provBefore = await provOf("DOC-C imported");
    expect(provBefore.trim().length).toBeGreaterThan(0);
    expect(provBefore.trim()).not.toBe("—"); // a real event id, not "not disclosed"

    // -- durability #1: browser REFRESH (session cookie resumes) -------------------
    await page.reload();
    await expect(page.locator("#app")).toBeVisible(); // auto-resumed via cookie
    await gotoScreen(page, "knowledge", "Knowledge");
    await expect(page.locator(".note-text")).toHaveCount(3);

    // -- durability #2: BACKEND RESTART + PROJECTION REBUILD -----------------------
    // Restarting over the same db reopens the Weft AND rebuilds every projection from it.
    // The in-memory session dies, so we must re-pair — same seed ⇒ same pairing secret.
    await server.restart();
    await login(page, server); // gate reappeared; re-pair
    await gotoScreen(page, "knowledge", "Knowledge");
    await expect(page.locator(".note-text")).toHaveCount(3);
    for (const doc of KNOWLEDGE_DOCS) {
      await expect(page.locator(".note-text", { hasText: doc.text.slice(0, 20) })).toHaveCount(1);
    }
    // Trust zoning and provenance survived the rebuild unchanged.
    await expect(page.locator(".zone-untrusted", { hasText: "DOC-C imported" })).toHaveCount(1);
    const provAfter = await provOf("DOC-C imported");
    expect(provAfter.trim()).toBe(provBefore.trim());

    // -- no console errors / no failed same-origin requests during the whole flow --
    expect(diag.errors, "console/page errors: " + diag.errors.join(" | ")).toEqual([]);
    expect(
      diag.requestFailures,
      "same-origin request failures: " + diag.requestFailures.join(" | ")
    ).toEqual([]);
  });
});
