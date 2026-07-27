# Design: Egress Mediation — so the `network` tier could have an executor

**Status:** **proposed — no wave has landed.** Nothing in this document is implemented; it is
the design for the one platform item that three of the four vision surfaces are already
blocked on (`docs/design/vision-surfaces.md:28-33`, gap **G4**) and that the Nona plan
deliberately left as an absence rather than a policy
(`docs/design/nona-self-extension.md:334-337`, Decision 2).
**Date:** 2026-07-27
**Scope:** what would have to become TRUE before `promotion.SIGNER_POLICY["network"]` stops
saying `NOT_EXECUTABLE`, and the mechanism that would make it true. It is a companion to
`docs/design/nona-self-extension.md` (which owns the promotion boundary) and it narrows
`docs/design/vision-surfaces.md` **D4** (which chose a topology in one line and did not cost
it).
**Hard dependency:** `docs/design/syscall-filtering.md` landed on this branch while this
document was being written, and it verified two escapes from the jail every worker runs in
(`SECURITY.md:302-336`). What contains them today is that a worker has **no route out** —
which is precisely the sentence wave **E5** deletes. So that document's **S0** is a
prerequisite of E5, not a parallel lane: §6 **E-R0**, §5.7 item 0, **D10**.
**Content-addressed bytes:** **`needs_fixture_regen = false`** for every wave as written —
`protocol/fixtures/fold.json` pins a 6-event script whose `type_counts` are
`{note: 1, type: 1}`, so it contains no capability, no receipt and no worker. §7 names the
two ways an implementer could accidentally flip that (rewriting caveat bodies at mint;
re-shaping the worker IPC's *existing* v1 fields) and says to **stop and report** instead.
`CONTAINMENT_MATRIX_VERSION` 3 → 4 is **not** a golden vector; it is a runtime constant that
changes `reckoner.environment_digest`, so its cost is **re-evaluation**, not regeneration
(§5.6).

---

## 1. One-paragraph summary

The shipping product already makes outbound network calls, and that path is **trusted, not
mediated** — while the path that does not exist yet is the only one anybody has written a
refusal for. `decima/services/api/models_setup.py:176` calls
`urllib.request.urlopen` **inside the API daemon process**, with no capability, no INVOKE, no
approval, no receipt and no redaction; the only control anywhere near it is
`_base_url_is_loopback` (`models_setup.py:506-540`), which resolves the configured host
**once at construction time** and then trusts every later call. Meanwhile
`decima/workers/execution.py:1181-1186` refuses every network-permitted worker profile at the
primitive for every caller, `decima/workers/profiles.py:84-95` says PROVIDER is "STRUCTURE
(not yet wired)", and `decima/services/nona/promotion.py:57` marks the `network` tier
`NOT_EXECUTABLE` so the Shell renders "NOT EXECUTABLE — no mediated egress"
(`decima/shell/frontend/js/screens/nona.js:51`) instead of an approval prompt. So the honest
statement of today's posture is not "Decima has no egress" — it is **"Decima has exactly one
egress path, it is the least confined code in the system, and generated code is held to a
standard the daemon does not meet."** This document proposes closing that asymmetry first
(the product's own model calls go behind the seam before any organ gets a tier), and it
argues for a topology that is *cheaper and stronger* than the one `vision-surfaces.md` D4
recommended: **do not give the worker a route. Keep `CLONE_NEWNET` mandatory for every
profile — including the mediated one — and hand the child a brokered request/response channel
instead of a network.** That choice is what lets `run_worker`'s existing fail-closed refusal
stay untouched, lets `network_isolation` stay `enforced: True` for every profile in the
containment matrix, and lets `reckoner.require_host_containment`'s hard `network_denied`
requirement (`decima/services/nona/reckoner.py:199-204`) keep holding for a tier that talks
to the network. It also reports the load-bearing negative result: **a destination allowlist as
an attenuable caveat does not work in this tree today** — `_caveats_downhill`
(`decima/kernel/capability.py:93-104`) treats a non-numeric caveat as a *truthiness* test, so
a child may replace `["api.example.com"]` with `["evil.example.net"]` and still prove
"downhill", and an *empty* allowlist constrains nothing at all, which means
`QUARANTINE_BASELINE`'s `network_allow: []` (`decima/services/nona/candidate.py:52-57`) is
documentation rather than a mechanism. All four of those claims were reproduced against the
code before this sentence was written (§5.3).

## 2. Why this is a decision, not a patch

1. **The refusal it removes is the strongest single claim in `SECURITY.md`.**
   `SECURITY.md:285-293` tells an operator: *"`run_worker` therefore refuses every
   network-permitted profile at the primitive, for every caller — this residual is currently
   unreachable rather than merely undefended. Do not route real provider traffic through a
   worker until that seam lands, and do not relax the refusal to get a demo working."* And
   `docs/design/nona-self-extension.md:490` banks it as a safety layer: *"No egress executor
   exists — survives exfiltration by a promoted organ (there is literally no networked
   worker)."* Any work here spends that claim. Spending it is an owner decision about risk
   posture, exactly as un-deferring automatic promotion was.
2. **It is the first *outward* effect the product would have.** Every containment layer in
   `decima/workers/` bounds what a worker may *touch*. Egress is the first thing that bounds
   what a worker may *tell*, and the two have different logic: a filesystem bind can be made
   total (`resolve_bind_source`, `decima/workers/mount.py:105-145`), whereas an allowlist is
   never total — an allowed destination is a channel. §6 is about that gap and is the reason
   this document exists.
3. **It cannot be scoped to Nona.** The moment a mediator exists, the daemon's own
   `urlopen` calls are the *unmediated* ones, and a mediator that bounds only generated code
   while the trusted process is unbounded protects against the less likely attacker. So the
   first useful wave touches a live product lane (Q&A, plan, workspace all route through
   `svc.models.propose`, e.g. `decima/services/api/qa_service.py:362`), which makes this a
   change to shipping behaviour rather than an addition beside it.
4. **`_caveats_downhill` is kernel.** Making a set-valued caveat narrow correctly is a change
   to a pure function that both `verify_delegation_detail` (`capability.py:195`) and
   `attenuation_valid` (`capability.py:513`) depend on — i.e. to the read-side rule that
   decides authority on a log already on disk. That is the class of change N7 was careful
   about, and it deserves the same discipline: additive refusal, measured blast radius, no
   body rewritten.

## 3. Motivation

- **Three surfaces, one blocker.** `docs/design/vision-surfaces.md:28-33` counted it: the
  studio (remote generation), the inbox (mail), and every ops lane (posting, CRM, social) all
  need the box to talk outward under mediation. Its §8 wave **S4** is this document's subject,
  described in one table row.
- **The `network` tier already exists everywhere except in an executor.** A candidate may
  *declare* `network` (`candidate.EFFECT_CLASSES`, `candidate.py:61-67`), the Reckoner will
  *evaluate* it, `Weave.may_mint_root_grant` deliberately allows the Reckoner to *mint* the
  permanently-unusable grant so the surface has something to display
  (`decima/kernel/weave.py:885-904`), the powerbox *denies* brokering it
  (`powerbox.py:182-183`), the Shell *labels* it, and `executor.TIER_PROFILES`
  (`executor.py:145-149`) simply has no entry. Everything but the last two lines is built.
- **The dead-code liability, again.** §3 of the Nona design named the trap: un-exercised
  safety code drifts. `containment_report`'s `egress_mediation` row (`execution.py:431-441`)
  and its cross-cutting `warnings` entry (`execution.py:445-457`) describe a mechanism that
  does not exist, and `profiles.PROVIDER`'s docstring promises a "mediation/redaction seam"
  — a *redaction* seam this design recommends never claiming (§6, E-R5). A comment that
  promises a component nobody is building is the same defect class as a test that passes
  vacuously.
- **The reference proves the shape and the failure mode.** `heartbeat/decima/egress.py` is a
  complete gated-fetch loop: an allowlist in the capability's caveats, a refusal Cell for a
  blocked target, the response body forced through the untrusted-data boundary. It also
  contains the exact bug this design must not port (§4.4): the allowlist is checked by the
  *calling helper*, not by the code that opens the socket.

## 4. What exists today vs. what is missing

### 4.1 Exists in the shipping product (`decima/`)

| Piece | Where | State |
|---|---|---|
| Refusal of every network-permitted profile, at the primitive, for every caller | `execution.py:1181-1186` | **live + adversarially pinned** |
| `network_isolation` enforced for every profile *except* PROVIDER | `execution.py:385-396`; flag at `:717` (`want_net = not bool(cfg["network"])`) | live |
| `egress_mediation` declared honestly **un**enforced | `execution.py:431-441`; matrix pinned by `tests/adversarial/test_containment_matrix.py` | live |
| The loud worst-case warning (network permitted + no seccomp arch) | `execution.py:445-457` | live |
| `PROVIDER` profile as *structure only* | `profiles.py:84-95` | live, **no caller may use it** |
| Tier → profile map with no `network` entry, refusing as `NO_EXECUTOR` | `executor.py:145-149`, `:568-583` | live + tested |
| `network` marked `NOT_EXECUTABLE`, unanchorable, unbrokerable | `promotion.py:57`, `anchors.py:70`, `powerbox.py:182-183` | live + tested |
| The honest UI label (Decision 2) | `nona.js:51`; asserted in `tests/browser/specs/nona.spec.js:272` | live |
| An `attack.network_egress` baseline adversarial case | `decima/services/api/nona_service.py:186-200` | live — but see §6, E-R7 |
| The rug-pull scan mapping network imports to a tier | `stages.py:50-70` | live — and blind at this tier (§6, E-R6) |
| **The operator-declared-ceiling pattern this design copies** | `mount.py:105-145` + `executor.generated_code_effect(workspace_root=…)` | live, real, and the right precedent |
| Fd-passing into the jail (a capability, not a path) | `execution.py:1042-1057` (`pass_fds`, the pinned `O_PATH` workspace fd) | live |
| Worker IPC that structurally refuses key material | `protocol.py:37-48, 57-74` | live |
| A refusal-as-evidence discipline (`FAILED` receipts with named refusal codes) | `executor.py:161-171` | live |
| A canary that revokes on a HIGH finding by an anchored auditor | `monitor.high_findings_by_auditors`; `weave.canary_health` | live + tested |
| **One real outbound path** | `models_setup.py:133-216` (chat), `:220-259` (embeddings), both `urlopen` | live — see §4.3 |
| Its only control: a construction-time loopback check for `kind=local` | `models_setup.py:506-540`, applied at `:582-589` and `:332-338` | live, and narrower than it reads |
| Inbound exposure triple-gated | `decima/services/api/server.py:157-161` | live |

### 4.2 Missing

| Missing | Consequence today |
|---|---|
| Any component that decides **where** an outbound request may go, per call | The destination of the one live path is whatever `DECIMA_LIVE_BASE_URL` said at boot, re-resolved by `urllib` on every call. |
| A profile that permits egress **without** permitting a route | `WorkerProfile.network=True` means "no `CLONE_NEWNET`" (`execution.py:729`), i.e. *host routing table*. The only shape the type can express is the maximal grant. |
| A worker IPC that can carry a mid-run request | `protocol.py:26` is `PROTOCOL_VERSION = 1` and the shape is strictly one request → one response; `_spawn` reads the manifest pipe to EOF and *then* the result pipe to EOF (`execution.py:1073,1088`), so there is no channel a child could ask a question on. |
| Set-valued caveat semantics (subset, and empty = deny) | §5.3 — verified: a child may widen a list caveat and still prove "downhill". |
| A powerbox vocabulary for narrowing a set | `_BOUNDS` (ints, shrink) and `_CONSTRAINTS` (bools, add) at `powerbox.py:116-123`. There is no third category, so an allowlist cannot even be *requested* through the broker. |
| A Morta floor that can bite a network organ | `MORTA_FLOORS` (`capability.py:480-486`) is keyed on **effect**, and every organ's effect is `generated_code` (`executor.py:124,367`). Flooring that name floors `pure` organs too. The distinguishing fact is the **tier**, which `authorize_detail` deliberately does not key on because `attenuate` drops it (`capability.py:393-401`, `SECURITY.md:228-240`). |
| Any receipt of an outbound call | `routing.record` (`decima/models/routing.py:437-459`) writes a `model_routing` provenance Cell naming *which model was chosen and why*. Nothing records that a request left the box, where it went, or how many bytes. `models/accounting.py`'s `UsageRecord`/`record_usage` has **zero product callers**. |
| A redactor for outbound content | The only redactor in the tree is `decima/services/diagnostics/service.py:226-243`, a whole-line/entropy filter for support bundles. |
| A wire chokepoint | The reference arms one inside `urllib.request`'s global opener (`heartbeat/decima/wire.py`, described at `heartbeat/decima/egress.py:37-45`). `decima/__init__.py` arms nothing; there is no `decima/services/wire.py`. |

### 4.3 The comparison that matters: the product already talks to providers, and that path is trusted

This is the section to read if you read only one. **Decima's existing model-provider egress is
not mediated. It is trusted, and it is less confined than any effect a capability can reach.**

*How the call actually happens.* `decima/models/providers.py` is scrupulous: a
`ModelResponse` carries no capability, no key, no `.execute()`, and the docstring at
`providers.py:1-29` explains that a provider "PROPOSES" and holds zero authority.
`LocalProvider`/`CloudProvider` "make NO live network call by themselves"; the live seam is an
injected `backend` callable that "FAILS CLOSED (raises `LiveTransportRequired`)" when absent
(`providers.py:317-367`). All true — and all of it is about *authority over the proposal*, not
about *reach*. The `backend` that gets injected is
`models_setup.openai_chat_backend(base_url)`, and inside it is
`urllib.request.urlopen(req, timeout=timeout_s)` (`models_setup.py:176`), executing in the API
daemon process. `ModelStack.propose` is then called directly by the product lanes —
`qa_service.py:362`, `plan_service.py:676`, `workspace_service.py:476`.

*What gates it.* Nothing that the rest of this codebase would recognise as a gate:

| Control the ocap spine applies to an effect | Applied to a model call? |
|---|---|
| An identified principal + a grant in its envelope | **no** — there is no capability for "call a model" |
| An `INVOKE` on the Weft, authorized by `capability.authorize_detail` | **no** — the call is an ordinary Python function call |
| A Morta approval where the effect class is floored | **no** |
| An `EffectReceipt` recording what happened | **no** — only a `model_routing` Cell naming the *chosen model* (`routing.py:437`) |
| A jail (netns / chroot / rlimits / seccomp / non-dumpable) | **no** — it runs in the daemon, in the host network namespace |
| A digest binding on the code that runs | n/a (the adapter is trusted code) |
| A destination decision, per call | **no** — see below |

*The one control that does exist, and exactly what it is worth.* `build_model_stack`
**refuses to construct** a `kind=local` provider whose base URL is not loopback
(`models_setup.py:582-589`), and `build_embedder` does the same for embeddings
(`:332-338`), with an unusually good justification: a `local` provider is classed
`privacy_class=local_only`, the pure routing policy therefore treats it as eligible for
*sensitive* tasks, and imported personal documents are the input to every embedding call, so
a mislabelled "local" endpoint would exfiltrate precisely the data the privacy class promises
stays home. That reasoning is right. The enforcement has four holes, and each is a preview of
a hole the mediator must not have:

1. **It is resolve-then-trust.** `_base_url_is_loopback` calls `socket.getaddrinfo` once, at
   stack-construction time (`models_setup.py:527-540`); `urlopen` re-resolves on every
   subsequent call. A name that answers `127.0.0.1` at boot and something else afterwards
   passes the check and then leaves the box. This is DNS rebinding against the product's own
   privacy filter.
2. **It is first-hop only.** `urlopen` follows redirects. Verified against this tree's Python
   (3.11.15) with a two-server harness: a `302` on the POST is followed, the body is dropped
   (POST→GET, `Content-*` stripped), **and the `Authorization` header is forwarded to the
   redirect target** (`urllib.request.HTTPRedirectHandler.redirect_request` keeps every header
   except `content-length`/`content-type`). So today the *document text* does not follow a
   redirect — but the moment a `SecretsBroker` supplies a key (`providers.py:410-424`), the
   credential does, to a host nobody allowlisted.
3. **It is scoped to one config shape.** The check runs for `DECIMA_LIVE_PROVIDER=local` and
   `DECIMA_EMBED_PROVIDER=local`. `CloudProvider` is "deliberately NOT constructed here —
   that remains an operator decision" (`models_setup.py:32-35`). That is a real and defensible
   choice, but it means "Decima never talks to a cloud provider" is a *configuration* fact,
   not an enforced one; `openai_chat_backend` will POST to any base URL it is handed.
4. **There is no redaction and no accounting.** `request.context` is imported personal
   document text. It is framed *to the model* as untrusted data behind
   `_UNTRUSTED_PREFIX` (`models_setup.py:127-130`) — which is a prompt-injection control, not
   an egress control — and nothing scrubs it on the way out. No byte count, no cost, no
   destination is recorded anywhere.

*The asymmetry, stated plainly.* The daemon may POST personal documents to a host it resolved
once at boot, with no capability, no approval and no receipt. A promoted organ may not open a
socket under any circumstances, and the code says so in four places. If a mediator lands that
bounds only the organ, Decima will have built a careful cage around the least likely attacker.
Hence §7's ordering: **E3 (the product's own egress goes behind the seam) precedes E5 (the
tier becomes executable)**, and D2 makes that an explicit owner decision rather than a
sequencing preference.

One more asymmetry worth recording because it is the same code review, inverted:
`server.py:157-161` gates *inbound* off-loopback exposure three ways (an explicit
`allow_nonloopback=True`, provisioned per-user authentication, and the pairing secret). The
daemon is careful about who may reach in and indifferent about where it may reach out.

### 4.4 Where the reference cannot simply be copied

`heartbeat/decima/egress.py` is the right *vocabulary* and the wrong *enforcement point*.

- **The allowlist is checked by the caller, not by the socket.** `fetch()`
  (`egress.py:147-202`) matches `_host_of(url)` against the capability's `egress_allowlist`
  caveat and records an `egress_refusal` Cell on a miss — and then `_egress_handler`
  (`egress.py:85-98`), the thing that would actually make the request, checks **only** that
  the URL has a host. A holder that invokes the capability directly, without going through
  `fetch`, is unmediated. Shipping equivalent: any code path that reaches the effect handler
  without the helper. **The allowlist must be evaluated at the point where the connection is
  created, and it must be impossible to reach that point another way.** This is the same
  lesson `SECURITY.md:189-200` recorded for Morta floors — a rule enforced only by the code
  that happens to mint is a property of the minter, not of the grant.
- **Host matching is loose in the directions that matter.** `_host_of` lowercases and
  **strips the port** (`egress.py:77-83`), so an entry permits *any* port on that host; there
  is no scheme, method or path in the decision; and a name is never resolved, so the
  allowlist binds a label, not a machine.
- **The chokepoint is in-process.** `wire.py` arms a guard inside `urllib.request`'s global
  opener. `vision-surfaces.md:441` already called the limit: it "cannot bound a child
  process, which is where shipping effects run." It also cannot bound raw `socket`,
  `http.client`, or anything a `subprocess` does.
- **`k.integrate_tool` does not exist.** Same rewrite-against-extracted-seams problem the
  Nona design hit (`nona-self-extension.md:133-156`): the shipping tree is function-shaped
  over an explicit `Weft`, effects arrive through `executor.organ_effects`
  (`executor.py:726-750`), and `tests/architecture/test_no_raw_invoke_append.py` fails the
  build if a non-kernel module appends a raw `INVOKE`.

## 5. Architecture

### 5.1 The central choice: the child gets a file descriptor, not a network

`vision-surfaces.md` D4 recommends "worker netns routed only to a mediator". This design
**diverges**: route nothing. Keep `CLONE_NEWNET` mandatory for every profile — including the
mediated one — and give the child a brokered request/response channel over a pipe that the
parent already knows how to pass (`pass_fds`, `execution.py:1048-1057`, the same mechanism
that hands in the pinned `O_PATH` workspace fd).

```
  parent (trusted, in the daemon)                child (untrusted, jailed)
  ┌───────────────────────────────┐              ┌──────────────────────────────┐
  │ egress.policy  (pure)         │   cfg  ──▶   │ CLONE_NEWNET: no interfaces  │
  │ egress.broker  (the only      │   man  ◀──   │ chroot, rlimits, no-new-priv │
  │   code that opens a socket)   │   res  ◀──   │                              │
  │   resolve → pin → connect     │   egress ⇄   │ egress_request(dest, …)      │
  │   decision Cell BEFORE socket │   (NEW)      │   → a dict of DATA           │
  │   receipt AFTER               │              │ socket/urllib: no route      │
  └───────────────────────────────┘              └──────────────────────────────┘
```

Why this shape rather than a routed namespace:

1. **`run_worker`'s refusal never gets relaxed.** `execution.py:1181-1186` refuses
   `profile.network`. A mediated profile has `network=False`, so that line is *untouched* and
   `PROVIDER` stays refused forever. The strongest sentence in `SECURITY.md:285-293` survives
   the wave that unblocks the tier — the residual it describes stays unreachable, because the
   shape it describes (host routes) is never built.
2. **`network_isolation` stays `enforced: True` for every profile.** The containment matrix
   loses a gap row instead of gaining an exception (`execution.py:385-396` currently carries
   PROVIDER's "there is no network namespace and NO egress mediation"), and the cross-cutting
   `warnings` entry for "network permitted + no seccomp arch" (`:438-445`) becomes
   *unreachable* rather than merely rare.
3. **It keeps the Reckoner's Decision-5 rule true for this tier.**
   `reckoner.require_host_containment` **hard-requires** `network_denied` before it will
   record any result (`reckoner.py:199-204`), and `environment_digest` includes it
   (`reckoner.py:207-221`). Under a routed netns, evaluating a network candidate would require
   an exception to exactly the rule that exists to stop a weaker result being recorded under
   the same suite id. Under this design `net_isolated` stays true in the in-child manifest, so
   `_isolation_projection` (`executor.py:492-525`) keeps reporting `net_isolated: true` on
   every organ receipt and no exception is needed.
4. **The destination is decided by the parent, which is what kills the TOCTOU.** The child
   never resolves a name, never sees an address, and cannot connect to a resolved address —
   because it has no route at all. Match-then-resolve-then-pin all happen on the trusted side
   in one function.
5. **It is less code.** A routed namespace needs a veth pair or a socketpair-based TUN, an
   address plan, a transparent proxy that must intercept DNS, and — to enforce anything
   about *hostnames* rather than addresses — TLS interception with a local CA. That is a large
   new trusted surface whose failure mode is silent. A request/response verb over a pipe is a
   protocol version bump and one select loop.

The cost, stated: **the child cannot use arbitrary network libraries.** `socket`, `urllib`,
`requests` in generated source will fail (no route) rather than be transparently proxied. The
mediated profile therefore offers a *narrow declared verb* — one request/response call — not
"the internet". That is a feature by this repo's standards (`workspace_service.py:170-173`
already restricts executed check source to a declared catalogue) and it must be said in the
organ-authoring surface, because a candidate that imports `urllib` will not work and the
Reckoner should say so rather than letting it fail in the jail.

**And one thing this topology does NOT inherit, which arrived in the tree while this document
was being written.** "The mediated profile is PURE plus a pipe" is a statement about the
profile, not a statement that PURE contains a hostile effect. `docs/design/syscall-filtering.md`
§4.3–§4.4 reproduces two escapes against a real worker, and `SECURITY.md:302-336` now carries
both: `chroot()` does not move the cwd and the worker holds `CAP_SYS_CHROOT` over its own user
namespace, so a second `chroot` plus a `..` walk re-roots on the host filesystem (it read
`/etc/passwd`, listed `~/.ssh`, wrote host files — "there is no configuration in which this is
contained"); and the pinned `O_PATH` workspace fd is reachable through `sys.argv` and works as
an `openat` dirfd above the operator's containment root. Note precisely which layer survives
both: *"The PID and network namespaces still hold (a host PID is `ESRCH`; there is no route
out)."* That is simultaneously the strongest available argument for this topology — the netns
is the layer that has never been walked around, so build on it rather than replacing it with a
route — and a **hard prerequisite** on this design, because egress is exactly what those
escapes are currently missing. See §6 E-R0 and D10.

### 5.2 The policy object, and why the ceiling is not on the log

The precedent is `workspace_root`, and it should be copied literally.
`mount.resolve_bind_source(containment_root, relative)` (`mount.py:105-145`) takes two
arguments with deliberately different trust: a root **the operator declares out of band, never
on the Log**, and a relative subpath **a caveat may choose freely** — because "a caveat is data
written by whoever built the grant, so if the caveat could name an absolute path the grant
would choose its own blast radius" (`executor.py:55-59`).

Egress gets the same two-level shape:

| Level | What it is | Trust |
|---|---|---|
| **Ceiling** | An operator-declared allowlist handed to the broker at construction (`egress_policy=None` ⇒ concede nothing ⇒ every call refused). Entries are exact `(scheme, host, port, path-prefix, method)` tuples. | A deployment fact. Never on the Weft, never chooseable by a grant. |
| **Narrowing** | A set-valued caveat on the capability naming a **subset** of the ceiling. Absent ⇒ the whole ceiling (mirroring `WORKSPACE_CAVEAT`'s "absent means the whole conceded root", `executor.py:157-160`). | Data on the log, may only narrow. |
| **Audit** | Every decision and receipt carries the **digest** of the policy in force. | The policy stays off the log; which policy was in force does not. |

Matching rules, each chosen against a specific failure:

- **Exact host, no wildcards.** `*.example.com` hands the destination choice back to whoever
  controls the zone — the same defect as a caveat naming an absolute path. (D4.)
- **Port and scheme are part of the entry**, unlike the reference (`egress.py:77-83` strips
  the port).
- **Match BEFORE resolve.** The order is load-bearing and copies
  `resolve_bind_source`'s: refuse on the literal name first, so an unlisted name never becomes
  a DNS query (a query whose QNAME the child chose is an exfiltration channel — §6, E-R2).
  Then resolve, then refuse any answer in a loopback/link-local/private/multicast range unless
  the matched entry explicitly named a loopback endpoint (that carve-out is what lets the
  local llama.cpp endpoint keep working).
- **Resolve once, connect to the pinned literal address, set Host/SNI from the matched
  entry.** No re-resolution between the check and the connection. This is the exact analogue
  of the workspace bind's inode pin (`execution.py:686-693`: "the source path was swapped
  between the parent's check and this mount"), and it closes the same class of bug that
  `_base_url_is_loopback` has today.
- **Redirects are refused, never followed.** A `3xx` is returned to the caller as data
  (status + `Location` as an untrusted string). Following it would be a *second* destination
  decision, and it must be taken as one. This also permanently closes the
  credential-forwarding behaviour verified in §4.3.
- **Every refusal names its rule**, in the style `MountRefused` already uses, and lands as an
  `egress_refusal` Cell (the reference's shape, `egress.py:135-146`) — because a refused
  attempt is the highest-value signal this whole design produces (§6).

### 5.3 Does a destination allowlist work as an attenuable caveat? Measured: **no, not today**

The obvious shape is `caveats["network_allow"] = [...]`, narrowed by `attenuate` and proved by
`attenuation_valid`. It does not hold. Reproduced against `decima/kernel/capability.py` as it
stands:

| Attempt | Result | Why |
|---|---|---|
| parent `network_allow: ["api.example.com"]`, child `["evil.example.net"]` | `_caveats_downhill` → **True**; `attenuation_valid` → **(True, 'ok')** | For any key outside `_SHRINK_ONLY`, the rule is *"parent constraints must persist"* implemented as `if v and not cc.get(k): return False` (`capability.py:99-104`) — a **truthiness** test. A non-empty list satisfies a non-empty list. |
| parent `network_allow: []`, child `["evil.example.net"]` | **True** | `v` is `[]`, falsy, so the key is skipped entirely. "Deny everything" is the one value this rule cannot express — so `QUARANTINE_BASELINE`'s `network_allow: []` (`candidate.py:52-57`) constrains nothing, and nothing anywhere reads that key (it is the only occurrence in `decima/`). |
| child omits the key when the parent's list is empty | **True** | same falsy skip |
| `attenuate(parent, {"network_allow": [...]})` | child gets the caller's list **verbatim**; `target` is copied from the parent unchanged; `declared_effect_class` is **dropped** | `capability.py:518-546`. `attenuate` "does NOT validate — it writes every non-numeric `stricter` key VERBATIM" (`powerbox.py:26-33` says so). |

Four consequences for the design:

1. **A set-valued caveat needs its own narrowing rule**: a third category beside
   `_SHRINK_ONLY` (ints, may only shrink) and the boolean constraints (may only be added) —
   call it `_SET_CAVEATS`, whose rule is **child ⊆ parent**, with **present-and-empty meaning
   deny** and **absent meaning "inherit the parent's set"** rather than "no constraint". That
   last clause is what stops a child dropping the key to escape it, and it is the opposite of
   the current falsy skip, so it is a **read-side tightening** and must be measured before
   landing (E1) exactly as the grantee rule was: *"Measured before changing anything: zero
   product mint sites lacked a grantee"* (`SECURITY.md:186-188`). Every child `attenuate`
   produces copies the parent's caveats, so the expected blast radius is hand-built test
   fixtures — but that is a claim to verify, not to assume.
2. **The destination cannot live in `target` instead.** `attenuation_valid`'s target grammar
   is `*` ⊇ everything ⊇ an exact string (`capability.py:510-512`) — no sets, no suffixes —
   and `attenuate` does not even accept a narrower target (`capability.py:538` copies the
   parent's). So `target` can express "exactly one destination" or "anything", and nothing
   between.
3. **The caveat is an INPUT to the decision, never the enforcement.** `authorize_detail`
   reads nothing from `args` except `cost` (`capability.py:423`). It cannot compare a
   requested URL to an allowlist without a new clause, and even with one, a clause there would
   only bind callers who put the destination in `args` — the effect handler could ignore it.
   This is the reference's bug (§4.4) restated as a kernel fact: **the allowlist must be
   enforced in the broker, at the point of connection.** The caveat's job is to *narrow the
   ceiling the broker consults for this grant*, and the broker must read it from the folded
   capability rather than from anything the caller passes.
4. **The Morta floor cannot be keyed on the tier today.** `MORTA_FLOORS` is effect-keyed
   (`capability.py:480-486`), re-derived at read time since N7
   (`SECURITY.md:189-200`), and every organ's effect is `generated_code`
   (`executor.py:124,367`) — so flooring that name floors `pure` organs too. The two honest
   options are (a) give the mediated tier its **own effect name** (e.g.
   `generated_code_egress`) so the effect-keyed floor bites, at the cost of a second entry in
   `organ_effects` (`executor.py:726-750`) and a second `TIER_PROFILES`-shaped decision, or
   (b) close `SECURITY.md:228-240`'s live residual first — put `declared_effect_class` back
   into attenuated children, which **re-ids every `broker_grant:` cell** and is therefore its
   own wave. (a) is additive and is what E1 recommends; (b) is better and is D6's subject.
   Note one piece of luck: `powerbox.tier_floor` derives `requires_approval` from
   `SIGNER_POLICY` being `HUMAN` (`powerbox.py:133-152`) and `AUTO_TIERS` derives from
   `AUTOMATED` (`powerbox.py:109-112`), so setting `SIGNER_POLICY["network"] = HUMAN` in E5
   floors brokered egress grants and keeps them out of auto-issue **with no second table to
   remember**.

**And for DNS, redirects, and a host that resolves differently later, the caveat gives
nothing at all.** A caveat names a label. Pinning the resolution for the life of one call is
the broker's job (§5.2) and closes rebinding *within* a call. Nothing in the capability system
can close "the machine behind `api.example.com` is hostile today", because that is not a
statement about authority. The only binding available for *identity* is TLS: a certificate for
the allowlisted name, validated against a trust store the operator declares. So the honest
reading of an allowlist entry is **"this NAME, authenticated by TLS, on this port, for this
method and path-prefix"** — never "this machine", and never "this machine's owner is honest".

### 5.4 The profile, and what the containment matrix would then say

```
MEDIATED = WorkerProfile(
    name="mediated",
    network=False,          # CLONE_NEWNET still engages: no interfaces, no route out
    filesystem_jail=True,
    namespaces_mandatory=True,
    egress_broker=True,     # NEW, and REQUIRED like workspace_bind: a dispatch with no
                            # broker channel is REFUSED, never downgraded to PURE
)
```

`workspace_bind`'s discipline is the model and every clause of it transfers
(`profiles.py:41-44`: *"Required, not permitted: a dispatch under such a profile with no
declared subtree is refused, so the profile can never decay into the one below it"*). So:
a `MEDIATED` dispatch handed no broker is an `IsolationError`; a broker handed to a profile
that declares none is an `IsolationError` (both directions, as at `execution.py:1195-1205`);
and the in-child manifest reports whether the channel was actually present, so the receipt's
`_isolation_projection` can carry `egress_broker: true/false` the way it already carries
`workspace_bind` (`executor.py:517-524` — "a receipt saying the organ ran without the
containment its tier is named for … is exactly the thing a reader must be able to see").

Matrix consequences (`containment_report`, `execution.py:158-477`):

- `egress_mediation` becomes `enforced: True` **for the mediated profile**, with a
  `manifest_proof` key, so `tests/adversarial/test_containment_matrix.py` proves it against a
  real worker manifest rather than a claim. For every other profile it stays what it is: not
  applicable.
- `network_isolation` stays `enforced: True` for **every** profile; the PROVIDER gap text and
  the top-level `warnings` clause survive only as the description of the shape we refuse.
- `CONTAINMENT_MATRIX_VERSION` 3 → 4. This is not a fixture; see §5.6 for the real cost.
- The row that must **not** be added: anything claiming redaction. See E-R5.

### 5.5 The IPC: one new channel, and the one delicate change in the plan

`protocol.py` is versioned and fails closed on an unknown version (`protocol.py:1-18,
158-163`), which is exactly the affordance needed. `PROTOCOL_VERSION` 1 → 2 adds two message
kinds on a **new, fourth pipe** — not on the existing `cfg`/`manifest`/`result` fds, whose
shapes stay byte-identical so a v1 encoder/decoder is unaffected:

```
EgressRequest  { protocol_version, invocation_id, seq, destination{scheme,host,port,path,method},
                 headers{}, body_ref|body, max_response_bytes }
EgressResponse { protocol_version, invocation_id, seq, decision: ALLOWED|REFUSED,
                 refusal, status, headers{}, body, bytes_out, bytes_in }
```

`_assert_no_private_keys` (`protocol.py:57-74`) already runs on encode *and* decode and
covers both directions for free — the broker must reuse `encode_*`/`decode_*` rather than
hand-rolling JSON, so a key can never ride outbound in a header.

**The delicate part is the parent's read loop.** `_spawn` today reads the manifest pipe to
EOF and then the result pipe to EOF (`execution.py:1073,1088`), each via `_read_to_eof`'s
single-fd `select` against one wall-clock deadline (`execution.py:957-968`). A child that
blocks writing an egress request while the parent is blocked reading the result pipe
deadlocks until the timeout kills it — which would surface as a mysterious `UNKNOWN`, the
worst possible failure mode because `UNKNOWN` is deliberately *not* a pass
(`nona-self-extension.md:266-269`). So E4's real content is: turn those two sequential reads
into one multiplexed `select` over `{man_r, res_r, egress_r}` with the same single deadline,
and prove three properties adversarially — a child that never asks still completes
identically; a child that asks and ignores the answer still hits the wall-clock backstop and
still yields `UNKNOWN`; and a broker call's own timeout is *inside* the worker's deadline so a
slow destination cannot extend the worker's budget. Wall-clock stays where it already is:
outside recorded content.

### 5.6 What is recorded, and what must never be

Two events per call, and the order is the security content — the reference got this right
(`egress.py:37-45`: the wire gate "records every allow/deny decision on the Weft **before the
socket exists**"):

1. **The decision, before the socket.** An `egress_decision` Cell: the invocation it belongs
   to, the matched entry's index or the refusal rule, the policy digest, `allowed: bool`.
   Authored by the broker's principal, edged to the invocation. A crash between the two events
   leaves a decision with no outcome, which is honest; one event written after the fact would
   have to lie in one direction or the other.
2. **The outcome, after.** Integers and digests only: `bytes_out`, `bytes_in`, `status`, a
   `blob_id` of the response body. **Never** the body itself in hashed content, **never**
   latency, **never** the resolved IP address (host-variable — a DNS answer differs between
   honest hosts and would break replay exactly as `_isolation_projection` explains for the
   seccomp state, `executor.py:497-505`).

Determinism, restated for this lane: a response is not replayable, so nothing derived from it
may enter the gate or a signed comparison. The body is untrusted DATA
(`instruction_eligible: False`), stored like any other admitted content, and — per
`vision-surfaces.md:330-347` — it is the *largest* firehose of attacker-chosen text the
product would have. Nothing in this design makes an outbound response safe to obey.

**The `environment_digest` consequence, priced.** `matrix_version` is inside
`reckoner.environment_digest` (`reckoner.py:214-221`). Bumping `CONTAINMENT_MATRIX_VERSION`
therefore changes that digest on every host, which means (a) any suite that *pinned* a digest
refuses to evaluate afterwards, by design — `require_host_containment` says so
(`reckoner.py:192-198`); and (b) newly recorded `evaluation_result` content differs, so it is a
new content-addressed cell. Existing promotions cite existing results and keep working. The
product lane deliberately declares `environment_digest=""` today
(`nona_service.py:522-525`), so the practical bill is small — but it is a **re-evaluation**
bill, and it should be paid deliberately in the wave that bumps the constant, not discovered.
D9 asks the related question: should the **policy digest** join the environment digest, so a
suite can pin *which allowlist was in force*? The Decision-5 logic applies verbatim, and the
answer is probably yes.

### 5.7 What would have to be TRUE before `SIGNER_POLICY["network"]` changes

Not a wish list — a checklist, each item with the test that would prove it. Until every line
is green, the Shell keeps saying **NOT EXECUTABLE — no mediated egress** and that label is
the truth.

0. **The two verified jail escapes are fixed** — `pivot_root` (or dropping `CAP_SYS_CHROOT`
   once the jail is built) and closing the pinned workspace fd before the implementation runs
   (`SECURITY.md:302-336`, `docs/design/syscall-filtering.md` §4.3–§4.4, that document's **S0**).
   Numbered zero because it is not this design's residual and not this design's fix, but it is
   this design's prerequisite: mediation supplies the *destination* those escapes currently
   lack. *(The hostile-effect tests that document's S0 owes: a second `chroot` fails; an
   `openat` through `sys.argv`'s dirfd fails.)*
1. `run_worker` still refuses `profile.network` for every caller, and `PROVIDER` is still
   refused. *(The existing adversarial test, unmodified.)*
2. A mediated profile exists whose network namespace **still engages**, proven from the
   in-child manifest, not from the profile table. *(A real worker manifest shows
   `net_isolated: true` **and** `egress_broker: true` on the same run.)*
3. A worker under that profile **cannot reach the network except through the broker.** *(In
   the jail, `socket.create_connection` to the broker's own destination fails; the same
   destination through `egress_request` succeeds.)*
4. An off-ceiling destination is refused **with no socket opened**. *(A listener on the
   refused destination records zero connections; the refusal lands as a Cell.)*
5. The **grant cannot choose the blast radius**: the ceiling is operator-declared, a caveat
   may only name a subset, and an entry outside the ceiling is refused with a named rule.
   *(The `resolve_bind_source` test shapes, transposed: absolute-equivalent = an unlisted
   host; traversal-equivalent = a wildcard; symlink-equivalent = a name that resolves into a
   refused address range.)*
6. Set-valued caveats are **subset**-checked, and an empty set **denies**. *(E1, with the
   positive control that every existing bool/int caveat behaves identically.)*
7. An unfloored egress grant **authorizes nobody at read time** — `MORTA_FLOOR_MISSING`, the
   denial an approval cannot clear. *(Mirrors `tests/kernel/test_denial_codes.py`'s unfloored
   Morta grant, with its positive control.)*
8. Every call has a decision Cell **before** the socket and a receipt after, and a refused
   attempt is visible to `monitor.high_findings_by_auditors`.
9. **The product's own model egress goes through the same broker** (E3). Otherwise the claim
   is asymmetric and §4.3 is still true.
10. `SIGNER_POLICY["network"] = HUMAN` — **never** `AUTOMATED` — and `anchors.SIGNABLE_TIERS`
    still excludes it, exactly the precedent `workspace_write` set
    (`anchors.py:63-70`: "Having an executor and being auto-promotable are two separate
    grants").

### 5.8 Deliberate divergences from the heartbeat reference

1. **Enforcement at the connection, not in a helper** (§4.4). The single most important one.
2. **No route, ever** — a brokered verb instead of a proxied namespace, which is also a
   divergence from `vision-surfaces.md` D4-B (§5.1).
3. **No wire monkeypatch.** The reference arms a guard inside `urllib.request`'s global opener;
   this design does not, because the shipping tree's outbound calls are two named functions
   (`models_setup.py:176,241`) and *changing those two* is stronger than a process-global hook
   that cannot bound a child. A lint-style architecture guard (no `urlopen` outside
   `decima/services/egress/`) is the honest replacement, and it fails the build instead of
   failing at run time.
4. **No wildcard hosts, ports and schemes in the entry** (the reference strips the port).
5. **No redaction claim** (E-R5). The reference's profile note promises a
   "mediation/redaction seam"; this design ships the seam defaulted to identity and asks for
   the comment to stop promising a redactor (D7).
6. **Refusals are evidence with teeth.** The reference records an `egress_refusal` Cell. This
   design routes it into the existing canary so a pattern of refused destinations becomes a
   HIGH finding and an automatic revoke — which is the one thing mediation buys that testing
   cannot (§6).

## 6. Trust and safety analysis

**Start from what this cannot be.** `KERNEL.md:233`'s warning about test-gated promotion has
an exact analogue here: *an allowlist is only as good as the assumption that an allowed
destination is not a channel, and that assumption is false.* Mediation does not prevent
exfiltration. What it does is **make the destination a decision, and the attempt a record** —
and this repo already has a machine that acts on records (`monitor.monitor_canary` demotes on
a breach and revokes on a HIGH finding by an anchored auditor). That conversion — from an
unobservable capability into an auditable, budgeted, revocable one — is the whole value, and
it should be the only thing release notes claim.

Ranked by what they actually buy:

| Layer | What it survives | Where it would live |
|---|---|---|
| **The child has no route** | every library-level escape: `socket`, `urllib`, a spawned process, a DNS query the child composes | `CLONE_NEWNET` unchanged (`execution.py:729`), profile `network=False` |
| **The destination is chosen by the parent, from an operator-declared ceiling** | a grant, a caveat, or generated code trying to pick its own destination | `egress.policy` + the `mount.py:10-17` precedent |
| **Match before resolve; resolve once; connect to the pinned address** | DNS rebinding between check and connect — the hole `_base_url_is_loopback` has today | `egress.broker` |
| **Redirects refused** | a first-hop-only allowlist; the verified `Authorization`-forwarding behaviour (§4.3) | `egress.broker` |
| **Decision recorded before the socket exists** | "we cannot tell what it tried to do" | `egress_decision` Cell |
| **Refusals folded into canary health** | an organ that passed its suite and is now probing | `monitor.high_findings_by_auditors` |
| **Byte + call budgets as shrink-only caveats** | unbounded volume through an allowed destination; a covert channel's bandwidth | `_SHRINK_ONLY`-style caveats |
| **Morta floor at READ time on the egress effect** | a minting authority that forgot the gate (`SECURITY.md:189-200`) | `MORTA_FLOORS` + `authorize_detail` |
| **Leases** (`expires_at`, `max_uses`) | an organ that is fine now and wrong later | `capability.py:211-238` (`lease_status`), unchanged |
| **Revocation cascade** | delegated egress surviving a revoke | `weave.py` cascade, unchanged |
| **The response is DATA** | the largest injection firehose the product would have | the untrusted-content discipline (and `vision-surfaces.md` S0's structural boundary, which does **not** exist in `decima/` yet) |
| **An allowlist** | *only* a destination that is not on it. Nothing about what leaves through one that is. | — |

### Named residual risks this design does not close

- **E-R0 — the destination is the thing the existing jail escapes are missing, and it is what
  this design supplies.** Numbered zero because it is not a residual *of* this design: it is a
  live defect in the layer beneath it, landed in the tree
  (`SECURITY.md:302-336`, `docs/design/syscall-filtering.md` §4.3–§4.4) while this document was
  being written, and it changes this design's prerequisites. A hostile effect under **any**
  profile can escape the chroot (a second `chroot` plus a `..` walk; verified reading
  `/etc/passwd`, listing `~/.ssh`, writing host files) and a `WORKSPACE` effect can reach above
  the operator's containment root through the inherited `O_PATH` dirfd. `SECURITY.md` states
  the blast radius exactly — the filesystem "is where the Weft database, the keystore, and
  `~/.ssh` live" and the worker acts as the parent's real uid — and states what still holds:
  *"there is no route out."* So today those escapes yield **read without reach**. Egress
  mediation converts that into **read with a bounded, budgeted, recorded reach** — which is
  strictly better than an unbounded one and strictly worse than the status quo, because the
  payload available to E-R1 is not a prompt, it is a signing key. Two consequences, both
  non-negotiable: (a) the escapes are fixed **before** E5, not in parallel with it (D10, §5.7
  item 0); (b) even after they are fixed, this stays the argument for keeping the netns rather
  than routing it — it is the one layer nobody has walked around.
- **E-R1 — Exfiltration through an allowed destination is not closed, and cannot be.** The
  canonical instance is the model provider itself: a prompt field is an arbitrary
  attacker-chosen string, and the endpoint is on the allowlist by construction. Every allowed
  request has a path, a query, headers and a body, all of which carry bytes the organ chose.
  Bounding it means bounding *volume and count* (byte budgets, call budgets, response-size
  caps) and *recording* every one, not preventing it. **The claim to make is "an organ may
  talk only to destinations the operator conceded, only within a budget, and every attempt is
  on the log" — never "an organ cannot leak."**
- **E-R2 — DNS is both a side channel and a naming problem.** Match-before-resolve stops a
  child's chosen QNAME from ever reaching a resolver, which closes the classic
  `<base32-secret>.evil.com` beacon. Three things remain: a wildcard entry would re-open it
  entirely (hence D4 = never); an allowed HTTPS destination that happens to be a DoH resolver
  *is* arbitrary DNS, so the ceiling must be read as "these endpoints", not "these
  protocols"; and resolver-cache timing is observable to a local co-tenant. Also worth stating
  because it is easy to forget: the broker's own resolution leaks the *set of allowlisted
  names* to the operator's resolver — which is a privacy fact about the box, not a
  capability leak.
- **E-R3 — A host that resolves differently later is out of scope by construction.** An
  allowlist entry names a label; per-call pinning closes intra-call swapping; nothing closes
  "the server behind that label is now hostile". The only identity binding available is TLS
  against an operator-declared trust store, so an entry means *name + TLS + port + method +
  path-prefix*. An IP allowlist would be neither necessary (CDNs) nor sufficient (shared
  tenancy).
- **E-R4 — Timing and volume channels survive everything.** Request count, sizes and
  inter-arrival times leak to an allowed destination even if the payload were fully fixed. A
  budget of *n* calls leaks on the order of log₂(*n*) bits per invocation, not zero. This is a
  bound, not a fix, and the fix does not exist.
- **E-R5 — Redaction is not something this design can honestly deliver.** The only redactor
  in the tree is a line-oriented keyword/entropy filter for support bundles
  (`diagnostics/service.py:226-243`). Applied to a prompt it would neither preserve meaning
  nor guarantee removal — "does this sentence contain a secret" is not a decidable
  keyword question. So: ship the seam defaulted to **identity**, never let a policy or a
  receipt record the word "redacted" unless a caller supplied a redactor, and **change
  `profiles.PROVIDER`'s docstring and `containment_report`'s row so the code stops promising
  one** (D7). Shipping a scrubber that appears to work is worse than shipping none.
- **E-R6 — The rug-pull scan goes blind at exactly this tier.** `stages._REACH`
  (`stages.py:50-70`) maps `socket`/`urllib`/`http`/`asyncio` to `"network"` and fires a HIGH
  finding only when `_rank(needed) > allowed`. A candidate that *declares* `network` therefore
  gets **no** finding for importing them — and because the ladder is totally ordered
  (`candidate.py:61-67`: `pure < read_only < workspace_write < network < financial`), it also
  gets no finding for `subprocess`, `ctypes`, `shutil` or `os`, which sit *below* `network`.
  So the tier that most needs the scan is the tier where the scan says least. The mediated
  profile must still deny process and filesystem reach at the OS level — it is PURE plus a
  pipe, and per E-R0 that is a weaker sentence today than it reads — and the scan needs a
  per-tier reach *set* rather than a rank comparison. That is a real change and it belongs in
  E5, not later.
- **E-R7 — The baseline adversarial case currently proves the wrong thing, and after E5 it
  proves nothing.** `attack.network_egress` (`nona_service.py:186-200`) is evaluated under
  `PURE`, where there is no route at all, so it is contained by the absence of a mechanism. A
  mediated organ needs different attacks: *asks for an off-ceiling destination*, *asks for an
  on-ceiling destination beyond its caveat's subset*, *exceeds its byte budget*, *follows a
  redirect*. And they must run against an **injected stub broker** that opens no socket
  (keeping evaluation deterministic and replayable, the same reasoning that makes `run` an
  injected seam at `stages.py:27-30`). The residual that leaves is blunt: **an organ evaluated
  against a stub broker has never made a real call**, so its evaluation proves conformance to
  the broker's contract, not behaviour against a real endpoint. That is the same
  deterministic-default-versus-live-qualification split the model lane already lives with, and
  it should be labelled, not hidden.
- **E-R8 — The broker is new trusted code in the daemon, and a bug in it is ambient egress.**
  It is the only component in the system whose *job* is to open sockets. Bounds: it mints
  nothing, holds no key (`protocol._assert_no_private_keys` covers both directions if it
  reuses the encoders), reads the ceiling from construction rather than from any Cell, and
  reads the narrowing from the *folded* capability rather than from a caller argument. It
  should also be the only module in `decima/` permitted to import `urllib`/`socket` for
  outbound use, enforced by an architecture test that fails the build.
- **E-R9 — Terminating TLS in the broker moves a hostile-input parser into the trusted
  process.** The alternative (a blind tunnel) means the broker cannot enforce Host, method or
  path, cannot count anything but bytes, and cannot ever redact — i.e. it stops being a
  mediator. Recommendation: terminate (D5), and note honestly that the daemon already parses
  hostile HTTP today with stdlib `urllib` (`models_setup.py:176`), so this is a *relocation*
  of an existing exposure rather than a new one.
- **E-R10 — `attenuate` drops the tier, so a brokered child of an egress organ is the sharpest
  edge in the design.** `SECURITY.md:228-240` already carries this: a brokered child carries
  `effect: generated_code` and **no tier**, so a tier-keyed floor is not a pure function of
  the folded cell, and `powerbox.request_capability` takes `tier` from the *caller*. For a
  `financial` organ that is bad; for an egress organ it is worse, because the destination set
  is exactly the thing a child must not widen — and §5.3 shows the set caveat would not stop
  it either until E1 lands. This design's answer is (a) a distinct effect name so the
  effect-keyed floor bites, plus (b) a recommendation to close the tier residual before E5
  (D6).
- **E-R11 — The ceiling is not on the log, so it is unforgeable and also unauditable.** Same
  trade `workspace_root` makes. Mitigation: the *digest* of the policy rides on every decision
  Cell and receipt, so a reader can tell which ceiling was in force and detect that it
  changed, without the policy itself becoming forgeable content.
- **E-R12 — Rollback never un-sends a request.** The `NONA_RECKONER.md:218` rule
  (`nona-self-extension.md` R5) applies with full force: revoking an egress organ contains and
  compensates. Every rollback of a network-tier organ must assert an `incident` Cell and say
  so in the Shell, and the approval text for the tier must state irreversibility outright —
  `vision-surfaces.md:331` already says it for ops lanes ("a published post cannot be
  retracted from the world, only from the log").
- **E-R13 — Non-guarded Cell retraction and ASSERT are still unauthenticated in the kernel**
  (`SECURITY.md:241-250`). `egress_decision`, `egress_refusal` and a receipt are non-guarded
  types, so a key-holder can retract or re-type them. The `finding` precedent is the fix
  pattern and it is not optional here: anything the canary reads about egress must be derived
  from the **asserting events** and judged against an anchored auditor, exactly as
  `monitor.high_findings_by_auditors` now does, or the containment action is suppressible by
  one unauthenticated event (`SECURITY.md:154-168`).

## 7. Phased plan

Each wave is independently gate-green: `python3 -m ruff format --check . && python3 -m ruff
check .`; `python3 -m mypy` (bare); the full `pytest` suite; the conformance +
`tests/adversarial` lane; `npx playwright test` from the repo root. All new code lives outside
`decima/kernel/` except the two named kernel edits in E1, so
`tests/architecture/test_import_boundaries.py` is untouched and **no new third-party
dependency is required by any wave** (stdlib `urllib`/`socket`/`ssl` already exist outside the
TCB).

| Wave | Content | Touches kernel? | Fixtures? |
|---|---|---|---|
| **E0** | **Decisions locked** (§9). No code. Also: correct the two comments that currently promise a redactor (`profiles.PROVIDER`, `containment_report`'s `egress_mediation` row) — a doc-only change that stops a false claim. | no | no |
| **E1** | **Make the caveat vocabulary able to express a destination set, and floor the effect.** `_SET_CAVEATS` in `capability.py`: child ⊆ parent, present-and-empty = deny, absent = inherit. Plus the egress effect name in `MORTA_FLOORS`, so `authorize_detail` answers `MORTA_FLOOR_MISSING` for an unfloored grant *before any such grant exists* (blast radius zero, measured — the ideal moment). Plus a `_SETS` category in `powerbox`'s request vocabulary. **Nothing can egress after this wave**; it is the read-side rule arriving before the mechanism, deliberately. | **yes** (`capability.py`, read-side only) | no — and **do not** normalize caveat bodies at mint; a sorted-list rewrite would move capability content addresses. If a golden vector would move, **stop and report**. |
| **E2** | **The policy and the broker, refusing everything.** `decima/services/egress/policy.py` (pure: normalization, exact matching, match-before-resolve, address-range refusal, one named rule per refusal) and `broker.py` (resolve-once-and-pin, no redirects, TLS to the entry's name, decision Cell before the socket, receipt after, byte caps). Shipped default: **empty ceiling ⇒ every call refused.** No worker change, no profile change, no product lane touched. Adversarial: an off-ceiling host is refused with **zero** connections observed by a listener on it; a redirect is refused; an allowlisted name resolving into a private range is refused; a suffix match is refused. | no | no |
| **E3** | **Put the EXISTING egress behind the seam.** `openai_chat_backend` / `openai_embeddings_backend` call the broker instead of `urlopen`; the loopback rule becomes a *policy entry* enforced per call rather than a constructor check; an architecture test forbids `urlopen`/raw `socket` outbound anywhere but `decima/services/egress/`. The deterministic-offline path must stay byte-identical (it never reaches the broker) so every existing test that does not configure a live provider is unaffected. **After this wave §4.3 stops being true, and it is the only wave with standalone value even if the tier never ships.** | no | no |
| **E4** | **The `MEDIATED` profile + protocol v2.** `network=False`, `egress_broker=True`, required-in-both-directions; a fourth pipe; `EgressRequest`/`EgressResponse` through the existing encoders; `_spawn`'s two sequential `_read_to_eof` calls become one multiplexed `select` under one deadline; `egress_broker` in the in-child manifest and in `_isolation_projection`; matrix row flips, `CONTAINMENT_MATRIX_VERSION` → 4. Adversarial: no route from inside the jail; a child that never asks behaves identically to v1; a child that asks and stalls still yields **UNKNOWN**; a slow destination cannot extend the worker's budget. | no (workers are not kernel) | no — the v1 `cfg`/`manifest`/`result` shapes are unchanged. **Re-shaping any existing v1 field is the tripwire: stop and report.** |
| **E5** | **The tier becomes executable — and NOT before `docs/design/syscall-filtering.md`'s S0 has closed the two verified jail escapes (E-R0, D10).** `TIER_PROFILES[network] = MEDIATED`; a distinct egress effect name wired into `organ_effects`; `SIGNER_POLICY["network"] = HUMAN` (`tier_floor` then floors it and `AUTO_TIERS` excludes it, with no second table); `anchors.SIGNABLE_TIERS` **still** excludes it; the Shell label moves from NOT EXECUTABLE to HUMAN **and shows the destination set inline**; `powerbox.policy_decision`'s DENY clause stops firing for the tier, so its floor must be proven in the same commit. Reckoner: the four new baseline attacks against an injected stub broker, and a per-tier reach set replacing `_REACH`'s rank comparison (E-R6). | no | no |
| **E6** | **Canary + surface.** Receipts carry destination index, `bytes_in/out` and the policy digest; refused-destination patterns become a HIGH finding **derived from asserting events and judged against an anchored auditor** (E-R13), so the existing auto-revoke fires on a probing organ; the Shell shows an organ's destinations, its refusals and its budget consumption. Playwright: an organ card renders its destination set and a refusal. | no | no |

**Fixture position.** `protocol/fixtures/fold.json` pins a 6-event script with
`type_counts == {note: 1, type: 1}` — no capability, no receipt, no worker, no promotion. So
none of E0–E6 alters a golden vector and **`needs_fixture_regen = false`** throughout. The
three ways an implementer could break that, each of which is a **stop-and-report** rather than
a judgement call: (a) normalizing set-valued caveats into sorted form *at mint* in E1 (moves
capability content addresses); (b) re-shaping an existing worker-IPC v1 field in E4 instead of
adding a channel; (c) recording a response body, a resolved address, or a latency in hashed
content anywhere. `CONTAINMENT_MATRIX_VERSION` 3 → 4 is not a fixture — it is a
re-**evaluation** cost (§5.6).

**Decision points inside the plan.** After E2: does the policy refuse the *interesting* cases,
or only the obvious ones? (Write the rebinding and redirect attacks first; if the policy
passes them, E3 waits.) After E3: is the live local-model lane still green with the
per-call check, and did the qualification path change shape? (If the answer needs an
exception, the exception is the finding.) After E4: does a stalled broker call reliably yield
`UNKNOWN` rather than a fabricated `FAILED`? After E5: does the canary fire on a **real**
probing organ, or only on a synthetic refusal? — note that the equivalent question from the
Nona plan (`nona-self-extension.md:638-641`) is **still unanswered** for the existing canary,
and this design should not pretend to answer it twice.

## 8. Non-goals

- **A mediated browser, or arbitrary sockets.** The mediated profile offers one
  request/response verb. A stream, a WebSocket, a raw socket, or a headless browser is a
  different design with a different trust analysis (`vision-surfaces.md:411-415` defers both).
- **Inbound anything.** No listener, no webhook, no MCP transport. Egress is outbound only;
  inbound exposure keeps its existing triple gate (`server.py:157-161`).
- **Cloud provider credentials.** Secrets stay with the `SecretsBroker` seam
  (`providers.py:307-313, 410-424`), applied inside the broker and never stored. Whether the
  operator configures a cloud model is a policy decision the mediator makes *safer to make*,
  not one it makes.
- **Redaction.** Explicitly not delivered (E-R5); the seam exists and defaults to identity.
- **A remote candidate source or capability marketplace.** Still out of scope
  (`nona-self-extension.md:645-647`), and egress does not move it: a remote candidate is an
  untrusted candidate authored by an unknown principal.
- **Auto-promotion of the `network` tier.** Ever. §5.7 item 10, D3.
- **A new store, a new verb, or a new dependency.** None are needed. `egress_decision`,
  `egress_refusal` and the receipt are ordinary content Cells.
- **Fixing the two live cross-principal retractions** (`invoke.py`'s approval consumption,
  `cancellation.py`'s lease termination) that block a general kernel retraction rule
  (`SECURITY.md:241-250`). This design works around them the way `monitor.py` did, per E-R13.

## 9. Decisions required (owner sign-off)

**Decision 1 — Topology.**

| Option | Trade |
|---|---|
| **A. Brokered request/response over a pipe; the child keeps `CLONE_NEWNET` and gets no route** *(recommended)* | `run_worker`'s refusal is never relaxed; `network_isolation` stays enforced for every profile; the Reckoner's `network_denied` requirement keeps holding; no DNS/TLS interception. Costs: protocol v2, a multiplexed read loop, and generated code cannot use ordinary network libraries. |
| B. Routed netns + transparent proxy (`vision-surfaces.md` D4-B) | Generated code uses normal libraries. Costs: an address plan, DNS interception, and TLS interception with a local CA to enforce *hostnames* at all — a large new trusted surface whose failure mode is silent — plus a Decision-5 exception for every evaluation. |
| C. Pass a pre-connected socket fd | Parent picks the destination (good) but the child then speaks TLS, so the broker cannot see Host, method, path or bytes-in-context — it becomes a blind pipe that can only count. |

**Decision 2 — Does the product's own model egress go behind the seam before any organ gets a
tier?** Recommendation: **yes — E3 before E5, non-negotiably.** Otherwise Decima ships a cage
around generated code while the daemon POSTs personal documents to a host it resolved once at
boot (§4.3).

**Decision 3 — Is `network` ever automatically promotable?** Recommendation: **no, and
`anchors.SIGNABLE_TIERS` keeps excluding it even after the executor exists** — the precedent
`workspace_write` set (`anchors.py:63-70`). Having an executor is not being auto-promotable.

**Decision 4 — Wildcards / subdomains in the allowlist?** Recommendation: **never.**
`*.example.com` returns the destination choice to whoever controls the zone, which is the same
defect as a caveat naming an absolute path — and it re-opens DNS as an exfiltration channel
(E-R2). Expect pressure on this one from the first real endpoint that shards by subdomain;
the honest answer is to enumerate.

**Decision 5 — Does the broker terminate TLS?** Recommendation: **yes.** Tunnelling makes the
mediator unable to enforce anything but bytes. The cost is a hostile-HTTP parser in the
daemon, which is a relocation of an exposure that already exists (E-R9).

**Decision 6 — Do we close the `attenuate`-drops-the-tier residual before E5, or route around
it with a distinct effect name?** Recommendation: **route around it in E1 (additive, zero
blast radius) and close it properly in its own wave** — putting `declared_effect_class` back
into attenuated children re-ids every `broker_grant:` cell, which is not an additive refusal
and deserves the same care N7 got (`SECURITY.md:228-240`).

**Decision 7 — Do we claim redaction?** Recommendation: **no.** Ship the seam defaulted to
identity and correct the two comments that promise a redactor. A profile note that describes
a component nobody is building is the same defect class as a vacuous test.

**Decision 8 — Keep `PROVIDER`, or delete it?** Recommendation: **keep it, refused, and
retitle it as the shape we refuse.** Its permanent refusal at `execution.py:1181-1186` and its
matrix row are a live test that "host routes" never becomes reachable. Deleting it would
remove the counter-example along with the dead code.

**Decision 9 — Does the suite's `environment_digest` pin the policy digest, and does a network
candidate evaluate against a stub broker?** Recommendation: **yes to both.** Decision 5's
reasoning applies verbatim — a host (or a ceiling) that cannot deliver the declared
containment should refuse to evaluate rather than record a weaker result under the same suite
id — and a stub keeps evaluation deterministic and socket-free, at the cost named in E-R7.

**Decision 10 — Does E5 wait for the jail escapes to be fixed?** Recommendation: **yes, and
say so in both documents.** `docs/design/syscall-filtering.md` §4.3–§4.4 verified that a
hostile effect can leave the chroot on every architecture and every profile, and that a
`WORKSPACE` effect can write above the operator's containment root through an inherited
descriptor; `SECURITY.md:302-336` records both, including that the filesystem in question
holds the Weft database and the keystore, and that what contains them today is that there is
**no route out**. E5 is the wave that removes that sentence. Shipping E5 first would be
building the destination before closing the door — and the cost of waiting is one document's
S0, which is already scoped and sequenced first there. (This is a *sequencing* decision, not a
scope decision: E1–E4 are unaffected and E3 is where most of the value is.)

## 10. Risks

- **This design's prerequisite is somebody else's wave.** E-R0/D10 make E5 depend on
  `docs/design/syscall-filtering.md`'s S0, which is scoped but not built. The failure mode is
  not that the dependency is missed — it is that E1–E4 land, look complete, and someone flips
  `TIER_PROFILES[network]` as a one-line follow-up because the hard parts are done. The
  mitigation is that the flip is one line and therefore has to be *guarded by a test*, not by
  a paragraph: E5 should not be mergeable while the hostile-effect tests that document owes
  are absent.
- **The most likely real exfiltration path after all of this is the model provider, which is
  on the allowlist by construction.** Everything in §6 is a bound on volume and an improvement
  in auditability; none of it makes an allowed destination safe. If the release notes say
  anything stronger, they are wrong.
- **E4's multiplexed read loop is the single most dangerous change in the plan.** It sits
  between the wall-clock backstop and the `UNKNOWN`-versus-`FAILED` distinction, which is the
  honesty rule the whole promotion gate rests on (`nona-self-extension.md:266-269`). A
  deadlock there is indistinguishable from a slow organ. It deserves its own review and its
  own adversarial tests, and it should not share a commit with anything else.
- **E1 is a read-side tightening on a load-bearing pure function.** `_caveats_downhill` is
  consulted by both `verify_delegation_detail` and `attenuation_valid`, i.e. by every
  authority read on every log including restored backups. The mitigation is the one N7 used:
  measure the product mint sites first, make the new branch unreachable for every existing
  bool/int caveat, and pair each refusal test with a positive control on the same fixture.
- **An operator will want the wildcard**, and will otherwise maintain the ceiling by hand
  until they stop. A ceiling nobody updates fails closed (good) and gets routed around by
  turning the feature off (bad). Whether an enumerated allowlist is *operable* is genuinely
  unknown and is a UX question, not a security one.
- **Byte budgets meet float arithmetic.** `authorize_detail` compares
  `spent + float(args.get("cost", 0)) > float(budget)` (`capability.py:423`). Exact for byte
  counts under 2⁵³, but it is a float in an authorization decision; an egress budget should be
  its own int caveat handled like `_SHRINK_ONLY`, not smuggled through `cost`.
- **A second supervised component.** Even in-process, the broker adds an operational surface:
  a policy file to configure, a failure mode to observe, and a fail-closed default that will
  look like a bug the first time someone forgets to declare a ceiling — exactly as
  `workspace_root=None` ⇒ `NO_EXECUTOR` does today. The refusal messages have to be as good
  as `MountRefused`'s.
- **Unknowns worth stating plainly.** (1) Whether an organ can do anything *useful* through a
  single request/response verb with no streaming is untested — the first three network organs
  should be chosen to answer that before the surface is widened. (2) Whether a stub-broker
  evaluation predicts real-endpoint behaviour at all is unknown, and it is the same gap the
  model lane already has between the deterministic default and live qualification. (3) Whether
  `run_worker`'s ~10s default timeout (`execution.py:92-101`) is compatible with a real remote
  call plus TLS is unmeasured; per-dispatch budgets may be needed before the tier is usable
  rather than merely permitted.
