# Decima — Build Backlog

The shared board for multi-instance work. One source of truth for **what's next,
who can take it, and how not to collide.**

> Decima is built in the **Python reference** until the design stops moving (see [`VISION.md`](../VISION.md)).

## Status

**Cycle 1 — ✅** A1/A2/F1 · B1/B2 · A3 · C1 · E1.
**Cycle 2 — ✅** D1 (CLI worker) · D2 (sessions) · D3 (org policy).
**Cycle 3 — ✅** M1 (merge layer) · B3 (memory maturation) · C2 (router engines) · S1 (`SYNC.md`) · S2 (`SNAPSHOTS.md`).
**Cycle 4 — ✅** **M2** (Sequence/Map/Counter/Append-log + adjudication) · **SN1** (snapshots: verifiable cache) · **SY1** (sync convergence sim).
**Cycle 5 — ✅** **R1** (REDACT → §11 8/8) · **SY2** (sync transport, two real Wefts) · **B4** (memory-as-governance).
**Cycle 6 — ✅** **DET1** (detection-as-code, security beachhead) · **INS1** (Capability Inspector + the Constellation) · **SH1** (agent shorthand).
**Cycle 7 — ✅** **SB1** (sandboxed-principal substrate) · **GX1** (sync at scale: Merkle-DAG + gossip) · **VOX1** (voice contract slice).
**Cycle 8 — ✅** **WV1** (Wager/Verdict learning loop) · **OR1** (Orientation lens) · **AR1** (auto-router).
**Cycle 9 — ✅** **DISP1** (disposition routing) · **PAY1** (Morta-gated payments rail) · **IFB1** (incremental fold-from-base).
**Cycle 10 — ✅** **CRED1** (secrets broker) · **INF1** (self-hosted/private inference) · **LOOP1** (live governance gate).
**Cycle 11 — ✅** **INTAKE1** (live disposition loop) · **TRIAGE1** (blue-team triage/SIEM) · **TRADE1** (trading on the rail).
**Cycle 12 — ✅** **CASCADE** (retraction cascade to derived authority) · **RED1** (red-team capability) · **PLAN1** (planning/decomposition) — *first sub-agent fleet*.
**Cycle 13 — ✅** **DOC1** (documents) · **CONTACTS1** (people) · **WATCH1** (reactive triggers) · **AUDIT1** (audit/compliance) · **PURPLE1** (purple-team loop) · **BUDGET1** (finance analytics) — *6-lane fleet*.
**Cycle 14 — ✅** **SCHED1**·**MSG1**·**FILES1**·**PROJ1**·**HOME1**·**HEALTH1**·**NOTIFY1**·**REVIEW1**·**KNOW1**·**TIMELINE1** — *10-lane fleet* (Part B personal-OS sweep + dev/knowledge).
**Cycle 15 — ✅** **GOALS1**·**JOURNAL1**·**FEED1**·**SHOP1**·**RESEARCH1**·**IR1**·**RECON1**·**METRICS1**·**DASH1**·**EXPENSE1** — *10-lane fleet* (composing-the-substrate breadth: goals↔wager, research↔observe+docs, IR↔triage+plan+projects, recon↔red, dashboard↔timeline+notify+sched+projects).
**Cycle 16 — ✅** **TRAVEL1**·**CRM1**·**DEVOPS1**·**LEGAL1**·**LEARN1**·**SUBS1**·**SUPPORT1**·**MEDIA1**·**TRANSLATE1**·**WEATHER1** — *10-lane fleet* (more Part B: travel/crm/devops/legal/learning/subscriptions/support/media/translate/weather).
**Cycle 17 — ✅** **LEASE1** (core: time-locked/single-use authority) · **CAPITAL1** (ephemeral cards + fiat/crypto rails) · **IDENTITY1** (SSO=cap issuance) · **PARSE1** (untrusted-input firewall) · **PATTERN1** (9 agentic patterns + deterministic selector + manual override) · **WEBHOOK1** (synchronous real-time approval) — *fleet, kernel-stressing capability classes*.
**Cycle 18 — ✅** **AUTO1** (autonomy ladder D5) · **BROKER1** (wrapped agentic brokerage, Stripe-funded) · **VAULT1** (sovereign data substrate D6 — the OneDrive equivalent) · **LEDGER1** (double-entry accounts) · **CTX1** (code-aware context engine, Augment learning) — *financial + sovereignty fleet*.
**Cycle 19 — ✅** **SEARCH1** (search over all Cells, RAG-provenance) · **TRACING1** (causal traces over the Weft DAG) · **EGRESS1** (gated outbound fetch, allowlist) · **JOBS1** (durable queue = future-authority-as-lease) · **EVALOPT1** (evaluator-optimizer loop, real) · **TAX1** (tax over the ledger) — *B2/B3 engine+SRE layers + money/cognitive depth*.
**Cycle 20 — ✅** **MAPS1** · **PHOTOS1** · **RECIPES1** · **FITNESS1** · **VULN1** (vuln-mgmt/threat-intel) · **DISPATCH1** (executes PATTERN1's chosen pattern — selection made live) — *last personal-OS slabs + security/cognitive depth*.
**Cycle 21 — ✅** **OFFICE1** · **OCR1** · **BOOKMARKS1** · **TIMETRACK1** · **SOCIAL1** · **INSURANCE1** — *breadth mop-up: last domain stragglers; CAPABILITY_MAP Part B broadly slabbed*.
**Cycle 22 — ✅ (DEPTH)** **LIVE1** (core: autonomy ladder live at every invoke, inert-by-default) · **BRAIN1** (dispatch+planning in the decide loop) · **REACTOR1** (reactive tick: watchers+scheduled events+jobs fire in one pass) — *depth phase begins: wiring the cognitive layer into the live loop*.
**Cycle 23 — ✅** **RESILIENCE1** (circuit-breaker/backpressure/bulkhead) · **DATASCI1** (analytics/group-by/chart-spec) · **ACCESS1** (accessibility audit+shaping) · **API1** (API surface = capability invocation) · **TERMINAL1** (terminals/session-mux) — *breadth completeness mop-up: every reference-buildable catalog item now slabbed. Remaining catalog = engine-wrapping (depth).*
**Cycle 30 — ✅ (DEPTH · make-a-stub-real)** **STRIPE-RAIL** — the FIRST real external engine. Dependency decision RESOLVED: stay pure-stdlib by default, but WRAP real engines for high-liability domains (recreating money movement is the liability). Stripe is an HTTPS API → wrapped over stdlib `urllib`, **zero pip deps**. `stripe_rail.py` charges via a transport seam (injected in tests → offline oracle), maps succeeded/declined/timeout → SUCCEEDED/FAILED/UNKNOWN + `provider_ref`, on the SAME PAY1 spine (Morta-gated, spend-capped, idempotent). **TEST-MODE ONLY** (refuses `sk_live_` before any request); key via CRED1 `use_secret` (applied inside the broker, never on the Weft). Check 288; oracle green. — *solo lane (new `stripe_rail.py` + `secrets.use_secret`), off main.*
**Cycle 31 — ✅ (DEPTH · make-a-stub-real)** **OIDC-IDENTITY** — wrap the real auth provider (never roll your own auth). IDENTITY1 already mints an attenuated capability from a provider attestation; this makes the STUB exchange real: `oidc.exchange_code` does the OAuth2 authorization-code→token exchange at the provider's real token endpoint over stdlib `urllib` (zero deps, transport seam → offline oracle), and the provider's ACTUAL granted scope drives the clamp — Decima can't issue authority wider than the provider attested. HTTPS-only (refuses cleartext before the secret is sent); client secret + access token both held by CRED1 (never on the Weft); fail closed (a declined exchange mints no session/capability). Check 290; oracle green. — *solo lane (new `oidc.py`), stacked on Cycle 30 (needs `secrets.use_secret`).*
**Tooling — ✅** `heartbeat/checks/NN_*.py` auto-run by `smoke.py`; new lanes add a file there, never edit `smoke.py`. Cycles now run as **parallel sub-agent fleets** (one worktree per lane; all-non-core batches land with zero contention; ≤1 core lane per batch).

Oracle: **all 8 FOLD §11 invariants hold.** What's real in the reference now spans: the **kernel**
(merge · snapshots + incremental fold · sync sim/transport/scale · redaction · receipts · retraction
cascade · live governance + live intake), the **cognitive layer** (memory + governance · orientation ·
wager/verdict · auto-router · disposition · planning), the **blue/red security flagship**
(detection → triage/SIEM · red-team · purple loop), **sovereign action** (payments · secrets broker ·
private inference · trading), and a **personal-OS surface** (documents · contacts · watchers · audit ·
budget). The scope catalog of what's still ahead is [`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md).

## Coordination rules

1. **Core-kernel files serialize:** `weave.py`, `weft.py`, `kernel.py`, `executor.py`
   — **one owner per batch.** Everyone else builds in new modules and *calls* the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, not `smoke.py`.** Own a free `NN`.
3. **`specs/` is collision-free** — one owner per file.
4. Keep the oracle green: `cd heartbeat && python3 smoke.py` → `alive. ✓`, exit 0.

## No active cycle

Cycles run on demand as sub-agent fleets — the next batch is chosen when asked. Max parallelism
is an **all-non-core batch** (every lane a new module → lands in any order, zero rebase contention);
include **at most one core lane** per batch.

## Backlog (future candidates)

- **A proper `Weft.ingest()`** with full WEFT §2 validation (real networked sync transport; pairs with GX1) — core.
- **More retraction modes / lease trees** (SUPERSEDE/TERMINATE; lease expiry) building on CASCADE — core.
- **Red-team depth** (more probe classes / engagement reporting) and **blue-team depth** (correlation rules, response playbooks).
- **Wire the cognitive layer fully into the live loop** — planning → delegation; watchers driving dispositions; orientation/wager on every decision.
- **More personal-OS domains** (`CAPABILITY_MAP` Part B) — calendar/scheduling, email/messaging, files, projects, health, home.
- **Make a stub real** — DEPENDENCY DECISION RESOLVED: pure-stdlib default, WRAP real engines for high-liability domains. ✅ **Stripe** (Cycle 30, via urllib, zero deps, test-mode). Next candidates: real hosted model engines (already urllib-reachable), other financial rails / identity / KYC (wrap via API). Still port/dep-gated: heavy LOCAL engines (local model weights, whisper/Piper voice, real browser, WASM runtime, seccomp/landlock sandbox).
- **The Constellation GUI** (Skyrim-style skill tree) over INS1's data model.
