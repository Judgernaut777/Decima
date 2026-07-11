## Wave 2b — the trusted Shell (decima/shell/)

A conventional, hand-written, zero-dependency web UI (plain HTML + CSS + vanilla JS, no build step, no npm, no CDN) plus a stdlib host that serves it and delegates the API — the whole Shell runs from one loopback origin.

### What was built
- **`decima/shell/serve.py`** — pure-stdlib `ShellApp`. Non-`/api` paths are served as static files from `frontend/` (path-traversal confined to the frontend root); `/api/*` is delegated verbatim to the imported backend `Application.dispatch` (no rewrite, no added authority). A socket-free `handle()` core drives tests deterministically; `__call__` adapts to WSGI for the real loopback server. Every static reply carries a strict same-origin CSP (`default-src 'self'`, no `unsafe-inline`/`unsafe-eval`, `object-src 'none'`) plus `nosniff`/`X-Frame-Options: DENY`.
- **`frontend/`** — an app shell (sidebar nav + view switcher + login/pairing gate + toast + trusted modal host) and all nine required screens: Conversation (streaming transcript), Today, Projects, Knowledge, Plans, Approval inbox, Capability inspector, Activity timeline, Settings.
- **Security by construction**: `sanitize.js` is the single escape choke point (also Node-loadable so it is unit-tested against hostile inputs); `dom.js` builds every node via `createElement` + `textContent` (no `innerHTML`/`eval`/`new Function` anywhere); `api.js` sends the CSRF token + `credentials:'same-origin'` on mutations and the pairing secret in `X-Reauth` only for approvals. Content is separated into four labelled trust zones (untrusted/imported, model, system, human).
- **Trusted approval inbox**: the ONLY place approval buttons exist. Each pending card discloses requesting-agent / effect / exact-target / args / data-leaving-machine / provider / max-cost / expiry / reversibility / causal-step / reason (undisclosed API fields shown honestly as "not disclosed"), with Deny / Approve once / Approve with stricter limits. Approve opens a trusted reauth modal that never stores the secret. No "approve everything from this agent" control exists.

### API surface consumed (read-only backend, unedited)
Reads: `/api/v1/{health,session,tasks,projects,agents,notes,approvals,activity,stream}`. Mutations via the declared command endpoints; gated proposals (terminate/revoke/import/export) correctly surface as 202 → pending inbox items; approve is reauth-gated.

### Verification (all green)
- `pytest tests/shell -q` → **74 passed** (static serving + CSP + traversal; API delegation incl. CSRF/reauth/gated-defer; the Node-run sanitizer against `<script>`/`<img onerror>`/approval-chrome-imitation/`javascript:` URLs; the nine screens exist and reference only real endpoints; no forbidden JS sink present).
- `pytest tests/architecture -q` → **19 passed** (TCB import boundary intact).
- `python3 -c "import decima.shell.serve"` → OK.
- Live loopback smoke: booted the real stdlib server and confirmed index+CSP header+local JS+`/api` delegation+login all served from one origin.

### Boundary
Only `decima/shell/` and `tests/shell/` were created. `heartbeat/`, `decima/kernel/`, `decima/services/api/`, and `protocol/fixtures/` are untouched.