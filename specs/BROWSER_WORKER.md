# Browser Worker Contract

## 1. Decision

Decima should adopt Vercel's `agent-browser` as the first browser execution
engine behind a Decima-owned contract.

It is a useful implementation donor because its Rust CLI already provides:

- a persistent, named-session daemon with a JSON command protocol;
- direct CDP control, remote browser providers, and partial WebDriver support;
- accessibility-tree snapshots with agent-friendly element references;
- screenshots, annotated screenshots, downloads, traces, HAR, and video;
- action policies, domain allowlists, credential providers, and launch plugins;
- browser state, tabs, frames, network interception, and React inspection.

These are engine features. They do not become Decima's public object model.
The engine remains replaceable by Playwright, a hosted browser, a mobile
driver, or a later native worker.

## 2. Principal and Cell model

Each browser worker runs as its own principal. It receives no ambient user,
orchestrator, workspace, network, or secret authority.

```text
BrowserSession Cell {
  engine
  engine_version
  worker_principal
  sandbox_id
  lifecycle
  created_by_invocation
  policy_digest
  allowed_origins
  state_handle?
  current_page_receipt?
}
```

The session Cell contains references and metadata, not cookies, passwords,
tokens, or raw engine state. Secret browser state lives in the secrets broker
or encrypted artifact store and is exposed through short-lived handles.

Every browser command is an `INVOKE`. Its completion produces a receipt Cell.
Page snapshots, screenshots, downloads, traces, and extracted values are
artifact Cells linked to that receipt. Replay folds the receipts; it never
repeats browser effects.

## 3. Capability surface

Do not issue a single broad `browser` capability. Effects are separated so
observation authority cannot silently become publication or account control.

```text
browser.session.create
browser.session.close
browser.navigate
browser.observe.accessibility
browser.observe.screenshot
browser.observe.console
browser.observe.network
browser.extract
browser.interact.click
browser.interact.fill
browser.interact.select
browser.interact.upload
browser.download
browser.auth.login
browser.state.load
browser.state.persist
browser.script.evaluate
browser.network.modify
browser.submit
browser.publish
```

Selectors constrain origins, URL paths, session Cells, local file paths, and
artifact destinations. Network and filesystem restrictions are also enforced
outside the browser process.

## 4. Effect classes

| Class | Examples | Default treatment |
|---|---|---|
| Observe | snapshot, screenshot, read DOM, console | Auto-allow within an authorized origin |
| Reversible interaction | open tab, scroll, expand UI, fill an unsent draft | Allow when narrowly scoped; retain a receipt |
| Sensitive access | login, upload, download, load state, reveal account data | Brokered capability, secret/data classification checks |
| Arbitrary execution | JavaScript evaluation, CDP attachment, network rewrite, extension/init script | Deny by default; specialist capability in an isolated worker |
| Outward effect | submit form, send message, publish post, deploy, purchase, trade | Morta permanent gate and final preflight |
| Identity/policy mutation | change password, MFA, recovery email, permissions, API keys | Strong reauthentication and explicit constitutional approval |

Clicking is not intrinsically low risk. A click on a disclosure widget and a
click on "Buy" use different Decima effects even if the engine command is the
same. The adapter classifies semantic intent before translating it to an
engine action.

## 5. Observation and visual reasoning

The normal observation bundle is:

```text
BrowserObservation {
  url
  origin
  title
  accessibility_snapshot
  screenshot?
  viewport
  active_frame
  ref_epoch
  page_version_hint
}
```

Accessibility references are scoped to a session, page, and `ref_epoch`.
Navigation or meaningful DOM mutation invalidates them. Agents refresh the
snapshot after navigation, form submission, modal transitions, or stale-ref
errors.

Visual browsing combines the accessibility snapshot with a screenshot and a
vision-capable model. The browser engine executes and observes; it does not
own visual reasoning, planning, memory, or approval.

Annotated screenshots are debugging evidence, not canonical coordinates.
Coordinate actions are a fallback when semantic references are unavailable.

## 6. Untrusted-page law

All browser-derived content is untrusted external data, including:

- visible text and accessibility labels;
- HTML, scripts, metadata, and structured data;
- downloaded files and filenames;
- console, network, and error messages;
- text recognized from screenshots;
- instructions rendered by a site.

The adapter labels these artifacts `instruction_eligible=false`. They may be
quoted as evidence or recalled as data, but cannot alter objectives, policy,
capabilities, model routing, or memory permissions.

Promoting a page-derived claim into trusted memory requires provenance and the
applicable verifier/attestation policy. Content-boundary markers improve model
behavior but are not a security boundary.

## 7. Required execution envelope

The initial local profile is a container or microVM with:

