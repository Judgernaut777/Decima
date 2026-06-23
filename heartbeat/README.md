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

**Scored tree.** Each `task` cell records its worker's own leaf metrics (steps,
denials, latency, status), so `score` folds the whole tree into an organization
outcome — workers, total steps, denials, statuses — without double-counting. A
denied INVOKE or a depth-refused delegation shows up as a `denial`. This is the
first rung toward *learned organization policy*: which topologies completed, how
much they cost, and what got blocked are now durable, foldable signals.

**Evidence-gated promotion (Nona's Reckoner).** `forge` no longer promotes on a
green test alone. It gathers two pieces of evidence — a deterministic sandbox
test **and** a static scan (`reckoner.scan`, a quarantined, network-denied stub
of the NVIDIA SkillSpector contract) — and promotes only if the test passes
*and* the scan is clean. A capability whose behavior under test is benign but
which hides a `curl … | sh` payload passes the test yet is **rejected by the
scan** and stays quarantined. The scan produces evidence, never authority.

**The self-improvement loop, closed.** The cycle runs end to end and is visible
in graph state: Decima is briefed to use a capability it doesn't hold → the gap
is recorded as an `ungranted` task (a `denial` in the score) → **Nona forges**
the capability (test + scan gate) → a re-brief now **spawns a worker that uses
it** → the score moves from a blocked gap to a completed task. Observe a gap,
forge the organ, put it to work — the loop that makes Decima more capable the
longer it runs. (The smoke test shows `rev` go from "not held" to `[rev] part`.)

**Browser capability (split + untrusted pages).** `browser.observe` (read-only,
auto-allowed) and `browser.publish` (an outward effect, **Morta-gated** via
`requires_approval`) are the same `browser` effect but distinct capabilities —
observation can never silently become publication. The observe receipt is marked
`instruction_eligible=false`: page content — even an embedded "ignore your
instructions" injection — is recalled as **data, never obeyed**, because the
brain only acts on the user's utterance. (`say browse <url>`, `say publish: <text>`.)
A stub executor against the existing spine; the full contract is
[`specs/BROWSER_WORKER.md`](../specs/BROWSER_WORKER.md).

**Domain model (types-as-data).** An `ASSERT` now carries an assertion **kind**
(WEFT Protocol §4): `CONTENT` (a Cell version — the default, today's path),
`EDGE` (a typed relation `src → rel → dst`), and `TYPE_DEF` (a type is itself a
Cell — Law 3). The fold (`weave.py`) dispatches on `kind`: edges are folded onto
both endpoints (`Cell.edges_out` / `edges_in`, queried with
`Weave.edges_from` / `edges_to`), and a TYPE_DEF registers the type in
`Weave.types`. So **adding a new type or edge-kind is data** — a `TYPE_DEF` cell
or a `rel` string — never kernel code, which is exactly what lets the eventual
Rust port *read* the model instead of re-hardcoding it. The thin helpers live in
`model.py`: `define_type`, `assert_content`, `assert_edge`. Content is
deliberately free-form (schemas/validation are a later phase).

**Memory / WikiBrain (`memory.py`).** Built on the model. A **claim** is a Cell
(`type=claim`; `proposition`, integer-millionths `confidence`, a `scope`, and the
four separate permissions from Codex's `MEMORY_ARCHITECTURE.md` §5 — *may store*
is the `memory_write_allowed` gate; `recallable`, `citable`, and
`instruction_eligible` are stored on the claim); **evidence** is an EDGE
`claim —supported_by→ source`;
an **entity** link is an EDGE `claim —about→ entity`; **provenance** is the
events that asserted the claim. `remember` writes a claim + its edges, `recall`
returns matching claims **as data** (honoring the `recallable` permission and an
optional `scope` filter — authorization-first, thin), and `why` walks both the
evidence edges and the asserting events. **Recall-vs-instruct** is the same law the browser receipt
obeys: claims from untrusted sources are written `instruction_eligible=false`;
`recall` returns them as data, and the brain never treats a recalled untrusted
claim as an instruction. Retrieval is a pluggable **seam** — the prototype ships
a substring `Retriever`; a real semantic/vector index (Chroma/Milvus,
GraphRAG/RAPTOR) wraps in behind the same interface later, with **no vector
dependency** pulled into the Heartbeat. Contradiction-resolution, freshness
decay, consolidation, and embeddings are deferred. The smoke test exercises all
of this (`DOMAIN MODEL` and `MEMORY / WIKIBRAIN` sections).

**Effects registry + integrating tools (`executor.py`).** Effects are a registry
(`register(effect, handler)`); a new effect kind — a CLI tool, a media op, a data
source — is **one `register` call**, never a kernel edit.
`kernel.integrate_tool(name, handler)` registers an effect *and* grants Decima a
capability to run it, so **integrating a tool (claude-code, codex, anything) is one
call** — and it's then delegable to a worker that runs it as its own sandboxed
principal (the smoke integrates a `codex` tool at runtime and delegates it to a
`Reviewer`). `authorize` still gates who may invoke it; the registry decides only
what it *does*. (`effects` lists the registry.)

**Browser → memory ingestion (`kernel.ingest_observation`).** The capability→memory
path: `browser.observe` produces an untrusted receipt, ingested into memory as a
**claim** whose `instruction_eligible` follows the source (False for the web), linked
`supported_by→` the receipt. The web becomes **provenance-stamped data, never an
instruction** — even an embedded "ignore your instructions" page is recalled as data
with `instruction-eligible: 0`. (`ingest <url>`.)

**Workspace (projections over the Weave — `workspace.py`).** The workspace is **not
new storage** (Law 5: views are derived). The same Cells project into four lenses:
`notes` (document outline of doc-like cells + their edges), `board` (`task` cells by
status), `graph` (claim/entity nodes + edges), and `timeline` (events in causal
order). A single `claim` Cell shows up in `notes` *and* as a node in `graph`, and its
asserting events appear in `timeline` — **one Weave, many lenses, no copies**. This
is the projection model that a real GUI (infinite canvas, block editing, mobile,
CRDT collaboration — all deferred to post-Rust-port) would render. (`view
<notes|board|graph|timeline>`.)

## Shell commands

| command | shows |
|---|---|
| `say <text>` | a turn: Decima decides, allots a capability, acts |
| `forge <name> <upper\|lower\|reverse\|wc> <in> <expect>` | **Nona** authors a capability; promotion is **evidence-gated** — a deterministic test *and* a clean static scan |
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
| `score` | the **organization outcome** folded from the tree — workers, steps, denials, statuses (learned-policy signal) |
| `ingest <url>` | **browser → memory**: observe a URL (untrusted) and store it as a non-instruction claim with provenance |
| `effects` | the registered effect handlers (the executor registry) |
| `view <notes\|board\|graph\|timeline>` | a **projection of the Weave** — the same cells as a document / board / knowledge-graph / transcript |
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
