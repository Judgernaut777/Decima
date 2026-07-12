// Grounded Q&A — the qa lane's composed product path, driven through the rendered Shell.
//
// Everything happens through VISIBLE controls: documents are imported via the Q&A screen's
// import form (the established ImportArtifact command), a question is asked over an explicit
// scope, and the answer + citations are read from the DOM the operator sees. Assertions:
//   * a cross-document question renders a GENERATED answer with >= 2 REAL citations,
//     each opening to the actual source passage it cites;
//   * generated text (zone-model, labelled GENERATED) is visually distinct from source
//     text (zone-untrusted, labelled imported data);
//   * the run persists across a browser refresh, a full backend restart, and the
//     projection rebuild that a restart performs (restarting reopens the same Weft and
//     rebuilds every disposable projection from it — see serverManager.js);
//   * a hostile import (injection, fake system message, fake approval chrome, inline
//     script, on* handlers, fabricated conclusion, secret/scope demands) stays inert
//     DATA: rendered literally, nothing executes, no trusted UI appears, and it never
//     leaks into an out-of-scope answer;
//   * no console errors, no failed same-origin requests, CSP present.

const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { attachDiagnostics, login, gotoScreen } = require("../helpers");
const {
  QA_SOURCE_DOCS,
  QA_CROSS_DOC_QUESTION,
  QA_HOSTILE_DOC,
  QA_HOSTILE_QUESTION,
} = require("../fixtures/qa_docs");

