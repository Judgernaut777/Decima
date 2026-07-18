# Library-Tier Scope Ruling (Batch U)

## 1. The ruling

Decima ships roughly 84 domain and security **engine packs** under `heartbeat/decima/`
(`stripe_rail.py`, `tax_engine.py`, `kyc.py`, `brokerage_engine.py`, `esign.py`,
`comms.py`, `payouts.py`, `accounting.py`, `shipping.py`, `calendar_engine.py`,
`cloud_storage.py`, `exchange.py`, `maps_engine.py`, `weather_engine.py`, `payroll.py`,
`insurance_claim.py`, `cloud_compute.py`, `ecommerce.py`, `paging.py`, `ocr_engine.py`,
`translate_engine.py`, `background_check.py`, `dns.py`, `ads.py`, `banking.py`, `ride.py`,
`crm_engine.py`, `ticketing.py`, `sms.py`, `storage.py`, the blue-team packs
(`detection.py`, `vuln.py`, `triage.py`, `incident_response.py`, `quarantine.py`,
`purple.py`, …) and the red-team packs (`red.py`, `recon.py`, …), plus every other
non-kernel domain module in the tree). This document rules, explicitly and for the
record:

> **Every one of these ~84 packs is LIBRARY-TIER: importable, callable only through
> the gate, and it confers no authority merely by existing.** A pack becomes a live
> capability only via an explicit, Morta-gated operator action — never at import.

This is not a new mechanism. It is a naming of a property the kernel already
enforces (`kernel.py` `_boot`, `capability.authorize`, `manifest.py`, `discovery.py`,
`builtin_manifests.py`) and a proof, in `heartbeat/checks/508_library_tier.py`, that
the property actually holds in the running system — not just in the packs' own
docstrings.

## 2. Definition: what "library-tier" means

A pack is **library-tier** when, as plain importable Python:

- importing its module executes no Weft write — no `ASSERT`, no `INVOKE`, no
  capability, no grant. `decima.weft.count()` is unmoved by `import decima.foo`;
- it holds no capability and confers no grant on its own — nothing in the module
  puts an entry into any agent's `envelope`;
- it opens no egress and touches no network, disk outside the sandbox, or external
  provider — its "real engine" HTTP calls (§ dependency policy, `docs/BACKLOG.md`
  Cycles 30-46) live behind a `transport` parameter that is never invoked unless a
  caller supplies real arguments through `kernel.invoke`;
- it runs no effect — the module defines functions and (for a few packs) registers a
  handler in the process-wide `executor` dispatch table, but defining/registering a
  handler is not *running* it: the handler executes only when `kernel.invoke` reaches
  it, which requires a capability that does not exist yet (§4).

In short: **a library-tier pack is inert code until wired.** It is a set of pure
functions the kernel *could* stand behind a capability — a plug, not a live wire.

### The one subtlety: `executor.register` is process state, not Weft state

A handful of packs (`red.py`, `recon.py`, `translate.py`, `mediated_browser.py`,
`selfupdate.py`, `quarantine.py`) call `executor.register(effect_name, handler)` at
module level, so importing them *does* mutate the process-wide `executor._REGISTRY`
dict — a real, if narrow, import-time side effect. This is deliberately not treated
as a violation of library-tier status, and the check proves why: `executor.register`
only makes a handler *reachable if a capability names it*. It writes nothing to the
Weft, mints no capability, and grants nothing to any agent. `kernel.invoke` still
resolves authority from the Weft first (`capability.authorize`: envelope → grantee →
delegation → caveats) and refuses a bare effect name with **"no such capability"**
before the executor is ever consulted. A populated dispatch-table entry with no
Weft-backed, granted capability behind it is unreachable — registering a handler is
not authorizing one. The check (§4 below) exercises exactly this case rather than
looking away from it.

## 3. The promotion path: library → capability

A pack graduates from library-tier to a live capability only through one of these
explicit, gated seams — never by being imported, never by being on disk, never by
being merely discoverable:

1. **`kernel.integrate_tool(name, handler, caveats=None)`** — the one-call install
   seam every "make a stub real" engine (Cycles 30-46) and every ordinary CLI/agent
   tool goes through. It does two things atomically: `executor.register` (the same
   dispatch-table entry § 2 discusses) **and** `kernel._assert_cap` + `kernel.grant`
   — the latter is what actually puts the capability in `decima_agent_id`'s
   envelope on the Weft. Only after this call does `kernel.invoke` have anything to
   authorize.
2. **`golive.activate_engine(k, name, host, ...)`** — the live-registry path: proves
   an engine entry (host reachability, transport shape), then installs it through
   the same `integrate_tool`-backed spine, with `_prune_dead_engines`/`doctor`
   auditing the live set afterward.
3. **A Morta-gated inbox approval (`discovery.submit_activation` /
   `ApprovalInbox.approve`)** — the catalog "use" path (check
   `heartbeat/checks/495_catalogactivation.py`): `discover()` finds a bundled
   manifest for a goal and enqueues a **pending** `inbox_item`; nothing is installed
   until a human `approve()`s it, at which point the enactor calls
   `kernel.integrate_tool` on the human's behalf. Deny, or an approve with no bound
   handler, installs nothing (fail-closed, never a stub).

