# Decima — Heartbeat

The smallest Decima that is *alive*: an append-only signed log, the four verbs,
a fold that materializes state, object-capability authority, one agent loop, and
Nona's self-extension bootstrap — the capability to author capabilities.

Pure Python 3 standard library. **No dependencies. No network.** This is a
prototype to prove the Five Laws hold by running; the kernel ports to Rust once
the laws are proven in motion.

## Run

```bash
cd /tmp/decima/heartbeat
python3 smoke.py            # scripted tour: watch all five laws hold
python3 run.py --fresh      # interactive shell (the "one program"), from genesis
python3 run.py              # warm start, reusing weft.db
```

## Shell commands

| command | shows |
|---|---|
| `say <text>` | a turn: Decima decides, allots a capability, acts |
| `forge <name> <upper\|lower\|reverse\|wc> <in> <expect>` | **Nona** authors + test-gates + promotes a new capability |
| `caps` | the authority surface (capabilities + caveats + quarantine) |
| `log` | the Weft — every event, with its authorizing capability |
| `cells` | the materialized Weave (folded state) |
| `why <cell-prefix>` | **Law 4**: provenance walk of how a cell came to be |
| `fold <seq>` | **Law 5**: rebuild the world as of event `<seq>` — time travel |
| `revoke <cap-prefix>` | **Morta**: RETRACT a capability; next INVOKE fails closed |
| `attack` | **Law 2**: a zero-authority sandbox agent is structurally denied |
| `whoami` | the principals in this kernel |

## The Five Laws, and where each lives

1. **Nothing happens off the Log** → every change goes through `weft.append`. `weft.py`
2. **No ambient authority** → `capability.authorize` gates every `INVOKE`; authority only attenuates downhill. `capability.py`, `kernel.invoke`
3. **Everything is a Cell, including the system** → capabilities and agents are cells; `forge` writes new capabilities. `weave.py`, `reckoner.py`
4. **Identity is content + cause** → `content_id` (blake2b) + per-event provenance + signature verification on read. `hashing.py`, `weft.events`
5. **State is a fold; views are projections** → `Weave.fold(weft, upto_seq)` *is* time-travel, undo, and reproducibility. `weave.py`

## The trinity

- **Decima** (the allotter) — the orchestrator agent; apportions capability to the work. `kernel.py`
- **Nona** (the spinner) — the Reckoner; forges, verifies, and promotes new capabilities. `reckoner.py`
- **Morta** (the cutter) — revocation (`RETRACT`), and the caveat gates (`requires_approval`, `sandbox_only`). `capability.py`, `kernel.revoke`

## Known seams (deliberate, marked in code)

- **Signing** is a dev-grade symmetric HMAC stand-in for ed25519 (`crypto.py`).
- **The brain** is a deterministic rule stub; the LLM plugs in at `agent.Brain.decide`.
- **The executor** is a tiny safe allowlist; real sandboxing (landlock/bubblewrap) slots behind the same `(effect, args) -> result` contract (`executor.py`).
- **Budget** is an in-memory ledger; production folds spend into the Weft.
- **The Weft is linear** (single process); the `parents` field is already a DAG, ready for merge/CRDT.

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*
