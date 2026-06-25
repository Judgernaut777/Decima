# Cycle assignments + kickoff prompts

Per-instance briefs for the **current cycle (11).** Tasks/lanes in
[`../docs/BACKLOG.md`](../docs/BACKLOG.md); rationale in
[`../specs/CAPABILITY_MAP.md`](../specs/CAPABILITY_MAP.md) (Part C, D3.4).

**One core lane this cycle: INTAKE1 owns `kernel.py`.** Two rules:
1. Only INTAKE1 touches core (`weave.py`/`weft.py`/`kernel.py`/`executor.py`); others call the public API.
2. **Feature demos go in `heartbeat/checks/NN_*.py`, never in `smoke.py`.** Own a free `NN`
   (116/118/120 assigned). See `heartbeat/checks/README.md`.

Bootstrap any lane first: `scripts/kickoff.sh <dir> <branch>`.

---

## Instance 1 — kernel · Live disposition loop  (clone `~/decima-claude`)

**Task:** INTAKE1 (core). **Owns:** `heartbeat/decima/kernel.py`, `heartbeat/checks/116_live_intake.py` (new).
**Must not touch:** `triage.py`, `trading.py`, other `checks/` files.

```text
You are the Claude kernel instance for Decima, in ~/decima-claude. Read docs/BACKLOG.md (brief
INTAKE1), decima/disposition.py (DISP1 — dispose()), the existing kernel.ingest_observation, and
heartbeat/checks/README.md first.

Task INTAKE1 — branch claude/intake1-live-intake — make the disposition router live:
  In kernel.py, route inbound through disposition.dispose(): make ingest_observation (after it
  observes) pass the observed text through dispose() (untrusted → DATA), and add a general
  ingest(source, text, *, trusted=False) entry that auto-disposes any inbound (messages, tool
  output). Keep the existing observation receipt + provenance; record the disposition with its
  disposed_as edge. Untrusted inbound must NEVER elevate to a task/invoke/policy (DISP1's law holds
  end-to-end through the kernel). Demo in a NEW file heartbeat/checks/116_live_intake.py exposing
  run(k, line): an inbound observation auto-disposes via the kernel — noise → archive, fact →
  memory, injection-laced page → flagged DATA (never invoke) — each with a disposed_as edge; an
  untrusted intake never elevates. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude claude/intake1-live-intake
You OWN kernel.py this cycle. Uses disposition (public). Demo in checks/116; only relevant wording
in smoke.py may change if needed. Keep the oracle green (cd heartbeat && python3 smoke.py →
"alive ✓", exit 0). Commit small; git pull --rebase; push; fast-forward to main when green.
```

---

## Instance 2 — Claude · Blue-team triage / SIEM  (worktree `~/decima-claude-triage`)

`git worktree add ~/decima-claude-triage claude/triage1-siem`.

**Task:** TRIAGE1. **Owns:** `heartbeat/decima/triage.py` (new), `heartbeat/checks/118_triage.py` (new).
**Must not touch:** any core file, `detection.py`, `trading.py`, `smoke.py`.

```text
You are a Claude blue-team instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md (brief
TRIAGE1), specs/CAPABILITY_MAP.md Part C, decima/detection.py (DET1 emits `finding` cells), and
heartbeat/checks/README.md first.

Task TRIAGE1 — branch claude/triage1-siem — turn detections into incidents:
  New module heartbeat/decima/triage.py: read `finding` Cells (DET1) from the Weave; correlate them
  into `incident` Cells (group by rule / severity / source within a window), compute an incident
  severity, and link each incident to its findings (includes edges) with provenance. Propose a
  RESPONSE — a disposition (e.g. open a remediation task) or a Morta-gated action proposal — recorded
  on the Weft. A single benign/low finding must NOT escalate to an incident. The signed Weft is the
  tamper-evident SIEM. Demo in a NEW file heartbeat/checks/118_triage.py exposing run(k, line):
  several related findings correlate into one incident citing them with a computed severity; a lone
  benign finding does not escalate; the incident proposes a response; all on the Weft. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-triage claude/triage1-siem
Stay in triage.py + checks/118. Public weave/memory/model API; reads DET1 findings; no core edit, no
detection.py edit, no smoke.py edit. Keep the oracle green. Commit small; git pull --rebase; push;
fast-forward when green.
```

---

## Instance 3 — Claude · Trading on the payments rail  (worktree `~/decima-claude-trade`)

`git worktree add ~/decima-claude-trade claude/trade1-trading`.

**Task:** TRADE1. **Owns:** `heartbeat/decima/trading.py` (new), `heartbeat/checks/120_trading.py` (new).
**Must not touch:** any core file, `payments.py`, `wager.py`, `secrets.py`, `triage.py`, `smoke.py`.

```text
You are a Claude trading instance for Decima, in a dedicated worktree. Read docs/BACKLOG.md (brief
TRADE1), specs/CAPABILITY_MAP.md D3.4, decima/payments.py (pay/install_rail/find_payment/settle),
decima/wager.py (WV1), decima/secrets.py (CRED1), and heartbeat/checks/README.md first.

Task TRADE1 — branch claude/trade1-trading — trade stocks on the rail:
  New module heartbeat/decima/trading.py: buy(k, ...) / sell(k, ...) that REUSE payments.pay for the
  Morta-gated, spend-capped, IDEMPOTENT money movement (no double-fill), bind a WV1 wager on the
  predicted return and settle a verdict on the realized outcome, and update a `portfolio` Cell
  (positions: symbol → qty/cost) on the Weft. Broker credentials come from the CRED1 secrets broker
  (a handle, never the raw key). An over-cap trade is refused; a sell closes/reduces the position.
  A trade IS a Morta-gated payment with a price wager — compose, don't reinvent. Demo in a NEW file
  heartbeat/checks/120_trading.py exposing run(k, line): a buy is Morta-gated + idempotent (a
  duplicate doesn't double-fill), updates the portfolio, binds a price wager + records a verdict; an
  over-cap trade is refused; a sell reduces the position; all on the Weft. Fail loud.

Bootstrap: scripts/kickoff.sh ~/decima-claude-trade claude/trade1-trading
Stay in trading.py + checks/120. Compose payments/wager/secrets/kernel public APIs; edit none of
them; no core edit, no smoke.py edit. Keep the oracle green. Commit small; git pull --rebase; push;
fast-forward when green.
```

---

## Notes
- **Land order:** INTAKE1 (core) is independent of TRIAGE1/TRADE1 (new modules); land in any order,
  but a quick re-verify on rebase is cleanest.
- **TRIAGE1 builds on DET1; TRADE1 composes PAY1 + WV1 + CRED1** — all via public APIs, editing none.
- **Pushing:** SSH deploy keys push code (no token); fast-forward small green changes to `main`.
- **Next:** cascade/lease-tree retraction + `Weft.ingest()` (core); red-team depth; real engines; the
  Constellation GUI — see `docs/BACKLOG.md`.
