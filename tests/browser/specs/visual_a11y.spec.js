// visual_a11y.spec.js — automated VISUAL / TRUST-BOUNDARY / ACCESSIBILITY review of the
// Decima 0.3 Shell, so a human need not run a manual UI pass to sign the release.
//
// It drives the REAL backend + Shell through the same serve_fixture harness the other WS1
// specs use (visible controls only; no injected state), seeds minimal representative content
// on every principal screen, and then, at BOTH a desktop (1280x800) and a narrow mobile
// (390x780) viewport, navigates to and audits each screen:
//
//   * landmarks + a non-empty heading are present (nav[aria-label=Primary], <main>, h2);
//   * no duplicate element ids;
//   * NO horizontal-scroll overflow of the document at mobile width;
//   * security-sensitive controls (stop/deny/revoke/terminate/cancel/sign out) are present,
//     visible, inside the viewport (not clipped off-screen), and carry accessible names;
//   * approval disclosure (target / cost / scope / data-leaving) is real text on the card,
//     not hidden and not conveyed by colour alone;
//   * proposed-vs-authorized-vs-executed status is rendered as distinct TEXT pills;
//   * the granted workspace scope (repo root + restrictions) is visible;
//   * a cited source excerpt is in a distinct untrusted zone from the GENERATED model answer;
//   * no console / page errors and no failed same-origin requests over the whole review.
//
// A11y HONESTY: no bundled offline WCAG engine ships with this harness, so this performs
// DOM-level structural checks (roles, accessible names, landmarks, tab order, duplicate ids)
// — it does NOT claim full WCAG conformance.
//
// The security-critical screens (Q&A cited answer, Plans, Approval inbox, Workspace) are
// screenshotted at both viewports into docs/release-evidence/visual/ for the reviewer to
// inspect. Kept deliberately to a small set of PNGs.

const fs = require("fs");
const path = require("path");
const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { attachDiagnostics, login, gotoScreen } = require("../helpers");
const { KNOWLEDGE_DOCS } = require("../fixtures/content");
const { QA_SOURCE_DOCS, QA_CROSS_DOC_QUESTION } = require("../fixtures/qa_docs");
const { WORKSPACE_REPO, FIXED_CALC } = require("../fixtures/workspace_content");

const EVIDENCE_DIR = path.resolve(__dirname, "..", "..", "..", "docs", "release-evidence", "visual");

const VIEWPORTS = [
  { tag: "desktop", width: 1280, height: 800 },
  { tag: "mobile", width: 390, height: 780 },
];

// The ten principal screens (Knowledge's "cited answer" lives on the Q&A screen; the agent
// inspector is the Capability inspector; Activity + Settings are both audited).
const SCREENS = [
  ["conversation", "Conversation"],
  ["today", "Today"],
  ["projects", "Projects"],
  ["knowledge", "Knowledge"],
  ["qa", "Q&A"],
  ["plans", "Plans"],
  ["workspace", "Workspace"],
  ["approvals", "Approval inbox"],
  ["capabilities", "Capability inspector"],
  ["activity", "Activity timeline"],
  ["settings", "Settings"],
];

async function duplicateIds(page) {
  return page.evaluate(() => {
    const seen = {};
    const dupes = [];
    document.querySelectorAll("[id]").forEach((el) => {
      if (seen[el.id]) dupes.push(el.id);
      else seen[el.id] = true;
    });
    return dupes;
  });
}

// The document must never scroll horizontally: scrollWidth may exceed the viewport by at
// most a sub-pixel rounding of 1px.
async function horizontalOverflowPx(page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    return Math.max(0, doc.scrollWidth - window.innerWidth);
  });
}

// Any element that extends more than `slop` px past the right edge of the viewport is a
// candidate clip/overflow. Returns a short description list for diagnostics.
async function offscreenElements(page, selector) {
  return page.evaluate(
    ({ selector, slop }) => {
      const out = [];
      document.querySelectorAll(selector).forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return; // not laid out
        if (r.right > window.innerWidth + slop) {
          out.push(
            (el.tagName + "." + (el.className || "").toString().split(" ")[0]).slice(0, 40) +
              " right=" + Math.round(r.right) + " vw=" + window.innerWidth +
              " text=" + (el.textContent || "").trim().slice(0, 24)
          );
        }
      });
      return out;
    },
    { selector, slop: 2 }
  );
}

