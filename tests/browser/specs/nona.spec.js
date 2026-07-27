// Scenario: SELF-EXTENSION (Nona) — candidate → evidence → tier → promote / rollback,
// driven through the rendered Shell.
//
// This spec qualifies the real composed product path for the promotion boundary: a candidate
// proposed through visible controls with HOSTILE generated source, that source rendered as
// inert untrusted text in a real browser, evidence read back from the folded
// evaluation_result the gate itself read, a GATED promotion that changes nothing until a
// reauth-gated human decision lands in the trusted inbox, the derived quarantine actually
// lifting afterwards, discovery then answering "use" instead of "forge", and a rollback that
// DEMOTES (the organ, its grant and its history survive) rather than revoking — with the
// candidate's own detail re-opened on both sides of that rollback, because the promotion
// records are the audit surface and they may never disagree with what is enforced.
//
// Two preconditions the harness supplies, both standing in for components that are not the
// Shell (the same concession `--seed-agent` makes for the runtime):
//   * `seedNona` binds an evaluation host, because the lane's host seam defaults to refusing
//     and nothing in the API process may execute a candidate.
// Everything else — the trust anchor, every command, every gate, every refusal — is the
// shipped path. The anchored-promoter assertion below is deliberately first: if the store
// opened without its Nona anchor, no tier could be promoted here at all.

const { test, expect } = require("@playwright/test");
const { DecimaServer } = require("../serverManager");
const { attachDiagnostics, login, gotoScreen } = require("../helpers");
const {
  XSS_MARKER,
  HOSTILE_HTML,
  ORGAN_SOURCE,
  ORGAN_CASES,
  NETWORK_ORGAN_SOURCE,
} = require("../fixtures/nona_organ");

const INTENT = "add one to an integer";
const NETWORK_INTENT = "fetch a remote document over the network";

// Approve the single pending inbox item whose effect matches `command`, through the real
// trusted approval component: visible button → reauth modal → the pairing secret.
async function approveInInbox(page, server, command) {
  await gotoScreen(page, "approvals", "Approval inbox");
  const card = page.locator(".approval-card", { hasText: command });
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: "Approve once" }).click();
  await expect(page.locator("#reauth-secret")).toBeVisible();
  await page.fill("#reauth-secret", server.pairing);
  await page.locator("#modal-host .btn-primary").click();
  await expect(page.locator("#modal-host")).toBeHidden();
}

// The candidate card for one intent, and its currently rendered quarantine state.
function candidateCard(page, intent) {
  return page.locator(".nona-candidate", { hasText: intent });
}

