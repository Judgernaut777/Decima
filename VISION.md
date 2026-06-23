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
