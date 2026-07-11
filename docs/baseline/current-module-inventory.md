# Decima 0.3 — Current Module Inventory (baseline)

**Scope:** all 180 Python modules under `heartbeat/decima/*.py`. This is the maturity/trust
inventory that gates the 0.3 milestone (Phase 2 extracts the kernel/TCB from it).

## Method note

Each module was classified from two objective sources, not guesswork:

1. **Docstring / header** — the first ~10–25 lines of every module were read verbatim to
   derive its *Category* (target 0.3 layer) and *Status* (maturity language, plus whether
   `run.py` / `smoke.py` reference it — those two boot `shell`, `daemon`, `golive`,
   `kernel`, `weave`, `agent`, `hashing`, `reckoner`, `model`, `memory`, `retrieval`,
   `executor`, `workspace`, `powerbox`, `router`).
2. **Import analysis** — every file was grepped for effect libraries
   (`urllib`/`socket`/`subprocess`/`http`/`sqlite3`/`nacl`/`ctypes`/`mcp`/`threading`).
   *Trust class* and *External effects* are grounded in what a module **actually imports**,
   not what its prose implies. Notable facts: only `weft.py` imports `sqlite3`; only
   `crypto`, `keystore`, `rotation`, `sync` import `nacl` (Ed25519 signing); `isolation`
   is the only `ctypes`+`subprocess` module; `egress` imports only `urllib.parse` (URL
   parsing) while the real transport lives in `wire`/`live_wire`. Modules that reach the
   network *by composition* (e.g. `mediated_browser`, `inference`, `mcp_server`, `maps`)
   carry no direct effect import but are still classed `untrusted` because they route
   through the egress/MCP boundary.

The 25 bundled engines are the ones enumerated in `decima/builtin_manifests.py`
(`BUILTINS`): stripe_rail, payouts, brokerage_engine, exchange, payroll, shipping,
cloud_compute, ecommerce, ads, comms, paging, esign, insurance_claim, dns, oidc,
calendar_engine, tax_engine, kyc, background_check, accounting, maps_engine,
weather_engine, cloud_storage, ocr_engine, translate_engine.

**Keep-0.3 rule applied:** the daily-driver keeps kernel + runtime/supervisor +
workers/isolation + model routing + knowledge/notes/documents + tasks/projects +
artifacts + dev workspace + restricted filesystem + shell/api + approvals/capability/
activity inspectors + backup/restore/doctor. Everything on the handoff's explicit
deferral list (public marketplace, Nona auto-promotion, financial automation, live
brokerage, autonomous payments, healthcare, insurance, tax filing, KYC production, full
browser automation, mobile, cross-device replication, cloud relay, the dozens of new
domain packs) is marked `defer` (`no` only for harness-only code).

---

## Summary counts

### By category
| Category | Count |
|---|---|
| kernel | 19 |
| runtime | 34 |
| projection | 22 |
| service | 19 |
| capability | 85 |
| legacy | 1 |
| **Total** | **180** |

### By trust class (from actual imports)
| Trust class | Count |
|---|---|
| trusted | 24 |
| restricted | 86 |
| untrusted | 70 |
| **Total** | **180** |

### By Keep-0.3 disposition
| Disposition | Count |
|---|---|
| yes | 81 |
| defer | 98 |
| no | 1 |
| **Total** | **180** |

---

## Kernel (TCB candidates) — 19

The trusted computing base: append (`weft`), fold (`weave`), verify (`verifier`),
authorize (`capability`/`autonomy`/`roe`/`powerbox`), crypto (`crypto`/`keystore`),
identity (`identity`), approvals (`inbox`), checkpoints (`snapshot`), hashing (`hashing`),
canonical types-as-data (`model`), and the mandatory fail-closed data boundaries
(`quarantine`, `parse`, `redact`). Runs in the kernel process; pure verify/authorize/
fold/append; no network or subprocess.

