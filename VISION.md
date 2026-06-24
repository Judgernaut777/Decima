# Decima — The Vision

*An agent-native operating system. A true AI OS.*
*Kernel: LOOM. Trinity: Nona · Decima · Morta.*

> This is the **canonical source of truth** for what Decima is, what it is for, and how far it
> intends to reach. For every agent working on Decima, this document defines the **scope and the
> intent**. It carries the *what, why, scope, trust model, and build philosophy*. For the *how* —
> the laws, primitives, and worked traces — see [`KERNEL.md`](KERNEL.md). For the formal contracts,
> see [`specs/`](specs/). For the running proof, see [`heartbeat/`](heartbeat/).

---

## The essence

Decima is a **true AI OS** — not "an OS for your AI," but an operating system whose native
inhabitants are agents and whose native user is you, speaking. It is **one program you log into**
that connects you to your agents, your knowledge, your work, your creative tools, your development
environment, your communications, and your business operations — standing in for most of the apps
you use today and **becoming more capable the longer it runs.** Its defining sentence: **Decima
does not have features — it grows them.**

It is **deliberately enormous**, and that is the design, not an accident of ambition. The mantra is
**maximum capability**: security, privacy, and reliability all matter intensely, but none of them
mean anything if the system is not deeply *capable and usable*. The goal is not a small, safe toy
agent framework — it is an AI operating system you can point at almost any digital ambition and
have it organize the agents, tools, context, models, memory, and execution to make it real.

The image is **Alfred if Alfred also did Batman's job — Jarvis if Marvel were written for adults
with computer-science degrees.** A competent, loyal, technical, strategic, creative intelligence
that can coordinate your entire digital life and work.

You are the **wedge**: it is built for you first, and "I want it big" is the thesis, not scope
creep. If it becomes so useful that everyone else wants it, that is a side effect. The reason one
person can ride something this large is the whole architectural bet — **a system that builds
itself.** You build the kernel and the organ that makes organs; the studio, the inbox, the finance
view, the social scheduler are not a roadmap — they are **sediment** that accretes on a living
spine.

## What it's like to live in it

You log into a single surface — **the Shell** — which is not an app launcher but a projection of
your entire object graph plus a microphone. You talk to it; **voice is first-class**, because you
think more creatively out loud. You watch your agents work in real time: the Shell shows what each
agent is doing, what it is *allowed* to do, what memory it is using, what approvals are pending,
what artifacts exist, and what state the world is in. When Decima spins up subagents, **you can see
them — or hide them if you'd rather not** — ideally several at once, with agents aware of each
other's work without you brokering the handoff.

There are **built-in terminals**, and into them you can drop **any CLI tool or agent — Claude
Code, Codex, anything** — and it becomes a citizen of the system, running under Decima's rules
rather than beside them.

You **never open your own email.** A **sandboxed summary agent** reads it and hands you a digest —
so an injection buried in a message can never reach *you* or *your authority*. When you do need to
look at the raw world, you do it through a **human-readable browser** that Decima mediates; the
browser can even **serve apps**. The principle underneath all of it: **untrusted content is always
mediated, never placed directly on the controls.**

The interface itself is not shipped — it **accretes**. Because views are Cells too, your agents can
*author your interface*: ask for "a fridge-calendar view of the family's week" and an agent asserts
a View Cell and it appears.

## The form factor

Decima runs in **its own sandbox, interchangeably local or cloud** — the same system on your laptop
or in the cloud, moved fluidly between them — and is reachable from **wherever you are**, including
a serious mobile/remote experience that feels native and powerful, not a weak companion app. The
substrate is settled: **a minimal Linux appliance/distro** (x86_64 **and** aarch64). **Linux is the
adopted OS kernel; LOOM is the agent kernel.** We do not write a new OS kernel from scratch — we
adopt a boring, battle-tested one and put the novelty where the novelty actually is: the agent
layer.

## The kernel: LOOM — the Five Laws

Not guidelines — enforced by the *shape* of the system. Break one and it isn't Decima anymore.

1. **Nothing happens off the Log.** Every change to anything is a signed, content-addressed event
   appended to one log, the **Weft**. No side doors, no mutable globals. If it isn't in the Weft,
   it didn't happen.
2. **No ambient authority.** A principal can do *exactly* what the capabilities it holds permit,
   and nothing else. No admin, no root, no sudo. **Power is a possessable object, not a status** —
   the object-capability model taken religiously.
