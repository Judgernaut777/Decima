# Decima — session handoff (post Batch S / 4th-quality re-audit)

_Updated 2026-07-07. Repo `~/decima-claude`, canonical `Judgernaut777/Decima` main._

## 4th-quality verdict (run `wf_2020686f-b61` / `reaudit3.js`, resumed clean — 7 agents, 0 errors)
**NO-GO on P6. `roadmap_green=false, p6_ready=false`.** Batch S is genuine (all six flagged seams verified closed with load-bearing production callers). P2 + P4 clean green. Oracle observed green twice (216 checks, exit 0); one uncorroborated exit-1 near `checks/270_api.py` needs a single `python3 -u smoke.py` unbuffered re-run to retire. **Two independent code-gap blockers (neither is an operator gate):**
- **(1) P3 red at its heart** — `candidate.py:58-70` `model_codegen` raises `CodegenUnavailable` UNCONDITIONALLY (the live post is a comment, not code — verified directly), defaults to a keyless brain, and `discovery.bind_default_codegen` has zero production callers. A fully keyed, grant-approved boot still cannot live-forge. "Green modulo the operator key" is FALSE for the self-extension loop that P3 IS.
- **(2) SWEEP fails its own rule at scale** — 110/179 modules check-proven with no production caller, incl. named roadmap items: `sync.py`/merkle/gossip/snapshot/vault (P1 channel), `terminal.py`/`session.py` (P5 terminals-as-citizens), `agent.present()` (P1's "the ONLY door" chokepoint, zero callers). New recurrences one level up: `builtin_manifests.register_builtins` never called → production discovery catalog is EMPTY so `use` never fires; `k.live_engines` transports have ZERO consumers (do_flip is doctor decoration, ~35 adapters non-invocable); `serve_stdio` launcher-less; `kernel.py:553` "approve to activate" submits no inbox item; `context_fold` never wired into ModelBrain → `self.messages` grows unbounded on the live path.

Legitimately EXCLUDED from the gate: `ANTHROPIC_API_KEY` + human egress approval (fail-closed by design); real whisper.cpp/Piper voice (deferred-by-design behind the voice contract). Nothing else named above qualifies.

**Path to green: ~~Batch T~~ (DONE) → Batch U → gate check → Batch D.**

## Batch T — LANDED (Cycle 66, pushed)
5 disjoint Fable-5 lanes, 5/5 APPROVE (mutation_caught + reproduced_green), zero core edits. Combined oracle **216 → 221 green**, exit 0 (cross-lane integration verified, not just per-lane). Harness: `/home/mini/.claude/jobs/15fb634f/tmp/batchT.js`, run `wf_b4b33632-5e7`.
- **livecodegen** (candidate.py+golive.py, 494): model_codegen posts through the egress-bound brain's gated _post (DATA, fail-closed unarmed); golive.boot arms it (register_builtins + bind_default_codegen over the live brain); activate_engine installs the engine consumer (k.invoke drives the engine fn over its registered wire-gated transport). **P3 red closed.**
- **catalog-activation** (discovery.py+inbox.py, 495): discover() submits use-suggestion → Morta-gated ApprovalInbox item → approve() installs via kernel.integrate_tool (no core edit; kernel.say inherits it).
- **surface** (shell.py, 496): `engine <name> <op> <json>` via kernel.invoke + `sync <peer>`/`listen`/`id` over SecureChannel.
- **contextfold** (agent.py, 497): context_fold.fold wired into ModelBrain.decide — outbound window bounded (pure Law-5 projection; record untouched).
- **presentwire** (research.py+mailpoll.py, 498): research synthesis + received mail routed through agent.present()/admit_engine_output — P1's "only door" gets real callers.
- Non-blocking reviewer notes (deferred, not defects): process-global registries (mirror pre-existing bind_default patterns); in-memory activation enactor + armed mail polls don't survive restart → **Batch U rehydration** territory.

## Next: Batch U (Cycle 67) — mechanical launchers + the scope ruling. See lane list below.

### Batch T (Cycle 66) — running-path correctness [DONE — see above]. All Fable.
- **livecodegen** (`candidate.py`+`golive.py`+`discovery.py`): wire `model_codegen` to the golive-bound ModelBrain's gated `_post` (redaction+spend already ride it), return source as DATA, fail-closed when unarmed (injected-transport testable); `golive.boot` calls `discovery.bind_default_codegen`. THE P3 red.
- **catalog-activation** (`golive.py`/`kernel.py`+`discovery.py`+`inbox.py`): `register_builtins` at boot (catalog non-empty → `use` can fire); build suggestion→ApprovalInbox→activation installer for `kernel.py:553`.
- **enginedispatch** (`golive.py`+`shell.py`): name→module-entry-fn→gated-transport dispatch so a flipped engine is actually callable; unlocks the ~35 adapters. (do_flip currently dead-ends in doctor decoration.)
- **presentwire** (`agent.py`+`research.py`/`mailpoll.py`): route engine/research/mail output through `present()` before any re-injection into `decide()` — closes P1's callerless chokepoint.
- **contextfold** (`agent.py`): wire `context_fold` (Law-5) into ModelBrain history so `self.messages` is bounded on the live path.
- **syncverb** (`shell.py`+`sync.py`/`vault.py`): `sync <peer>` verb over `sync.serve_once`/`sync_socket` — pulls the whole sync/merkle/gossip/snapshot/vault stack onto the running path.

### Batch U (Cycle 67) — mechanical launchers + the scope ruling. Mostly Sonnet.
- **mcplauncher** (`mcp_server.py`+`run.py`): `python3 run.py --mcp-serve` binding a consumer + starting the loop. [Sonnet]
- **apiserve** (`api.py`+`run.py`/`shell.py`): serving hook for the inbound RPC surface (proven handler, no transport driver). [Sonnet]
- **processeffect** (`shell.py`+`process_effect.py`): `wrap <name> <argv...>` → `process_effect`. [Sonnet]
- **forgesurface** (`kernel.py:547-556`): surface forged/refused outcomes as transcript lines + discovery Cell (today only `action=='use'` is handled — mid-turn forge is silent, PromotionBlocked swallowed). [Sonnet]
- **terminalwire** (`citizens.py`+`terminal.py`/`session.py`): compose terminal/session into the citizens admit path. [Fable]
- **packscope** (`docs/BACKLOG.md`+roadmap): POLICY — after T makes the 35 engine adapters reachable, explicitly rule the remainder (49 domain/record packs, security pack, powerbox/cli_worker/shorthand/inspector/manifest_pack/inference) as WIRED or library-tier/harness-only, so the sweep rule is stated truthfully before P6 freezes scope. [Sonnet]

### Gate check → Batch D (P6 on-ramp, ONLY after T+U land + fresh sweep passes + one clean `python3 -u smoke.py`)
- **conformance** (new `conformance.py`+fixtures): golden-vector suite freezing observable behavior as the Rust port's oracle. [Fable]
- **spawnaudit** (`isolation.py`): harden `assert_no_raw_spawn` beyond AST name-matching (aliased imports, getattr, importlib). [Fable]
- **oraclefreeze** (`smoke.py`+checks): pin the check set in a manifest (count+filenames+digests); formally retire the 270_api exit-1. [Sonnet]

---
_Below: prior 3rd-re-audit notes, superseded by the verdict above but kept for the per-gap detail and harness recipe._

# (archived) post Batch S / 3rd re-audit

## Where we are
- **main = `a7299b3` (Cycle 65, Batch S), 216 checks green** (`cd heartbeat && python3 smoke.py` → `heartbeat: alive. ✓`, exit 0). Pushed. Clean tree.
- Standing directive: **"use ultracode for all of what's left"** — autonomous model-routed Workflow batches (Fable 5 = correctness/security; Sonnet 5 = mechanical), each lane adversarially mutation-reviewed by a Fable 5 skeptic, integrated only on APPROVE + full oracle green. Roadmap-green declaration + P6 kickoff are gated on a **passing re-audit**, not self-report.
- 6-phase roadmap at the TOP of `docs/BACKLOG.md` (~lines 8-19). P6 = the single Rust port, LAST, gated on "the reference being stable with this roadmap green."

## The 3rd re-audit verdict (run `wf_2020686f-b61` / `reaudit3.js`)
Partial — **P1 green, P2 green, SWEEP ran and is decisive; P3/P4/P5 + synthesizer all ERRORED on a hard session cap ("resets 3am Asia/Shanghai")**. The SWEEP alone rules the roadmap **NOT green, P6 NOT ready.**

SWEEP facts: 180 decima modules — 69 reachable from run.py, 3 legit harness-only (liveworld, cli_worker, powerbox), 108 check-only. Batch S is genuine (all six seams verified on the running path: do_view→workspace.render shell.py:1019, do_research→research.research :878, mail arm→mailpoll.schedule_poll :321 + beat fires :650, do_forge→forge.forge :789, do_mcpserve→mcp_server.handle :913, discover() defaults forge at discovery.py:449-451 inherited by kernel.py:544 + agent.py:317). **But six REAL code gaps remain** (not operator-gated — actual missing wiring), the same recurring pattern one layer deeper:

### Green-blocking gaps (Batch T)
1. **[mechanical] builtin catalog never registered.** `builtin_manifests.register_builtins(k)` has ZERO production callers → live `discover()` searches an EMPTY registry; the 31 bundled engines are unfindable. Fix: call it at `golive.boot` (or kernel genesis). Mutation check: a live boot's `discover('charge a credit card')` → action `use` → `stripe_rail`. WHERE: `golive.py` (boot) + `builtin_manifests.py:160`.
2. **[correctness-heavy] the forge-real loop is IMPOSSIBLE in production.** `candidate.model_codegen` (candidate.py:58-70) UNCONDITIONALLY raises `CodegenUnavailable` — even the egress-bound branch (line 70). No production path binds any codegen. So P3 self-extension can NEVER run live even fully credentialed — a code gap, not an operator gate. Fix: implement the LIVE branch (post the intent through the egress-bound ModelBrain transport, return source as DATA, fail-closed when unarmed — testable offline via an injected transport stub, the wrapped-engine idiom), AND arm it: `golive.bind_brain` should `discovery.bind_default_codegen(...)` / set `shell.forge_codegen` when the brain goes live. This flips gap 2 from "code gap" → "operator-gated" (which is green). WHERE: `candidate.py:58-70` + `golive.py` (bind_brain) + `shell.py:84`.
3. **[mechanical] mcp_server.serve_stdio has no launcher.** Only caller is checks/492. No `__main__`, no run.py flag, no shell start → Decima-as-MCP-server can't actually be consumed externally. Fix: `python3 run.py --mcp-serve` flag binding a consumer and running the loop over real stdio (NOT a REPL shell verb — an infinite stdio loop can't live inside the prompt). WHERE: `run.py` + `mcp_server.py:629`.
4. **[correctness-heavy] armed mail polls don't survive restart.** Each occurrence's handler is an in-process closure (`mailpoll.py:127-161`); after restart the durable job Cell exists but its handler + self-rescheduling successor are gone → the "always-on" poll chain silently dies (P4 thinness). The current check 482 never leaves one process so the module-global executor registry masks it. Fix: a `mailpoll.rearm(k)` fold that rebuilds handlers + gated transports from the Weft at boot (`run.py` resume_loop / `golive.boot`), plus a cross-process check. WHERE: `mailpoll.py` + `run.py`.
5. **[correctness-heavy] flipped engines are decorative.** `flip stripe_rail api.stripe.com` records a transport in `k.live_engines[name]['transport']` (golive.py:473-474) that NO code ever hands to `stripe_rail.charge` → a fully-credentialed operator still cannot drive any of the 31 wrapped engines. Fix: `activate_engine` installs a real invokable handler at flip time (register as an executor effect / `k.integrate_tool` so it rides the standard authorize+Morta invoke path), plus an `engine <name> <op> <json>` shell verb to drive it. WHERE: `golive.py` (activate_engine) + `shell.py`.
6. **[mechanical] sync has no operator surface.** `sync.py` (mutual-auth encrypted channel, the P1 CHANNEL work) + merkle/gossip/vault are all check-only. Fix: a `sync <host:port>` / `sync listen` shell verb over the proven transport. (Lower priority — P1 was ruled green in all 3 re-audits because a single-shell reference has no 2nd instance; this is polish that stabilizes the sweep.) WHERE: `shell.py` over `sync.py`.

