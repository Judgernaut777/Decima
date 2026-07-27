# WS1 — Browser-rendered Shell qualification

Lane: **WS1 (qual/browser)**. Charter: `docs/DECIMA-0.3-RELEASE-QUALIFICATION.md` (WS1 owns
`tests/browser/`, `playwright.config.*`, this evidence dir; narrow fixes allowed in
`decima/shell/frontend/` and the Shell backend `decima/services/api/` / `decima/shell/serve.py`).

This lane drives the **REAL** trusted Shell — backend + frontend served by
`python3 -m tests.browser.serve_fixture` over a real temporary Weft on an ephemeral loopback
port — through the **rendered UI in headless Chromium** (Playwright's own bundled build,
`--no-sandbox`). Every assertion is made against visible controls and the DOM the operator
sees. Nothing is injected into SQLite or a projection; the browser types into inputs and
clicks buttons like a human.

## Result

> **The numbers and the scope in the two sections below are the WS1 lane's own run and were
> accurate for it. Both have since been overtaken — see
> [the 0.3.1 addendum](#031-addendum--2026-07-27-branch-claudeworkspace-write-and-residuals)
> at the foot of this file for the current spec count and for the three "product gaps" that
> are now closed.** This section is not rewritten to track main, for the reason the release
> metadata guard states: release evidence that gets edited to stay true is not evidence.

**8/8 browser specs PASS** against the real rendered Shell on this ARM host.

```
✓ a11y.spec.js                — nav keyboard-operable, named controls, text status, no dup ids, landmarks
✓ knowledge.spec.js           — Scenario A: import 3 docs, trust zones, provenance durable across refresh + restart + rebuild
✓ project_lifecycle.spec.js   — Scenario B: create project (durable), start/pause plan, agent inspector, gated proposal defers
✓ security_chrome.spec.js     — invariant 5: hostile content inert; only the real reauth-gated component can approve
✓ security_cross.spec.js (x4) — unauth /api/* → 401; strict CSP present; imported HTML/MD cannot script; clean console/network on full nav walk
```

Reproduce (from a clean checkout):

```bash
TESTENV=/path/to/pyenv bash tests/browser/run.sh
# or, if @playwright/test + chromium are already installed and decima importable:
TESTENV=/path/to/pyenv npx playwright test --config playwright.config.js
```

Launch the same server a human can poke by hand:

```bash
PYTHONPATH="$TESTENV:$PWD" python3 -m tests.browser.serve_fixture --db /tmp/weft.db --port 8991
# → prints DECIMA_SHELL_PAIRING=<secret> and DECIMA_SHELL_READY=http://127.0.0.1:8991/
# health: GET /api/v1/health  ·  unauth /api/* → 401  ·  strict CSP on every response
```

Screenshots: `knowledge-trust-zones.png` (signed-in Shell, trust-zoned notes + provenance —
also proves the login-gate bug fix below), `approval-inbox-trusted.png` (the trusted approval
card with reauth-gated actions).

## Product bugs found by this lane and FIXED (all in-lane)

The rendered-browser qualification caught three real daily-driver defects that the in-process
unit tests (307 green) never exercised. All fixes are inside this lane's permitted surface.

1. **Signed-in Shell was unreachable — the login gate overlaid the app.** `decima/shell/frontend/app.css`
   set `.gate { display: grid }` and `.app { display: grid }` unconditionally. Author `display`
   rules override the UA `[hidden] { display: none }` rule, so toggling the `hidden` attribute
   never hid anything; after a successful login the full-screen `#gate` (position:fixed inset:0)
   stayed painted on top and `elementFromPoint(center)` returned the pairing input. **Fix:** a
   global `[hidden] { display: none !important; }` normalize in `app.css`.

2. **Every authenticated read/mutation 500'd over the real threaded server.** `make_http_server`
   uses a per-connection-threaded `ThreadingWSGIServer`, but the kernel Weft holds a single
   `sqlite3` connection created on the build thread, and a plain sqlite connection may only be
   used from its creating thread → `sqlite3.ProgrammingError` on the first `driver.update()` in a
   worker thread. The existing loopback test only exercised public `health`/`login` (no
   `driver.update()`), so it was never seen. The root fix (`check_same_thread=False` + a lock) is
   in `decima/kernel/weft.py`, which is **off-limits to this lane**. **In-lane mitigation:**
   `decima/shell/serve.py` now serves the Shell on a **single-threaded** loopback server
   (`make_loopback_server`) so all Weft access stays on one thread — correct for a single-user
   local daemon over single-threaded sqlite (projection reads are in-memory; `/stream` frames are
   finite). See `known-issues.md` for the kernel-lane follow-up.

3. **Strict CSP blocked the Projects progress bar (console CSP violation).** `projects.js` set the
   fill width via a `style` **attribute** (`el(…, {style:"width:X%"})`); `style-src 'self'`
   forbids inline style attributes, so the bar never filled and each Projects render logged a CSP
   violation. **Fix:** set `fill.style.width` via the CSSOM, which `style-src` does not govern.

## Honest scope — what the shipped 0.3 Shell does NOT do (product gaps, not test gaps)

> **All three gaps below are CLOSED as of the 0.3 completion candidate** — the audit records
> finding M-3 as RESOLVED (`../audit/0.3-audit.md`, addendum 2026-07-12) and each workflow now
> has its own spec. Read this section as the lane's honest scope AT THE TIME, not as a
> statement about the shipped product. See the 0.3.1 addendum below.

The WS1 brief lists an aspirational A/B/C. The shipped Shell surface is narrower; this lane
qualifies what the product actually renders and records the rest as gaps rather than faking a
pass. None of these are failures of the harness — the controls simply do not exist yet.

- **Scenario A — cross-source Q&A + clickable citations:** the Shell surfaces knowledge as
  trust-zoned **notes** with per-item **provenance** (the Weft event ids that asserted them). It
  ships **no** question-answering engine and **no** clickable inline "citation" that opens a
  segment. Qualified instead: importing ≥3 docs, trust separation, and the durable provenance
  (the real "source" the UI exposes) across refresh + restart + projection rebuild. The
  cross-scenario assert "no citation to a nonexistent segment" is therefore **N/A**.
- **Scenario B — model-generated plan → accept → agent hierarchy → resume → budget inspector:**
  the Shell has no "request a plan" model call, no "accept" step, and no runtime that spawns an
  agent forest from the UI. Qualified instead: create project, plan **start/pause** lifecycle, the
  capability/agent **inspector** (objective/principal/budgets/deadline/status), and gated
  terminate/revoke **proposals** deferring to the trusted Approval inbox. The bounded agent used
  is a harness precondition created via the **canonical kernel path** (`--seed-agent` →
  `cells.create_agent`, an Agent Cell on the Weft — NOT a projection/SQLite injection), standing
  in for the runtime the Shell does not embed.
- **Scenario C — coding workspace (isolated edits, diff/test artifacts, no push):** the Shell has
  **no coding-workspace surface at all**. This is out of scope for the browser layer; the
  worker-isolation guarantees are covered at the kernel/worker layer by
  `tests/adversarial/test_worker_isolation.py`, not through the UI.

## a11y — deliberately a SMOKE check (not overclaimed)

`a11y.spec.js` checks concrete high-value properties only: nav items are keyboard-operable named
`<button>`s (focus + Enter activates), the approval controls have accessible names, status is
conveyed as **text** (pills) not colour alone, there are no duplicate element ids on the primary
screens, and headings + landmarks (`nav[aria-label]`, `main`, `h2#view-title`) are present. This
is **not** a full WCAG audit and must not be read as one.

## Environment notes

- Playwright's bundled `chromium-headless-shell` (v1228, arm64) launches in ~0.4s with
  `--no-sandbox`; the host's `/usr/bin/chromium` raw launch hangs and is not used.
- The launcher uses `secure_cookie=False` (same concession as `tests/shell/conftest.py`) because
  the browser talks plain HTTP to loopback; it touches no authority path.
- A fixed keyring seed makes the pairing secret reproducible, so a restart over the same db
  re-derives the same identity — which is how the durability-across-restart assertion works.

---

## 0.3.1 addendum — 2026-07-27 (branch `work/workspace-write-and-residuals`)

_This addendum does NOT rewrite the lane record above; the 8/8 result and the three documented
gaps were accurate for the WS1 lane. It records what the tree actually contains now, because a
reader was otherwise being told by this file that the product lacks its three headline
workflows._

**Spec count.** `npx playwright test` → **14 tests across 10 spec files** (single worker, no
retries), re-run twice back-to-back on this host. The five specs added since the lane record:

- `qa.spec.js` — cross-doc grounded answer with citations that open the real source passage.
- `planning.spec.js` — routed model proposal → deterministic validation → `AcceptPlanProposal`
  → scheduler execution → pause / resume / cancel / gated terminate.
- `workspace.spec.js` (×2) — granted repo root → bounded change → jailed networkless worker →
  durable diff + test artifacts → restart recovery; plus a hostile-edit-path/inert-render case.
- `nona.spec.js` — self-extension: hostile candidate source stays text, a gated promotion needs
  a human, rollback demotes.
- `visual_a11y.spec.js` — the visual / trust-boundary / a11y walk that writes `../visual/*.png`.

**The three "product gaps" are closed.** Scenario A (grounded Q&A + citations), B (model-planned
durable agents) and C (isolated coding workspace) are all driven end-to-end through the rendered
Shell and each is qualified by its own spec above. The audit records this as finding **M-3
RESOLVED** (`../audit/0.3-audit.md`, addendum 2026-07-12). The `(307 green)` in-process figure
quoted above is likewise a lane-time number: the suite now collects **1269** tests.

**The threading known-issue's root fix has LANDED.** `known-issues.md` filed the real fix for
the kernel lane; it is in the tree — `decima/kernel/weft.py` opens the connection
`check_same_thread=False` and serializes every path that touches it under a re-entrant
per-store lock (0.3.1 T1.3). The Shell still serves single-threaded, but now **by choice**, not
because the store forbids anything. See the resolution note in `known-issues.md`.

**`../visual/*.png` are now byte-reproducible.** They were not: the transient toast stack
(`#toasts`, `position: fixed`) is dropped by a wall-clock `setTimeout(..., 4000)` in
`decima/shell/frontend/js/app.js`, so the number of toasts still painted when a capture fired
was a function of host speed alone — two back-to-back full-suite runs on an unchanged tree
rewrote `plans-desktop.png`, `plans-mobile.png` and `qa-mobile.png`, with every differing pixel
inside a toast box. `visual_a11y.spec.js` now clears the toast host immediately before each
capture (`clearToasts`, which fails loudly if `#toasts` ever disappears), and all ten files
then reproduce byte-for-byte across runs. **Any future modification to those PNGs is therefore
a real UI-change signal and must be reviewed as one, not written off as noise** — which is
exactly how `nona-desktop.png` / `nona-mobile.png` came to be stale: they predated the canary
`ORGAN HEALTH` panel and its `Run health sweep` control, and the churn hid it. Both are
refreshed here and now depict the shipped Self-extension screen.

_Addendum author: release-evidence engineer, 2026-07-27. Numbers re-measured on this host, not
carried over._
