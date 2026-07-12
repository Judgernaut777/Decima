# Changelog

All notable changes to Decima are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — Local Daily Driver

_Candidate. Package version `0.3.0.dev0`; the lead bumps it to `0.3.0` and applies the `v0.3.0`
tag after re-running the full gate on the exact tag commit and completing the manual UI review
(see [`docs/RELEASE-READINESS.md`](docs/RELEASE-READINESS.md)). The live-provider qualification is
already done against a real local model._

Decima 0.3 turns the frozen `heartbeat/` reference into a locally-hosted, single-user daily-driver
app: a new `decima/` package **extracted from and proven equivalent to** the reference, fronted by
a trusted loopback Shell. Architecture preserved throughout — four verbs over one append-only Weft,
the Weft as the sole canonical store, and no ambient authority.

### Added

- **Trusted-core extraction** — a `decima.kernel` package (signed append-only Weft, four verbs,
  fold + state-root, object-capability authority) copied verbatim from the reference and **proven
  byte-equal** via golden conformance fixtures (`tests/kernel/`).
- **Conformance harness** — canonical-bytes, event-id, fold-state-root, and tamper-detection golden
  vectors, plus a kernel import-boundary guard that forbids ambient authority in the kernel.
- **Durable runtime** — crash-recoverable, idempotent plan/job execution with supervision, leases,
  and revocation cascade; effects land as receipts on the Weft.
- **Isolated workers** — the coding-workspace/worker capability runs commands in an isolated child
  (chroot jail, no network, no credentials); worker-escape suite green.
- **Model routing + real local live inference** — a provider abstraction where models only
  *propose* (deterministic code authorizes); routing records provider, model, reason codes,
  estimated cost, and sensitivity class. The **deterministic provider is the default**; a live
  provider is opt-in via `DECIMA_LIVE_*`. The live path is **qualified against a real local model**
  — a llama.cpp Qwen3-30B-A3B served OpenAI-compatible on `127.0.0.1:8080`, selected by the
  product's own routing through the actual app path (`tests/live/test_app_path_live.py`,
  `docs/release-evidence/models/shell-driven-live-routing.md`). No cloud credential is used or
  needed.
- **Disposable projections** — the Weave, boards, knowledge, activity, and approvals are rebuildable
  projections; a rebuild reproduces state without changing canonical meaning.
- **Local API + trusted Shell** — a same-origin loopback service (`127.0.0.1`) that serves a static
  frontend and gates every write; strict same-origin CSP, unauthenticated `/api/*` → 401,
  reauth-gated approvals.
- **Three daily-driver workflows, delivered through the Shell** — each driven end-to-end through
  visible controls and qualified by a Playwright spec against the real backend:
  - **Grounded Q&A** (`decima/services/api/qa_service.py`, `js/screens/qa.js`, `qa.spec.js`) —
    import documents → choose a retrieval scope → ask → generated answer with citations that
    **open the real source passage**; generated text is visually distinct from imported data;
    hostile imports stay inert DATA; runs are durable across refresh / restart / projection-rebuild.
  - **Model-planned durable agents** (`plan_service.py`, `js/screens/plans.js`, `planning.spec.js`)
    — objective → routed **model proposal** → deterministic validation → `AcceptPlanProposal` mints
    durable Plan / Step / Agent Cells → scheduler execution → **pause / resume / cancel / gated
    terminate** (server-enforced); proposed vs. authorized vs. executed are visually distinct.
  - **Isolated coding workspace** (`workspace_service.py`, `js/screens/workspace.js`,
    `workspace.spec.js`) — grant a repo root → bounded change → jailed, networkless `decima.workers`
    child → durable diff + test artifacts (rendered as untrusted text) → restart recovery; no push,
    no credential, no network; hostile worker output inert.
- **Operations** — `decima-doctor`, `decima-backup`, `decima-restore`, and a systemd user unit +
  install/uninstall scripts under `deploy/`; backups exclude signing keys.
- **Qualification evidence** — browser (Playwright), clean-install (systemd container), and
  model-provider lanes with an independent release audit under `docs/release-evidence/`.
- **`python3 -m decima.shell.serve <weft.db>`** now really starts the Shell (a real `__main__`
  entry), and the pairing secret is written to a `0600` file beside the Weft — no longer printed to
  the journal (`--print-secret` opts back in).

### Changed

- The Shell daemon serves **single-threaded** so all Weft access stays on the one `sqlite3` thread
  (a kernel follow-up to relax this is filed for 0.3.1).
- The systemd unit runs the real Shell host (`deploy/decima-shell-server`) via an idempotent
  first-run wrapper, reduced to the user-service-enforceable hardening subset.

### Fixed

- The wheel now ships the Shell frontend as package data (`GET /` served 404 before); an isolated
  `--target` install serves the UI — the wheel now ships **18** frontend files, including the three
  new workflow screens (`js/screens/qa.js`, `workspace.js`, `plans.js`).
- **CLI ops honor process arguments** — the installed console scripts (`decima-doctor`,
  `decima-backup`, `decima-restore`, `decima-rebuild`) now read their own argv instead of ignoring
  passed arguments.
- **Mobile UI fixes** from the automated visual/trust-boundary/a11y review of the principal screens.
- Rendered-Shell defects caught by browser qualification: a login gate that overlaid the app, a
  cross-thread `sqlite3` error on the first authenticated request, and a CSP-blocked progress bar.

### Path A complete — the three daily-driver workflows now ship in the Shell

The original independent audit's crux (scenarios A–C were implemented but **not wired into the
Shell**, and the live provider was BLOCKED-pending-operator) is **RESOLVED**: grounded Q&A,
model-planned durable agents, and the isolated coding workspace are now delivered through the
rendered Shell (see _Added_), and a real **local** provider is qualified through the actual product
routing path. Full gate on the candidate: **498 passed / 25 skipped**, **13 Playwright specs across
9 files**, adversarial **34**; `heartbeat/`, `decima/kernel/`, and `protocol/` remain byte-frozen.

### Known limitations

- **Single-user, loopback-only daemon** (`127.0.0.1`); **single-threaded** server (the `weft.py`
  `check_same_thread=False` + lock kernel fix is filed for 0.3.1); the **deterministic provider is
  the default** (a live provider is opt-in via `DECIMA_LIVE_*`, qualified against a local model);
  Q&A **retrieval is local lexical scoring**, not embeddings.
- Service / reboot lifecycle is proven in a systemd-enabled container (the host systemd is
  degraded), not on a bare host.
- Intentionally out of 0.3 (handoff §3.2): financial automation, live brokerage, full browser
  automation, mobile, replication/multi-device sync, and the eventual single Rust port.

[Unreleased]: https://example.invalid/decima/compare/v0.3.0...HEAD
[0.3.0]: https://example.invalid/decima/releases/tag/v0.3.0
