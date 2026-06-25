# Decima — Build Backlog

The shared board for multi-instance work. One source of truth for **what's next,
who can take it, and how not to collide.**

> Decima is built in the **Python reference** until the design stops moving; the
> single Rust port is the **last** step (see [`VISION.md`](../VISION.md)).

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
**Tooling — ✅** `heartbeat/checks/NN_*.py` auto-run by `smoke.py`; new lanes add a file there, never edit `smoke.py`.

Oracle: **all 8 FOLD §11 invariants hold.** Merge + snapshots (incl. incremental fold) + sync
(sim/transport/scale) + redaction + memory governance + detection-as-code + capability inspector +
agent shorthand + sandbox + voice + wager/verdict + orientation + auto-router + disposition +
payments all real in the reference. The scope catalog of what's next (donor adoptions + ecosystem
capabilities + blue/red-team + ideas D1–D4) is [`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md).

## Coordination rules

1. **Core-kernel files serialize:** `weave.py`, `weft.py`, `kernel.py`, `executor.py`
   — **one owner per cycle.** Everyone else builds in new modules and *calls* the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, not `smoke.py`.** Own a free `NN`.
3. **`specs/` is collision-free** — one instance per file.
4. Keep the oracle green: `cd heartbeat && python3 smoke.py` → `alive. ✓`, exit 0.

## Cycle 10 — active

Make **sovereign access** real and bring the **cognitive layer alive**: a **secrets broker** (the
credential layer the powerbox explicitly defers — so Decima can hold a payment method and engine
keys without any agent touching the raw secret), a **self-hosted/private inference** engine
contract (sensitive data never leaves the box), and the **live governance gate** (Decima
auto-consults its own rules *before* it acts — the wiring the docstrings keep deferring). Only
**LOOP1** touches core.

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **CRED1** | **Secrets broker** (`CAPABILITY_MAP` D3.2) — `powerbox.py` issues scoped capability *grants* but (its own words) "never sees secrets … not modelled". CRED1 is that layer: an opaque credential (payment / engine / API key) is held in the broker; agents get only a **scoped, attenuable, revocable handle** (a capability) bound to principal + purpose; the broker **dispenses, never discloses** the raw secret (which stays in the broker's store, a stand-in for an HSM/enclave — never on the Weft in clear). Per-service privacy email aliases as metadata. | `secrets.py` (new) + `checks/110_secrets.py` | P1 | `checks/110`: store a credential → a scoped handle (raw secret never returned / not on the Weft); use via the handle (broker dispenses, audited); attenuate downhill; **revoke → handle fails closed**; a privacy alias recorded |
| **INF1** | **Self-hosted / private inference** (`CAPABILITY_MAP` D3.3) — a **LocalInferenceEngine** (on-host stub) whose capability carries an **SB1 sandbox profile with `network=False`**, and a **RemoteInferenceEngine** (network allowed), behind one Engine-compatible contract. Sensitive prompts route **local** and **prove no egress** (a network attempt by the local engine is sandbox-refused). Plugs into AR1's `Router` via its **public engines seam** — no `router.py` edit. "Rent a GPU / self-host open weights": the data never leaves. | `inference.py` (new) + `checks/112_inference.py` | P2 | `checks/112`: a sensitive prompt runs on the local engine and a network attempt by it is **sandbox-refused** (no egress); a non-sensitive prompt may use remote; engines plug into the router via its public seam |
| **LOOP1** | **Live governance gate** (core) — `memory.governance_check` (B4) exists but nothing consults it automatically ("Decima auto-consulting this before it delegates … is the kernel wiring — a later core cycle"). LOOP1 is that cycle: in `kernel._delegate`, **before spawning a worker** (next to the existing `org_policy` gate), consult governance; a delegation whose objective/capability is a recorded `banned_action` is **refused at delegate-time** with the rule + prior evidence cited. | `kernel.py` (core, single-owner) + `checks/114_live_governance.py` | P1 | `checks/114`: record a banned action → a delegation to do it is refused at delegate-time with the rule + prior evidence; an unbanned delegation proceeds; the refusal is on the Weft |

**Collision note:** only **LOOP1** touches core (`kernel.py`). **CRED1** = new `secrets.py` (public
`capability`/`weave`/`kernel` API; does **not** edit `powerbox.py`); **INF1** = new `inference.py`
(public `router`/`executor`/`kernel` API; does **not** edit `router.py` — extends via the engines
seam). Distinct `checks/` (110/112/114). Disjoint.

## Suggested allocation (Cycle 10)

- **Instance 1 — worktree**: **CRED1** — `secrets.py` (the secrets broker).
- **Instance 2 — worktree**: **INF1** — `inference.py` (private inference engine).
- **Instance 3 — kernel** (`~/decima-claude`): **LOOP1** — sole owner of `kernel.py`. The live gate.

## Backlog (future cycles)

- **Wire DISP1 into the live inbound loop** — Decima auto-disposes inbound observations/messages (the disposition router going live, like LOOP1 does for governance).
- **Real sandbox enforcement** (namespaces/seccomp/landlock + WASM-component runtime — needs deps); **real model engines** behind the auto-router; **real voice engines** behind VOX1.
- **Cascade / lease-tree retraction** (`REDACT` cascade); a proper `Weft.ingest()` with full WEFT §2 validation (pairs with GX1 networked sync).
- **The Constellation GUI** (Skyrim-style skill tree, post-port) over INS1's data model.
- **The Rust port** — last, once the reference is stable and complete.

## Pick-up-cold briefs (Cycle 10)

### CRED1 — Secrets broker `secrets.py` + `checks/110_secrets.py`
**Why:** `powerbox.py` (E1) issues scoped capability *grants*, but its docstring says "The broker
never sees secrets (it would issue broker handles; not modelled in the prototype)." CRED1 is that
missing layer — so Decima can hold a payment method (PAY1) and engine/API credentials while **no
agent ever touches the raw secret**.
**Deliverable:** `secrets.py` — a `SecretsBroker` that: (1) `store(name, secret, alias=None)` holds
an **opaque** credential — the raw value lives in the broker's in-memory store (a stand-in for an
HSM/enclave) and is **never written to the Weft in clear** (record a reference/digest + metadata);
(2) issues a **scoped, attenuable, revocable handle** (a capability) bound to a principal + purpose;
(3) `use(handle, …)` performs the credentialed action **on the holder's behalf without disclosing
the secret** (dispense-don't-disclose); (4) records per-service **privacy email aliases** as
metadata; (5) `revoke(handle)` → the handle **fails closed**. Everything (store/issue/use/revoke) is
audited on the Weft. Build on `capability` (`attenuate`/`authorize`); do **not** edit `powerbox.py`.
**Acceptance:** `checks/110`: store a credential → a scoped handle (the raw secret is never returned
and never appears on the Weft); using the handle works (the broker dispenses, audited); attenuation
narrows downhill; `revoke` → the handle fails closed; a privacy alias is recorded. Fail loud.
**Lane:** `secrets.py` + `checks/110`. Public `capability`/`weave`/`kernel` API; no core edit.

### INF1 — Self-hosted / private inference `inference.py` + `checks/112_inference.py`
**Why:** D3.3 — "rent a GPU / self-host open weights" so sensitive data (and authorized-but-refused
work) stays on infra you control. AR1 already routes private→local tier; INF1 builds the **engine
layer that proves the data never leaves**.
**Deliverable:** `inference.py` — a `LocalInferenceEngine` (on-host stub) whose capability carries an
**SB1 sandbox profile with `network=False`**, and a `RemoteInferenceEngine` (network allowed), behind
one `Engine`-compatible contract (see `router.py`'s `Engine`/`default_engines`). A
`private_infer(k, prompt, sensitive=True)` routes sensitive prompts to the **local** engine and
proves **no egress** — a network effect attempted by the local engine is **refused by the executor's
sandbox** (SB1). Non-sensitive prompts may use the remote engine. Plug both into AR1's `Router` via
its **public engines seam** (`Router(engines=…)` / `default_engines`).
**Acceptance:** `checks/112`: a sensitive prompt runs on the local engine and a network attempt by it
is **sandbox-refused** (no egress); a non-sensitive prompt may use remote; the engines plug into the
router via its public seam. Fail loud.
**Lane:** `inference.py` + `checks/112`. Public `router`/`executor`/`kernel` API; no core edit, **no
`router.py` edit** (extend via the engines seam).

### LOOP1 — Live governance gate `kernel.py` + `checks/114_live_governance.py`
**Why:** `memory.governance_check` (B4) exists, but nothing consults it automatically — its own
docstring: "Decima auto-consulting this *before it delegates* … is the kernel wiring — a later
**core** cycle." LOOP1 is that cycle: the "memory prevents repeated bad actions" promise, going live.
**Deliverable:** in `kernel._delegate`, **before spawning a worker** (right next to the existing
`org_policy` gate), call `memory.governance_check` on the delegation's objective (and/or capability).
If the verdict is **deny**, refuse the delegation at delegate-time — record a `refused` task carrying
the governance verdict's reason + prior evidence (mirroring the `org_policy` refusal path) and skip.
A non-banned delegation proceeds unchanged; with empty governance the gate is inert (allow).
**Acceptance:** `checks/114`: record a `banned_action` (`memory.remember_governance`); a delegation
whose objective/capability matches is **refused at delegate-time** with the rule + prior evidence
cited; an unbanned delegation proceeds; the refusal is on the Weft. Fail loud.
**Lane:** `kernel.py` (core, single-owner) + `checks/114`. Uses `memory.governance_check` (public);
only the relevant wording in `smoke.py` may change if required.

| ID | Task | Lane | Pri | Done when |
|---|---|---|---|---|
| **DISP1** | **Disposition routing** (`CAPABILITY_MAP` D4.2) — GTD for the machine age: an **Intake Event** (an observation / inbound message, **untrusted data**) resolves to a first-class **disposition** — archive (deterministic noise filter), write to memory, open a task, propose an INVOKE, or update policy. Deterministic filtering split from model/orientation analysis; the disposition is **Decima's** decision, never the intake's instruction. | `disposition.py` (new) + `checks/104_disposition.py` | P1 | `checks/104`: noise → archived (no action); a fact → memory write; an actionable intake → a task/INVOKE proposal; an injection-laced intake stays DATA and Decima (not the payload) picks the disposition; an `disposed_as` edge records each |
| **PAY1** | **Morta-gated payments rail** (`CAPABILITY_MAP` D3.4) — financial transactions (trading, ads, paying for compute) as the canonical **irreversible** effect: `FINANCIAL` effect_class, **hard spend caps** (budget caveat), **requires_approval** (Morta), an **idempotency key** so replay never double-spends, full `EffectReceipt` audit, and binding to a **WV1 wager → verdict** (bet → approve → act → verify). | `payments.py` (new) + `checks/106_payments.py` | P1 | `checks/106`: an in-cap payment runs only after approval; an over-cap payment is refused; a duplicate (same idempotency) does not double-spend; the payment binds a wager and records a verdict; all on the Weft |
| **IFB1** | **Incremental fold-from-base** (core perf) — `snapshot.py` restores a verified base at a frontier; add a `Weave` path that folds **from that base + only the events after it** to current state, producing the **same `state_root`** as a genesis fold (a verifiable cache; FOLD §11.1). Skips replaying genesis for a long-running log. | `weave.py` (core, single-owner) + `checks/108_incremental_fold.py` | P1 | `checks/108`: snapshot at frontier F; incremental fold (base@F + events>F) `state_root` == full genesis fold; a tampered base is rejected; duplicate events stay idempotent |

**Collision note:** only **IFB1** touches core (`weave.py`; may read `snapshot.py`'s public API).
**DISP1** = new `disposition.py` (reads `memory`/`model`/`orientation` public API); **PAY1** = new
`payments.py` (public `kernel`/`capability`/`executor.register`/`wager` API; uses SB1 sandbox + WV1
+ Morta but edits none of them). Distinct `checks/` (104/106/108). Disjoint.

## Suggested allocation (Cycle 9)

- **Instance 1 — worktree**: **DISP1** — `disposition.py` (the intake→action router).
- **Instance 2 — worktree**: **PAY1** — `payments.py` (the payments rail).
- **Instance 3 — kernel** (`~/decima-claude`): **IFB1** — sole owner of `weave.py`. The perf win.

## Backlog (future cycles)

- **Credential / billing powerbox + secrets broker** (D3.2) — engine + payment credentials as scoped, attenuable, revocable capabilities, never ambient; per-service privacy aliases. Pairs with PAY1.
- **Self-hosted / private inference** (D3.3) — the inference-engine contract behind the auto-router (privacy routing already routes local).
- **Real sandbox enforcement** (namespaces/seccomp/landlock + WASM-component runtime — needs deps); **real model engines** behind the auto-router; **real voice engines** behind VOX1.
- **Cascade / lease-tree retraction** (`REDACT` cascade); a proper `Weft.ingest()` with full WEFT §2 validation (pairs with GX1 networked sync).
- **The Constellation GUI** (Skyrim-style skill tree, post-port) over INS1's data model.
- **The Rust port** — last, once the reference is stable and complete.

## Pick-up-cold briefs (Cycle 9)

### DISP1 — Disposition routing `disposition.py` + `checks/104_disposition.py`
**Why:** D4.2 — every intake should resolve to a first-class **disposition** (the GTD-for-machines
ingestion→action path). The browser→memory ingestion is one slice; generalize it so any inbound
event is captured and routed, with deterministic filtering separated from model/orientation analysis.
**Deliverable:** `disposition.py` — `dispose(k, intake)` takes an **Intake Event** (an observation
or inbound message; **untrusted data**, `instruction_eligible=False`) and resolves it to one of:
**archive** (a deterministic noise/spam filter — no action), **remember** (a memory write), **task**
(open a task cell), **invoke** (propose an effect, still subject to authorize/Morta), or **policy**
(a governance update). Record the choice as a `disposition` Cell + an `disposed_as` edge from the
intake. The disposition is **Decima's** decision — an injection-laced intake must still be DATA, and
the payload must never pick its own disposition. May consult OR1 `orientation` to choose.
**Acceptance:** `checks/104`: noise → archived (no action); a fact → memory write; an actionable
intake → a task/INVOKE proposal; an injection-laced intake stays DATA and Decima picks the
disposition; each intake carries a `disposed_as` edge. Fail loud.
**Lane:** `disposition.py` + `checks/104`. Public `memory`/`model`/`weave`/`orientation` API; no core edit.

### PAY1 — Morta-gated payments rail `payments.py` + `checks/106_payments.py`
**Why:** D3.4 — financial transactions (trading, ads, paying for compute/models) are the canonical
**irreversible** Morta-gated effect, and the thing WV1's wagers verify. This makes the D3 "give
Decima a payment method" vision real and safe.
**Deliverable:** `payments.py` — a payment effect (register via the public `executor.register`)
realized through a capability with `effect_class="FINANCIAL"`, a **hard spend cap** (budget caveat),
`requires_approval` (Morta), and a sandbox profile (network to the rail only). A `pay(k, ...)` flow:
(1) optionally record a **WV1 wager** (the predicted outcome of the spend); (2) is **Morta-gated**
(denied until `approve`); (3) carries an **idempotency key** so a replayed/duplicate request never
double-spends; (4) emits an `EffectReceipt` on the Weft (full audit); (5) a later **verdict**
measures the outcome. An over-cap spend is refused.
**Acceptance:** `checks/106`: an in-cap payment runs only after approval; an over-cap payment is
refused; a duplicate (same idempotency key) does not double-spend; the payment binds a wager and
records a verdict; everything is on the Weft. Fail loud.
**Lane:** `payments.py` + `checks/106`. Public `kernel`/`capability`/`executor`/`wager` API; uses
SB1 sandbox + WV1 + Morta but **edits none of them**; no core edit.

### IFB1 — Incremental fold-from-base `weave.py` + `checks/108_incremental_fold.py`
**Why:** `snapshot.py` explicitly defers this to a core cycle. A long-running OS log grows unbounded;
folding from genesis every time is O(all events). Incremental fold-from-base makes state
materialization O(events-since-snapshot) while staying provably equal to a full fold.
**Deliverable:** in `weave.py`, a fold path that starts from a **restored, verified snapshot base**
(use `snapshot.restore`) at frontier F and applies **only events after F**, producing a `Weave`
whose `state_root()` equals the genesis fold to the current head (a verifiable cache; FOLD §11.1).
Keep the full-fold default; verify the base's `state_root` before trusting it (a tampered base is
rejected). Respect arrival-order independence and idempotency (re-applying an event changes nothing).
**Acceptance:** `checks/108`: snapshot at frontier F; incremental fold (base@F + events>F)
`state_root` == full genesis fold to head; a tampered base is rejected; duplicate events stay
idempotent. Fail loud.
**Lane:** `weave.py` (core, single-owner) + `checks/108`. May read `snapshot.py` public API; only
the relevant wording in `smoke.py` may change if required.
