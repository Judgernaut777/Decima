# Decima

*A true AI OS: an agent-native operating system where state is a fold, authority is a held object, and the system grows its own capabilities. Kernel: LOOM — spun by Nona, allotted by Decima, cut by Morta.*

<!-- ^ Canonical one-line tagline. Keep in sync with the GitHub repository "About" description. -->

**One program you log into** that connects you to your agents, your knowledge, and your work — and becomes more capable the longer it runs. **Decima does not have features — it *grows* them.**

> Kernel: **LOOM**. The whole OS is four verbs over one append-only log. State is a fold. Authority is a held object. The system is made of the same stuff as your data, so it can rewrite itself with the same tools it uses to edit your notes.

**Start with [`VISION.md`](VISION.md)** — the **canonical source of truth**: what Decima is, what it's for, how far it reaches, the trust model, and the build philosophy. For the *how* — the laws, primitives, and worked traces — see [`KERNEL.md`](KERNEL.md).

## The trinity

Named for the Roman Fates (Parcae), with function mapped to myth:

- **Nona** (Clotho, the spinner) — the self-extension engine: forges, verifies, and promotes new capabilities.
- **Decima** (Lachesis, the allotter) — the orchestrator: apportions capability, budget, model, and memory to the work.
- **Morta** (Atropos, the cutter) — revocation, termination, and the gates on irreversible effects.

## The Five Laws

1. Nothing happens off the Log (the *Weft*).
2. No ambient authority — power is a possessable object (object-capability model).
3. Everything is a Cell, including the system itself (homoiconic).
4. Identity is content + cause (content-addressed + provenance).
5. State is a fold; everything you see is a projection.

## The Heartbeat — a running prototype

A pure-stdlib Python prototype — **no dependencies, no network** — that proves the laws by *running*. It is **not the product**: it is the smallest Decima that is alive, and the executable reference + conformance oracle the eventual Rust port must pass. Today it already breathes:

- a signed, append-only **Weft** → the **four verbs** → a **fold** with time-travel;
- object-capability authority with **signed possession + anti-replay** — a public Cell id is not a bearer token;
- a real reasoning **brain** (optional `claude-opus-4-8` call) that only ever *proposes* — `authorize` gates every effect, so a prompt-injected model has no more power than the offline rule stub;
- multi-agent **delegation**: attenuated downhill grants, fan-out, bounded depth, and a scored org tree folded from the Weave;
- **Nona's** evidence-gated self-extension — forge → deterministic test **+** static scan → promote → use;
- a **types-as-data** domain model (`CONTENT` / `EDGE` / `TYPE_DEF`) and **memory / WikiBrain** with the recall-vs-instruct law;
- an extensible **effect registry** (integrate any CLI tool — claude-code, codex — in one call) and **browser → memory** ingestion (the web enters as untrusted, provenance-stamped data, never an instruction);
- the **workspace** as four projections of one graph — notes / board / knowledge-graph / timeline;
- a **conformance oracle**: `smoke.py` asserts the [`FOLD §11`](specs/FOLD_AND_LIFECYCLE.md) invariants (6 hold, 1 partial, 1 deferred) and fails loud on regression.

It is deliberately a **profile** — smaller than the durable protocol. [`heartbeat/PROFILE.md`](heartbeat/PROFILE.md) pins exactly what is built versus deferred; [`specs/`](specs/) is the target contract.

## Layout

| path | what |
|---|---|
| [`VISION.md`](VISION.md) | the vision — what Decima is, what it's for, and how far it reaches (**start here**) |
| [`KERNEL.md`](KERNEL.md) | the kernel design — laws, primitives, and worked traces |
| [`specs/`](specs/) | formal protocol specs — Weft, fold lifecycle, Nona, Morta/capabilities, memory, browser, donor matrix |
| [`heartbeat/`](heartbeat/) | the **running** pure-stdlib prototype (see [`heartbeat/README.md`](heartbeat/README.md)) |
| [`heartbeat/PROFILE.md`](heartbeat/PROFILE.md) | the prototype's profile vs. the durable protocol — **what's built vs. deferred** |

The Heartbeat is intentionally smaller than the durable protocol. Before persistent or shared Wefts are introduced, reconcile the prototype with the target guarantees in [`specs/README.md`](specs/README.md) — especially signed capability-possession proofs, effect receipts, canonical encoding, and replay rules.

## Run the Heartbeat

```bash
cd heartbeat
python3 smoke.py          # guided tour — all five laws + the FOLD §11 invariants
python3 run.py --fresh    # the interactive shell (the "one program"), from genesis
```

Optional: export `ANTHROPIC_API_KEY` to let `claude-opus-4-8` decide each turn instead of the offline rule brain — either way `authorize()` gates every effect.

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*