test.describe("Scenario: self-extension (candidate → evidence → tier → promote/rollback)", () => {
  let server;

  test.beforeAll(async () => {
    server = await new DecimaServer({ seedNona: true }).start();
  });
  test.afterAll(async () => {
    await server.stop();
    server.cleanup();
  });

  test("hostile source stays text; a gated promotion needs a human; rollback demotes", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const diag = attachDiagnostics(page, server.baseURL);
    // THE load-bearing trap for this screen: the candidate source carries a </pre> breakout,
    // a <script> and an inline onerror handler. If any of them executed, a dialog fires.
    page.on("dialog", async (d) => {
      await d.dismiss();
      throw new Error("unexpected dialog (generated source executed): " + d.message());
    });

    await login(page, server);
    await gotoScreen(page, "nona", "Self-extension");

    // -- the promotion authority is ANCHORED on this store (not assumed in code) --------
    const authority = page.locator(".zone-system", {
      hasText: "Promotion authority anchored on this store",
    });
    await expect(authority).toBeVisible();
    await expect(authority.locator(".nona-promoter")).toHaveCount(1);
    await expect(authority).toContainText("may sign: pure, read_only");

    // -- discovery answers FORGE while the catalogue is empty ---------------------------
    await page.fill("#nona-goal", INTENT);
    await page.click("#nona-discover");
    const discovery = page.locator(".nona-discovery");
    await expect(discovery).toBeVisible();
    await expect(discovery).toContainText("FORGE");
    await expect(discovery).toContainText("ProposeCapability");

    // -- propose the candidate: the source travels as DATA ------------------------------
    await page.fill("#nona-intent", INTENT);
    await page.selectOption("#nona-tier", "pure");
    await page.fill("#nona-output-type", "int");
    await page.fill("#nona-source", ORGAN_SOURCE);
    await page.click("#nona-propose");

    const card = candidateCard(page, INTENT);
    await expect(card).toBeVisible();
    await expect(card).toContainText("QUARANTINED");
    await expect(card).toContainText("no organ grant yet");
    // The tier's signer policy is the BACKEND's word, shown verbatim.
    await expect(card).toContainText("AUTOMATED");
    // With no evidence there is nothing to promote, so there is no promote control.
    await expect(card.locator(".nona-promote")).toHaveCount(0);

    // -- the generated source is rendered as INERT UNTRUSTED TEXT ------------------------
    await card.locator(".nona-open").click();
    const detail = page.locator("#nona-detail");
    await expect(detail).toContainText("Candidate detail");
    const sourceZone = detail.locator(".zone-untrusted", {
      hasText: "Generated implementation",
    });
    await expect(sourceZone).toBeVisible();
    await expect(sourceZone.locator(".zone-label")).toContainText(/untrusted/i);
    const sourcePre = sourceZone.locator("pre.nona-source");
    // The payload is present as TEXT, byte for byte…
    await expect(sourcePre).toContainText(HOSTILE_HTML);
    await expect(sourcePre).toContainText("onerror=alert");
    // …and NOTHING was constructed from it: no script, no img, no anchor, no handler.
    await expect(sourceZone.locator("script")).toHaveCount(0);
    await expect(sourceZone.locator("img")).toHaveCount(0);
    await expect(sourceZone.locator("a")).toHaveCount(0);
    // The <pre> is a single text node whose textContent still holds the marker — proof the
    // bytes never round-tripped through a markup parser.
    const inert = await sourcePre.evaluate((pre, marker) => ({
      nodes: pre.childNodes.length,
      firstIsText: pre.firstChild ? pre.firstChild.nodeType === 3 : false,
      hasMarker: (pre.textContent || "").indexOf(marker) >= 0,
      elements: pre.querySelectorAll("*").length,
      overflowX: getComputedStyle(pre).overflowX,
    }), XSS_MARKER);
    expect(inert.nodes).toBe(1);
    expect(inert.firstIsText).toBe(true);
    expect(inert.hasMarker).toBe(true);
    expect(inert.elements).toBe(0);
    // POSITIVE CONTROL for the four assertions above. They are only meaningful if the
    // payload is LIVE — an inert string would satisfy them no matter what the Shell did.
    // Parsed as markup in a DETACHED element (never inserted into the document, so nothing
    // loads or runs) the same bytes produce real elements. So "0 elements, 1 text node" is a
    // fact about how the Shell rendered them, not a fact about the fixture.
    // The probe deliberately uses only markup that FETCHES NOTHING and RUNS NOTHING when
    // parsed (an innerHTML <script> never executes; an <a href="#"> loads nothing), so the
    // control cannot itself add a console error or a failed request to the diagnostics.
    const wouldBecomeMarkup = await page.evaluate((html) => {
      const probe = document.createElement("div");
      probe.innerHTML = html;
      return probe.querySelectorAll("script, a").length;
    }, HOSTILE_HTML + '<a href="#">x</a>');
    expect(wouldBecomeMarkup).toBeGreaterThan(0);
    // …and it can be READ: the <pre> is its own scroll container inside the clipping zone.
    expect(["auto", "scroll"]).toContain(inert.overflowX);

    // -- run the Reckoner: the evidence is the folded result -----------------------------
    await card.locator("textarea.nona-cases").fill(ORGAN_CASES);
    await card.locator(".nona-evaluate").click();
    const evidence = detail.locator(".nona-evidence").first();
    await expect(evidence).toBeVisible();
    await expect(evidence).toContainText("all gates passed");
    await expect(evidence).toContainText("promote-eligible");
    // Integers, as recorded — the same tallies the pure gate read.
    await expect(evidence).toContainText("deterministic pass");
    await expect(evidence).toContainText("hostile contained");
    await expect(evidence).toContainText("No security findings recorded.");

    // -- SUBMIT the promotion: gated, so NOTHING moves yet -------------------------------
    await expect(card.locator(".nona-promote")).toHaveCount(1);
    await card.locator(".nona-promote").click();
    const decision = page.locator(".nona-decision", { hasText: "Promotion awaiting" });
    await expect(decision).toBeVisible();
    await expect(decision).toContainText("pending");
    await expect(decision).toContainText("Approval inbox");
    // The tier on the pending decision is re-derived from the FOLD, not from the request.
    await expect(decision).toContainText("AUTOMATED");
    // The capability does not exist and the candidate is still un-promoted: submitting a
    // gated command has zero durable effect beyond the inbox item.
    await expect(card).toContainText("no organ grant yet");
    await expect(page.locator(".nona-candidate .nona-rollback")).toHaveCount(0);

    // -- the human decides, in the trusted inbox, with a fresh reauth ---------------------
    await gotoScreen(page, "approvals", "Approval inbox");
    const promoteCard = page.locator(".approval-card", { hasText: "PromoteCandidate" });
    await expect(promoteCard).toHaveCount(1);
    // The disclosure names the consequence honestly: reversible as a STATE, not as an effect.
    await expect(promoteCard).toContainText("RollbackPromotion re-quarantines the organ");
    await promoteCard.getByRole("button", { name: "Approve once" }).click();
    await expect(page.locator("#reauth-secret")).toBeVisible();
    await page.fill("#reauth-secret", server.pairing);
    await page.locator("#modal-host .btn-primary").click();
    await expect(page.locator("#modal-host")).toBeHidden();

    // -- the derived quarantine has LIFTED, and the organ grant exists --------------------
    await gotoScreen(page, "nona", "Self-extension");
    const promoted = candidateCard(page, INTENT);
    await expect(promoted).toContainText("PROMOTED — sandbox_only lifted");
    const grantId = await promoted.locator(".nona-id").nth(1).innerText();
    expect(grantId.trim().length).toBeGreaterThan(20); // a real 56-char base32 id

    // -- the promotion RECORD on the detail says the same thing enforcement does ----------
    // POSITIVE CONTROL for the rolled-back assertion at the end of this test: while the
    // promotion is in force the record reads "live" and looks ok, so "rolled back" later is
    // a fact about the fold rather than about a pill that renders one way no matter what.
    await promoted.locator(".nona-open").click();
    const records = page.locator("#nona-detail .nona-promotion-card");
    await expect(records).toContainText("Promotion records");
    const livePill = records.locator(".nona-promotion-state .pill");
    await expect(livePill).toHaveCount(1);
    await expect(livePill).toHaveText("live");
    await expect(livePill).toHaveClass(/pill-ok/);
    await promoted.locator(".nona-open").click(); // close it again

    // -- discovery now answers USE, with the tokens that made it rank ---------------------
    await page.fill("#nona-goal", INTENT);
    await page.click("#nona-discover");
    const useAnswer = page.locator(".nona-discovery");
    await expect(useAnswer).toContainText("USE");
    await expect(useAnswer).toContainText("A grant is still required");
    await expect(useAnswer.locator(".nona-tokens").first()).toContainText("matched:");
    await expect(useAnswer.locator(".nona-match")).not.toHaveCount(0);

    // -- ROLLBACK is a second gated command, and it is DEMOTION ---------------------------
    await expect(promoted.locator(".nona-rollback")).toHaveCount(1);
    await expect(promoted.locator(".nona-rollback")).toContainText("demote");
    await promoted.locator(".nona-rollback").click();
    const rollbackDecision = page.locator(".nona-decision", { hasText: "Rollback awaiting" });
    await expect(rollbackDecision).toBeVisible();
    await expect(rollbackDecision).toContainText("demotion");
    await expect(rollbackDecision).toContainText("Revokes the capability");
    await expect(rollbackDecision).toContainText("does not revoke the capability");
    // Still promoted: the proposal alone moved nothing.
    await expect(candidateCard(page, INTENT)).toContainText("PROMOTED — sandbox_only lifted");

    await approveInInbox(page, server, "RollbackPromotion");

    await gotoScreen(page, "nona", "Self-extension");
    const demoted = candidateCard(page, INTENT);
    await expect(demoted).toContainText("QUARANTINED — sandbox only");
    // The organ, its grant and its history SURVIVE — a rollback is not a revocation.
    await expect(demoted).not.toContainText("no organ grant yet");
    await expect(demoted.locator(".nona-id").nth(1)).toContainText(grantId.trim());
    await expect(demoted.locator(".nona-rollback")).toHaveCount(0);

    // …and the DETAIL agrees. The promotion boundary's own audit surface must not tell the
    // operator a withdrawn promotion is still in force on the same screen that says the
    // organ is quarantined, so the record's pill is keyed off the `live` field the reader
    // actually sends. Keyed off a field the reader never sends, this reads green "live".
    await demoted.locator(".nona-open").click();
    const demotedRecords = page.locator("#nona-detail .nona-promotion-card");
    await expect(demotedRecords).toContainText("Promotion records");
    const rolledBackPill = demotedRecords.locator(".nona-promotion-state .pill");
    await expect(rolledBackPill).toHaveCount(1);
    await expect(rolledBackPill).toHaveText("rolled back");
    await expect(rolledBackPill).toHaveClass(/pill-warn/);
    await expect(rolledBackPill).not.toHaveClass(/pill-ok/);
    // The record itself SURVIVES the demotion — it is withdrawn, not erased.
    await expect(demotedRecords.locator(".nona-promotion")).toHaveCount(1);
    await demoted.locator(".nona-open").click(); // close it again

    // -- a tier with NO EXECUTOR says so, and offers no approval --------------------------
    await page.fill("#nona-intent", NETWORK_INTENT);
    await page.selectOption("#nona-tier", "network");
    await page.fill("#nona-output-type", "int");
    await page.fill("#nona-source", NETWORK_ORGAN_SOURCE);
    await page.click("#nona-propose");
    const networkCard = candidateCard(page, NETWORK_INTENT);
    await expect(networkCard).toBeVisible();
    await expect(networkCard).toContainText("NOT EXECUTABLE — no mediated egress");
    await expect(networkCard).toContainText("nothing an operator can approve makes it run");
    // The refusal is STRUCTURAL: no promote control, and the words never say "requires
    // approval" — prompting for something that cannot run teaches people to click yes.
    await expect(networkCard.locator(".nona-promote")).toHaveCount(0);
    await expect(networkCard).not.toContainText("requires approval");

    // Even after evidence exists, a tier with no executor stays unpromotable.
    await networkCard.locator("textarea.nona-cases").fill(ORGAN_CASES);
    await networkCard.locator(".nona-evaluate").click();
    await expect(candidateCard(page, NETWORK_INTENT).locator(".nona-promote")).toHaveCount(0);

    // -- the canary panel: promoted organs are WATCHED, and the sweep is reachable --------
    // N5 shipped suspend-on-breach and auto-revoke with zero production callers and no
    // surface. This is the half that makes it behaviour: the panel reads the same folded
    // facts the sweep acts on, so it can never claim an organ is healthy while enforcement
    // disagrees.
    const healthCard = page.locator(".nona-health-card");
    await expect(healthCard).toBeVisible();
    await expect(healthCard).toContainText("demote on a breach");
    // The demoted organ above has no live promotion left, so nothing is being watched.
    await expect(healthCard).toContainText("No promoted organs to watch yet.");

    // The sweep is a plain control, deliberately NOT gated: it can only demote or revoke on
    // evidence the fold already carries, and a containment action that waits for a click is
    // not containment.
    await healthCard.locator(".nona-sweep").click();
    await expect(page.locator(".toast").last()).toContainText("Sweep found nothing to contain");

    // -- and the whole run was clean ------------------------------------------------------
    expect(diag.errors, "console/page errors: " + diag.errors.join(" | ")).toEqual([]);
    expect(
      diag.requestFailures,
      "same-origin request failures: " + diag.requestFailures.join(" | ")
    ).toEqual([]);
    expect(
      diag.badResponses,
      "bad same-origin responses: " + diag.badResponses.join(" | ")
    ).toEqual([]);
  });
});