| Module | Category | Trust class | External effects | Secrets | Status | Destination | Keep 0.3 |
|---|---|---|---|---|---|---|---|
| `__init__.py` | kernel | trusted | None (arms egress guard at import) | None | production-candidate | `decima/__init__.py` | yes |
| `weft.py` | kernel | trusted | sqlite | None | production-candidate | `decima/kernel/weft.py` | yes |
| `weave.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/weave.py` | yes |
| `model.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/model.py` | yes |
| `crypto.py` | kernel | trusted | crypto-signing (nacl) | signing keys via keystore | production-candidate | `decima/kernel/crypto.py` | yes |
| `keystore.py` | kernel | trusted | crypto-signing (nacl) | signing keys via keystore | production-candidate | `decima/kernel/keystore.py` | yes |
| `hashing.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/hashing.py` | yes |
| `capability.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/capability.py` | yes |
| `authorization → autonomy.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/autonomy.py` | yes |
| `roe.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/roe.py` | yes |
| `powerbox.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/powerbox.py` | yes |
| `inbox.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/inbox.py` | yes |
| `verifier.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/verifier.py` | yes |
| `identity.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/identity.py` | yes |
| `snapshot.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/snapshot.py` | yes |
| `quarantine.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/quarantine.py` | yes |
| `parse.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/parse.py` | yes |
| `redact.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/redact.py` | yes |
| `kernel.py` | kernel | trusted | None | None | production-candidate | `decima/kernel/kernel.py` | yes |

---

## Runtime — 34

Scheduler / supervisor / workers / execution / model-tier routing / recovery. `merkle`,
`sync`, `gossip` are the replication substrate (deferred); the Nona forge loop
(`reckoner`, `forge`, `candidate`, `promotion`, `selfupdate`, `evalopt`) is deferred
auto-promotion.

| Module | Category | Trust class | External effects | Secrets | Status | Destination | Keep 0.3 |
|---|---|---|---|---|---|---|---|
| `daemon.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/daemon.py` | yes |
| `reactor.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/reactor.py` | yes |
| `resume.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/resume.py` | yes |
| `jobs.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/jobs.py` | yes |
| `scheduling.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/scheduling.py` | yes |
| `concurrency.py` | runtime | restricted | threading | None | production-candidate | `decima/runtime/concurrency.py` | yes |
| `isolation.py` | runtime | untrusted | subprocess, ctypes | None | production-candidate | `decima/runtime/isolation.py` | yes |
| `cli_worker.py` | runtime | untrusted | subprocess (via isolation) | None | production-candidate | `decima/runtime/cli_worker.py` | yes |
| `executor.py` | runtime | restricted | None (delegates to capability) | None | production-candidate | `decima/runtime/executor.py` | yes |
| `dispatch.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/dispatch.py` | yes |
| `disposition.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/disposition.py` | yes |
| `planning.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/planning.py` | yes |
| `agent.py` | runtime | untrusted | network | None | production-candidate | `decima/runtime/agent.py` | yes |
| `engagement.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/engagement.py` | yes |
| `session.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/session.py` | yes |
| `migrate.py` | runtime | trusted | None | None | production-candidate | `decima/runtime/migrate.py` | yes |
| `rotation.py` | runtime | trusted | crypto-signing (nacl) | signing keys via keystore | production-candidate | `decima/runtime/rotation.py` | yes |
| `router.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/router.py` | yes |
| `provider_router.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/provider_router.py` | yes |
| `context_fold.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/context_fold.py` | yes |
| `shorthand.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/shorthand.py` | yes |
| `resilience.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/resilience.py` | yes |
| `watch.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/watch.py` | yes |
| `merkle.py` | runtime | trusted | None | None | production-candidate | `decima/runtime/merkle.py` | defer |
| `sync.py` | runtime | untrusted | network (socket), crypto-signing, threading | signing keys via keystore | production-candidate | `decima/runtime/sync.py` | defer |
| `gossip.py` | runtime | untrusted | network (via sync/merkle) | None | production-candidate | `decima/runtime/gossip.py` | defer |
| `multihuman.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/multihuman.py` | defer |
| `patterns.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/patterns.py` | defer |
| `reckoner.py` | runtime | untrusted | None (drives forge/exec) | None | experiment | `decima/runtime/reckoner.py` | defer |
| `forge.py` | runtime | untrusted | None (generates code) | None | experiment | `decima/runtime/forge.py` | defer |
| `candidate.py` | runtime | restricted | None | None | experiment | `decima/runtime/candidate.py` | defer |
| `promotion.py` | runtime | restricted | None | None | production-candidate | `decima/runtime/promotion.py` | defer |
| `selfupdate.py` | runtime | untrusted | subprocess (via promotion) | None | experiment | `decima/runtime/selfupdate.py` | defer |
| `evalopt.py` | runtime | untrusted | None | None | experiment | `decima/runtime/evalopt.py` | defer |