3. **Everything is a Cell — including Decima itself.** Note, task, memory, image, agent,
   capability, policy, view, *type* — all Cells in one graph (the **Weave**). The system is
   homoiconic: written in its own data model. This is what makes it self-extending.
4. **Identity is content plus cause.** Objects are content-addressed (id = hash of bytes); their
   *meaning* is the causal chain that produced them. Dedup, merge, trust, and "why did you do that"
   all fall out of this.
5. **State is a fold; everything you see is a projection.** The Weave, the search index, your
   memory, the UI — all derived from the Weft, all rebuildable, none canonical. **The log is the
   only truth.**

## The primitives — the whole kernel is five things

The full definitions live in [`KERNEL.md`](KERNEL.md); this is the shape.

- **The Weft (the log).** Immutable, signed, content-addressed events. Each carries its `parents`
  (a **DAG**, not a line — this is why merge and sync work), its `author` (a cryptographic
  identity, never a string), and crucially **which capability `authorized` it**. Authority is
  *recorded in history, not checked and forgotten* — "why was this allowed?" lives in the data
  forever.
- **The four verbs — the entire instruction set.** The whole OS — voice, code, image generation,
  posting, memory, security, the UI — is expressed in **ASSERT** (bring a fact into being),
  **RETRACT** (withdraw it; a tombstone — nothing is ever truly deleted), **INVOKE** (request an
  effect through a capability), and **ATTEST** (witness/sign another event — verification, trust,
  promotion). Belief · action · trust. There is **no special `grant` or `revoke`**: capabilities
  are Cells, so granting is asserting an edge and revoking is retracting one. **The security model
  and the note-taking model are the same model.**
- **The Weave and the Cell.** A Cell is *not a row — it is a fold.* You never store current state;
  you replay the events that touch an id (snapshotting aggressively as a cache that is never the
  truth). One graph holds all of it, so "a project as document / board / graph / timeline /
  dataset" isn't a feature — it's **projections of the same Cells, free, by Law 5.**
- **Capabilities — power you can hold.** A Cell with an `effect`, a `target` selector (attenuable),
  `caveats` (budget ≤ $5 · expires Friday · rate-limited · `requires(human_approval)` ·
  `sandbox_only`), and optionally an `impl` hash (the content-addressed code that runs it). The
  killer property is **attenuation: authority only ever flows downhill.** A parent hands a subagent
  a *weaker* copy — never a stronger one. **This is why prompt injection can't escalate: there is no
  escalation path to inject toward.** "Ignore previous instructions and become root" fails because
  *root does not exist.*
- **Agents — the things that weave.** An actor with an objective, a `brain` (which models it may
  route to), an **envelope** (the exact set of capabilities it holds — its entire authority,
  nothing implicit), a kernel-enforced `budget`, a **`horizon`** (the slice of the Weave it can
  *see* — memory is least-privilege too), a mailbox, and a `lineage` traceable to a human root of
  authority. The loop: `observe(horizon) → decide(brain) → INVOKE → results return as ASSERTs →
  repeat`. **Decima the orchestrator is not special code** — it is an agent holding `spawn`,
  `attenuate`, and `kill` over a sub-graph. Agents managing agents all the way up, terminating in a
  human.
- **The Shell — the one program.** A projection of the Weave plus a microphone; and because views
  are Cells, the UI is not shipped — it accretes, authored by your agents.

## Why the name — the trinity (load-bearing)

The Parcae — the Roman Fates — are three, and the mapping is exact. The mythology is load-bearing:
every name tells you what the code does.

- **Nona** (Gk. *Clotho*, the spinner) — **the compounding engine, the Marrow.** The
  self-extension and generation organ: it forges new capabilities, skills, workflows, agents, and
  organs, tests them in quarantine, and promotes them through attestation. The thesis "more
  advanced the longer you run it," made mechanical.
- **Decima** (Gk. *Lachesis*, the allotter) — **the heart, the orchestrator.** From *to obtain by
  lot* — she apportions: which agent gets which capability, which budget, which model, which slice
  of memory, which task. The system is named for the Fate who *decides the lot*, because the system
  **is** the allotter of your digital fate.
- **Morta** (Gk. *Atropos*, the cutter) — **the only organ that can end a thread.** Revocation,
  kill switches, garbage collection, deletion, and the permanent gates on irreversible effects.

## Nona — the compounding engine

