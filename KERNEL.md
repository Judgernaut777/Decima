# DECIMA

**An agent-native operating system.**
**Kernel: LOOM. Trinity: Nona · Decima · Morta.**

> The whole OS is four verbs over one append-only log. State is a fold. Authority is a held object. The system is made of the same stuff as your data — so it can rewrite itself with the same tools it uses to edit your notes. Decima does not have features. It *grows* them.

---

## Why the name

The Parcae — the Roman Fates — are three, and the mapping is exact:

- **Nona** (Gk. *Clotho*) — **the spinner.** Brings thread into being. → the generative organ.
- **Decima** (Gk. *Lachesis*) — **the allotter.** Measures the thread and *assigns the lot*. → orchestration and routing. **The heart.**
- **Morta** (Gk. *Atropos*) — **the cutter.** Ends the thread. Irrevocable. → revocation, termination, the approval gates.

Lachesis/Decima is the allotter — from *lanchánō*, "to obtain by lot," to apportion. Her job is to decide *how much* each thread gets and *what falls to it*. That is precisely what this system's soul does: it apportions authority — which agent gets which capability, which budget, which model, which slice of memory. The project is named for the Fate who decides the lot, because the system *is* the allotter of your digital fate.

The mythology is load-bearing. Every name tells you what the code does.

---

## The Five Laws

Not guidelines. Enforced by the shape of the system. Break one and it isn't Decima anymore.

1. **Nothing happens off the Log.** Every change to anything is a signed, content-addressed event appended to one log (the **Weft**). No side doors. No mutable globals. If it isn't in the Weft, it didn't happen.

2. **No ambient authority.** A principal can do *exactly* what the capabilities it holds permit — and nothing else. No admin mode, no root, no sudo. Power is a possessable object, not a status. (The object-capability model, taken religiously.)

3. **Everything is a Cell — including Decima itself.** Note, task, memory, image, agent, capability, policy, view, *type* — all Cells in one graph (the **Weave**). The system is homoiconic: written in its own data model. This is what makes it self-extending.

4. **Identity is content plus cause.** Objects are content-addressed (id = hash of bytes). Their *meaning* is the causal chain that produced them. Dedup, merge, trust, and "why did you do that" all fall out of this.

5. **State is a fold; everything you see is a projection.** The Weave, the search index, your memory, the UI — all derived from the Weft, all rebuildable, none canonical. The log is the only truth.

The rest of this document is just watching what these five force into existence.

---

## The Primitives

Five. That's the whole kernel.

### 1. The Weft — the log

The atom of history. Immutable, signed, content-addressed.

```
Event {
  id:         Hash         // = hash(everything below). the event IS its content.
  parents:    Hash[]       // causal predecessors — a DAG, not a line. this is why merge works.
  author:     Principal    // user | agent | system — a cryptographic identity, never a string
  authorized: CellId       // WHICH capability permitted this. provenance of power itself.
  verb:       Verb         // one of four.
  body:       Hash         // payload, content-addressed
  lamport:    u64          // logical clock for causal ordering
  sig:        Signature    // author signs `id`. unforgeable.
}
```

Every event names the capability that authorized it. **Authority is recorded in history, not checked and forgotten.** "Why was this allowed?" is in the data forever.

### The four verbs — the entire instruction set

The whole OS — voice, code, image generation, social posting, memory, security, the UI — is expressed in **four verbs**:

```
ASSERT   — bring a fact/version of a Cell into being
RETRACT  — withdraw a prior assertion (a tombstone; nothing is ever deleted, only retracted)
INVOKE   — request an effect in the world through a capability
ATTEST   — witness/sign another event (verification, trust, promotion, consensus)
```

A three-part spine — **belief** (assert/retract), **action** (invoke), **trust** (attest) — and it echoes the trinity: Nona spins facts into being, Decima invokes and allots, Morta retracts and cuts.

There is no `grant` and no `revoke`. Because capabilities are Cells (Law 3), granting is just `ASSERT`-ing an edge and revoking is `RETRACT`-ing it. Authority management isn't a special subsystem — it's *more graph writes*. The security model and the note-taking model are the same model.

### 2. The Weave — the graph, and the Cell

```
Cell {
  id:    CellId        // stable identity across all its versions
  type:  CellId        // a Cell too — types are data, types are versionable
  head:  Hash          // current content version
}
// Everything else — edges, full history, provenance, the capabilities pointing
// at it — is NOT stored. It is COMPUTED by folding every event whose body touches this id.
```

**A Cell is not a row. A Cell is a fold.** You never store current state; you replay events. (In practice you snapshot aggressively — see *The Hard Parts* — but the snapshot is a cache, never the truth.)

One graph holds all of it. "A research project as document / board / graph / timeline / dataset" isn't a feature — it's six projections of the same Cells, free, by Law 5.

### 3. Capabilities — power you can hold

```
Capability {              // a Cell. authority is data.
  id:        CellId
  effect:    EffectRef    // invoke(shell) · write(weave/notes/*) · post(x.com) · spawn(agent) …
  target:    Selector     // over WHICH cells/resources — an attenuable query
  caveats:   Predicate[]  // budget ≤ $5 · expires(friday) · rate(10/min) · requires(human_approval) · sandbox_only
  delegable: bool
  impl:      Hash?        // for authored capabilities: the content-addressed code/workflow that runs it
}
```