---

## Projection — 22

Rebuildable read-models over the folded Weave: knowledge/notes/documents, activity,
dashboards, inspectors, dev-workspace views.

| Module | Category | Trust class | External effects | Secrets | Status | Destination | Keep 0.3 |
|---|---|---|---|---|---|---|---|
| `knowledge.py` | projection | restricted | None | None | production-candidate | `decima/projections/knowledge.py` | yes |
| `memory.py` | projection | restricted | None | None | production-candidate | `decima/projections/memory.py` | yes |
| `retrieval.py` | projection | restricted | None | None | production-candidate | `decima/projections/retrieval.py` | yes |
| `search.py` | projection | restricted | None | None | production-candidate | `decima/projections/search.py` | yes |
| `doc.py` | projection | restricted | None | None | production-candidate | `decima/projections/doc.py` | yes |
| `corpus.py` | projection | restricted | filesystem | None | production-candidate | `decima/projections/corpus.py` | yes |
| `journal.py` | projection | restricted | None | None | production-candidate | `decima/projections/journal.py` | yes |
| `context.py` | projection | restricted | None | None | production-candidate | `decima/projections/context.py` | yes |
| `workspace.py` | projection | restricted | None | None | production-candidate | `decima/projections/workspace.py` | yes |
| `orientation.py` | projection | restricted | None | None | production-candidate | `decima/projections/orientation.py` | yes |
| `inspector.py` | projection | restricted | None | None | production-candidate | `decima/projections/inspector.py` | yes |
| `discovery.py` | projection | restricted | None | None | production-candidate | `decima/projections/discovery.py` | yes |
| `dashboard.py` | projection | restricted | None | None | production-candidate | `decima/projections/dashboard.py` | yes |
| `timeline.py` | projection | restricted | None | None | production-candidate | `decima/projections/timeline.py` | yes |
| `tracing.py` | projection | restricted | None | None | production-candidate | `decima/projections/tracing.py` | yes |
| `observ.py` | projection | restricted | None | None | production-candidate | `decima/projections/observ.py` | yes |
| `audit.py` | projection | restricted | None | None | production-candidate | `decima/projections/audit.py` | yes |
| `metrics.py` | projection | restricted | None | None | production-candidate | `decima/projections/metrics.py` | yes |
| `review.py` | projection | restricted | None | None | production-candidate | `decima/projections/review.py` | yes |
| `access.py` | projection | restricted | None | None | production-candidate | `decima/projections/access.py` | defer |
| `datasci.py` | projection | restricted | None | None | production-candidate | `decima/projections/datasci.py` | defer |
| `timetrack.py` | projection | restricted | None | None | production-candidate | `decima/projections/timetrack.py` | defer |

---

## Service — 19

Inbound/outbound surfaces and the network/secret/data boundaries. `wire` and `egress`
are the trusted egress gates (network effect, but the enforcement itself); the live/relay
surfaces (`live_wire`, `mediated_browser`, `webhook`, `golive`, `citizens`, voice) are
deferred.

