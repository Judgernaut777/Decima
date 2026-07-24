# Changelog

All notable changes to Decima are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_In development as `0.3.1.dev0`. Everything below landed on `main` **after** the `0.3.0`
release candidate (commit `26a1024`) and is not part of the 0.3.0 candidate._

### Added (post-0.3.0 evolution waves, previously unlisted)

- **Capability-based provider routing, deepened** — graded capability tiers on model
  entries, a hard `min_context` floor, sensitive-task→local-only enforcement before
  ranking, and soft locality/latency biases (`decima/models/routing.py`).
- **Versioned read contract** — `decima.read_contract` (`READ_CONTRACT_VERSION = "0.1"`),
  a narrow read-only facade over the projections for downstream consumers.
- **Q&A retrieval upgrades** — deterministic hybrid lexical ranking with incremental
  indexing, plus citation-relevance scoring and de-duplication in answers.
- **Planner composition** — model-planned agents now compose real product capabilities as
  typed multi-capability plan steps.
- **Workspace stage 2** — model-*proposed* bounded workspace changes
  (proposal → deterministic validation → capability authorization → isolated execution).
- **Worker containment hardening** — PID-namespace isolation as a capability-detected
  hard floor, best-effort seccomp deny filter, and a containment matrix emitted as data
  and asserted by tests.

### Fixed (0.3.1 stabilization)

- **The mypy gate is now real.** The type-check lane had never passed (~680 errors; CI
  red on every run since Phase 1, including the `v0.3.0` tag commit): the config both
  excluded `decima/kernel/` and held `decima.kernel.*` to a strict override that fired on
  followed imports, and the tree was never checked locally. The kernel is now annotated
  to the strict bar, the exclusion contradiction is removed, the remaining errors are
  burned down, and `mypy` is pinned exactly (same rationale as the ruff pin).
- **Version discipline** — `main` now identifies as `0.3.1.dev0` instead of masquerading
  as the released `0.3.0`; the release-metadata guard enforces dev/release CHANGELOG
  discipline and stops force-syncing released docs to the current tree.
- **`adversarial` marker actually applied** — it was registered but attached to no test,
  so CI's `-m "not adversarial"` deselected nothing; the suite now runs as its own lane.
- **seccomp filter is architecture-aware** — the deny filter hard-coded aarch64 constants
  and would kill every worker at its first syscall on any other architecture; it now
  engages only on aarch64 and records itself as skipped elsewhere (it was always
  documented as best-effort).
- **`services/api/server.py` serves single-threaded** — it built a threading WSGI server
  over the single-connection Weft, which faults under concurrent requests
  (`shell/serve.py`, the shipping entrypoint, already served single-threaded); it also no
  longer prints the pairing secret to stdout (journald leak).
- **Heartbeat seed file permissions** — the legacy boot kernel wrote the master seed
  world-readable with no `O_EXCL`; it now writes `0600` exclusive, matching the
  production provisioner's discipline.
- **Structured authorization reason codes** — denial reasons are now classified from a
  structured result computed at the denial site instead of substring-matching the
  human-readable sentence (which silently degraded to `DENIED` on any rewording).
- **CI hardening** — coverage now enforces a threshold and runs once (it ran the whole
  suite twice, thresholdless); the Playwright suite has a CI lane; actions are pinned.

### Meta

- Added a root `LICENSE` file matching the package's proprietary metadata.

## [0.3.0] — Local Daily Driver

_Qualified as the `0.3.0` release candidate on the fully-gated candidate commit, but **not yet
tagged** — cutting the `v0.3.0` tag is still pending (re-run the full gate on the exact tag commit,
then bump `0.3.0.dev0` → `0.3.0` and apply the `v0.3.0` tag; see condition 3 in RELEASE-READINESS).
Live-provider qualification was done against a real local model, and the trust-boundary/UI review
was performed automatically (see [`docs/RELEASE-READINESS.md`](docs/RELEASE-READINESS.md))._

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
routing path. Full gate on the candidate: **626 passed / 25 skipped**, **13 Playwright specs across
9 files**, adversarial **49**; `heartbeat/`, `decima/kernel/`, and `protocol/` remain byte-frozen.

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
