# Decima

**An agent-native operating system — one program you log into that connects you to your agents, your knowledge, and your work, and becomes more capable the longer it runs.** You talk to it; it organizes the agents, tools, context, models, and memory to get things done — and it holds the authority so that a rogue or prompt-injected agent still can't hurt you.

*Decima does not have features. It **grows** them.*

> **The poetic framing (it's load-bearing):** the whole OS is **four verbs over one append-only log**. State is a *fold*. Authority is a *held object*. The system is made of the same stuff as your data, so it can rewrite itself with the same tools it uses to edit your notes. Kernel: **LOOM** — spun by **Nona**, allotted by **Decima**, cut by **Morta**.

<!-- ^ Keep the first paragraph in sync with the GitHub repository "About" description. -->

**New here?** Read this file top-to-bottom (~2 min). Then go deep:
[`VISION.md`](VISION.md) is the canonical source of truth (the *what & why*) · [`KERNEL.md`](KERNEL.md) is the *how* (laws, primitives, worked traces) · [`heartbeat/`](heartbeat/) is the running proof.

---

## Decima 0.3 — Local Daily Driver (in progress)

The [0.3 milestone](docs/DECIMA-0.3-HANDOFF.md) turns the reference into a locally-hosted
daily-driver app. It is built as a clean `decima/` package **extracted from and proven
equivalent to** `heartbeat/` (the frozen reference oracle, never modified):

```
decima/  kernel · runtime · workers · models · projections · services · capabilities · shell · cli
```

**Status:** all 13 phases have landed — trusted-core extraction (byte-equal via
golden fixtures), the durable runtime (crash-recoverable, idempotent), isolated workers,
model routing, disposable projections, the local API + trusted Shell, the three
daily-driver workflows delivered **in the Shell** (grounded Q&A, model-planned durable
agents, isolated coding workspace), and ops (backup/restore/doctor). **510 tests green
(510 passed / 25 skipped)** plus **13 Playwright specs across 9 files** driving the real
rendered Shell; the kernel import boundary and the crash-recovery / revocation /
backup-restore / approval-gating end-to-end scenarios pass headless. See
[`docs/architecture/system-overview.md`](docs/architecture/system-overview.md), the
release-readiness matrix in [`docs/RELEASE-READINESS.md`](docs/RELEASE-READINESS.md), and
the release notes in [`docs/releases/0.3.0.md`](docs/releases/0.3.0.md).

```bash
make install-dev          # editable install + dev tooling
make test                 # ruff + mypy + pytest
python3 -m decima.shell.serve <weft.db>   # run the local Shell on 127.0.0.1:8973
```

> **Experimental.** Decima 0.3 is a reference / prototype "Local Daily Driver," not a
> finished product. Run it only on data you can afford to lose and always keep a backup
> (`decima-backup`). The default model provider is deterministic (offline); a live provider
> is opt-in via `DECIMA_LIVE_*` and runs against a **local** OpenAI-compatible endpoint
> (llama.cpp on `127.0.0.1:8080`) — no cloud credential is used or needed. The default
> recommended local model is **Qwen3.6-35B-A3B** (forward guidance; routing stays
> model-agnostic — the id comes from `DECIMA_LIVE_MODEL`, never hardcoded).

### 0.3 Shell scope & limitations (read before you rely on it)

The 0.3 Shell is a **single-user, loopback-only local daemon** (`127.0.0.1`). What the
**rendered Shell actually delivers today** — every item below is driven end-to-end through
visible controls and qualified by a Playwright spec against the real backend (**verified**):

- **Grounded Q&A** — import documents, choose a retrieval scope, ask a question, and get a
  generated answer with **citations that open the real source passage** they cite; generated
  text is visually distinct from imported source data, and a hostile import stays inert DATA
  (`qa.spec.js`, `js/screens/qa.js`, `decima/services/api/qa_service.py`).
- **Model-planned durable agents** — type an objective, a routed **model proposes** a plan,
  deterministic code validates it, and only an explicit **accept** mints durable Plan / Step /
  Agent Cells that the scheduler executes; **pause / resume / cancel / gated terminate** are
  server-enforced; proposed vs. authorized vs. executed are visually distinct (`planning.spec.js`,
  `js/screens/plans.js`, `decima/services/api/plan_service.py`).
- **Isolated coding workspace** — grant a repository root, request a bounded change, and it runs
  ONLY inside a **jailed, networkless `decima.workers` child** (no push, no credential, no
  network); durable diff + test artifacts render as untrusted text, the source repo outside is
  untouched, and a restart recovers the run honestly (`workspace.spec.js`,
  `js/screens/workspace.js`, `decima/services/api/workspace_service.py`).
- **Notes / tasks / projects**, the **agent inspector**, the trusted **approval inbox** (gated
  terminate / revoke defer to a reauth-gated approve), **capabilities**, **activity**, and durable
  per-item **provenance** (the Weft event ids that asserted each item).

Genuinely still deferred in 0.3 (**deferred**, honestly scoped):

- **Single-user loopback daemon** — no multi-user, no remote exposure, no authentication beyond
  the local pairing secret.
- **Deterministic provider is the default**; a real live provider is **opt-in** via `DECIMA_LIVE_*`
  (see below) — no cloud credential is used or needed.
- **Single-threaded server** — all Weft access stays on the one `sqlite3` thread; the kernel
  follow-up (`weft.py` `check_same_thread=False` + lock) is filed for **0.3.1**.
- **Service / reboot lifecycle is proven in a systemd-enabled container** (the qualification
  host's own systemd is degraded), not on a bare host.
- **Retrieval is local lexical scoring**, not embeddings / a vector index.
- **Intentionally out of 0.3** (handoff §3.2): financial automation, live brokerage, full browser
  automation, mobile, replication / multi-device sync, and the eventual single Rust port.

Every security invariant the workflows rely on (worker isolation, approval gating,
horizon-scoping, strict CSP, unauth-401) is independently proven at the layer that enforces it.
Full accounting: [`docs/RELEASE-READINESS.md`](docs/RELEASE-READINESS.md) and
[`docs/releases/0.3.0.md`](docs/releases/0.3.0.md).

---

## Architecture at a glance

One append-only log is the only truth. Everything else — your notes, your memory, the UI, the agents themselves — is *derived from it by folding*, and *written back to it* through four verbs, gated by capabilities.

```text
   You  ⟷  SHELL — the one program you log into
            │   (you talk to it; it organizes the rest)
            ▼
   AGENTS — brain · envelope · budget
            │   write via the four verbs:  ASSERT · RETRACT · INVOKE · ATTEST
            │   CAPABILITIES gate every INVOKE — no ambient authority
            ▼
   WEFT — one append-only, signed, content-addressed log   ◀── the ONLY truth
            │   fold
            ▼
   WEAVE — one graph of Cells
            notes · tasks · memory · agents · capabilities · types · views
            │   project
            ▼
   LENSES — notes · board · knowledge-graph · timeline · the UI
            │
            ▼
   ...back to You   (everything you see is derived from the Weft, never canonical)
```

*Read path (down): Weft → fold → Weave → project → Lenses → Shell. Write path (up): Shell → Agents → four verbs → Weft. Capabilities gate every effect.*

---

## The Five Laws

Not guidelines — enforced by the *shape* of the system. Break one and it isn't Decima anymore.

1. **Nothing happens off the Log.** Every change is a signed, content-addressed event appended to one log, the **Weft**. If it isn't in the Weft, it didn't happen.
2. **No ambient authority.** A principal can do *exactly* what the capabilities it holds permit — no admin, no root, no sudo. Power is a possessable object, not a status. (The object-capability model, taken religiously.)
3. **Everything is a Cell — including Decima itself.** Note, task, memory, agent, capability, policy, view, *type* — all Cells in one graph (the **Weave**). The system is homoiconic, which is what makes it self-extending.
4. **Identity is content plus cause.** Objects are content-addressed (id = hash of bytes); their meaning is the causal chain that produced them.
5. **State is a fold; everything you see is a projection.** The Weave, the search index, your memory, the UI — all derived from the Weft, all rebuildable, none canonical. The log is the only truth.

## The four verbs — the entire instruction set

The whole OS — voice, code, image generation, posting, memory, security, the UI — is expressed in four verbs. There is no special `grant`/`revoke`: capabilities are Cells, so granting is `ASSERT`-ing an edge and revoking is `RETRACT`-ing one. **The security model and the note-taking model are the same model.**

| verb | meaning | axis |
|---|---|---|
| **ASSERT** | bring a fact / Cell version into being | belief |
| **RETRACT** | withdraw a prior assertion (a tombstone — nothing is ever truly deleted) | belief |
| **INVOKE** | request an effect in the world, through a capability | action |
| **ATTEST** | witness / sign another event (verification, trust, promotion) | trust |

## The trinity — Nona · Decima · Morta

Named for the Roman Fates (Parcae). The mythology is load-bearing: every name tells you what the code does.

| Fate | Role | What it does |
|---|---|---|
| **Nona** (Clotho, the spinner) | self-extension engine | forges, tests, and promotes new capabilities — the organ that makes organs |
| **Decima** (Lachesis, the allotter) | orchestrator / router | apportions capability, budget, model, memory, and tasks to the work (also the project's name) |
| **Morta** (Atropos, the cutter) | revocation & the gates | termination, revocation, and the *unstrippable* human-approval gates on irreversible effects |

```text
   NONA  ───────────▶  DECIMA  ───────────▶  MORTA
   the spinner          the allotter           the cutter
   forge · test ·       route capability,      revoke · terminate ·
   promote new          budget, model,         gate the irreversible
   capabilities         memory, task
```

---

## Two flows that show how it works

**The action path** — every effect travels the same gated road, and lands as an auditable receipt on the log:

```text
   you say a goal
       ▼
   DECIMA decides — the brain PROPOSES an action
       ▼
   authorize + MORTA gate ──▶ denied / needs a human ──▶ fails closed (or waits for approval)
       │ allowed
       ▼
   INVOKE through a capability
       ▼
   executor runs the effect (as a sandboxed principal)
       ▼
   receipt ASSERTed on the Weft  ──fold──▶  new state  ──▶  Shell
```

A prompt-injected model has no more power than the offline rule stub: it can only *propose*, and `authorize` gates every effect. Attenuation means authority only flows *downhill*, so there is no escalation path to inject toward.

**The plug-in flow** — Decima grows capability instead of shipping a fixed feature set. Given a goal it can't yet serve, it looks for a fit; if none exists, Nona forges one — and either way the result is a *gated* capability:

```text
   a goal the system can't yet serve
       ▼
   discovery searches the capability registry
       ├─ found: a local manifest  ──▶  plug it in (declarative manifest)
       ├─ found: an external tool   ──▶  mount an MCP server's tools
       └─ missing                   ──▶  NONA forges one
                                          (quarantine → test + scan → promote)
                       ▼
   every path lands as a GATED capability   (authorize + Morta still apply)
```

---

## What's real today vs. the vision

This repo is a **reference / prototype**, not a finished product. It is honest about the gap.

### Real, running now — the Heartbeat

A pure-Python-**stdlib** reference (`heartbeat/`) — **one dependency** (PyNaCl, for real Ed25519 signing; everything else is stdlib) — that is both an *executable spec* and a *conformance oracle*. It proves the Five Laws by running. What breathes today:

- **The kernel** — a signed, append-only Weft; the four verbs; a fold with time-travel; object-capability authority with signed possession, attenuation, leases, and anti-replay; retraction + cascade; effect receipts; and networked sync / ingest.
- **A cognitive layer** — typed memory (and memory-as-governance), orientation, planning → execution, dispatch, and an autonomy ladder wired into the live loop.
- **A blue/red security flagship** — detection-as-code → triage/SIEM, red-team, and a purple-team loop.
- **~25 real external engines** — payments (Stripe), OIDC, tax, KYC, brokerage, comms, shipping, and more — each wrapped over stdlib `urllib` (zero pip deps). **These are test-mode / sandbox / Morta-gated**: money movement, posting, deploys, and other irreversible effects require human approval, and most external actions are gated stubs by design.
- **A modularity / plug-in layer** — declarative capability manifests + a registry; an **MCP client** (mount any MCP server's tools as gated capabilities) and an **MCP server** (expose Decima's own); tool **discovery** (find a fit → plug in → forge if missing); Rules of Engagement; and deterministic context folding.

The Heartbeat is deliberately a **profile** — smaller than the durable protocol. [`heartbeat/PROFILE.md`](heartbeat/PROFILE.md) pins exactly what's built vs. deferred (e.g. the Weft is signed with real Ed25519 via libsodium; JSON still stands in for CBOR).

### The vision (see [`VISION.md`](VISION.md))

A user-owned, agent-native OS you can point at almost any digital ambition: voice-first, multimodal, a studio, an inbox you never open directly, a workspace, social/business ops, full SDLC automation — all accreting as Cells on a living spine. The eventual **single Rust port is the last step**, gated on the reference being stable — it is *distant*, not near.

### Quickstart

```bash
cd heartbeat
python3 smoke.py          # scripted tour: the Five Laws + the FOLD §11 invariants
python3 run.py --fresh    # the interactive Shell (the "one program"), from genesis
```

Optional: export `ANTHROPIC_API_KEY` to let `claude-opus-4-8` decide each turn instead of the offline rule brain. Either way `authorize()` gates every effect, so the model never exceeds its envelope.

---

## Layout

| path | what |
|---|---|
| [`VISION.md`](VISION.md) | the vision — what Decima is, what it's for, how far it reaches (**start here**) |
| [`KERNEL.md`](KERNEL.md) | the kernel design — the laws, primitives, and worked traces |
| [`specs/`](specs/) | formal protocol specs — Weft, fold lifecycle, Nona, Morta/capabilities, memory, browser, donor matrix |
| [`heartbeat/`](heartbeat/) | the **running** pure-stdlib prototype (see [`heartbeat/README.md`](heartbeat/README.md)) |
| [`heartbeat/PROFILE.md`](heartbeat/PROFILE.md) | the prototype's profile vs. the durable protocol — **what's built vs. deferred** |
| [`docs/BACKLOG.md`](docs/BACKLOG.md) | the build board — status, cycles, coordination rules |

---

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*
