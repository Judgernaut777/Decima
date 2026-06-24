# heartbeat/checks/ — collision-free oracle extension

`smoke.py` is the conformance oracle, but it's a single file — when several
instances each append a section, they collide (and they did: E1 vs C1 in cycle 1).

This directory removes that bottleneck. `smoke.py` auto-discovers every
`checks/NN_*.py` (filename starting with a digit) and runs it **after** the inline
sections and **before** tamper-evidence. A feature lane adds its **own file here**
and never edits `smoke.py` — so parallel instances no longer conflict.

## Contract

A check module exposes one function:

```python
def run(k, line):
    """k: a live decima.kernel.Kernel (already booted, post-inline-sections).
       line: the print helper (line("..."))."""
    line("\n== MY FEATURE (what it proves) ==")
    ...
    assert <invariant>, "loud failure message"   # fail hard on regression
```

- **Naming:** `NN_short_name.py`, `NN` a two-digit order prefix (e.g. `50_org_policy.py`).
  Pick a free number; lanes own distinct numbers so even ordering doesn't collide.
- **Shared kernel:** `k` carries all state the inline sections built. Don't assume a
  capability another section revoked still exists; forge/grant what you need.
- **Fail loud:** raise `AssertionError` on a broken invariant — the run exits nonzero,
  exactly like the `FOLD §11` section.
- **No DB-corrupting tricks** — tamper-evidence owns that and must stay last.

## Why a digit-prefix glob
`smoke.py` globs `[0-9]*.py`, so this `README.md` (and any helper without a digit
prefix) is ignored. Modules are loaded by path via `importlib`, so the digit prefix
that would be an illegal identifier (`import 50_x`) is fine.