Holding the *id* of a Capability Cell means you can `INVOKE` it. Unforgeable, because grants are signed events on an append-only log.

The killer property: **attenuation.** A parent can hand a subagent a *weaker* copy — smaller budget, narrower target, sooner expiry, an added `requires(approval)` — but **never a stronger one**. Authority only flows downhill. A compromised subagent's blast radius is mathematically bounded by what it was handed. This is why prompt injection can't escalate in Decima: there is no escalation path to inject toward. "Ignore previous instructions and become root" fails because root does not exist.

### 4. Agents — the things that weave

An actor: a mailbox, an envelope of capabilities, and a loop.

```
Agent {                  // a Cell. fork it, snapshot it, inspect it, version it.
  id:        CellId
  objective: CellId
  brain:     Selector    // which model(s) it may route to (frontier · local reasoner · judge)
  envelope:  CellId[]    // the EXACT set of capabilities it holds. its entire authority. nothing implicit.
  budget:    Caveat      // tokens/$/wall-clock — enforced by the kernel, not by good behavior
  horizon:   Selector    // the slice of the Weave it can SEE (memory is also least-privilege)
  mailbox:   CellId       // inbox; messages are just ASSERTs addressed to it
  lineage:   CellId?     // who spawned it — traceable to a human root of authority
}
```

The loop:

```
observe(horizon) → decide(brain) → INVOKE(capabilities) → results return as ASSERTs → repeat
```

Because an Agent is a Cell, an agent holding the right capability can read, fork, throttle, pause, or kill another agent — using the same primitives it uses on your notes. **Decima the orchestrator** is not special code: it is an agent holding `spawn`, `attenuate`, and `kill` capabilities over a sub-graph — the allotter, apportioning lots to the threads beneath it. Agents-managing-agents all the way up, terminating at a human who is the root of all authority.

### 5. The Shell — the one program

The thing you log into. Not an app launcher — a projection of the Weave plus a microphone. Conversation, voice, a live view of your agents at work, and your object graph rendered through views.

Views are Cells too (Law 3) — so your agents can *author your interface*. Ask for "a fridge calendar view of the family's week" and an agent asserts a View Cell; it appears. The UI is not shipped. It accretes.

---

## The Cascade — what you get for *free*

Eight hard problems. One mechanism each, forced by the laws. You don't build these. You can't *avoid* them.

| You want… | It exists because… |
|---|---|
| **Undo / time-travel** | State is a fold (Law 5). Fold to before any event. |
| **Total audit — "why did you do that?"** | Every event names its authorizing capability and causal parents (Laws 1, 4). The *why* is a graph walk. |
| **Local-first multiplayer & sync** | Events form a DAG with parents (Law 1). Merge = DAG union; conflicts resolve by type-specific CRDT. Sync = "send me events I'm missing." |
| **Security against rogue/injected agents** | No ambient authority + attenuation (Law 2). Blast radius = envelope. Nothing to escalate toward. |
| **Perfect reproducibility** | Content-addressed + deterministic fold (Laws 4, 5). Same events → same state, anywhere, forever. |
| **Self-modification** | The system is Cells (Law 3). The editor that changes your notes changes the OS. No separate admin API to secure. |
| **Trustworthy memory** | Provenance + attestation (Laws 1, 4). A memory's trust is *computed* from its lineage. "May recall" vs. "may treat as instruction" = two different caveats on the memory Cell. |
| **Right to be forgotten** | `RETRACT` tombstones the fact; derived indexes rebuild without it; orphaned bytes are GC'd once no live event references them. Delete = retract + sweep. |

Eight venture-scale problems. The same five laws. Economy of mechanism is the thing that separates a kernel from a feature list.

---

## Nona — the compounding engine (the Marrow)

Your thesis — *"becomes more advanced the longer you run it"* — made mechanical.

A capability's implementation is a content-addressed Cell (`impl: Hash`). So the set of things Decima can do is **itself a fold over the Weft** — and the Weft is writable by the system. The kernel API is not fixed. It is an accreting, versioned, test-gated corpus that **only ever grows.**

**Nona** is the spinner — the organ that makes new organs:

```
1. ASSERT    an agent writes a new Capability Cell + its impl. Born QUARANTINED:
             a caveat `sandbox_only · no_outward_effects` — it can touch nothing real.

2. INVOKE    the Reckoner (eval harness) runs it against verifiers:
             · deterministic where possible — tests, type-checks, math, sandboxed execution
               (exactly where a cheap local reasoner earns its keep: generate candidates
                + check claims, for less than one frontier call)
             · adversarial critics where not — N independent skeptics prompted to REFUTE

3. ATTEST    on passing, a trusted principal signs a promotion — an attestation that
             loosens the quarantine. Promotion is a signature. Rollback is RETRACT-ing it.

4. The next agent that needs it holds it and INVOKEs it.
   Decima just grew an organ — and won't un-grow it without a retraction in the record.
```