All three seams terminate in the same place: a Weft `ASSERT` that mints a
`capability` Cell, granted into the orchestrator agent's envelope. **Importing a
pack is not on this list, and is not equivalent to any step on it.** A manifest
(`builtin_manifests.register_builtins`) is even weaker than an import: it only
describes an engine for `discovery` to rank — "a manifest GRANTS NOTHING"
(`manifest.py`) — so registering all ~25 bundled manifests still installs zero
capabilities.

## 4. The scope boundary: library-tier vs. always-on boot

**All ~84 domain/security engine packs are library-tier, without exception.** None
of them is wired into `Kernel._boot`. A fresh boot (`Kernel(db_path, fresh=True)`
with an empty Weft) mints exactly five capabilities and stops:

| Capability | Effect | Notes |
|---|---|---|
| `echo` | `echo` | reference no-op |
| `shell` | `shell` | budget-capped (100) |
| `forge` | `forge` | Nona's forge seam |
| `browser.observe` | `browser` | read-only, auto-allowed |
| `browser.publish` | `browser` | outward, Morta-gated (`requires_approval`) |

That is the entire always-on **kernel/cognitive substrate** — the fixed set that
exists before any operator has done anything: the four-verb Weft/Weave/Cell
machinery (`weft.py`, `weave.py`, `model.py`, `hashing.py`, `merkle.py`), the ocap
gate (`capability.py`, `manifest.py`), Morta/approvals (`inbox.py`), the brain/router
seam (`reckoner.py`, `router.py`, `provider_router.py`), the executor boundary
(`executor.py`, `isolation.py`), discovery/forge (`discovery.py`, `forge.py`), and
the always-present `echo`/`shell`/`forge`/`browser.*` reference capabilities above.
Everything else — every named engine pack in the opening paragraph, every "make a
stub real" wrap (Stripe, KYC, tax, brokerage, esign, comms, payouts, accounting,
shipping, calendar, storage, exchange, maps, weather, payroll, insurance, compute,
ecommerce, paging, OCR, translate, background-check, DNS, ads, banking, ride, CRM,
ticketing, SMS, S3-storage, …) and every blue/red security pack (detection, vuln,
triage, incident-response, quarantine, purple, red, recon, …) — is library-tier:
importable, inert, and absent from that boot set. The pack **categories**, for
reference:

- **Financial rails** (Morta-gated, `effect_class=FINANCIAL`): `stripe_rail`,
  `payouts`, `brokerage_engine`, `exchange`, `payroll`, `shipping`,
  `cloud_compute`, `ecommerce`, `ads`, `banking`, `ride`.
- **Communication rails** (Morta-gated, `COMMUNICATION`): `comms`, `paging`, `sms`.
- **Legal rails** (Morta-gated, `LEGAL`): `esign`, `insurance_claim`.
- **Infra rail** (Morta-gated, `INFRA`): `dns`.
- **Identity / scheduling effects**: `oidc`, `calendar_engine`.
- **Compute read/record engines** (`READ`/domain-specific, auto-allowed once
  installed): `tax_engine`, `kyc`, `background_check`, `accounting`,
  `maps_engine`, `weather_engine`, `cloud_storage`, `ocr_engine`,
  `translate_engine`, `crm_engine`, `ticketing`, `storage`.
- **Blue-team packs**: `detection`, `vuln`, `triage`, `incident_response`,
  `quarantine`, `purple`, plus the SIEM/DFIR-shaped modules they compose with.
- **Red-team packs**: `red`, `recon`, plus the kill-chain modules they compose
  with — every offensive action is scoped to an `engagement` caveat and Morta-gated
  by construction (`red.engagement_caveats`).

## 5. Non-goals

This ruling does **not**:

- delete, rename, or move any pack;
- change a single line of any pack's code;
- auto-install, auto-flip, or auto-activate anything — no cron, no startup hook, no
  "helpful default" that installs a capability without an explicit operator action;
- weaken or bypass Morta on any outward/irreversible effect class — a promoted
  pack is exactly as gated after promotion as `builtin_manifests.py` and the
  per-pack docstrings already declare (`requires_approval` for FINANCIAL /
  COMMUNICATION / LEGAL / INFRA effect classes);
- introduce a new promotion path — `integrate_tool` / `activate_engine` / the
  catalog-approval enactor already exist; this document names them as *the only*
  legitimate ones and rules out import as an alternative.

## 6. The citable invariant

> No engine pack confers authority at import; a fresh boot's installed capability
> set contains none of the library-tier packs.

Proven structurally, empirically, and deterministically in
`heartbeat/checks/508_library_tier.py`: a fresh `Kernel` boots with exactly the
five-capability kernel/cognitive set (§4); importing a representative sample of
library-tier packs — financial, legal, identity/read, and blue/red security alike —
leaves `weft.count()` and the installed-capability-name set byte-identical before
and after; and even the one pack category that *does* touch process state at import
(`executor.register`, §2) is proven unreachable through `kernel.invoke` without a
Weft-backed, granted capability. If any pack ever self-installs a capability at
import, the before/after capability set diverges and the check goes red.
