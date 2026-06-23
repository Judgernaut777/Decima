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

**Real reasoning (optional):** export `ANTHROPIC_API_KEY` and `say` is decided by
`claude-opus-4-8` instead of pattern matching — Decima reasons over your held
capabilities and picks one (or replies). Force the offline rule brain with
`DECIMA_BRAIN=rules`; pick a model with `DECIMA_BRAIN_MODEL`. The model only
*proposes* — `authorize()` still gates every INVOKE, so the brain can't exceed
its envelope no matter what it returns.

**Delegation by reasoning:** the brain can also choose to **delegate** — Decima
spawns a worker, grants it ONE attenuated capability, hands it a brief, and the
worker then reasons over *its* narrow envelope and acts (its INVOKE signed by its
own key, gated by `authorize()`). With the model brain this happens from natural
language; offline, trigger it explicitly:
`say delegate shell as Clock: date` → Decima spawns *Clock* with an attenuated
`shell` grant and the brief "date", and Clock runs it.

**Fan-out, depth, and the task graph.** A delegate decision is a *list* of
briefs — Decima can spawn several workers from one request (offline: separate
them with `;`). A worker can itself delegate, bounded by `MAX_DELEGATION_DEPTH`
(Decima → worker → sub-worker; the third level is refused). **Every briefing is
recorded as a typed `task` cell** linking delegator → worker → grant → result →
parent task, so the whole organization tree is a fold over the Weave — run
`tasks` to see it. Try:
`say delegate shell as Clock: date ; echo as Echoer: echo hi` (fan-out), or
`say delegate shell as Foreman: delegate shell as Runner: date` (depth).

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
| `delegate` | **Decima allots a downhill, signed grant** to a subagent with its own key; shows the approval gate, budget caveat, the impostor refusal, and downhill clamping |
| `replay` | **AuthorizationProof anti-replay** — a captured proof fails when args or the causal frontier change |
| `tasks` | the **delegation tree** — who briefed whom, with what capability, and the outcome (folded from `task` cells) |
| `whoami` | the principals in this kernel |

## Capability possession (per [`specs/`](../specs/) reconciliation)

Authority is **not** id-possession — a Cell id is a public content hash. Authority
is a *signed grant to a principal*, and every `INVOKE` is signed by the acting
agent's own key. `authorize` (`capability.py`) checks, in order: the signer is the
acting agent → the grant is in its envelope → the grant names that principal as
grantee → the delegation path is downhill and granter-held → the caveats. So an
impostor that copies a public grant id is refused (`grant issued to a different
principal`), and a subagent cannot re-widen what it sub-delegates. Run `delegate`
to watch all of it. Every INVOKE also carries an **AuthorizationProof** whose
`holder_sig` is bound to the exact request (verb + body + nonce + causal
frontier), so a captured proof can't be replayed against a different request —
run `replay` to watch that fail closed. **Exact replay** also holds: the fold (`weave.py`) never
re-executes effects — it replays their recorded receipt cells.

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

- **Signing** is a dev-grade symmetric HMAC stand-in for ed25519, keyed by a
  persisted master seed (`crypto.py`, `*.keys` — gitignored, never commit it).
  Production: asymmetric ed25519 keypairs in an OS keystore.
- **The brain** has two implementations (`agent.py`): `RuleBrain` (deterministic,
  offline) and `ModelBrain` (a real `claude-opus-4-8` call via stdlib `urllib` —
  no SDK, to keep the zero-dependency property). `make_brain()` uses the model
  brain when `ANTHROPIC_API_KEY` is set, else rules; either way `authorize()`
  gates the decision, so the model has no more authority than the stub.
- **The executor** is a tiny safe allowlist; real sandboxing (landlock/bubblewrap) slots behind the same `(effect, args) -> result` contract (`executor.py`).
- **Budget** is an in-memory ledger; production folds spend into the Weft.
- **The Weft is linear** (single process); the `parents` field is already a DAG, ready for merge/CRDT.

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*