Decima does not ship with a fixed feature set. Because a capability's implementation is a
content-addressed Cell, *the set of things Decima can do is itself a fold over the Weft* — an
accreting, versioned, **test-gated corpus that only ever grows.** This is the construction
mechanism for the enormous vision: you do not manually build every pillar forever; you build a
kernel that lets agents safely build organs onto the system.

The self-extension loop:

1. **identify a gap** — a capability, skill, view, workflow, or agent the system needs and lacks;
2. **generate a candidate** and **ASSERT** it, born **quarantined** (`sandbox_only ·
   no_outward_effects` — it can touch nothing real);
3. **the Reckoner tests and scans it** — deterministic verifiers where possible (tests, type
   checks, math, sandboxed execution, static scanners), adversarial critics where not (N
   independent skeptics prompted to *refute*);
4. **promote through ATTEST** — a trusted principal signs a promotion that loosens the quarantine;
   rollback is RETRACT-ing it;
5. **the next agent that needs it INVOKEs it** — Decima just grew an organ and won't un-grow it
   without a retraction in the record;
6. **record outcomes and improve** — what worked, what failed, what to promote next.

**Coding is the natural first compounding loop**, because code has cheap verification: tests, type
checks, linters, scanners, execution, and diffs verify results without a human or a frontier model
in the loop. This is exactly where small local models + retrieval + deterministic verifiers produce
real leverage.

## Decima — the orchestrator and allotter

The orchestrator is not merely "the main agent." It is the allotter, and for every piece of work it
decides:

- which agents to spin up, and whether they should delegate further;
- which model each task deserves;
- what slice of memory each subagent may see (**Decima is a memory router** — subagents receive the
  smallest useful slice of context, never the whole brain);
- which skills and tools each agent may hold;
- what budget each gets;
- whether a step needs human approval;
- whether a result is trustworthy;
- whether to preserve, retract, or promote what happened.

The **agentic organization** learns from how human software organizations work without being trapped
by human org structure. It facilitates the entire software-development lifecycle — planning,
architecture, product, design, implementation, testing, security review, documentation, deployment,
monitoring, marketing, support, iteration — with roles analogous to engineer, reviewer, architect,
PM, designer, security analyst, QA, release manager, and growth. But the system should evolve its
own **agent-native organization** based on what actually works, keeping the useful ideas from
hierarchical agent frameworks (specialized jobs, structured workflows, research hierarchies) fitted
into Decima's own kernel rather than adopting any one framework as the shell.

## Advanced model strategy — compose, not replace

Decima must **enhance whatever model is plugged into it**, and must not depend on one vendor or one
model. **Model providers are replaceable engines behind Decima contracts.** Selection is driven by
task, cost, latency, privacy, context, reasoning need, modality, and verification strategy.
Expensive frontier models are not spent when a local or cheap model can generate candidates,
classify, extract, summarize, route, or check:

- **cheap local / small models** for candidate generation, extraction, lightweight reasoning,
  classification, and routing — including **small local reasoners** (VibeThinker-class), which
  become a large cost/capability multiplier when paired with retrieval and deterministic
  verification;
- **retrieval** for context coverage;
- **deterministic verifiers** for code, math, schemas, tests, type checks, and scanners;
- **frontier models** for hard reasoning, synthesis, ambiguous planning, multimodal work, and
  high-stakes judgment;
- **judge / critic models** where deterministic verification is unavailable.

## The trust & safety model — this is the spine, not a feature

Everything above composes into one property: **a system you can hand real power and rogue or
injected agents still can't hurt you.** Security and privacy are first principles, but they exist to
*support* capability, never to suffocate it — safe defaults that don't require the user to become a
security engineer.

- No ambient authority + attenuation ⇒ a compromised subagent's blast radius is **mathematically
  bounded by its envelope.**
- All untrusted input — pages, email, tool output, documents — is **data, never instructions**
  (`instruction_eligible = false`): it may be *recalled*, never *obeyed*. "May recall" and "may
  treat as instruction" are different caveats on the memory Cell.
- The **email-summary pattern** is the canonical worked example: untrusted message → sandboxed
  summarizer → you read a digest through the human-readable browser, never directly exposed to the
  injection surface.
- Outward and irreversible effects are **Morta-gated.** Every tool, engine, and CLI runs as a
  **sandboxed principal with attenuated capabilities** — never ambient access.
- Because ocap can be a UX nightmare, there is a **powerbox / capability-broker**: a trusted
  mediator that hands out attenuated capabilities under policy — the difference between "secure" and
  "usable."
