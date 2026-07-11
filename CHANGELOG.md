# Changelog

All notable changes to Decima are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — Local Daily Driver

_Candidate. Package version `0.3.0.dev0`; the `v0.3.0` tag is applied only after the operator
completes the live-provider smoke and the manual UI review (see
[`docs/RELEASE-READINESS.md`](docs/RELEASE-READINESS.md))._

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
- **Model routing** — a provider abstraction where models only *propose* (deterministic code
  authorizes); routing records provider, model, reason codes, estimated cost, and sensitivity
  class. The **deterministic provider is the default**; a live provider is opt-in.
- **Disposable projections** — the Weave, boards, knowledge, activity, and approvals are rebuildable
  projections; a rebuild reproduces state without changing canonical meaning.
- **Local API + trusted Shell** — a same-origin loopback service (`127.0.0.1`) that serves a static
  frontend and gates every write; strict same-origin CSP, unauthenticated `/api/*` → 401,
  reauth-gated approvals.
- **Daily-driver capabilities (library)** — source-grounded Q&A with citations
  (`decima/capabilities/qa.py`) and an isolated coding workspace
  (`decima/capabilities/workspace.py`), both unit-tested. **Not yet wired into the Shell** — see
  _Known limitations_.
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
  `--target` install serves the UI.
- Rendered-Shell defects caught by browser qualification: a login gate that overlaid the app, a
  cross-thread `sqlite3` error on the first authenticated request, and a CSP-blocked progress bar.

### Known limitations

- The charter's "scenarios A–C through the Shell" are **not literally delivered in the Shell**: A
  (Q&A + citations) and C (coding workspace) are library-only modules not routed into the UI, and B
  is a **manual** project/plan start-pause lifecycle with no model-generated plan or agent forest.
  Acceptable for a scoped 0.3 "Local Daily Driver"; disclosed at the top of `README.md`.
- Service / reboot lifecycle is proven in a systemd-enabled container (the host systemd is
  degraded), and the live-provider smoke is operator-gated pending a real credential.

[Unreleased]: https://example.invalid/decima/compare/v0.3.0...HEAD
[0.3.0]: https://example.invalid/decima/releases/tag/v0.3.0