This is why "it's just me" and "I want it enormous" stop fighting. **You do not build the pillars.** You build the kernel and Nona. Your agents author capabilities — a poster generator, inbox triage, a deploy pipeline, a social scheduler — each quarantined, tested, promoted, permanently added. The studio, the finance app, the email client are not your roadmap. They are sediment. A system that builds itself is the only kind one person rides to enormous.

---

## Morta — the cutter (revocation, termination, the irreversible)

The only organ that can end a thread. Everything outward-facing or irreversible passes through Morta.

- **Revocation** = `RETRACT` of a capability edge. Authority withdrawn propagates by re-fold; the next `INVOKE` that depended on it fails closed.
- **Kill** = an agent holding a `kill` capability retracts a target Agent Cell's run-lease. The actor stops at its next checkpoint.
- **The unstrippable gate.** Anything that touches the real world — sending, posting, paying, deploying, deleting — carries a permanent `requires(human_approval)` caveat that no amount of self-improvement can remove, because removing it is itself a recorded, attestable, retractable event that Morta governs.
- **Garbage collection.** Retracted, unreferenced bytes are swept. Right-to-be-forgotten lives here.

Nona spins, Decima allots, Morta cuts. No effect escapes the trinity.

---

## A Worked Trace

You say, aloud: *"Make a poster for the show Friday and pin it to the fridge calendar."*

```
e001 ASSERT   author=you           Cell(utterance, audio+transcript)         authorized=mic_cap
e002 ASSERT   author=Decima        Cell(goal: "poster + place on calendar")  parents=[e001]
e003 ASSERT   author=Decima        Cell(agent: Designer, envelope=[image_gen≤$2, read(events/show)])
e004 INVOKE   author=Designer      image_gen(prompt=…, ref=brand_kit)         authorized=e003.image_gen
e005 ASSERT   author=image_worker  Cell(image, content=Hash, provenance=e004) parents=[e004]   ← the bytes
e006 ATTEST   author=Critic        e005 "matches brief, text legible"         ← verified, not assumed
e007 INVOKE   author=Designer      write(weave/calendar/friday, attach=e005)  authorized=e003 (attenuated)
e008 ASSERT   author=calendar_view re-projects → poster now on the fridge view
e009 INVOKE   author=Decima        speak("Done — it's on Friday. Want it sized for Instagram too?")
```

Everything reversible (fold to e002). Everything auditable (every event names its capability). The image's license and model provenance ride in e005 forever. The Designer never held a capability to post publicly, so it *couldn't* have, no matter what the transcript said. Nine events. No special cases.

---

## The Hard Parts (no lies)

- **Folding from genesis is insane at scale.** The Weft is truth, but you read from *snapshots* — periodic materialized checkpoints plus incremental fold-forward. Discipline: snapshots are caches you can always nuke and rebuild.
- **Not every thought deserves to be history.** Agent scratch reasoning shouldn't pollute the permanent Weft. Tier it: an ephemeral, GC'd scratch log vs. the durable Weft. Deciding what graduates is real policy.
- **LLMs aren't deterministic, but the fold must be.** Resolve it cleanly: *we don't replay the model, we replay the record of what the model said.* The model's output is an `ASSERT`ed fact. Generation was stochastic; replay is exact, because you fold the recorded event, not re-roll the dice. This is load-bearing — it's what lets Law 5 survive contact with stochastic agents.
- **ocap is secure but can be a UX nightmare.** Agents and humans need to discover and request authority without drowning in grant prompts. You need a powerbox / capability-broker: a trusted mediator that hands out attenuated capabilities under policy. The difference between "secure" and "usable."
- **Test-gated promotion is only as good as the tests.** A subtly harmful capability can pass a green suite. Defense in depth: even promoted capabilities stay sandboxed by default; outward/irreversible effects keep Morta's unstrippable approval caveat.
- **Merge is easy for text, hard for intent.** CRDTs handle sets and prose. Two agents concurrently restructuring the same plan is a genuine conflict needing an adjudicator (human or referee agent) — and the adjudication is itself a first-class, recorded event.

None fatal. All known shapes with known engineering. Walk in seeing the bodies.

---

## The First Heartbeat

The smallest Decima that is *alive*. Build this and nothing else first:

1. A single process. An append-only signed Weft (SQLite is fine to start).
2. A materializer that folds the Weft into a Weave (in-memory + SQLite cache).
3. The four verbs. An ocap check on `INVOKE`. One executor (shell + code-run in a sandbox).
4. One agent actor running observe→decide→invoke.
5. A bare Shell — even a TUI — that is a projection of the Weave plus a mic.

And **the first capability Decima ships with is the capability to author capabilities.** That's the bootstrap — Nona's first beat.

The moment that loop closes — an agent writes a capability, the Reckoner tests it, an attestation promotes it, and the next agent invokes it — Decima is breathing. From that second on, it is a system that can become more than what you shipped. Everything else — studio, workspace, voice, social, identity — is sediment on a living spine.

You don't build the ocean. You build the first cell that divides.

---

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*
