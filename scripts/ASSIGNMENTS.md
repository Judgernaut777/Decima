# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle (10).** Tasks/lanes in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); rationale in
[`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md) (D3.2, D3.3).

**One core lane this cycle: LOOP1 owns `kernel.py`.** Two rules:
1. Only LOOP1 touches core (`weave.py`/`weft.py`/`kernel.py`/`executor.py`); others call the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`.** Own a free `NN`
   (110/112/114 assigned). See `heartbeat/checks/README.md`.

Bootstrap any lane first: `scripts/kickoff.sh <dir> <branch>`.

---

## Instance 1 — Claude · Secrets broker  (worktree `~/decima-claude-cred`)

`git worktree add ~/decima-claude-cred claude/cred1-secrets`.

**Task:** CRED1. **Owns:** `heartbeat/decima/secrets.py` (new), `heartbeat/checks/110_secrets.py` (new).
**Must not touch:** any core file, `powerbox.py`, `inference.py`, `smoke.py`.

```text
You are a Claude secrets-broker instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md
(brief CRED1), specs/CAPABILITY_MAP.md D3.2, decima/powerbox.py (E1 — it explicitly defers the
secrets layer), specs/MORTA_CAPABILITIES.md, and heartbeat/checks/README.md first.

Task CRED1 — branch claude/cred1-secrets — the credential layer powerbox defers:
  New module heartbeat/decima/secrets.py: a SecretsBroker that (1) store(name, secret, alias=None)
  holds an OPAQUE credential — the raw value lives in the broker's in-memory store (a stand-in for an
  HSM/enclave) and is NEVER written to the Weft in clear (record a reference/digest + metadata);
  (2) issues a scoped, attenuable, REVOCABLE handle (a capability) bound to principal + purpose;
  (3) use(handle, ...) performs the credentialed action ON THE HOLDER'S BEHALF without disclosing the
  secret (dispense-don't-disclose); (4) records per-service privacy email aliases as metadata;
  (5) revoke(handle) → the handle fails closed. Store/issue/use/revoke audited on the Weft. Build on
  decima/capability (attenuate/authorize); do NOT edit powerbox.py. Demo in a NEW file
  heartbeat/checks/110_secrets.py exposing run(k, line): store → scoped handle (raw secret never
  returned / never on the Weft); use works (audited); attenuate downhill; revoke → fails closed; a
  privacy alias recorded. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-cred claude/cred1-secrets
Stay in secrets.py + checks/110. Public capability/weave/kernel API; no core edit, no powerbox.py
edit, no smoke.py edit. Keep the oracle green (cd heartbeat && python3 smoke.py → "alive ✓", exit 0).
Commit small; git pull --rebase; push; fast-forward to main when green.
```

---

## Instance 2 — Claude · Self-hosted / private inference  (worktree `~/decima-claude-inf`)

`git worktree add ~/decima-claude-inf claude/inf1-inference`.

**Task:** INF1. **Owns:** `heartbeat/decima/inference.py` (new), `heartbeat/checks/112_inference.py` (new).
**Must not touch:** any core file, `router.py` (AR1's), `secrets.py`, `smoke.py`.

```text
You are a Claude private-inference instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md
(brief INF1), specs/CAPABILITY_MAP.md D3.3, decima/router.py (Engine/default_engines/Router engines
seam), specs/SANDBOX.md (SB1), and heartbeat/checks/README.md first.

Task INF1 — branch claude/inf1-inference — rent-a-GPU / self-host, data never leaves:
  New module heartbeat/decima/inference.py: a LocalInferenceEngine (on-host stub) whose capability
  carries an SB1 sandbox profile with network=False, and a RemoteInferenceEngine (network allowed),
  behind one Engine-compatible contract (see router.py's Engine/default_engines). private_infer(k,
  prompt, sensitive=True) routes sensitive prompts to the LOCAL engine and PROVES no egress — a
  network effect attempted by the local engine is REFUSED by the executor's sandbox (SB1).
  Non-sensitive prompts may use the remote engine. Plug both into AR1's Router via its PUBLIC engines
  seam (Router(engines=...) / default_engines) — do NOT edit router.py. Demo in a NEW file
  heartbeat/checks/112_inference.py exposing run(k, line): a sensitive prompt runs local and a network
  attempt by it is sandbox-refused (no egress); a non-sensitive prompt may use remote; engines plug
  into the router via its public seam. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-inf claude/inf1-inference
Stay in inference.py + checks/112. Public router/executor/kernel API; no core edit, no router.py
edit, no smoke.py edit. Keep the oracle green. Commit small; git pull --rebase; push; fast-forward
when green.
```

---

## Instance 3 — kernel · Live governance gate  (clone `~/decima-claude`)

**Task:** LOOP1 (core). **Owns:** `heartbeat/decima/kernel.py`, `heartbeat/checks/114_live_governance.py` (new).
**Must not touch:** `secrets.py`, `inference.py`, other `checks/` files.

```text
You are the Claude kernel instance for Decima, in ~/decima-claude. Read docs/BACKLOG.md (brief LOOP1),
decima/memory.py (governance_check + remember_governance, B4), the existing org_policy gate in
decima/kernel.py (_delegate), and heartbeat/checks/README.md first.

Task LOOP1 — branch claude/loop1-live-governance — make governance live:
  In kernel._delegate, BEFORE spawning a worker (right next to the existing org_policy gate), call
  memory.governance_check on the delegation's objective (and/or capability). If the verdict is deny,
  refuse the delegation at delegate-time — record a `refused` task carrying the governance verdict's
  reason + prior evidence (mirror the org_policy refusal path) and skip. A non-banned delegation
  proceeds unchanged; with empty governance the gate is inert (allow). Demo in a NEW file
  heartbeat/checks/114_live_governance.py exposing run(k, line): record a banned_action
  (memory.remember_governance); a delegation whose objective/capability matches is refused at
  delegate-time with the rule + prior evidence cited; an unbanned delegation proceeds; the refusal is
  on the Weft. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude claude/loop1-live-governance
You OWN kernel.py this cycle. Demo in checks/114; only relevant wording in smoke.py may change if
needed. Keep the oracle green. Commit small; git pull --rebase; push; fast-forward to main when green.
```

---

## Notes
- **Land order:** LOOP1 (core) is independent of CRED1/INF1 (new modules); land in any order, but a
  quick re-verify on rebase is cleanest.
- **CRED1 pairs with PAY1** (the payment method becomes a brokered credential); **INF1 builds on
  SB1 + AR1** but edits neither.
- **Pushing:** SSH deploy keys push code (no token); fast-forward small green changes to `main`.
- **Next:** wire DISP1 into the live inbound loop; real sandbox enforcement; real engines; the
  Constellation GUI — see `docs/BACKLOG.md`.
