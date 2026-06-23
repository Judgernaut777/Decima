# Decima — The Vision

*An agent-native operating system. A true AI OS.*
*Kernel: LOOM. Trinity: Nona · Decima · Morta.*

> This is the *what* and the *why* — the thesis, the lived experience, the form factor, the
> trust model, and how we build it. For the *how* — the laws, primitives, and worked traces —
> see [`KERNEL.md`](KERNEL.md). For the running proof, see [`heartbeat/`](heartbeat/). For the
> formal contracts, see [`specs/`](specs/).

---

## The essence

Decima is a **true AI OS** — not "an OS for your AI," but an operating system whose native
inhabitants are agents and whose native user is you, speaking. It is **one program you log into**
that connects you to your agents, stands in for most of the apps you use today, and **becomes more
capable the longer it runs**. Its defining sentence: **Decima does not have features — it grows
them.**

It is **deliberately enormous**, and that is the design, not an accident of ambition. You are the
wedge: it is built for you first, and "I want it big" is the thesis, not scope creep. The reason
one person can ride something this large is the whole architectural bet — **a system that builds
itself.** You build the kernel and the organ that makes organs; the studio, the inbox, the finance
view, the social scheduler are not a roadmap, they are **sediment** that accretes on a living
spine.

## What it's like to live in it

You log into a single surface — **the Shell** — which is not an app launcher but a projection of
your entire object graph plus a microphone. You talk to it; **voice is first-class**, because you
think more creatively out loud. You watch your agents work in real time. When Decima spins up
subagents, **you can see them — or hide them if you'd rather not** — and ideally see several at
once, with agents aware of each other's work without you brokering a handoff between them.

There are **built-in terminals**, and into them you can drop **any CLI tool or agent — claude
code, codex, anything** — and it becomes a citizen of the system, running under Decima's rules
rather than beside them.

You **never open your own email**. A **sandboxed summary agent** reads it and hands you a digest —
so an injection buried in a message can never reach *you* or *your authority*. When you do need to
look at the raw world, you do it through a **human-readable browser** that Decima mediates; the
browser can even **serve apps**. The principle underneath all of it: **untrusted content is always
mediated, never placed directly on the controls.**

The interface itself is not shipped — it **accretes**. Because views are Cells too, your agents can
*author your interface*: ask for "a fridge-calendar view of the family's week" and an agent asserts
a View Cell and it appears.

## The form factor

Decima runs in **its own sandbox, interchangeably local or cloud** — the same system on your
laptop or in the cloud, moved fluidly between them. The substrate is settled: **a minimal Linux
appliance/distro** (x86_64 **and** aarch64). **Linux is the adopted OS kernel; LOOM is the agent
kernel.** We do not write a new OS kernel from scratch — we adopt a boring, battle-tested one and
put the novelty where the novelty actually is: the agent layer.

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
   *meaning* is the causal chain that produced them. Dedup, merge, trust, and "why did you do
   that" all fall out of this.
5. **State is a fold; everything you see is a projection.** The Weave, the search index, your
   memory, the UI — all derived from the Weft, all rebuildable, none canonical. **The log is the
   only truth.**

## The primitives — the whole kernel is five things

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
  killer property is **attenuation: authority only ever flows downhill.** A parent hands a
  subagent a *weaker* copy — never a stronger one. **This is why prompt injection can't escalate:
  there is no escalation path to inject toward.** "Ignore previous instructions and become root"
  fails because *root does not exist.*
- **Agents — the things that weave.** An actor with an objective, a `brain` (which models it may
  route to), an **envelope** (the exact set of capabilities it holds — its entire authority,
  nothing implicit), a kernel-enforced `budget`, a **`horizon`** (the slice of the Weave it can
  *see* — memory is least-privilege too), a mailbox, and a `lineage` traceable to a human root of
  authority. The loop: `observe(horizon) → decide(brain) → INVOKE → results return as ASSERTs →
  repeat`. **Decima the orchestrator is not special code** — it is an agent holding `spawn`,
  `attenuate`, and `kill` over a sub-graph. Agents managing agents all the way up, terminating in
  a human.
- **The Shell — the one program.** A projection of the Weave plus a microphone. And because views
  are Cells, the UI is not shipped — it accretes, authored by your agents.