| Module | Category | Trust class | External effects | Secrets | Status | Destination | Keep 0.3 |
|---|---|---|---|---|---|---|---|
| `shell.py` | service | untrusted | network, socket | None | production-candidate | `decima/services/shell.py` | yes |
| `api.py` | service | untrusted | None (inbound RPC surface) | None | production-candidate | `decima/services/api/` | yes |
| `terminal.py` | service | untrusted | None (composes shell effect) | None | production-candidate | `decima/services/terminal.py` | yes |
| `mcp.py` | service | untrusted | network, subprocess, mcp | api keys via secret broker | production-candidate | `decima/services/mcp.py` | yes |
| `mcp_server.py` | service | untrusted | network (mcp, composed) | None | production-candidate | `decima/services/mcp_server.py` | yes |
| `wire.py` | service | trusted | network | None | production-candidate | `decima/services/wire.py` | yes |
| `egress.py` | service | trusted | network (urllib.parse) | None | production-candidate | `decima/services/egress.py` | yes |
| `secrets.py` | service | restricted | None | all credential handles (broker) | production-candidate | `decima/services/secrets.py` | yes |
| `files.py` | service | restricted | filesystem | None | production-candidate | `decima/services/files.py` | yes |
| `vault.py` | service | restricted | filesystem | None | production-candidate | `decima/services/vault.py` | yes |
| `backup.py` | service | restricted | filesystem | None | production-candidate | `decima/services/backup.py` | yes |
| `inference.py` | service | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/services/inference.py` | yes |
| `live_wire.py` | service | untrusted | network | None | production-candidate | `decima/services/live_wire.py` | defer |
| `mediated_browser.py` | service | untrusted | network (via gate) | None | production-candidate | `decima/services/mediated_browser.py` | defer |
| `webhook.py` | service | untrusted | network (via gate) | signing keys via keystore | production-candidate | `decima/services/webhook.py` | defer |
| `golive.py` | service | untrusted | network (via live_wire) | credentials via secret broker | production-candidate | `decima/services/golive.py` | defer |
| `citizens.py` | service | untrusted | mcp / network | scoped session tokens | production-candidate | `decima/services/citizens.py` | defer |
| `voice.py` | service | restricted | None (stub contract) | None | production-candidate | `decima/services/voice.py` | defer |
| `voice_shell.py` | service | untrusted | None (composes shell) | None | production-candidate | `decima/services/voice_shell.py` | defer |

---

## Capability — 85

The 25 bundled engines plus the composed domain packs. A handful of infra/generic
capabilities are kept (`manifest`, `manifest_pack` marketplace deferred,
`builtin_manifests`, `process_effect`, `projects`, `notify`, `research`, `spend`,
`embed_engine`). All the money/health/insurance/tax/KYC/comms/domain packs are on the
explicit deferral list. **Bundled engines** are destined for
`decima/capabilities/builtin/…`; composed domain modules for `decima/capabilities/…`.

| Module | Category | Trust class | External effects | Secrets | Status | Destination | Keep 0.3 |
|---|---|---|---|---|---|---|---|
| `builtin_manifests.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/builtin_manifests.py` | yes |
| `manifest.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/manifest.py` | yes |
| `process_effect.py` | capability | untrusted | subprocess | None | production-candidate | `decima/capabilities/process_effect.py` | yes |
| `projects.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/projects.py` | yes |
| `notify.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/notify.py` | yes |
| `research.py` | capability | untrusted | network (via egress) | None | production-candidate | `decima/capabilities/research.py` | yes |
| `spend.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/spend.py` | yes |
| `embed_engine.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/embed_engine.py` | yes |
| `manifest_pack.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/manifest_pack.py` | defer |
| `stripe_rail.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/stripe_rail.py` | defer |
| `payouts.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/payouts.py` | defer |
| `payroll.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/payroll.py` | defer |
| `brokerage_engine.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/brokerage_engine.py` | defer |
| `exchange.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/exchange.py` | defer |
| `shipping.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/shipping.py` | defer |
| `cloud_compute.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/cloud_compute.py` | defer |
| `cloud_storage.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/cloud_storage.py` | defer |
| `ecommerce.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/ecommerce.py` | defer |
| `ads.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/ads.py` | defer |
| `comms.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/comms.py` | defer |
| `paging.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/paging.py` | defer |
| `esign.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/esign.py` | defer |
| `insurance_claim.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/insurance_claim.py` | defer |
| `dns.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/dns.py` | defer |
| `oidc.py` | capability | untrusted | network | client secrets via secret broker | production-candidate | `decima/capabilities/builtin/oidc.py` | defer |
| `calendar_engine.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/calendar_engine.py` | defer |
| `tax_engine.py` | capability | restricted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/tax_engine.py` | defer |
| `kyc.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/kyc.py` | defer |
| `background_check.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/background_check.py` | defer |
| `accounting.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/builtin/accounting.py` | defer |
| `maps_engine.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/maps_engine.py` | defer |
| `weather_engine.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/builtin/weather_engine.py` | defer |
| `ocr_engine.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/ocr_engine.py` | defer |
| `translate_engine.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/builtin/translate_engine.py` | defer |
| `accounts.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/accounts.py` | defer |
| `banking.py` | capability | untrusted | network (via engine) | api keys via secret broker | production-candidate | `decima/capabilities/banking.py` | defer |
| `budget.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/budget.py` | defer |
| `expense.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/expense.py` | defer |
| `capital.py` | capability | untrusted | network (via rails) | api keys via secret broker | production-candidate | `decima/capabilities/capital.py` | defer |
| `payments.py` | capability | untrusted | network (via rails) | api keys via secret broker | production-candidate | `decima/capabilities/payments.py` | defer |
| `brokerage.py` | capability | untrusted | network (via engine) | api keys via secret broker | production-candidate | `decima/capabilities/brokerage.py` | defer |
| `trading.py` | capability | untrusted | network (via rail) | api keys via secret broker | production-candidate | `decima/capabilities/trading.py` | defer |
| `shop.py` | capability | untrusted | network (via rail) | api keys via secret broker | production-candidate | `decima/capabilities/shop.py` | defer |
| `subscriptions.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/subscriptions.py` | defer |
| `tax.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/tax.py` | defer |
| `insurance.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/insurance.py` | defer |
| `health.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/health.py` | defer |
| `fitness.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/fitness.py` | defer |
| `recipes.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/recipes.py` | defer |
| `goals.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/goals.py` | defer |
| `learn.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/learn.py` | defer |
| `contacts.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/contacts.py` | defer |
| `bookmarks.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/bookmarks.py` | defer |
| `feed.py` | capability | untrusted | network (via gate) | None | production-candidate | `decima/capabilities/feed.py` | defer |
| `maps.py` | capability | restricted | None (composes maps_engine) | None | production-candidate | `decima/capabilities/maps.py` | defer |
| `weather.py` | capability | restricted | None (composes weather_engine) | None | production-candidate | `decima/capabilities/weather.py` | defer |
| `travel.py` | capability | untrusted | network (via rail) | api keys via secret broker | production-candidate | `decima/capabilities/travel.py` | defer |
| `home.py` | capability | untrusted | network (via gate) | None | production-candidate | `decima/capabilities/home.py` | defer |
| `media.py` | capability | restricted | None (local playback) | None | production-candidate | `decima/capabilities/media.py` | defer |
| `photos.py` | capability | restricted | filesystem | None | production-candidate | `decima/capabilities/photos.py` | defer |
| `office.py` | capability | restricted | filesystem | None | production-candidate | `decima/capabilities/office.py` | defer |
| `ocr.py` | capability | restricted | None (composes ocr_engine) | None | production-candidate | `decima/capabilities/ocr.py` | defer |
| `translate.py` | capability | restricted | None (composes translate_engine) | None | production-candidate | `decima/capabilities/translate.py` | defer |
| `social.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/social.py` | defer |
| `messaging.py` | capability | untrusted | network (via gate) | None | production-candidate | `decima/capabilities/messaging.py` | defer |
| `mail_engine.py` | capability | untrusted | network (via gate) | mail creds via secret broker | production-candidate | `decima/capabilities/mail_engine.py` | defer |
| `maildigest.py` | capability | restricted | None (folds untrusted mail data) | None | production-candidate | `decima/capabilities/maildigest.py` | defer |
| `mailpoll.py` | capability | untrusted | network (via mail_engine) | mail creds via secret broker | production-candidate | `decima/capabilities/mailpoll.py` | defer |
| `sms.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/sms.py` | defer |
| `crm.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/crm.py` | defer |
| `crm_engine.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/crm_engine.py` | defer |
| `support.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/support.py` | defer |
| `ticketing.py` | capability | untrusted | network (via gate) | api keys via secret broker | production-candidate | `decima/capabilities/ticketing.py` | defer |
| `legal.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/legal.py` | defer |
| `ride.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/ride.py` | defer |
| `storage.py` | capability | untrusted | network | api keys via secret broker | production-candidate | `decima/capabilities/storage.py` | defer |
| `devops.py` | capability | untrusted | subprocess (via effect) | None | production-candidate | `decima/capabilities/devops.py` | defer |
| `incident_response.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/incident_response.py` | defer |
| `detection.py` | capability | untrusted | None (Nona-forged rules) | None | experiment | `decima/capabilities/detection.py` | defer |
| `purple.py` | capability | untrusted | None (via red/blue) | None | experiment | `decima/capabilities/purple.py` | defer |
| `red.py` | capability | untrusted | network (via gate) | None | experiment | `decima/capabilities/red.py` | defer |
| `recon.py` | capability | untrusted | network (via gate) | None | experiment | `decima/capabilities/recon.py` | defer |
| `vuln.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/vuln.py` | defer |
| `triage.py` | capability | restricted | None | None | production-candidate | `decima/capabilities/triage.py` | defer |
| `wager.py` | capability | restricted | None | None | experiment | `decima/capabilities/wager.py` | defer |

