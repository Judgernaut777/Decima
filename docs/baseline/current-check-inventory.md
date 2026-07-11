# Current check inventory (frozen 2026-07-11)

The Decima oracle is `heartbeat/smoke.py`. It runs a fixed in-file spine (genesis, weave,
merge, router, tamper-evidence) plus **every discovered feature check** under
`heartbeat/checks/`.

## Discovery contract

`smoke.py` globs `heartbeat/checks/[0-9]*.py` in **filename (numeric) order** and calls
each module's `run(k, line)` before the final tamper-evidence step. Each check `assert`s
loud and prints a `→` summary line. New lanes add their own `checks/NN_*.py` file and
never edit `smoke.py` — this is the collision-free extension point for parallel work.

| Metric | Value |
|---|---|
| Discovered check files (`checks/[0-9]*.py`) | **221** |
| Total files in `checks/` | 223 (221 numbered + `README.md` + non-numbered helpers) |
| Result at baseline | all green, `heartbeat: alive. ✓`, exit 0 |
| Highest-numbered lane | `498` (Batch T: livecodegen / catalog-activation / surface / contextfold / presentwire) |

## Frozen behavioral output

The full captured run is `docs/baseline/smoke-output.txt` (2242 lines). This is the
reference the Phase 3 conformance suite must reproduce implementation-independently, and
the invariant the incremental refactor (Phases 2+) must not regress.

## Rule for 0.3

Until the Phase 3 conformance suite (`protocol/conformance/`) supersedes it, `smoke.py`
green (exit 0, all discovered checks pass) remains a **hard gate** on every commit. The
Phase 1 CI wires `pytest` alongside it; the existing smoke suite is preserved and run
until the new Shell + conformance suite reach parity (handoff §4.2).