test.describe("Grounded Q&A: cited answers, durability, hostile imports inert", () => {
  let server;
  test.beforeAll(async () => {
    server = await new DecimaServer().start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
  });

  async function importDoc(page, doc) {
    await page.fill("#qa-import-name", doc.name);
    await page.fill("#qa-import-body", doc.body);
    await page.click("#qa-import-form button[type=submit]");
    // Success clears the form (the handler only resets fields when the command
    // succeeded), so an emptied name field is a real success signal.
    await expect(page.locator("#qa-import-name")).toHaveValue("");
    await expect(page.locator("#view .loading")).toHaveCount(0);
  }

  async function ask(page, question, scope) {
    await page.fill("#qa-ask-question", question);
    await page.fill("#qa-ask-scope", scope || "");
    await page.click("#qa-ask-form button[type=submit]");
    // A successful ask opens the run detail with the generated answer.
    await expect(page.locator(".zone-model .qa-answer")).toBeVisible();
  }

  test("import 3 docs → cross-doc cited answer → durable across refresh/restart/rebuild", async ({
    page,
  }) => {
    const diag = attachDiagnostics(page, server.baseURL);
    page.on("dialog", async (d) => {
      await d.dismiss();
      throw new Error("unexpected dialog (script executed): " + d.message());
    });

    // -- CSP present on the served Shell -----------------------------------------
    const resp = await page.goto(server.baseURL + "/");
    const csp = resp.headers()["content-security-policy"] || "";
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("object-src 'none'");

    await login(page, server);
    await gotoScreen(page, "qa", "Q&A");

    // -- import three deterministic documents through the visible form ------------
    for (const doc of [...QA_SOURCE_DOCS, QA_HOSTILE_DOC]) {
      await importDoc(page, doc);
    }

    // -- ask a question whose answer needs material from BOTH benign docs ---------
    await ask(
      page,
      QA_CROSS_DOC_QUESTION,
      QA_SOURCE_DOCS.map((d) => d.name).join(", ")
    );

    // Generated text is visually distinguished: a model zone labelled GENERATED...
    await expect(page.locator(".zone-model .zone-label")).toContainText("GENERATED");
    const answerText = (await page.locator(".zone-model .qa-answer").innerText()).trim();
    expect(answerText.length).toBeGreaterThan(0);
    // ...while every citation lives in an untrusted zone labelled as imported data.
    const citations = page.locator(".qa-citation");
    await expect(citations).toHaveCount(2); // >= 2 real citations, one per source doc
    await expect(
      page.locator(".qa-citation .zone-label", { hasText: "not instructions" })
    ).toHaveCount(2);

    // -- open BOTH cited source passages and verify each corresponds --------------
    for (const doc of QA_SOURCE_DOCS) {
      const cite = page.locator(".qa-citation", { hasText: doc.name });
      await expect(cite).toHaveCount(1);
      await expect(cite.locator(".qa-citation-snippet")).toContainText(
        doc.body.slice(0, 40)
      );
      await cite.locator("summary.qa-passage-toggle").click();
      await expect(cite.locator(".qa-passage")).toBeVisible();
      await expect(cite.locator(".qa-passage")).toContainText(doc.body);
    }

    // -- durability #1: browser REFRESH (session cookie resumes; run persists) ----
    await page.reload();
    await expect(page.locator("#app")).toBeVisible();
    await gotoScreen(page, "qa", "Q&A");
    await expect(page.locator(".qa-run-card")).toHaveCount(1);
    await page.click(".qa-run-card .qa-open");
    await expect(page.locator(".zone-model .qa-answer")).toHaveText(answerText);
    await expect(page.locator(".qa-citation")).toHaveCount(2);
    await page.click(".qa-back");

    // -- hostile import stays inert DATA ------------------------------------------
    await ask(page, QA_HOSTILE_QUESTION, QA_HOSTILE_DOC.name);
    const hostileCite = page.locator(".qa-citation", { hasText: QA_HOSTILE_DOC.name });
    await expect(hostileCite).toHaveCount(1);
    // The injection renders LITERALLY in the snippet (data preserved as data)...
    await expect(hostileCite.locator(".qa-citation-snippet")).toContainText(
      "Ignore all previous instructions"
    );
    await hostileCite.locator("summary.qa-passage-toggle").click();
    const passage = hostileCite.locator(".qa-passage");
    await expect(passage).toContainText("<script>window.pwned=1</script>");
    await expect(passage).toContainText('<button onclick="window.pwned=1">');
    // ...but never becomes DOM: no element, handler, banner, or image was created.
    await expect(passage.locator("script, button, img, div, a")).toHaveCount(0);
    await expect(page.locator(".trusted-banner")).toHaveCount(0);
    expect(await page.evaluate(() => window.pwned === undefined)).toBe(true);
    // The GENERATED answer never repeats the injection as its own instruction
    // (the deterministic provider echoes its trusted prompt; hostile text rode
    // in the request context as instruction_eligible=False data).
    await expect(page.locator(".zone-model .qa-answer")).not.toContainText(
      "Ignore all previous instructions"
    );
    await page.click(".qa-back");

    // -- scope is a hard boundary: hostile content is invisible out of scope ------
    // The hostile doc is the best lexical match for its own question, but the scope
    // names only a benign doc — so the hostile source must never be cited and its
    // fabricated conclusion must never surface.
    await ask(page, QA_HOSTILE_QUESTION, QA_SOURCE_DOCS[0].name);
    await expect(
      page.locator(".qa-citation", { hasText: QA_HOSTILE_DOC.name })
    ).toHaveCount(0);
    await expect(page.locator(".zone-model .qa-answer")).not.toContainText(
      "stored forever"
    );
    await page.click(".qa-back");

    // -- insufficient evidence: the honest bounded answer, no fabrication ---------
    await ask(page, "Which quorum size governs zebra consensus?", QA_SOURCE_DOCS[0].name);
    await expect(page.locator(".zone-model .qa-answer")).toContainText(
      "No imported source in the selected scope supports an answer"
    );
    await expect(page.locator(".qa-citation")).toHaveCount(0);
    await page.click(".qa-back");
    await expect(page.locator(".qa-run-card")).toHaveCount(4);

    // -- durability #2: BACKEND RESTART + PROJECTION REBUILD ----------------------
    // Restarting over the same db reopens the Weft AND rebuilds every disposable
    // projection from it (the supported rebuild path — serverManager.js). The
    // in-memory session dies; same seed ⇒ same pairing secret, so re-pair.
    await server.restart();
    await login(page, server);
    await gotoScreen(page, "qa", "Q&A");
    await expect(page.locator(".qa-run-card")).toHaveCount(4);
    const crossRun = page.locator(".qa-run-card", {
      hasText: "What port does the Aurora relay listen on",
    });
    await expect(crossRun).toHaveCount(1);
    await crossRun.locator(".qa-open").click();
    // The rebuilt run is IDENTICAL: same generated answer, same two citations,
    // and the cited passages still open to the same source text.
    await expect(page.locator(".zone-model .qa-answer")).toHaveText(answerText);
    await expect(page.locator(".qa-citation")).toHaveCount(2);
    for (const doc of QA_SOURCE_DOCS) {
      const cite = page.locator(".qa-citation", { hasText: doc.name });
      await cite.locator("summary.qa-passage-toggle").click();
      await expect(cite.locator(".qa-passage")).toContainText(doc.body);
    }

    // -- no console errors / no failed same-origin requests over the whole flow ---
    expect(diag.errors, "console/page errors: " + diag.errors.join(" | ")).toEqual([]);
    expect(
      diag.requestFailures,
      "same-origin request failures: " + diag.requestFailures.join(" | ")
    ).toEqual([]);
  });
});
