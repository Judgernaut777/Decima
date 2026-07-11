# Known failures & caveats (frozen 2026-07-11)

At baseline the oracle is **fully green** (`smoke.py` → `heartbeat: alive. ✓`, exit 0,
all 221 discovered checks pass). This file records the open caveats carried forward so
they are not mistaken for regressions during the 0.3 refactor.

## Not failures (intentional)

- **Tamper-evidence "tamper detected on fold" line** — the last step of `smoke.py`
  deliberately corrupts a payload byte in the throwaway DB and asserts the fold rejects
  it (`WeftError: content tampered`). This line is a PASS.

## Historical caveats (from the 4th-quality re-audit handoff, now closed or gated)

- **`checks/270_api.py` uncorroborated exit-1** — one prior run reported a single
  uncorroborated exit-1 near this check; two subsequent clean `python3 -u smoke.py` runs
  observed green (including this baseline). Phase-3 task **oraclefreeze** will pin the
  check set in a manifest and formally retire this note. Not reproduced at baseline.

## Operator-gated by design (NOT code gaps, excluded from any green gate)

- **Live model brain** requires the operator to export `ANTHROPIC_API_KEY`, run
  `run.py`, `grant api.anthropic.com`, and `approve` — fail-closed by design
  (`GOLIVE.md`). No baseline check depends on it.
- **Real voice** (whisper.cpp / Piper) is deferred behind the voice contract.

## Deferred-by-milestone (handoff §3.2)

Large domains present in the tree — financial automation, live brokerage, autonomous
payments, healthcare, insurance, tax filing, production KYC, full browser automation,
mobile, cross-device replication, cloud relay, the Rust port — are **out of scope for
0.3**. Their modules may remain but must not block or expand 0.3 work. See
`current-module-inventory.md` for the per-module `keep 0.3` disposition.