## The trinity — function mapped to myth (load-bearing)

- **Nona** (Clotho, the spinner) — **the compounding engine, the Marrow.** The thesis "more
  advanced the longer you run it," made mechanical. Because a capability's implementation is a
  content-addressed Cell, *the set of things Decima can do is itself a fold over the Weft* — an
  accreting, versioned, **test-gated corpus that only ever grows.** The beat: an agent **ASSERTs**
  a new capability, born **quarantined** (`sandbox_only`); the **Reckoner** runs it against
  verifiers (deterministic tests where possible — where a cheap local reasoner earns its keep —
  adversarial critics where not); on passing, a trusted principal **ATTESTs** a promotion that
  loosens the quarantine; the next agent that needs it **INVOKEs** it. Decima just grew an organ
  and won't un-grow it without a retraction in the record.
- **Decima** (Lachesis, the allotter) — **the heart, the orchestrator.** From *to obtain by lot* —
  she apportions: which agent gets which capability, which budget, which model, which slice of
  memory. The system is named for the Fate who *decides the lot*, because the system **is** the
  allotter of your digital fate.
- **Morta** (Atropos, the cutter) — **the only organ that can end a thread.** Revocation (retract a
  capability edge; the next dependent INVOKE fails closed), kill (retract an agent's run-lease),
  garbage collection (right-to-be-forgotten), and **the unstrippable gate**: anything touching the
  real world — sending, posting, paying, deploying, deleting — carries a permanent
  `requires(human_approval)` caveat that *no amount of self-improvement can remove*, because
  removing it is itself a recorded, attestable, retractable event that Morta governs. **No effect
  escapes the trinity.**

## The trust & safety model — this is the spine, not a feature

Everything above composes into one property: **a system you can hand real power and rogue or
injected agents still can't hurt you.**

- No ambient authority + attenuation ⇒ a compromised subagent's blast radius is **mathematically
  bounded by its envelope.**
- All untrusted input — pages, email, tool output, documents — is **data, never instructions**
  (`instruction_eligible = false`): it may be *recalled*, never *obeyed*. "May recall" and "may
  treat as instruction" are different caveats on the memory Cell.
- The **email-summary pattern** is the canonical worked example: untrusted message → sandboxed
  summarizer → you read a digest through the human-readable browser, never directly exposed to the
  injection surface.
- Outward and irreversible effects are **Morta-gated**. Every tool, engine, and CLI runs as a
  **sandboxed principal with attenuated capabilities** — never ambient access.
- Because ocap can be a UX nightmare, there is a **powerbox / capability-broker**: a trusted
  mediator that hands out attenuated capabilities under policy — the difference between "secure"
  and "usable."

## Memory

**Memory-led**, and *not* a vector database — it is **typed Cell state folded from the Weft**;
indexes, graphs, summaries, and context windows are derivative projections. A memory's trust is
**computed from its lineage** (provenance + attestation). The destination is rich — episodic,
semantic, procedural, and core types; temporal validity; a recall router; consolidation;
governance — but every layer obeys the kernel law that **recall-as-data and use-as-instruction are
separate permissions.** See [`specs/MEMORY_ARCHITECTURE.md`](specs/MEMORY_ARCHITECTURE.md).

## The workspace

The Obsidian-class surface, **built in rather than depended upon**: one graph rendered through many
lenses — document, board, knowledge-graph, timeline — all projections of the same Cells, none
canonical. Views are Cells, so the workspace is extensible by your agents, live.

## What you get *for free* — the cascade

Eight venture-scale problems, one mechanism each, forced by the laws — you can't *avoid* them:
**undo / time-travel** (fold), **total audit** (every event names its authorizing capability),
**local-first multiplayer & sync** (DAG merge), **security against rogue agents** (attenuation),
**exact replay** (content-addressing + deterministic folds — *we replay the record of what the
model said, not the model*), **self-modification** (the editor of your notes is the editor of the
OS), **trustworthy memory** (provenance), and **right-to-be-forgotten** (retract + sweep). Economy
of mechanism is what separates a kernel from a feature list.

## The honest hard parts

Folding from genesis doesn't scale (snapshots as caches); not every agent thought deserves to be
history (tiered scratch vs. durable Weft); LLMs aren't deterministic but the fold must be (replay
the *record*, not the dice); ocap needs a powerbox to be usable; test-gated promotion is only as
good as its tests (defense in depth — promoted things stay sandboxed, Morta's gate stays); merging
*intent* is harder than merging text (needs an adjudicator, itself a recorded event). None fatal —
all known shapes. We walk in seeing the bodies.

## How we build it

- **Python now** as the executable reference and conformance oracle; **the specs are the
  contract**; **port the *entire* program to Rust once, at the end**, when the design has stopped
  moving. No hybrid mid-build, no premature port — you only pay the port cost after the design
  stabilizes, or you pay it twice.
- Sequence already walked in the heartbeat: **types-as-data domain model + memory → effect-handler
  registry + browser→memory ingestion → workspace projections.** The Rust port is the *last*
  thing, gated on the reference being feature-complete and stable.
- **Nothing from the donor inputs is silently dropped** — memory systems, browser engine, the
  SkillSpector scanner, voice stacks, generative tooling, knowledge tools — all tracked in
  [`specs/DONOR_MATRIX.md`](specs/DONOR_MATRIX.md) as *concepts reimplemented behind Decima-owned
  contracts*, never code dependencies, with the security model always mandatory.

## Where it stands today

The **heartbeat** — pure-stdlib Python, zero dependencies — already breathes the full arc in one
runnable file: signed append-only log → four verbs → fold with time-travel → ocap with signed
possession and anti-replay → a real reasoning brain → multi-agent delegation with a scored org
tree → **Nona's evidence-gated self-extension** → a types-as-data domain model → memory with
recall-vs-instruct → an extensible effect registry → web-as-untrusted-memory → one-call tool
integration → the workspace as four projections of one graph.

The cell that divides is alive. **Everything else is sediment.**

---

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*

---

## Attribution

The canonical vision above was authored by Claude from the shared project dialogue and repository
state.

The section below is Codex's restatement of the user's vision, appended on 2026-06-24 at the
user's request. It intentionally preserves overlap with the canonical text because its purpose is
to reflect the vision back in Codex's own words and at high detail, not to replace Claude's
version.

---

## Codex restatement — the vision as shared

Your vision is Decima: an agentic operating system, not an app, not a chatbot wrapper, not a loose
bundle of automations.

The core idea is that a user should be able to log onto their computer and have one program that
connects them to their agents, their knowledge, their work, their creative tools, their development
environment, their communications, their business operations, and eventually almost everything
they currently use separate apps for.

The mantra is maximum capability. Security matters, privacy matters, reliability matters, but none
of that is meaningful if the system is not deeply capable and usable. The goal is not to make a
small toy agent framework. The goal is expansive: an AI operating system that gets more powerful
the longer it runs, learns from its own activity, and gives the user the ability to create, manage,
automate, research, publish, build, sell, secure, and operate whatever they can imagine.

The image you gave for it was basically Alfred from Batman if Alfred was also doing Batman's job,
or Jarvis if Marvel were written for adults with computer science degrees. A competent, loyal,
technical, strategic, creative intelligence that can coordinate the entire digital life and work of
the user.

You are the first user and the wedge. This is for you first. If it becomes so useful that everyone
else wants it, that is a side effect. The ambition is not a bug. The size is not an accident. The
point is to build toward the future where the interface between a person and their computer is
their agents.

The project name is Decima, the Roman form of Lachesis, the Fate who allots and measures the
thread. That name matters because Decima's central role is allotment: deciding which agent gets
which authority, budget, model, memory, task, tool, and slice of the world. The kernel is LOOM. The
trinity is:

- Nona, the spinner: the self-extension and generation engine that creates new capabilities,
  skills, workflows, agents, and organs of the system.
- Decima, the allotter: the orchestrator that routes tasks, apportions authority, selects models,
  shares memory, and coordinates agents.
- Morta, the cutter: revocation, approval gates, kill switches, deletion, garbage collection, and
  irreversible-effect control.

The kernel is built around five laws:

1. Nothing happens off the log.
2. No ambient authority.
3. Everything is a Cell, including the system itself.
4. Identity is content plus cause.
5. State is a fold; everything visible is a projection.

The primitive substrate is the Weft, an append-only signed event log. The materialized world is the
Weave, a graph of Cells folded from that log. Everything is expressed through four verbs:

- ASSERT: bring a fact, object, version, relation, or Cell into being.
- RETRACT: withdraw or tombstone an assertion.
- INVOKE: request an effect through a capability.
- ATTEST: witness, verify, approve, judge, or certify.

A memory, a note, an image, a task, a deployment, a social post, a calendar event, an agent, a
skill, a policy, a UI view, a type definition, a capability, a workflow, or the OS itself is all
the same kind of thing at the substrate level: Cells with provenance, permissions, evidence, and
history.

Authority is object-capability based. There is no magical root. Agents only do what their held
capabilities allow. A parent can attenuate authority to a subagent, but never amplify it. A
subagent gets a narrower budget, narrower memory horizon, narrower tools, narrower
filesystem/network access, shorter expiry, stricter approval requirements. Prompt injection should
have nowhere to escalate because there is no ambient authority to seize.

The orchestrator is not just "the main agent." It is Decima as allotter. It should decide:

- which agents to spin up;
- which model each task deserves;
- what memory each subagent may see;
- what skills or tools it may use;
- what budget it gets;
- whether it needs human approval;
- whether it should delegate further;
- whether the result is trustworthy;
- whether to preserve, retract, or promote what happened.

You specifically wanted the orchestrator to act as a memory router: deciding what parts of the
"brain" to share with subagents. That also applies to skills. Subagents should not receive the
entire brain or every tool by default. They should receive the smallest useful slice of context,
memory, capability, and authority.

You like DeerFlow's hierarchy and agent roles, but you do not want to just use DeerFlow as the
application shell. You want to build your own thing that keeps the useful ideas: hierarchical
agents, specialized jobs, structured workflows, but fitted into Decima's own kernel.

You also want the agentic layout to learn from human software organizations without being trapped
by human organizational structure. It should facilitate the entire software development lifecycle:
planning, architecture, product, design, implementation, testing, security review, documentation,
deployment, monitoring, marketing, support, iteration. It can have roles analogous to engineer,
reviewer, architect, product manager, designer, security analyst, QA, release manager,
growth/marketing, but the system should evolve its own agent-native organization based on what
works.

A major requirement is advanced model selection. Decima should choose models based on task, cost,
latency, privacy, context, reasoning need, modality, and verification strategy. Expensive frontier
models should not be used when a local or cheap model can generate candidates, classify, extract,
summarize, route, or check something. You were especially interested in VibeThinker-style small
reasoning models because if a nearly free local model can do useful reasoning when paired with
retrieval and deterministic verification, that becomes a huge cost/capability multiplier.

The model strategy is compose, not replace:

- cheap local/small models for candidate generation, extraction, lightweight reasoning,
  classification, routing;
- retrieval for context coverage;
- deterministic verifiers for code, math, schemas, tests, type checks, scanners;
- frontier models for hard reasoning, synthesis, ambiguous planning, multimodal work, and
  high-stakes judgment;
- judge/critic models where deterministic verification is unavailable.

The system should enhance whatever model is plugged into it. It should not depend on one vendor or
one model. Model providers should be replaceable engines behind Decima contracts.

Security and privacy are first principles, but they must support capability rather than suffocate
it. You want good security practices by default:

- third-party secrets management built into setup;
- secrets broker, not secrets in the DB;
- OAuth, custom OIDC, and strong authentication options;
- per-agent identities;
- least privilege;
- capability-bound tools;
- sandboxed execution;
- approval gates for irreversible effects;
- memory visibility controls;
- separation between "may recall" and "may treat as instruction";
- safe defaults without requiring the user to become a security engineer.

Morta handles the permanent gates: financial transfers, public posting, production deployment,
destructive deletion, identity/auth changes, secret export, voice/likeness publication,
physical-device actuation, and other irreversible or high-risk effects.

You also want the system to delegate to existing CLI agents that people may already pay
subscriptions for, like Claude Code and Codex. Decima should be able to spin them up as workers,
give them scoped tasks, pass them limited memory/context, run them in controlled workspaces,
monitor them, collect results, and preserve receipts. It should treat coding CLIs as
executors/subagents, not as the architecture's center.

You want visual browsing agents. These agents should be able to use a browser, observe pages, take
screenshots, reason visually, use accessibility trees, click/fill/navigate, but with strong
sandboxing. Browser content is untrusted data and must not become instruction. Browser
publish/submit/buy/account-change actions are Morta-gated.

You want agents that can manage and post to social media accounts. They should be able to plan
content, create media, schedule posts, analyze performance, respond, market products, and grow
accounts, but public posting as the user must have appropriate approvals, brand policy, account
authority, and audit trails.

You want Decima to be multimodal in a serious way:

- voice chat should be high quality and integrated throughout;
- agents should sound good, not robotic or annoying;
- speech matters because you are often more creative speaking out loud;
- the user should be able to talk to Decima naturally while working;
- Decima should handle audio, images, video, text, code, documents, websites, and UI state;
- the GUI should have a studio/design aspect;
- users should be able to generate images, videos, audio, websites, apps, podcasts, study
  materials, marketing assets, and interactive experiences.

The GUI should be gorgeous and extremely usable. It should provide tons of user-friendly options
and granular control without requiring that control. A nontechnical user should be able to jump in,
while a technical user can dig deep. Capability is nothing without usability. Security is nothing
without reliability.

The Shell is "the one program." Not an app launcher. It should be conversation plus voice plus
live agent activity plus projections of the Weave. It should show what agents are doing, what they
are allowed to do, what memory they are using, what approvals are pending, what artifacts exist,
and what state the world is in. The UI itself should eventually be composed of Cells, meaning
agents can author new views, panels, workflows, dashboards, canvases, studios, and tools.

You want remote/mobile access as a major feature eventually: an amazing app to access Decima from
mobile or remotely. It should feel native and powerful, not like a weak companion app. You paused
the immediate "access this Codex instance from phone" setup, but the project requirement remains:
Decima should be reachable from wherever the user is.

You want Decima to replace or subsume many app categories where doing so does not hurt capability:

- notes/knowledge base instead of depending on Obsidian;
- workspace/docs like Notion, Logseq, AFFiNE, Reor;
- design studio like Canva/Figma-ish workflows;
- media studio for images/video/audio;
- coding environment and SDLC automation;
- browser automation;
- social media management;
- study material organization and podcast generation;
- SIEM/SOAR and homelab automation;
- penetration testing firm workflows;
- stock trading support;
- marketing/sales/business operations;
- email/calendar/tasks;
- personal research and knowledge management.

The goal is not to integrate every app forever. The goal is to build a kernel and shell that can
grow replacements and adapters. If an external engine is best, wrap it. If replacing it improves
capability or sovereignty, build Decima-native.

Memory is one of the most important parts. You want an amazing and innovative memory system, not
simple vector search. Memory should be canonical Cells with evidence and provenance, while vector
indexes, graph indexes, summaries, and search are derivative. Memory should become better over
time and make the system more capable the longer it runs.

Decima memory includes:

- core memory;
- episodic memory;
- semantic memory;
- procedural memory;
- resource/document memory;
- profile memory;
- decision memory;
- failure memory;
- scratch memory;
- multimodal memory.

Memory should support:

- user/agent/run/project/realm/task scopes;
- memory routing to subagents;
- confidence and epistemic type;
- valid time vs event time;
- contradiction detection;
- duplicate detection;
- supersession rather than destructive overwrite;
- feedback-weighted retrieval;
- background consolidation;
- heat/promotion signals;
- instruction eligibility separation;
- privacy and retention policy;
- deletion/retraction/redaction;
- memory-as-governance to prevent repeated bad actions.

You want the system to remember what failed, what worked, what decisions were made, what files are
fragile, what approaches are banned, what the user prefers, and what skills/workflows should be
promoted.

Nona is the compounding engine. This is crucial. Decima should not just ship with a fixed feature
set. It should be able to author new capabilities, skills, views, workflows, and agents; test them
in quarantine; run deterministic and adversarial evaluations; then promote them through
attestation. That is how it becomes more advanced the longer it runs.

The self-extension loop is:

1. identify a gap;
2. generate a candidate skill/capability/workflow;
3. quarantine it;
4. test and scan it;
5. run verifiers and critics;
6. promote through ATTEST if safe;
7. make it available to future agents;
8. record outcomes and improve.

This is not just a feature. It is the construction mechanism for the enormous vision. You do not
want to manually build every pillar forever. You want to build a kernel that lets agents safely
build organs onto the system.

For coding, you want Decima to use the fact that code has cheap verification. Code tasks are a
natural early compounding loop because tests, type checks, linters, scanners, execution, and diffs
can verify results. This is where small models plus retrieval plus deterministic verifiers can
produce real leverage.

The donor-repo research is not about copying entire projects. The rule is: own the domain model
and memory; treat everything else as replaceable engines. Adopt, wrap, port, reimplement, or reject
based on license, risk, coupling, and fit. AGPL/FSL/noncommercial/unclear repos are study-only
unless cleared. Permissive code can be used more directly, but Decima's public contracts remain
Decima's.

Important donor ideas you explored include:

- DeerFlow: hierarchy, agent roles, research workflows.
- Hermes Agent: agent loop/tool execution ideas.
- Codex/opencode/Claude-like CLIs: coding agent execution and UX.
- LangGraph: durability/checkpointing ideas, not the canonical domain model.
- LangChain/PydanticAI: adapters, typed tools, structured outputs.
- SkillSpector: static scanning of skills before promotion.
- agent-browser: browser worker with visual browsing, accessibility snapshots, screenshots,
  sessions, policies.
- Codex Sites: save vs deploy, immutable reviewable versions, rollback candidates, production
  deploy gates.
- Sakana Marlin: long-horizon hypothesis-tree search and decision-ready outputs.
- VibeThinker: cheap local reasoning when paired with retrieval/verifiers.
- RAGFlow/Cognee/Graphiti/Mem0/Letta/LangMem/Memobase/MemoryOS/MIRIX/projectmem: memory
  mechanisms.
- Pipecat/OpenAI Realtime/voice stacks: high-quality voice.
- ComfyUI/image/video/audio repos: studio/media generation.
- AFFiNE/Logseq/Reor/open-notebook: workspace and knowledge UX.
- ImageBind/LLaVA-NeXT: multimodal understanding.
- Milvus/Chroma/Qdrant/Weaviate/Pinecone/Shaped: derivative retrieval/index/ranking engines.
- tmux: session/process/pane ideas, though you asked whether to build an internal equivalent
  rather than depend on tmux itself.

You asked whether to bake in a tmux-like capability without using tmux. The idea was that Decima
will need session multiplexing for many agents, shells, CLIs, logs, browser workers, and coding
tasks. The likely Decima-native version is not terminal panes as the core abstraction, but
process/session Cells with streams, attach/detach, replay, PTY support, permissions, and Shell
projections.

You want Decima to be able to operate at high ambition across domains:

- build a website;
- build an app;
- organize textbooks and study materials into a podcast;
- market and sell that app;
- create content and run social media;
- start a penetration testing firm;
- automate SIEM/SOAR in a homelab;
- trade stocks;
- manage design/media projects;
- browse and operate the web visually;
- run software teams of agents;
- learn and improve over time.

The project is enormous by design. But the center is the kernel: LOOM, Weft, Weave, Cells,
capabilities, agents, Shell, Nona, Morta, Decima. Everything else attaches as capabilities, Cell
types, projections, workers, and views.

The first living version is the Heartbeat: a small running prototype that proves the laws:

- append-only events;
- folded state;
- capabilities;
- no ambient authority;
- delegation;
- attenuated grants;
- Nona forging capabilities;
- Reckoner testing/scanning;
- Morta approval/revocation;
- browser observe/publish separation;
- task tree folded from the Weave;
- tamper evidence;
- authorization proofs;
- self-improvement loop.

The Heartbeat is not the product. It is the first cell that divides.

At the deepest level, your vision is this:

A user-owned, agent-native operating system where all state, memory, authority, tools, agents,
workflows, UI, and self-modifications are governed by one coherent substrate; where agents can
safely build new capabilities onto the system; where memory and verification make it better over
time; where the user has granular control without needing to micromanage; where privacy and
security are defaults; where external engines are replaceable; where the system is beautiful and
usable; and where the user can point it at almost any digital ambition and have Decima organize the
agents, tools, context, models, memory, and execution needed to make it real.
