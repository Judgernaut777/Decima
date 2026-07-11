# Contributing to Decima

Decima is built by a human and more than one coding agent (Claude, Codex)
working in parallel. These rules keep us from clobbering each other. The single
principle: **GitHub is the only shared truth; no two workers ever touch the same
directory.**

> **The shared task board is [`docs/BACKLOG.md`](docs/BACKLOG.md)** — what's next,
> who can take it, and the collision lanes. Pick from there; keep `smoke.py` green.

## Working copies

| worker | clone dir | ssh alias / remote |
|---|---|---|
| Claude | `~/decima-claude` | `git@github-decima-claude:Judgernaut777/Decima.git` |
| Codex  | `~/decima-codex`  | `git@github-decima-codex:Judgernaut777/Decima.git` |

Each agent has its **own deploy key** (in `~/.ssh/`, aliased in `~/.ssh/config`),
so access is independently revocable and the push audit trail stays distinct.
Check your key any time:

```bash
ssh -T github-decima-claude   # → "Hi Judgernaut777/Decima!"
```

> `/tmp/decima` is a stale pre-git workspace. Do not edit it. Everything in it is
> already committed here.

## The loop

**Pull before you work. Commit small. Push often.**

```bash
git pull --rebase                       # before starting (clean tree)
# ...do work...
git add -A
git commit -m "concise, imperative summary"
git pull --rebase                       # commit FIRST — rebase refuses a dirty tree
git push
```

`git pull --rebase` requires a clean tree, so always **commit (or stash) before
you rebase**, not after staging. If a rebase hits a conflict, resolve it in your
own clone and continue — never force-push `main`.

## Branch-per-agent (for anything non-trivial)

Direct commits to `main` are fine for small, isolated changes (a doc, a single
file). For multi-file or in-progress work, branch so the other agent never
fetches a half-finished `main`:

- Claude → `claude/<topic>`
- Codex  → `codex/<topic>`

Open a PR (or fast-forward merge) into `main` when the change is coherent and the
Heartbeat still runs.

## Ground rules

- **The Weft is local truth, not source.** `*.db` is gitignored; it rebuilds from
  genesis by folding events. Never commit a `weft.db`.
- **Never commit secrets** — no private keys, tokens, or `~/.ssh` material.
- **Keep the Heartbeat green.** Before pushing changes under `heartbeat/`, run:
  ```bash
  cd heartbeat && python3 smoke.py     # all five laws must still hold
  ```
- **Respect the boundaries.** `KERNEL.md` is the canonical design; `specs/` are
  the formal protocols; `heartbeat/` is the running prototype. If code and spec
  disagree, fix one and say which in the commit.
- **Conventions over cleverness.** Match the surrounding code's idiom and the
  Nona/Decima/Morta naming.

## Decima 0.3 milestone engineering policy

The [0.3 "Local Daily Driver" handoff](docs/DECIMA-0.3-HANDOFF.md) governs milestone
work. Additional rules while it is active:

- **Phases are sequential.** Do not start product functionality before the foundation
  (packaging, kernel extraction, conformance) lands. The restructuring is incremental —
  never move the whole tree in one destructive commit; leave compatibility imports and
  keep the legacy `heartbeat/` runnable until the new Shell reaches parity.
- **The TCB boundary is enforced, not aspirational.** `tests/architecture/` fails the
  build if trusted code imports network/subprocess/provider/MCP/web code. Do not weaken
  it to make something pass — fix the design or stop and report (see `SECURITY.md` §
  "What an agent must never do").
- **Commit conventions.** One architectural purpose per commit; conventional-style
  prefix (`chore:`, `build:`, `ci:`, `docs:`, `feat:`, `fix:`, `refactor:`, `test:`).
  Include tests, keep the baseline green, and don't mix protocol changes with UI changes
  or unrelated formatting churn. End messages with the `Co-Authored-By` /
  `Claude-Session` trailers.
- **Definition of done (handoff §18).** Code + unit tests + relevant property/adversarial
  tests + existing tests pass + types pass for touched non-legacy code + docs updated +
  failure states handled + no secrets in fixtures + user-visible behavior reachable
  through the Shell where applicable. "Code exists" is not done.
- **Protocol & migration policy.** A change to canonical serialization, event shape,
  capability semantics, or fold results is a **protocol change**: it must ship with
  golden fixtures (`protocol/fixtures/`), preserve existing stored event identifiers, and
  provide a migration path. Never silently rewrite existing events. Projection schema
  changes are handled by rebuild, not in-place mutation.
- **Local gate before pushing.** `make check` (ruff format-check + lint + mypy + pytest)
  for new code, and `make smoke` for anything under `heartbeat/`.

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*
