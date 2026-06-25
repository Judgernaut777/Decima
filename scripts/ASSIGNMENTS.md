# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle (9).** Tasks/lanes in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); rationale in
[`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md) (D3.4, D4.2).

**One core lane this cycle: IFB1 owns `weave.py`.** Two rules:
1. Only IFB1 touches core (`weave.py`/`weft.py`/`kernel.py`/`executor.py`); others call the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`.** Own a free `NN`
   (104/106/108 assigned). See `heartbeat/checks/README.md`.

Bootstrap any lane first: `scripts/kickoff.sh <dir> <branch>`.

---

## Instance 1 — Claude · Disposition routing (intake → action)  (worktree `~/decima-claude-disp`)

`git worktree add ~/decima-claude-disp claude/disp1-disposition`.

**Task:** DISP1. **Owns:** `heartbeat/decima/disposition.py` (new), `heartbeat/checks/104_disposition.py` (new).
**Must not touch:** any core file, `payments.py`, `weave.py`, `smoke.py`.

```text
You are a Claude disposition instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md
(brief DISP1), specs/CAPABILITY_MAP.md D4.2, specs/MEMORY_ARCHITECTURE.md, decima/orientation.py
(OR1), and heartbeat/checks/README.md first.

Task DISP1 — branch claude/disp1-disposition — GTD for the machine age:
  New module heartbeat/decima/disposition.py: dispose(k, intake) takes an Intake Event (an
  observation or inbound message — UNTRUSTED data, instruction_eligible=False) and resolves it to a
  first-class disposition: archive (deterministic noise/spam filter — no action), remember (memory
  write), task (open a task cell), invoke (propose an effect, still subject to authorize/Morta), or
  policy (governance update). Deterministic filtering split from model/orientation analysis; the
  disposition is DECIMA's decision — an injection-laced intake stays DATA and the payload never picks
  its own disposition. Record a disposition Cell + a disposed_as edge from the intake; may consult
  decima/orientation.py to choose. Demo in a NEW file heartbeat/checks/104_disposition.py exposing
  run(k, line): noise → archived; a fact → memory write; an actionable intake → task/INVOKE proposal;
  an injection-laced intake stays DATA with Decima picking the disposition; each carries disposed_as.
  Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-disp claude/disp1-disposition
Stay in disposition.py + checks/104. Public memory/model/weave/orientation API; no core edit, no
smoke.py edit. Keep the oracle green (cd heartbeat && python3 smoke.py → "alive ✓", exit 0). Commit
small; git pull --rebase; push; fast-forward to main when green.
```

---

## Instance 2 — Claude · Morta-gated payments rail  (worktree `~/decima-claude-pay`)

`git worktree add ~/decima-claude-pay claude/pay1-payments`.

**Task:** PAY1. **Owns:** `heartbeat/decima/payments.py` (new), `heartbeat/checks/106_payments.py` (new).
**Must not touch:** any core file, `disposition.py`, `weave.py`, `wager.py`, `executor.py`, `smoke.py`.

```text
You are a Claude payments instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md (brief
PAY1), specs/CAPABILITY_MAP.md D3.4, specs/MORTA_CAPABILITIES.md, specs/SANDBOX.md, decima/wager.py
(WV1), and heartbeat/checks/README.md first.

Task PAY1 — branch claude/pay1-payments — financial transactions as the canonical irreversible effect:
  New module heartbeat/decima/payments.py: a payment effect (register via the PUBLIC
  executor.register) realized through a capability with effect_class="FINANCIAL", a HARD spend cap
  (budget caveat), requires_approval (Morta), and a sandbox profile (network to the rail only). A
  pay(k, ...) flow: (1) optionally record a WV1 wager (decima/wager.py — the predicted outcome of the
  spend); (2) Morta-gated (denied until kernel.approve); (3) an IDEMPOTENCY key so a replayed/dup
  request never double-spends; (4) emit an EffectReceipt on the Weft (audit); (5) a later verdict
  measures the outcome. An over-cap spend is refused. Demo in a NEW file
  heartbeat/checks/106_payments.py exposing run(k, line): in-cap payment runs only after approval;
  over-cap refused; duplicate (same idempotency) does not double-spend; payment binds a wager +
  records a verdict; all on the Weft. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-pay claude/pay1-payments
Stay in payments.py + checks/106. Public kernel/capability/executor.register/wager API; uses SB1
sandbox + WV1 + Morta but edits none of them; no core edit, no smoke.py edit. Keep the oracle green.
Commit small; git pull --rebase; push; fast-forward when green.
```

---

## Instance 3 — kernel · Incremental fold-from-base  (clone `~/decima-claude`)

**Task:** IFB1 (core). **Owns:** `heartbeat/decima/weave.py`, `heartbeat/checks/108_incremental_fold.py` (new).
**Must not touch:** `disposition.py`, `payments.py`, other `checks/` files.

```text
You are the Claude kernel/perf instance for Decima, in ~/decima-claude. Read docs/BACKLOG.md (brief
IFB1), decima/snapshot.py (SN1: snapshot/restore — it explicitly defers incremental fold-from-base),
specs/FOLD_AND_LIFECYCLE.md §11.1, and heartbeat/checks/README.md first.

Task IFB1 — branch claude/ifb1-incremental-fold — the snapshot perf win:
  In decima/weave.py, add a fold path that starts from a RESTORED, VERIFIED snapshot base (use
  snapshot.restore) at frontier F and applies ONLY events after F, producing a Weave whose
  state_root() equals a genesis fold to the current head (a verifiable cache; FOLD §11.1). Keep the
  full-fold default; verify the base's state_root before trusting it (a tampered base is rejected).
  Respect arrival-order independence and idempotency (re-applying an event changes nothing). Demo in a
  NEW file heartbeat/checks/108_incremental_fold.py exposing run(k, line): snapshot at frontier F;
  incremental fold (base@F + events>F) state_root == full genesis fold to head; a tampered base
  rejected; duplicate events idempotent. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude claude/ifb1-incremental-fold
You OWN weave.py this cycle. Demo in checks/108; only relevant wording in smoke.py may change if
needed. Keep the oracle green. Commit small; git pull --rebase; push; fast-forward to main when green.
```

---

## Notes
- **Land order:** IFB1 (core) is independent of DISP1/PAY1 (new modules); land in any order, but a
  quick re-verify on rebase is cleanest.
- **PAY1 builds on WV1 + SB1 + Morta** but edits none of them — it composes via their public APIs.
- **Pushing:** SSH deploy keys push code (no token); fast-forward small green changes to `main`.
- **Next:** credential/billing powerbox + secrets broker (D3.2), self-hosted inference (D3.3), real
  engines, the Constellation GUI — see `docs/BACKLOG.md`.