### Not green-blocking, but do it to stabilize the sweep
7. **[mechanical/doc] Disposition the 77 remaining check-only libraries** (52 personal-OS breadth slabs: accounts/audit/health/journal/… + 25 infra libs: api/webhook/terminal/search/inspector/detection/triage/…). These are deliberate breadth (the "grows features" thesis), NOT roadmap P1-P5 named sub-items. Rather than 77 new verbs, add a **"library-by-design, wired at the Rust port / via the generic engine path"** designation section to `docs/BACKLOG.md` so the sweep gets a stable `harness-ok` allowlist instead of rediscovering them every audit. Optionally wire the handful the roadmap clearly wants live (search, terminal, files, detection/triage) as operator verbs. Do this as a doc lane, not a fleet.

## Next actions (in order)
1. **(optional) Resume the 3rd re-audit for the full P3/P4/P5 + synth verdict.** `Workflow({scriptPath: '/home/mini/.claude/jobs/15fb634f/tmp/reaudit3.js', resumeFromRunId: 'wf_2020686f-b61'})` AFTER the 3am Asia/Shanghai cap resets — P1/P2/SWEEP replay from cache, only the 4 errored agents re-run live. The SWEEP is already decisive (NOT green), so this is for the ordered synthesis + any P3/P4/P5 thinness the readers would add — nice-to-have, not required to proceed.
2. **Build & launch Batch T — the real-reachability batch** closing gaps 1-6. Recommended DISJOINT decomposition (watch the shell.py collision + cross-lane deps):
   - **Lane livepath** (`golive.py` + `candidate.py`): register_builtins at boot; implement `candidate.model_codegen` live branch (injected-transport-testable, fail-closed unarmed); `bind_brain` arms codegen; `activate_engine` installs a real invokable handler for the flipped engine. **Fable.** Closes 1, 2, and the install-half of 5. *(golive.py + candidate.py, both non-core.)*
   - **Lane surface** (`shell.py`): `engine <name> <op> <json>` verb driving the standard kernel.invoke on a flipped engine (pairs with livepath's install — but drive it via the EXISTING invoke path so it doesn't hard-depend on a new function), `sync`/`sync listen` verb over sync.py. **Fable.** Closes invoke-half of 5 + gap 6. *(shell.py only.)*
   - **Lane boot** (`run.py` + `mailpoll.py`): `--mcp-serve` flag launching serve_stdio; `mailpoll.rearm(k)` fold + boot rehydration of armed polls. **Fable** (correctness for rehydrate). Closes 3 + 4. *(run.py + mailpoll.py.)*
   - **CROSS-DEP CAUTION:** the `engine` verb (surface lane) needs livepath's `activate_engine` install to exist to test a real flipped engine. If the surface lane's check can't drive a real flipped engine off the Cycle-65 base, **sequence it: land livepath FIRST (Batch T1), then build surface+boot off the updated base (Batch T2).** Sequencing is the safe call for these final correctness-critical gaps. Use check numbers 494+.
   - Same harness as batchS.js (HOUSE rules, IMPL_SCHEMA/REVIEW_SCHEMA, pipeline impl→adversarial-review). Copy `/home/mini/.claude/jobs/15fb634f/tmp/batchS.js`, swap `meta`+`LANES`. **VALIDATE before launch:** `node --check` AND check for stray backticks inside `spec:` template literals AND `\'` inside single-quoted `seams:` strings (the Workflow parser is stricter than node).