---

## Legacy — 1

| Module | Category | Trust class | External effects | Secrets | Status | Destination | Keep 0.3 |
|---|---|---|---|---|---|---|---|
| `liveworld.py` | legacy | restricted | None (fault-injection harness) | None | experiment | `legacy/heartbeat/liveworld.py` | no |

---

## Kernel / TCB candidates callout (Phase 2 extracts exactly these — 19)

`__init__.py`, `weft.py`, `weave.py`, `model.py`, `crypto.py`, `keystore.py`,
`hashing.py`, `capability.py`, `autonomy.py`, `roe.py`, `powerbox.py`, `inbox.py`,
`verifier.py`, `identity.py`, `snapshot.py`, `quarantine.py`, `parse.py`, `redact.py`,
`kernel.py`.

Notes for Phase 2:
- `weft.py` is the **only** `sqlite3` importer — the durable store boundary sits here.
- `crypto.py` / `keystore.py` are the **only** `nacl` importers in the TCB (`rotation`
  and `sync` also sign but live in runtime). Signing keys never leave `keystore`.
- `parse.py`, `quarantine.py`, `redact.py` are the fail-closed **data firewalls**: pure
  text/structure scans over untrusted content, no effects — trusted, but they are gates,
  not fold/verify, so confirm they belong in-process during extraction.
- The network egress **gates** (`wire.py`, `egress.py`) are classed `service`/`trusted`
  and deliberately kept *out* of the TCB category even though they enforce policy — they
  perform the actual `urllib` I/O. Decide in Phase 2 whether the policy core splits from
  the transport.
