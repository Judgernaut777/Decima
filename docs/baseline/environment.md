# Baseline environment (frozen 2026-07-11)

Captured per DEC-001 before any Decima 0.3 restructuring. This is the reproducibility
anchor: the behavioral baseline below (`smoke-output.txt`) was produced on exactly this
environment.

| Field | Value |
|---|---|
| Date captured | 2026-07-11 |
| Repo | `github.com/Judgernaut777/Decima`, `main` @ `55cd359` |
| Working copy | `/home/mini/decima-claude` |
| Python | 3.11.2 (CPython) |
| OS | Linux 6.6.10-cix-build-generic, `aarch64` (CIX P1 ARM box) |
| Runtime deps | PyNaCl 1.6.2 (`pynacl>=1.5`, `heartbeat/requirements.txt`) |
| Everything else | Python standard library only |

## How the baseline was produced

```
cd heartbeat
python3 -u smoke.py     # → docs/baseline/smoke-output.txt
```

Result: `heartbeat: alive. ✓`, exit 0, all checks green. The run takes several minutes
(221 discovered checks + the fixed spine in `smoke.py`); run unbuffered / in background,
never under a short timeout.

## Required environment variables

- **None** for the offline oracle. The full check suite runs with zero credentials.
- Live operation (out of scope for the baseline, gated by design — see `GOLIVE.md`)
  requires the operator to export `ANTHROPIC_API_KEY` and approve an
  `api.anthropic.com` egress grant interactively. No baseline check depends on it.

## Known nondeterminism

- The oracle is designed to be deterministic: recorded content is ints-not-floats, no
  wall-clock / unseeded-random in the Weft. The tamper-evidence step at the end of
  `smoke.py` intentionally corrupts a DB byte and asserts the fold rejects it (that
  "tamper detected" line is a PASS, not a failure).
- `heartbeat/weft.db` + `weft.db.keys` are working-copy artifacts; the smoke run builds
  its own throwaway Weft in a tempdir, so it does not depend on repo DB state.