- a dedicated non-root UID and private temporary home;
- a private runtime/socket directory created as mode `0700`;
- no host browser profile, SSH agent, Docker socket, cloud metadata, or home
  directory mounts;
- read-only engine image and a small writable scratch volume;
- destination-scoped download and upload mounts;
- kernel/firewall egress enforcement in addition to browser domain filtering;
- resource, concurrency, wall-clock, and byte limits;
- a generated explicit configuration and sanitized environment;
- logs and artifacts passed through secret and personal-data redaction.

The wrapper verifies ownership and permissions of the daemon directory and
socket after launch. `agent-browser` creates these paths but does not
explicitly set private Unix modes in the reviewed implementation, so host
`umask` is not an acceptable control.

Auto-connecting to a user's existing Chrome instance and reusing a host
profile are prohibited by default because they expose unrelated sessions and
credentials.

## 8. Engine policy profile

Decima generates policy per invocation. The baseline is:

```json
{
  "default": "deny",
  "allow": [
    "launch",
    "navigate",
    "snapshot",
    "screenshot"
  ]
}
```

The actual allow set is derived from held capabilities. Never rely on the
engine's empty or absent allowlist behavior: its policy permits actions by
default unless configured to deny.

The wrapper supplies one explicit trusted config. It does not discover config
from the task repository or run with an untrusted repository as its config
root. Project configuration can add plugins, extensions, and init scripts,
which is executable supply-chain input.

Plugins, browser extensions, init scripts, custom executables, launch
mutators, credential providers, remote CDP targets, and remote browser
providers require separately attested capabilities. Plugin capability labels
are declarations, not process isolation.

## 9. Network rules

Browser domain allowlists are defense in depth, not the primary egress
boundary. Decima additionally enforces:

- DNS resolution and connection policy at the sandbox edge;
- private, loopback, link-local, metadata, and prohibited network denial;
- origin and redirect-chain validation;
- WebSocket, EventSource, beacon, and download policy;
- explicit exceptions for authentication redirects and required CDNs;
- byte and request budgets.

A hostname allowlist alone cannot prevent a permitted hostname resolving to a
private address. URL schemes such as `file:`, `data:`, browser-internal pages,
and local CDP endpoints are denied unless an exact capability permits them.

## 10. Credentials and browser state

The Decima secrets broker remains canonical. The preferred login flow is:

1. the agent requests `browser.auth.login` for an exact account and origin;
2. the powerbox resolves policy without revealing the secret;
3. the broker injects credentials directly into the isolated worker;
4. the worker completes login and returns a redacted receipt;
5. resulting state is sealed under a session/account-scoped handle;
6. plaintext credentials and transient form values are erased.

The engine's encrypted credential vault may be used only as a local adapter
cache under explicit policy. A key stored beside encrypted data provides
at-rest protection, not isolation from a compromised worker.

HAR files, screenshots, traces, downloads, DOM snapshots, and saved browser
state can contain credentials or personal data. They inherit the highest
classification detected in their contents and have bounded retention.

## 11. Approval and external effects

Engine confirmation prompts are a useful inner guard, but Morta owns approval.
Approval binds the exact Decima invocation digest, resolved target, displayed
content, account identity, cost ceiling, and expiry.

Immediately before an irreversible browser action, the adapter performs a
final preflight:

1. refresh page state;
2. resolve the target control and destination;
3. verify authority, revocation, and approval freshness;
4. compare material fields with the approved digest;
5. execute once with an idempotency key when available;
6. capture an outcome receipt and reconcile uncertain results.

Browser effects are not assumed idempotent. Timeouts after submission produce
`outcome=unknown`, followed by observation/reconciliation rather than blind
retry.

## 12. Shell projection

The Shell may project a live browser workspace containing:

- current viewport and accessibility outline;
- agent cursor/action intent;
- origin and trust classification;
- pending Morta approval with exact effect summary;
- downloads, console, network, trace, and receipts;
- pause, revoke, take-over, and terminate controls.

The Shell consumes Decima Cells and streams. It does not connect directly to
the engine daemon or inherit its authority.

## 13. Initial adapter boundary

The first adapter should implement:

```text
create_session(policy) -> BrowserSession
navigate(session, url) -> Receipt
observe(session, mode) -> BrowserObservation
interact(session, semantic_action) -> Receipt
close_session(session) -> Receipt
```

Start with local Chromium, accessibility snapshots, screenshots, navigation,
click/fill/select, and downloads. Add raw evaluation, network rewriting,
extensions, remote CDP, hosted providers, and persistent authenticated state
only after their capability and sandbox tests exist.

The conformance suite must run the same contract against `agent-browser` and
at least one reference backend. That is what keeps the donor replaceable.