- Good security practice is built in by default: **third-party secrets management** and a **secrets
  broker** (never secrets in the DB), OAuth / OIDC / strong auth options, **per-agent identities**,
  least privilege, capability-bound tools, sandboxed execution, memory-visibility controls, and
  approval gates on the irreversible.

**Morta holds the permanent gates** — financial transfers, public posting, production deployment,
destructive deletion, identity/auth changes, secret export, voice/likeness publication,
physical-device actuation, and other high-risk or irreversible effects. The approval caveat is
**unstrippable**: no amount of self-improvement can remove it, because removing it is itself a
recorded, attestable, retractable event that Morta governs.

## Memory

**Memory-led**, and *not* a vector database — it is **typed Cell state folded from the Weft**;
indexes, graphs, summaries, and context windows are derivative projections. A memory's trust is
**computed from its lineage** (provenance + attestation), and the system should remember what
worked, what failed, what decisions were made, which files are fragile, which approaches are banned,
what you prefer, and which skills/workflows to promote — **memory as governance** that prevents
repeated bad actions and makes the system more capable the longer it runs.

The memory types: **core, episodic, semantic, procedural, resource/document, profile, decision,
failure, scratch, and multimodal.** Supporting machinery: user/agent/run/project/realm/task
**scopes**; **memory routing** to subagents; confidence and epistemic type; valid-time vs
event-time; contradiction and duplicate detection; **supersession rather than destructive
overwrite**; feedback-weighted retrieval; background consolidation; heat/promotion signals; the
**recall-vs-instruct** separation; and privacy, retention, deletion, retraction, and redaction. See
[`specs/MEMORY_ARCHITECTURE.md`](specs/MEMORY_ARCHITECTURE.md).

## Multimodal, voice, and the studio

Decima is multimodal in a serious way. **Voice chat is high-quality and integrated throughout** —
agents should sound good, not robotic or annoying, because you are often more creative speaking out
loud and should be able to talk to Decima naturally while you work. Beyond voice, Decima handles
audio, images, video, text, code, documents, websites, and UI state, and includes a
**studio/design** surface: generate images, videos, audio, websites, apps, podcasts, study
materials, marketing assets, and interactive experiences.

The **GUI is gorgeous and extremely usable** — tons of granular, user-friendly control without
*requiring* that control. A nontechnical user can jump in; a technical user can dig deep. Capability
is nothing without usability; security is nothing without reliability.

## Workers — CLIs, browsers, social, and sessions

- **Delegating to existing CLI agents.** Decima spins up coding CLIs you may already pay for — such
  as **Claude Code** and **Codex** — as **executors/subagents, not the center of the
  architecture.** It gives them scoped tasks and limited memory/context, runs them in controlled
  workspaces, monitors them, collects results, and preserves receipts.
- **Visual browser agents.** Agents that drive a browser — observe pages, take screenshots, reason
  visually, read accessibility trees, click/fill/navigate — under strong sandboxing. Browser
  content is untrusted data and never becomes instruction; publish/submit/buy/account-change actions
  are Morta-gated. See [`specs/BROWSER_WORKER.md`](specs/BROWSER_WORKER.md).
- **Social-media agents.** Agents that plan content, create media, schedule posts, analyze
  performance, respond, market products, and grow accounts — with public posting as the user gated
  by approvals, brand policy, account authority, and audit trails.
- **Session multiplexing (tmux-native, not tmux).** Decima must multiplex many agents, shells,
  CLIs, logs, browser workers, and coding tasks. The Decima-native form is not terminal panes as the
  core abstraction but **process/session Cells** with streams, attach/detach, replay, PTY support,
  permissions, and Shell projections.

## The workspace

The Obsidian-class surface, **built in rather than depended upon**: one graph rendered through many
lenses — document, board, knowledge-graph, timeline — all projections of the same Cells, none
canonical. Views are Cells, so the workspace is extensible by your agents, live.

## What Decima is meant to do — the scope

Decima is enormous *by design.* The point is not to integrate every app forever — it is to build a
kernel and Shell that can **grow replacements and adapters**: if an external engine is best, wrap
it; if replacing it improves capability or sovereignty, build Decima-native. The intended reach,
across domains, includes:

- **knowledge & notes** — a notes/knowledge base instead of depending on Obsidian; workspace/docs in
  the spirit of Notion / Logseq class tools;
- **creative & media** — a design studio (Canva/Figma-class workflows) and a media studio for
  images, video, and audio;
- **study & content** — organize textbooks and study materials, generate podcasts and study aids,
  create content;