3. **Disposition gap 7** as a BACKLOG designation doc lane (stabilizes the sweep).
4. **4th re-audit** (resume/rerun reaudit pattern) → if the sweep is clean (every module reached, harness-ok, or explicitly designated) and P1-P5 green modulo the operator API key, declare roadmap green.
5. **Then P6 opens with Batch D — the Rust-port on-ramp:** conformance golden-vectors (new `conformance.py` + fixtures freezing observable behavior as the port's oracle, Fable); spawn-audit-hardening (`isolation.py` assert_no_raw_spawn beyond AST name-matching, Fable); oracle-freeze (smoke.py check-set manifest, Sonnet).

## Integration recipe (per landed batch)
Extract impl sources from the workflow journal → write module+check to repo → `cd heartbeat && python3 smoke.py` (must end `heartbeat: alive. ✓` exit 0, all checks green) → append a house-style Cycle NN entry to `docs/BACKLOG.md` (before the `**Tooling — ✅**` line) → `git add -A && commit` (Co-Authored-By + Claude-Session trailers) → `git pull --rebase origin main` → `git push origin HEAD:main`. Concurrent sessions can advance main between calls — always fetch/rebase, re-Read before editing.

## Gotchas (carry forward)
- Session caps ("resets Npm/3am Asia/Shanghai") are HARD account limits that kill agents mid-run — distinct from transient 529/"connection closed". **resumeFromRunId replays "completed" agents from cache and re-runs ERRORED ones live** — so cap-killed (errored) agents resume cleanly; garbage-but-"completed" results must be re-run FRESH (trim to a new script).
- Sonnet degrades under 529 overload (returns literal 'x'/'a'/'b') — re-route flaky mechanical lanes to Fable.
- Workflow parser rejects stray backticks / `\'` inside spec/seams strings even when `node --check` passes.
- `executor._REGISTRY` is module-global across checks in one smoke run — hermetic checks must register a uniquely-named effect, never reuse `echo`.
- Never edit core-serialized files in a fleet lane: `weave.py`, `weft.py`, `kernel.py`, `executor.py` (≤1 core lane per batch, and prefer fixing at a non-core seam — e.g. Batch S fixed forge at discovery.py's default instead of editing kernel.py/agent.py call sites).
- Live flip stays OPERATOR-ONLY by design (`GOLIVE.md`): the USER exports ANTHROPIC_API_KEY → run.py → `grant api.anthropic.com` → `approve` in inbox → say. Only the user can do this; it is a legitimate gate, not a code gap.