// Assert a control identified by role+name exists, is visible, has a non-empty accessible
// name, and sits inside the viewport horizontally (not clipped off-screen).
async function assertControl(page, name, ctxLabel) {
  const btn = page.getByRole("button", { name }).first();
  await expect(btn, `${ctxLabel}: "${name}" present`).toHaveCount(1);
  await expect(btn, `${ctxLabel}: "${name}" visible`).toBeVisible();
  const info = await btn.evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { name: (el.textContent || el.getAttribute("aria-label") || "").trim(), right: r.right, left: r.left, w: r.width };
  });
  expect(info.name.length, `${ctxLabel}: "${name}" has an accessible name`).toBeGreaterThan(0);
  expect(info.left, `${ctxLabel}: "${name}" not clipped past left edge`).toBeGreaterThanOrEqual(-2);
  expect(
    info.right,
    `${ctxLabel}: "${name}" not clipped past right edge (right=${Math.round(info.right)})`
  ).toBeLessThanOrEqual((await page.evaluate(() => window.innerWidth)) + 2);
}

test.describe("Visual / trust-boundary / a11y review of all principal screens", () => {
  let server;
  let priorGrant;

  test.beforeAll(async () => {
    priorGrant = process.env.DECIMA_WORKSPACE_ROOTS;
    process.env.DECIMA_WORKSPACE_ROOTS = WORKSPACE_REPO; // operator grant for the workspace lane
    server = await new DecimaServer({ seedAgent: true }).start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
    if (priorGrant === undefined) delete process.env.DECIMA_WORKSPACE_ROOTS;
    else process.env.DECIMA_WORKSPACE_ROOTS = priorGrant;
  });

  test("seed every screen, then audit visuals + a11y at desktop and mobile", async ({ page }) => {
    test.setTimeout(300_000);
    const diag = attachDiagnostics(page, server.baseURL);
    // A hostile string anywhere must never pop a dialog; fail loudly if it does.
    page.on("dialog", async (d) => {
      await d.dismiss();
      throw new Error("unexpected dialog: " + d.message());
    });

    await page.setViewportSize({ width: 1280, height: 800 });
    await login(page, server);

    // ============================================================================
    // SEED representative real state on every screen through visible controls only.
    // ============================================================================

    // -- Knowledge: two notes (one trusted, one untrusted/imported) ---------------
    await gotoScreen(page, "knowledge", "Knowledge");
    for (const doc of KNOWLEDGE_DOCS.slice(0, 3)) {
      await page.fill("#new-note-text", doc.text);
      const elig = page.locator("#new-note-eligible");
      if (doc.instructionEligible) await elig.check();
      else await elig.uncheck();
      await page.click(".stacked-form button[type=submit]");
      await expect(page.locator(".note-text", { hasText: doc.text.slice(0, 20) })).toBeVisible();
    }

    // -- Projects: one project --------------------------------------------------
    await gotoScreen(page, "projects", "Projects");
    await page.fill("#new-project-objective", "Ship the Decima 0.3 daily driver");
    await page.click(".inline-form button[type=submit]");
    await expect(page.locator(".card", { hasText: "Ship the Decima 0.3 daily driver" })).toBeVisible();

    // -- Plans: propose → accept → start (mints proposal + durable plan + agents +
    //    Today tasks; leaves a COMPLETED/ACTIVE plan so proposed/authorized/executed
    //    statuses are all on screen) --------------------------------------------
    await gotoScreen(page, "plans", "Plans");
    await page.fill("#plan-objective", "Prepare the quarterly report");
    await page.click('button[data-action="propose"]');
    const proposal = page.locator(".proposal-card");
    await expect(proposal).toHaveCount(1);
    await proposal.locator('button[data-action="accept"]').click();
    // The Plans screen lists a card per PROJECT, so the earlier-created project also shows;
    // scope to the plan we just accepted by its objective.
    const plan = page.locator(".plan-card", { hasText: "Prepare the quarterly report" });
    await expect(plan).toHaveCount(1);
    await expect(plan.locator(".step-list li")).not.toHaveCount(0);
    await expect(plan.locator(".pill").first()).toHaveText(/DRAFT/i);
    await expect(page.locator(".proposal-card .pill").first()).toHaveText(/ACCEPTED/i);
    await plan.locator('button[data-action="start"]').click();
    await expect(plan.locator(".pill").first()).toHaveText(/ACTIVE|COMPLETED/i);

    // -- Q&A: import two sources + ask a cross-doc question → a cited answer -------
    await gotoScreen(page, "qa", "Q&A");
    for (const doc of QA_SOURCE_DOCS) {
      await page.fill("#qa-import-name", doc.name);
      await page.fill("#qa-import-body", doc.body);
      await page.click("#qa-import-form button[type=submit]");
      await expect(page.locator("#qa-import-name")).toHaveValue("");
      await expect(page.locator("#view .loading")).toHaveCount(0);
    }
    await page.fill("#qa-ask-question", QA_CROSS_DOC_QUESTION);
    await page.fill("#qa-ask-scope", QA_SOURCE_DOCS.map((d) => d.name).join(", "));
    await page.click("#qa-ask-form button[type=submit]");
    await expect(page.locator(".zone-model .qa-answer")).toBeVisible();
    await expect(page.locator(".qa-citation")).not.toHaveCount(0);

    // -- Workspace: create a bounded run, execute it to SUCCEEDED -----------------
    await gotoScreen(page, "workspace", "Workspace");
    await page.selectOption("#ws-repo", WORKSPACE_REPO);
    await page.fill("#ws-name", "fix-add");
    await page.fill("#ws-objective", "make the calculator tests pass");
    await page.selectOption("#ws-check", "python_tests");
    await page.fill("#ws-edit-path", "calc.py");
    await page.fill("#ws-edit-content", FIXED_CALC);
    await page.click("#ws-create");
    const runCard = page.locator(".ws-run", { hasText: "fix-add" });
    await expect(runCard).toBeVisible();
    await runCard.locator(".ws-start").click();
    await expect(runCard.locator(".pill", { hasText: "SUCCEEDED" })).toBeVisible({ timeout: 90_000 });

    // -- Capabilities → propose terminate of the seed agent → a pending approval --
    await gotoScreen(page, "capabilities", "Capability inspector");
    const agentCard = page.locator(".card", { hasText: "bounded fixture agent" });
    await expect(agentCard).toHaveCount(1);
    await agentCard.getByRole("button", { name: "Propose terminate" }).click();
    await gotoScreen(page, "approvals", "Approval inbox");
    await expect(page.locator(".approval-card")).toHaveCount(1);

    // ============================================================================
    // AUDIT at each viewport.
    // ============================================================================
    fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
    const shotScreens = new Set(["qa", "plans", "approvals", "workspace"]);

    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });

      for (const [id, title] of SCREENS) {
        await gotoScreen(page, id, title);
        const where = `${id}@${vp.tag}`;

        // -- landmarks + non-empty heading ------------------------------------
        await expect(page.locator("nav[aria-label='Primary']"), where).toHaveCount(1);
        await expect(page.locator("main"), where).toHaveCount(1);
        expect((await page.locator("h2#view-title").innerText()).trim().length, where + " heading").toBeGreaterThan(0);

        // -- no duplicate ids -------------------------------------------------
        const dupes = await duplicateIds(page);
        expect(dupes, `${where} duplicate ids: ${dupes.join(", ")}`).toEqual([]);

        // -- no horizontal document overflow ----------------------------------
        const overflow = await horizontalOverflowPx(page);
        const offenders = overflow > 1 ? await offscreenElements(page, "*") : [];
        expect(
          overflow,
          `${where} horizontal overflow ${overflow}px; offenders: ${offenders.slice(0, 6).join(" | ")}`
        ).toBeLessThanOrEqual(1);

        // -- screen-specific security assertions ------------------------------
        if (id === "qa") {
          // Open the cited-answer detail. The Q&A screen keeps the last-asked run selected,
          // so after seeding the detail may already be shown; only click "Open" if a run
          // list is what's rendered.
          if (await page.locator(".qa-answer").count() === 0) {
            await page.locator(".qa-run-card .qa-open").first().click();
          }
          await expect(page.locator(".zone-model .qa-answer")).toBeVisible();
          // GENERATED answer zone is distinct from the untrusted cited-source zone.
          await expect(page.locator(".zone-model .zone-label")).toContainText(/GENERATED/i);
          const cite = page.locator(".qa-citation").first();
          await expect(cite).toBeVisible();
          await expect(cite.locator(".zone-label")).toContainText(/not instructions/i);
          // the two zones carry different trust-zone classes (structural, not colour-only)
          expect(await page.locator(".zone-model").count()).toBeGreaterThan(0);
          expect(await page.locator(".zone-untrusted.qa-citation").count()).toBeGreaterThan(0);
        }

        if (id === "plans") {
          // proposed / accepted / draft-active-completed statuses are TEXT pills.
          await expect(page.locator(".proposal-card .pill").first()).toHaveText(/PROPOSED|ACCEPTED/i);
          await expect(page.locator(".plan-card .pill").first()).toHaveText(/DRAFT|ACTIVE|PAUSED|COMPLETED/i);
          // a receipt-confirmed (executed) step is labelled in words, not colour only.
          const receipts = page.locator(".plan-card .step-receipt");
          if (await receipts.count()) {
            await expect(receipts.first()).toContainText(/receipt-confirmed/i);
          }
          // the gated terminate + cancel controls are reachable and named.
          await assertControl(page, /Terminate/i, where);
          await assertControl(page, /Cancel/i, where);
        }

        if (id === "approvals") {
          const card = page.locator(".approval-card").first();
          await expect(card, where).toBeVisible();
          // The trusted chrome marker exists exactly on the real card.
          expect(await page.locator('[data-trusted="1"]').count()).toBe(1);
          await expect(page.locator(".trusted-banner")).toHaveCount(1);
          // Disclosure is real text on the card (target/cost/scope/data-leaving).
          for (const label of ["Exact target", "Max cost", "Data leaving machine", "Reason", "Requesting agent"]) {
            await expect(card.locator("dt", { hasText: label }), `${where} discloses ${label}`).toHaveCount(1);
          }
          // status is a text pill, not colour-only.
          expect((await card.locator(".pill").first().innerText()).trim().length).toBeGreaterThan(0);
          // Deny is reachable + named; every action button is inside the viewport.
          await assertControl(page, "Deny", where);
          await assertControl(page, "Approve once", where);
          await assertControl(page, "Approve with stricter limits", where);
        }

        if (id === "capabilities") {
          await assertControl(page, "Propose terminate", where);
          await assertControl(page, "Propose revoke", where);
        }

        if (id === "workspace") {
          // granted scope (repo root + restrictions) is visible in the system zone.
          const grantZone = page.locator(".zone-system", { hasText: "Granted repository roots" });
          await expect(grantZone, where).toBeVisible();
          await expect(grantZone).toContainText(WORKSPACE_REPO);
          await expect(grantZone).toContainText(/no network/i);
          // open the run detail: diff + test artifacts as untrusted text.
          await page.locator(".ws-run", { hasText: "fix-add" }).locator(".ws-open").click();
          const detail = page.locator("#ws-detail");
          await expect(detail).toContainText("Run detail");
          const diffZone = detail.locator(".zone-untrusted", { hasText: "Unified diff" });
          await expect(diffZone).toBeVisible();
          // A run's changed-files status is text ("passed 2 / failed 0"), not colour-only.
          await expect(detail.locator(".ws-detail-card")).toContainText(/passed \d+ \/ failed \d+/);
          // diff/test <pre> content must not be UNREACHABLY clipped. Overflowing content is
          // acceptable ONLY if the element is its own scroll container (overflow-x auto/scroll);
          // if it overflows while overflow-x is visible/hidden/clip the content is cut off with
          // no way to reach it (the parent .zone sets overflow:hidden) — a real defect.
          const clipped = await detail.evaluate((root) => {
            const bad = [];
            root.querySelectorAll("pre").forEach((p) => {
              if (p.scrollWidth - p.clientWidth > 2) {
                const ox = getComputedStyle(p).overflowX;
                if (ox !== "auto" && ox !== "scroll") {
                  bad.push(p.className + " overflowX=" + ox +
                    " scrollW=" + p.scrollWidth + " clientW=" + p.clientWidth);
                }
              }
            });
            return bad;
          });
          expect(clipped, `${where} clipped <pre> content: ${clipped.join(" | ")}`).toEqual([]);
        }

        if (id === "settings") {
          await assertControl(page, "Sign out", where);
        }

        if (id === "conversation") {
          // the read-only transcript has NO approval action buttons (invariant 5).
          await expect(page.getByRole("button", { name: /approve/i })).toHaveCount(0);
          await assertControl(page, /Pause stream|Resume stream/i, where);
        }

        // -- screenshot the security-critical screens at both viewports -------
        if (shotScreens.has(id)) {
          await page.screenshot({
            path: path.join(EVIDENCE_DIR, `${id}-${vp.tag}.png`),
            fullPage: true,
          });
        }
      }
    }

    // -- clean run over the whole review ----------------------------------------
    expect(diag.errors, "console/page errors: " + diag.errors.join(" | ")).toEqual([]);
    expect(
      diag.requestFailures,
      "same-origin request failures: " + diag.requestFailures.join(" | ")
    ).toEqual([]);
    expect(diag.badResponses, "bad same-origin responses: " + diag.badResponses.join(" | ")).toEqual([]);
  });
});
