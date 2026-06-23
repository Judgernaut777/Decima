# Decima

**An agent-native operating system.** One program that connects you to your agents — and becomes more capable the longer it runs.

> Kernel: **LOOM**. The whole OS is four verbs over one append-only log. State is a fold. Authority is a held object. The system is made of the same stuff as your data, so it can rewrite itself with the same tools it uses to edit your notes. Decima does not have features — it *grows* them.

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

## Layout

| path | what |
|---|---|
| [`KERNEL.md`](KERNEL.md) | the kernel design — the canonical doc |
| [`heartbeat/`](heartbeat/) | a **running** pure-stdlib prototype (no deps): Weft, fold, ocap, the agent loop, Nona's self-extension bootstrap |
| [`specs/`](specs/) | formal protocol specs (Weft, fold lifecycle, Nona, Morta/capabilities, donor matrix) |

## Run the Heartbeat

```bash
cd heartbeat
python3 smoke.py          # guided tour — watch all five laws hold
python3 run.py --fresh    # the interactive shell, from genesis
```

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*
