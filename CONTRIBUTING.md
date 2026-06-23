# Contributing to Decima

Decima is built by a human and more than one coding agent (Claude, Codex)
working in parallel. These rules keep us from clobbering each other. The single
principle: **GitHub is the only shared truth; no two workers ever touch the same
directory.**

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

*Decima, woven on the Loom — spun by Nona, allotted by Decima, cut by Morta.*