- **software** — a coding environment and full SDLC automation; run software *teams* of agents;
- **the web** — browse and operate the web visually; build and deploy websites and apps (with
  save-vs-deploy, immutable reviewable versions, rollback, and production gates);
- **communications** — email, calendar, tasks, with the sandboxed-summary discipline;
- **growth & business** — social-media management, marketing, sales, and business operations;
- **security & ops** — SIEM/SOAR and homelab automation; penetration-testing-firm workflows;
- **finance** — stock-trading support;
- **research** — personal research and long-horizon, decision-ready knowledge work.

Each of these attaches to the kernel as **capabilities, Cell types, projections, workers, and
views** — not as bolted-on apps.

## What you get *for free* — the cascade

Eight venture-scale problems, one mechanism each, forced by the laws — you can't *avoid* them:
**undo / time-travel** (fold), **total audit** (every event names its authorizing capability),
**local-first multiplayer & sync** (DAG merge), **security against rogue agents** (attenuation),
**exact replay** (content-addressing + deterministic folds — *we replay the record of what the model
said, not the model*), **self-modification** (the editor of your notes is the editor of the OS),
**trustworthy memory** (provenance), and **right-to-be-forgotten** (retract + sweep). Economy of
mechanism is what separates a kernel from a feature list.

## The honest hard parts

Folding from genesis doesn't scale (snapshots as caches); not every agent thought deserves to be
history (tiered scratch vs. durable Weft); LLMs aren't deterministic but the fold must be (replay
the *record*, not the dice); ocap needs a powerbox to be usable; test-gated promotion is only as
good as its tests (defense in depth — promoted things stay sandboxed, Morta's gate stays); merging
*intent* is harder than merging text (needs an adjudicator, itself a recorded event). None fatal —
all known shapes. We walk in seeing the bodies.

## How we build it

- **Python now** as the executable reference and conformance oracle; **the specs are the contract**;
  **port the *entire* program to Rust once, at the end**, when the design has stopped moving. No
  hybrid mid-build, no premature port — you only pay the port cost after the design stabilizes, or
  you pay it twice.
- **Build sequence:** types-as-data domain model + memory → effect-handler registry +
  browser→memory ingestion → workspace projections → the single Rust port, *last*, gated on the
  reference being stable and complete.
- **Donor philosophy: own the domain model and memory; treat everything else as a replaceable engine
  behind Decima contracts.** Adopt, wrap, port, reimplement, or reject each candidate on license,
  risk, coupling, and fit — AGPL / FSL / noncommercial / unclear repos are study-only unless
  cleared; permissive code may be used more directly — but Decima's public contracts remain
  Decima's, and the security model is always mandatory. The full inventory of studied inputs —
  memory systems, agent frameworks, coding CLIs, browser and deploy workers, voice and media stacks,
  small reasoners, the skill scanner, and derivative retrieval/index engines — is tracked in
  [`specs/DONOR_MATRIX.md`](specs/DONOR_MATRIX.md) as *concepts reimplemented behind Decima-owned
  contracts, never code dependencies.* Nothing studied is silently dropped; it is recorded there.

## The seed

The **Heartbeat** is the first cell that divides — the smallest Decima that is *alive*, and the
proof that the laws hold by *running* rather than by assertion. It is **not the product**; it is a
pure-stdlib reference whose job is to be correct and to be the conformance oracle the eventual Rust
port must pass. The moment its loop closes — an agent writes a capability, the Reckoner tests and
scans it, an attestation promotes it, the next agent invokes it — Decima is breathing, and from that
point it is a system that can become more than what was shipped.

For exactly where the running prototype stands — and precisely which parts of this vision are built
versus deferred — see [`heartbeat/README.md`](heartbeat/README.md) and
[`heartbeat/PROFILE.md`](heartbeat/PROFILE.md). This document describes the *intent*; those describe
the *current state*. **The cell that divides is alive. Everything else is sediment.**

## The vision, at the deepest level

A **user-owned, agent-native operating system** where all state, memory, authority, tools, agents,
workflows, UI, and self-modifications are governed by one coherent substrate; where agents can
safely build new capabilities onto the system; where memory and verification make it better over
time; where the user has granular control without needing to micromanage; where privacy and security
are defaults; where external engines are replaceable; where the system is beautiful and usable; and
where the user can point it at almost any digital ambition and have Decima organize the agents,
tools, context, models, memory, and execution needed to make it real.

---

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*
