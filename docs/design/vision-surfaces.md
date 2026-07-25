# Design: Vision Surfaces — which accrete, and the platform that must exist first

**Status:** proposal — needs owner sign-off on six decisions (§8).
**Date:** 2026-07-25
**Scope:** `VISION.md`'s outward surfaces — the voice/multimodal Shell, the studio, the
inbox you never open, the workspace, social/business ops, SDLC automation.
**Protocol impact:** **none.** No hashing, canonical-encoding, id-text, pid-width, or
signed-struct change is proposed. **`needs_fixture_regen = false`** (see §7).

---

## 1. One-paragraph summary

`VISION.md` claims the surfaces are *sediment*, not roadmap: "the studio, the inbox, the
finance view, the social scheduler are not a roadmap — they are **sediment** that accretes
on a living spine" (`VISION.md:36-38`), and "the interface itself is not shipped — it
**accretes** … your agents can *author your interface*" (`VISION.md:60-63`). That claim is
true for the *effect* half of every surface and **false today for the *view* half of every
surface.** An agent can already forge a capability, run it in a jailed worker, and land its
result as a Cell — but the shipping Shell renders exactly twelve hand-written JavaScript
screens registered in a source-tree array (`decima/shell/frontend/js/app.js:13-25`,
`decima/shell/frontend/js/screens/`) behind a fixed route table
(`decima/services/api/routes.py:44-100`) and a closed event vocabulary
(`decima/services/api/events.py:35-47`). So a newly accreted capability is **invisible until a
human writes JS**, which means no surface accretes end-to-end regardless of how good Nona
gets. This document separates the surfaces that are genuinely Nona applications from the
four platform pieces that must land first — **view Cells, a media/blob pipeline, a duplex
audio transport, and egress mediation** — plus the one prerequisite that is *not* new work
but a port: the **structural** untrusted-content boundary that exists in `heartbeat/` and
does not exist in `decima/`. The honest headline is that three of the four vision surfaces
are gated on the *same* long pole (egress mediation), and that the highest-leverage single
item is the cheapest one (view Cells).

## 2. Why this is a decision, not a roadmap item

The temptation is to treat "voice shell", "studio", "inbox", "ops" as four epics and
schedule them. That is the exact failure mode `heartbeat/` already recorded once, in
`heartbeat/checks/480_shellsurface.py`:

> "The re-audit found the project's recurring failure mode again … the libraries — golive's
> engine flip, the MCP client, the gated mail engine, the corpus walker, the mediated
> browser, citizens, multi-human, self-update, the voice session, schema migration, real
> parallel effects — were all check-proven and NONE was reachable from `shell.py`/`run.py`."

The reference grew ~170 modules of surface capability (`heartbeat/decima/` — voice,
`media.py`, `mediated_browser.py`, `maildigest.py`, `notify.py`, `social.py`, `photos.py`,
`corpus.py`, `citizen_terminal.py`, …) and then had to add a dedicated adversarial check
whose whole job was proving they were *reachable*. Building surfaces before the accretion
mechanism exists reproduces that: N unreachable libraries and a growing wiring debt.

So the decision this document asks for is a **sequencing law**, mirroring the one
`VISION.md:373-375` already states for enforcement ("The ordering is a law, not a
preference"):

> **No surface is built as a pillar. Every surface either accretes through the four verbs
> over existing kernel primitives, or it is blocked behind a named platform gap. If it is
> neither, the design is wrong.**

## 3. Motivation

- **The accretion claim is currently unfalsifiable in the shipping package.** There is no
  `define_view` in `decima/` — the only hit for a view concept is a projection's own
  `state_root` payload key (`decima/projections/engine.py:206`). The reference *does* have
  it (`heartbeat/decima/workspace.py:32,72,110,124`, proven by
  `heartbeat/checks/484_viewcell.py`). Until it is ported, "views are Cells" is a property
  of the frozen oracle, not of the product.
- **Four surfaces, one bottleneck.** The studio (remote generation), the inbox (receiving
  mail), and ops (posting, CRM, social) all require the box to talk to the network under
  mediation. `decima/workers/execution.py:373-383` declares that dimension honestly
  **un-enforced** — "the PROVIDER profile permits network but this phase wires NO egress
  mediation. Do not route real provider traffic through a network-permitted worker until the
  mediation seam lands" — with a loud top-level warning for the worst case
  (`decima/workers/execution.py:401-409`). Recognizing that these three share one blocker
  changes the plan from "three epics" to "one platform item, then three thin capability
  lanes."
- **Every new input modality multiplies an un-enforced trust edge.** The shipping package
  enforces untrusted-is-data by *annotation*: `instruction_eligible: False` written onto
  cells at `decima/capabilities/documents.py:275,297`, `decima/capabilities/qa.py:219`,
  `decima/kernel/inbox.py:173`, `decima/capabilities/workspace.py:173,372,398,497`. The
  reference enforces it *structurally* — `heartbeat/decima/quarantine.py:93-131` makes an
  admitted blob an **opaque handle** whose `__str__` and `__format__` raise
  (`quarantine.py:112-119`), so untrusted text cannot be interpolated into a prompt at all,
  with `admit()` as the sole mint (`quarantine.py:134-154`) and a capability-gated
  `promote()` as the sole exit (`quarantine.py:196-233`). `VISION.md:378-382` names this the
  Phase-1 gate: "Today the trust model is largely *convention* … not a boundary." Adding
  audio, images, web pages, and mail *each* adds a new place an annotation can be forgotten.
  Porting the boundary is therefore a **prerequisite to modality work**, not a parallel task.
- **The surfaces are where the wedge user actually lives.** This is not gold-plating: the
  0.3 Shell delivers Q&A, planning, workspace, notes/tasks/projects, approvals, activity
  (`README.md:52-74`). None of those is voice, none is media, and the approval inbox is a
  queue you *do* open. The vision's distinguishing claims are exactly the ones not yet built.

## 4. The accretion test — and what each surface scores

A surface is genuine **capability-accretion** (a Nona application, expressible by an agent
with no kernel edit) iff all four hold:

| | Criterion | Why it is load-bearing |
|---|---|---|
| **A1** | It needs **no new event kind** — expressible as ASSERT/RETRACT/INVOKE/ATTEST (`VISION.md:100-109`). | The four verbs are the whole instruction set. A new verb is a protocol change. |
| **A2** | Its effects fit an existing **capability shape**: an `effect` name, a `target`, and caveats the gate already understands — `requires_approval` (`decima/kernel/capability.py:224`), `sandbox`/`sandbox_only` (`:226-229`), budget, `quarantined` (`:195-197`). | Nona forges capabilities; it cannot teach `authorize` a new caveat. |
| **A3** | Its output is a **deterministic fold** — a projection over events already on the log, integer-scored, no wall-clock. | Law 5, and the gate's determinism requirement. |
| **A4** | Its inputs arrive as **already-admitted untrusted bytes** through one chokepoint. | Otherwise the surface *is* a new trust edge, which is TCB work by definition. |

Scoring the vision's surfaces:

| Surface | A1 | A2 | A3 | A4 | Verdict |
|---|:--:|:--:|:--:|:--:|---|
| **SDLC automation** (coding agents, review, tests) | ✅ | ✅ | ✅ | ✅ | **Accretion today.** Largely landed: `decima/capabilities/workspace.py` mounts a repo, runs declared checks inside a jailed networkless worker, emits durable diff/test artifact Cells. What is missing is Nona *authoring* the change, which is `VISION.md`'s Phase 3, not a surface. |
| **Workspace lenses** (document/board/graph/timeline + user-defined) | ✅ | n/a | ✅ | ✅ | **Accretion — blocked on view Cells.** The fold is free (Law 5); the *rendering* is not. Platform gap **G1**. |
| **Ops** (social scheduling, CRM, marketing, business) | ✅ | ✅ | ✅ | ✅ | **Accretion — blocked on egress mediation.** Every act is an outward `requires_approval` effect; the pattern is fully worked in `heartbeat/decima/notify.py:160-173`. Platform gap **G4**. |
| **Inbox you never open** (mail digest, attention) | ✅ | ✅ | ⚠️ | ✅ | **Mostly accretion.** The digest fold is proven end-to-end (`heartbeat/decima/maildigest.py:84-198`). The *attention policy* — what earns an interrupt — has no home today and must be integer-ranked and clock-free. Platform gaps **G4** (receiving/sending) + **G5** (attention). |
| **Studio** (image/video/audio/site generation) | ✅ | ✅ | ⚠️ | ❌ | **Split.** Generation-as-INVOKE and artifact-as-blob fit; but classification/extraction is **text-only** (`decima/capabilities/documents.py:53-56,127-137` — plain text, markdown, source, PDF) and there is no media Cell type or derivation projection. Platform gaps **G2** + **G4**. |
| **Voice-first Shell** (streaming, multi-turn) | ✅ | ✅ | ❌ | ❌ | **Split — the deepest.** Session semantics accrete beautifully (`heartbeat/decima/voice_shell.py:41-136`). Capture/playback is transport, and the current server *cannot* carry it. Platform gaps **G3** + **G2**. |
| **Terminals as citizens** (drop Claude Code / Codex in) | ✅ | ✅ | ✅ | ⚠️ | **Accretion — blocked on isolation completeness.** `heartbeat/decima/citizen_terminal.py` + `isolation.py` prove the shape; the shipping worker is honest that `workspace_bind_mount` is not wired (`decima/workers/execution.py:384-394`) and cgroup control is absent (`:363-372`). |
| **Mediated browser** | ✅ | ✅ | ⚠️ | ❌ | **Platform first.** A browser engine is a new untrusted-content firehose and a new process class. Explicitly out of 0.3 (`README.md:87-89`). |

The pattern: **nothing here needs a new verb, and almost nothing needs a new caveat.** The
gaps are all at the two edges — *how bytes get in* and *how state gets seen*.

## 5. What exists today vs. what is missing

### 5.1 Exists in the shipping package (`decima/`)

- **A durable Morta queue.** `decima/kernel/inbox.py:74` — `ApprovalInbox`. `is_gated`
  (`:85-95`) is the sole predicate routing a turn into the queue; `enqueue` (`:122-178`)
  records the proposed cap+args plus a fresh `os.urandom(16)` nonce (`:146`) that **pins the
  exact operation** a human will approve, and stamps the item `instruction_eligible: False`
  (`:173`) because its description may quote untrusted text. `approve` (`:215-261`) carries
  the decision to `approve_invocation` and re-invokes with the pinned nonce, so a revoked cap
  still fails closed at the gate. **This is the inbox's spine, already built.**
- **Morta floors that attenuation cannot strip.** `decima/kernel/capability.py:275-279`
  (`MORTA_FLOORS` for `shell` and `financial`), merged by `with_morta_floor` (`:288-295`) so a
  scoped grant is *born* with its floor. Quarantine-on-birth at `:34,46`, enforced at
  `:195-197`.
- **Content-addressed blobs and a place to put them.** `blob_id` (`decima/kernel/hashing.py:86`)
  and an `artifacts/` directory whose *filename is the digest*
  (`decima/services/data_layout.py:8,30`), already inside `BACKUP_DIRS` (`:40`) and outside
  `EXCLUDED_FROM_BACKUP` (`:42`). **The studio's storage layer already exists.**
- **Isolated effect execution with an honest containment matrix.**
  `decima/workers/execution.py` — namespaces mandatory-or-fail-closed (`:300-351`), wall-clock
  timeout to `UNKNOWN` rather than a faked outcome (`:353-361`), and explicit non-enforcement
  rows for cgroups (`:363-372`), egress mediation (`:373-383`), and the workspace bind mount
  (`:384-394`).
- **Determinism guards at the API edge.** `decima/services/api/contracts.py:13,161,167` —
  every recordable field must be an `int`, "never a float — invariant 6".
- **A logical-cursor event stream.** `decima/services/api/events.py:106-124` (integer `seq`,
  no wall-clock), with an undeclared family refused (`:141-142,156-158`).
- **A strict same-origin Shell.** `decima/shell/serve.py:52-63` — `script-src 'self'`, no
  `unsafe-inline`, no `unsafe-eval`, `object-src 'none'`, traversal refused (`:113-127`).

### 5.2 Exists only in the frozen reference (`heartbeat/`) — proven, unported

- **The structural quarantine boundary.** `heartbeat/decima/quarantine.py` — opaque
  `Quarantined` (`:93-131`), `__str__`/`__format__` raise (`:112-119`), deterministic
  `neutralize` that escapes the fence characters and the ASCII colon so a `name: payload`
  instruction cannot form inside data (`:74-83`), `admit` as the only mint (`:134-154`),
  `instruction_stream` that strips fenced blocks and **fails closed on an unterminated
  fence** (`:167-183`), and capability-gated `promote` as the only exit (`:196-233`).
- **The voice contract + a multi-turn voice session.** `heartbeat/decima/voice.py:26-27`
  (`voice.listen` read-only, `voice.speak` Morta-gated at `:57-65`), a canned deterministic
  ASR stub including a deliberate injection clip (`:32-42`), transcripts recorded as
  proposals whose `instruction_eligible` follows *speaker authentication*, not content
  (`:75-104`). `heartbeat/decima/voice_shell.py` deepens this into a session Cell with turn
  edges (`:41-54`), and its `turn()` (`:57-116`) marks the `if owner:` branch **LOAD-BEARING**
  (`:73-76`): an ambient clip never reaches `k.say`, "not even once, not even to 'just look'".
- **A media rail as a benign local effect.** `heartbeat/decima/media.py` — track/playlist/
  `now_playing` Cells (`:30-40`), **integer** durations enforced loudly (`:62-72`), a sandbox
  profile allowing only `media.play` with network denied (`:89-99`), and the explicit
  reasoning that playback needs *no* Morta gate because it is benign and local (`:1-8`).
- **Notification with an integer priority lattice and a box-boundary split.**
  `heartbeat/decima/notify.py:36-37` (`PRIORITY_RANK` ints, unknown priority is a refusal not
  a silent default), in-box notification as pure Cell + provenance edge, deduped by source
  (`:59-89`), and `install_send` (`:160-173`) making *leaving the box* `requires_approval`.
- **The email-digest pattern, complete.** `heartbeat/decima/maildigest.py` — `ingest_email`
  scrubs then stores twice as DATA (`:84-123`), `digest` is a pure lens that asserts nothing
  (`:126-156`), and `propose_action` (`:171-198`) *cannot* run an effect: it asserts
  `"ran" not in result` because the capability it mints is always gated.
- **Egress enforced at the wire, not as policy.** `heartbeat/decima/egress.py:1-45` — an
  allowlist in the capability's caveats, a sandbox profile bounding the handler, and a
  chokepoint armed inside `urllib.request`'s global opener so no real socket opens except
  through `wire.real_transport`, which re-runs the allowlist plus a Morta approval per call.
- **Accreting views.** `heartbeat/decima/workspace.py:32` — the entire declarative surface is
  **four keys** (`types`, `scope`, `rel`, `backlink`); `define_view` (`:72`) records the spec
  as a Cell, `views` (`:110`) lists what has accreted, `render` (`:124`) folds it, unknown
  views fail closed (`:146`). `heartbeat/checks/484_viewcell.py` proves a user-defined lens
  survives a fresh Kernel over the same db, that rendering appends **zero** events, that a
  non-declarative spec (code strings, callables, floats) is refused loud, and that a built-in
  name cannot be shadowed.

### 5.3 Missing everywhere — the five platform gaps

| Gap | What is missing | Evidence it is missing |
|---|---|---|
| **G0** | **Structural untrusted-content boundary in `decima/`.** Only annotations exist. | `instruction_eligible` is a dict value at `decima/capabilities/documents.py:275`, `decima/capabilities/qa.py:219`, `decima/kernel/inbox.py:173`; nothing prevents a raw `str` reaching a prompt. No `quarantine` module in `decima/`. |
| **G1** | **View Cells + a declarative renderer.** No `define_view`; the UI is a fixed screen registry behind a fixed route table and a closed event vocabulary. | `decima/shell/frontend/js/app.js:13-25`; `decima/shell/frontend/js/screens/` (12 files); `decima/services/api/routes.py:44-100`; `decima/services/api/events.py:35-47`. |
| **G2** | **A media/blob pipeline.** Bytes can be stored (`blob_id`, `artifacts/`) but not *classified, derived, or rendered* beyond text. | `decima/capabilities/documents.py:53-56` (four types, all textual), `:127-137` (`classify` sniffs `%PDF-` and extensions only). No thumbnail/waveform/transcode derivation projection anywhere. |
| **G3** | **A duplex, low-latency media transport.** The stream is one-way, finite, and the server is deliberately single-threaded. | `decima/services/api/events.py:172-176` — `sse_stream` "drains the current buffer and ends"; `decima/shell/serve.py:245-273` — single-threaded *by design* because the Weft is a plain `sqlite3` connection with thread affinity; the `check_same_thread=False` + lock follow-up is filed for 0.3.1 (`README.md:82-83`). |
| **G4** | **Egress mediation.** Declared un-enforced, with an explicit "do not route real provider traffic" instruction. | `decima/workers/execution.py:373-383`, warning at `:401-409`; `decima/workers/profiles.py` `PROVIDER` is "structure, not wired". |
| **G5** | **Attention / interruption policy.** Nothing decides what earns the human's attention; the approval queue is pull-only. | `decima/kernel/inbox.py` has `pending()` but no ranking, no interrupt concept, no delivery. `decima/services/api/events.py:35-47` has no notification family. |

## 6. Architecture

### 6.1 G1 — View Cells: the accretion mechanism (do this first)

Port `heartbeat/decima/workspace.py`'s model into the shipping package, unchanged in spirit:

- A `view` Cell whose content is a **declarative spec** over the four keys
  `heartbeat/decima/workspace.py:32` already pins: `types`, `scope`, `rel`, `backlink`. Data,
  never code — the check that proves this refuses "code strings under unknown keys,
  callables, floats" (`heartbeat/checks/484_viewcell.py`).
- A **fixed, trusted renderer** in the frontend that interprets that spec. The renderer is
  shipped code under `script-src 'self'` (`decima/shell/serve.py:52-63`); the *spec* is user/
  agent data. This is the whole safety argument: an agent authoring a view can change *what*
  you see, never *what code runs*.
- Two new generic API routes replacing per-surface routes: `GET /api/v1/views` (list accreted
  lenses) and `GET /api/v1/views/render?name=…` (fold one). Both are `READ`/`READER` in the
  existing table shape (`decima/services/api/routes.py:52-58`); no new authority tier.
- Rendering appends **zero** events. That is testable exactly the way the reference tests it,
  and it is the property that keeps a lens a Law-5 projection.

Why first: it is the smallest of the five gaps, it has a complete proven reference, and it is
the *only* one that changes whether the other four are worth building. Without it, every
later surface pays a hand-written-JS tax forever.

**What it deliberately does not do:** it does not let an agent ship layout, CSS, components,
or templates. A spec language rich enough to express a "fridge-calendar view"
(`VISION.md:62-63`) is *strictly more* than four filter keys, and expanding it is the main
scope risk (§9). v1 ships the four keys and a small set of shipped presentations
(list/table/board/timeline) the spec may *select* but not define.

### 6.2 G2 — The media/blob pipeline

The storage half exists; the missing half is typing and derivation.

- **Bytes never enter the Weft.** A media artifact lands in `artifacts/` addressed by
  `blob_id` (`decima/kernel/hashing.py:86`, `decima/services/data_layout.py:8,30`); the Weft
  carries a `media` Cell citing the blob id plus integer metadata (bytes, duration in whole
  seconds, pixel dimensions) — exactly the shape `heartbeat/decima/media.py:62-72` already
  enforces for track durations. Backup already captures `artifacts/`
  (`decima/services/data_layout.py:40`), so this does not weaken restore.
- **Classification widens, extraction does not.** `classify`
  (`decima/capabilities/documents.py:127-137`) grows image/audio/video branches by magic
  bytes. It must stay a **pure sniff with no execution** — the existing docstring's promise
  ("Pure DATA; no execution") is the invariant, and it is why no codec, decoder, or parser
  library may be linked into the ingest process.
- **Derivations are projections with receipts, not magic.** A thumbnail, a waveform, or a
  transcript is produced by an INVOKE inside a worker and lands as its own blob + a
  `derived_from` edge. Non-deterministic derivations (ASR, OCR, generation) are recorded
  *once*; **replay replays the recorded derivation, never re-runs the engine** — the same
  discipline `VISION.md:332` states for models ("we replay the record of what the model said,
  not the model").
- **Codecs live in workers, never in the kernel.** The TCB import boundary permits exactly
  `nacl`, `sqlite3`, `blake3`, `cbor2` (`tests/architecture/test_import_boundaries.py:56`).
  A media library in `decima/kernel/*` would require amending that set and is refused here on
  principle: an image decoder is one of the largest memory-unsafe attack surfaces in
  computing and belongs behind the worker's namespace jail
  (`decima/workers/execution.py:300-351`), where a crash is a `FAILED` receipt.

### 6.3 G3 — Duplex audio transport (and the store-threading prerequisite)

This is the gap where the current architecture actively says no. `decima/shell/serve.py:250-258`
explains that the server is single-threaded *because* the Weft is a plain `sqlite3`
connection usable only from its creating thread, and that a per-connection-threaded server
"would hand a request to a fresh thread and raise `sqlite3.ProgrammingError` on the first
read." A long-lived duplex audio channel on that server occupies the one serving thread and
**starves the API**. Therefore:

1. **The 0.3.1 store-threading item is a hard prerequisite**, not a nicety
   (`README.md:82-83`: `weft.py` `check_same_thread=False` + a lock). No audio transport work
   should start before it lands, or the first working demo breaks the Shell.
2. **Audio is chunked to the artifact store, not streamed through the log.** A voice turn is:
   client captures → uploads a bounded clip as a blob → a `voice_turn` Cell cites the blob and
   carries the transcript. That is precisely `heartbeat/decima/voice_shell.py:103-114`, with
   the blob added. Raw frames on an append-only log would make the fold cost unbounded and is
   refused.
3. **The trust rule is inherited, not re-derived.** `heartbeat/decima/voice_shell.py:57-76`
   is the model: `owner=True` makes the transcript a *proposal* routed through the ordinary
   `authorize`/Morta gate; `owner=False` (ambient) **never reaches dispatch at all**,
   regardless of what the transcript says. Note precisely what this depends on: **speaker
   authentication**, which does not exist today in any form. Until it does, every clip is
   ambient — which is the safe default and also means voice-in cannot drive anything. That
   is an honest blocker, not a detail.
4. **Voice-out stays Morta-gated** (`heartbeat/decima/voice.py:27,57-65,107-114`). Synthesized
   speech leaves the box through a speaker; more importantly, voice/likeness publication is
   named in `VISION.md:243-245` as a permanent Morta gate.

### 6.4 G5 — Attention policy: the inbox you never open

"You **never open your own email**" (`VISION.md:54-58`) has two halves. The *reading* half is
done: `heartbeat/decima/maildigest.py:84-198` ingests as DATA, folds a pure digest, and can
only ever *propose* — its `propose_action` asserts that it never ran anything (`:196-197`).
The missing half is **what reaches you, and when**.

Design:

- An **integer priority lattice** copied from `heartbeat/decima/notify.py:36-37`, including
  its refusal to default an unknown priority ("never a silent default that would mis-order a
  critical alert").
- A **policy Cell** — declarative, like a view spec — mapping (source type, integer severity,
  scope) → an integer rank and a delivery class. Data, folded, retractable.
- An **interrupt budget as a capability.** Recommended in §8/D5: "may interrupt the human"
  becomes a real capability with an integer per-window budget, so "stop pinging me" is a
  RETRACT and "who woke me at 3am" is answerable from the log. It composes with the existing
  budget caveat machinery rather than adding a concept.
- **Ranking must not read the clock.** "How urgent is this *now*" is time-dependent; a
  `now=` parameter is threaded exactly as the existing gate does
  (`decima/kernel/capability.py:260-265` passes `now=` through), and the value used is
  recorded so the ranking replays.
- **Delivery out of the box is an outward effect** — `heartbeat/decima/notify.py:160-173` is
  the shape: `requires_approval` plus a sandbox permitting only `notify.send`. Which means
  push notification is **also** behind G4.

### 6.5 G4 — Egress mediation: the long pole

Three surfaces need the network and one file says not to use it
(`decima/workers/execution.py:373-383`). The reference's answer is a wire chokepoint plus a
per-call allowlist and Morta approval (`heartbeat/decima/egress.py:1-45`). The shipping
answer must be stronger, because the reference's guard is *in-process* (armed inside
`urllib.request`'s opener) and the shipping architecture executes effects in **child
processes** (`decima/workers/execution.py`). An in-process guard cannot bound a child. Hence
D4 in §8: a separate mediating process, with the worker's network namespace routed to it
and nothing else.

The safety properties the mediator must deliver, per surface:

| Surface | Egress shape | Non-negotiable |
|---|---|---|
| Studio (remote generation) | POST prompt, receive bytes | Response bytes are **untrusted** and land as a blob + DATA Cell; prompts are redaction-scrubbed before leaving (the `redact.scrub` seam `heartbeat/decima/maildigest.py:104` already uses). |
| Inbox (mail) | fetch/receive | Every message admitted through the G0 boundary; the digest is the only thing a human reads; actions are queued items, never invocations. |
| Ops (posting, CRM) | write to third parties | `requires_approval` floor, per-account capability, and **irreversibility acknowledged** — a published post cannot be retracted from the world, only from the log. |
| Provider inference | model calls | Currently local-only by design (`README.md:44-50`, `127.0.0.1:8080`). Going remote is a *policy* change with a credential-exposure consequence and should be a separate decision from egress mechanism. |

### 6.6 Quarantine at each new input modality (G0, generalized)

Each modality is a new door. The rule is one door per modality, and the door is the *only*
mint:

| Modality | Chokepoint | Admitted as | The specific hazard |
|---|---|---|---|
| Text/document | already `documents.import_*` | claim Cells, `instruction_eligible: False` (`decima/capabilities/documents.py:275,297`) | PDF text extraction is regex-bounded (`:140-145`) — keep it that way; a real PDF parser is a code-execution surface. |
| Audio (owner) | ASR derivation on an admitted blob | proposal, eligible **only** on authenticated speaker (`heartbeat/decima/voice.py:96-100`) | Speaker auth does not exist; absent it, nothing is owner. |
| Audio (ambient) | same | DATA, never dispatched (`heartbeat/decima/voice_shell.py:73-76`) | The reference ships a deliberate injection clip as a test fixture (`voice.py:34-36`); keep an equivalent in the adversarial lane. |
| Images | classify + OCR derivation | DATA | OCR output is *text an attacker chose*. Visually-hidden instruction text in an image is the canonical multimodal injection; it must pass the same fence as web text. |
| Web pages | mediated fetch | DATA (`heartbeat/decima/egress.py`, `mediated_browser.py`) | The largest firehose; explicitly out of 0.3 (`README.md:87-89`). |
| Mail | `ingest_email` | scrubbed DATA + episodic claim (`heartbeat/decima/maildigest.py:84-123`) | The textbook vector; the reference's own docstring says so (`:4-7`). |
| Tool/MCP output | admit-on-return | DATA | Already the reference's rule; the shipping package must not regress it when new engines land. |

The generalization that matters: **`neutralize` is text-shaped**
(`heartbeat/decima/quarantine.py:74-83` escapes fence characters and the ASCII colon). It has
no meaning for image bytes or audio samples. So the boundary for non-text modalities is
necessarily *two-stage*: the raw blob is inert-by-type (never interpreted, only stored and
hashed), and any **text derived from it** (transcript, OCR, caption) passes the ordinary text
boundary. Stating that explicitly prevents the tempting mistake of treating a blob as
"already safe because it isn't text."

## 7. Determinism and protocol impact

**No content-addressed bytes change. `needs_fixture_regen = false`.** The reasoning, so a
reviewer can check it:

- **New Cell types and new content shapes do not change the encoder.** Canonical bytes come
  from deterministic CBOR over the content dict (`decima/kernel/hashing.py:25-31`); a new
  key set is new *input*, not a new *encoding*. Existing fixtures replay unchanged.
- **No new id-kind prefix is added.** `decima/kernel/hashing.py:33-42` maps six kinds and
  documents that "unmapped kinds fall back to the kind string itself as the prefix." So
  `content_id(..., kind="view")` already yields `view_<base32>` with **zero** edit to
  `_PREFIX`. Adding an *abbreviation* (e.g. `view` → `vw`) would change id text for anything
  already minted under the fallback — which is why D6 in §8 asks the owner to forbid it.
- **Media bytes reuse `blob_id`** (`decima/kernel/hashing.py:86`) with the existing `blob`
  prefix. No new digest, no new width.
- **No floats.** Durations, byte counts, dimensions, priorities, interrupt budgets are all
  `int`, enforced at the API edge (`decima/services/api/contracts.py:161,167`) and by the
  pattern `heartbeat/decima/media.py:62-72` / `notify.py:36-37` already sets.
- **No wall-clock in signed content.** Attention ranking and voice turn ordering use logical
  sequence (`heartbeat/decima/voice_shell.py:53-54` counts turn edges;
  `decima/services/api/events.py:112` uses an integer `seq`) and thread `now=` where a time
  input is genuinely needed.
- **Non-deterministic engines are recorded, not replayed.** ASR/TTS/OCR/generation outputs
  land once as Cells+blobs; the fold reads the record. Same events ⇒ same `state_root`.

One kernel-adjacent change *is* implied and must be called out plainly: **the 0.3.1 Weft
threading fix** (`README.md:82-83`, `decima/kernel/weft.py` has no `check_same_thread`
argument today). It touches the TCB. It changes no bytes — it changes concurrency — but it is
the riskiest single line in this plan and deserves its own review, not a ride-along.

## 8. Phased plan

Each phase is internally gate-green: `ruff format --check` + `ruff check` (0.15.20), mypy
strict on `decima.kernel.*` with no `Any`, pytest with coverage ≥ 78%, the adversarial lane,
the release-metadata drift guard, and Playwright (`Makefile` `check`). Each phase adds at
least one **adversarial** test that goes red if its load-bearing line is stubbed — the
mutation-resistance discipline every `heartbeat/checks/*` file states explicitly.

| Phase | Content | Gate additions | Unblocks |
|---|---|---|---|
| **S0** | **Port the quarantine boundary (G0).** `decima/kernel/quarantine.py` or `decima/capabilities/quarantine.py` — opaque handle, `__str__`/`__format__` raise, `admit` sole mint, `instruction_stream` fail-closed, capability-gated `promote`. Route existing text ingest (`documents`, `qa` context) through it. | Adversarial: a raw `str` reaching the brain-facing assembly raises; an unterminated fence drops everything after it. | Every modality after this. |
| **S1** | **View Cells (G1).** `define_view`/`views`/`render` over the four declarative keys; two generic API routes; a trusted spec renderer; retire nothing (built-in screens keep working, as the reference's check requires). | Adversarial: a non-declarative spec (callable/float/unknown key) is refused; render appends zero events; unknown view fails closed. Playwright: define a view in the UI and see it. | **The accretion claim becomes true.** Every later surface renders for free. |
| **S2** | **Media/blob pipeline (G2).** Widen `classify` by magic bytes; a `media` Cell type citing a blob with integer metadata; a `derived_from` edge; derivation-in-worker with receipts. | Adversarial: no decoder runs in the ingest process; a malformed blob yields a `FAILED` receipt, never a partial Cell; derivation replays from the record. | Studio (local), image ingest, transcripts. |
| **S3** | **Weft threading (prerequisite) then duplex transport (G3).** `check_same_thread=False` + lock as its **own** reviewed change; then a bounded-clip upload path and a resumable turn stream; then the voice session port (`voice_shell`). Speaker authentication is scoped here or voice-in stays ambient-only. | Adversarial: an ambient turn fires zero INVOKEs regardless of transcript content (the reference's `448_voice_shell` (b)); concurrent Shell request + upload both succeed. | Voice shell. |
| **S4** | **Egress mediation (G4).** A separate mediating process; worker network namespace routed only to it; per-call allowlist + Morta approval + decision recorded before the socket exists; redaction on the outbound path. Flip `egress_mediation` to enforced in the containment matrix and delete the warning. | Adversarial: a non-allowlisted host is refused with no socket; a worker cannot reach the network except through the mediator; a denied call records a refusal Cell. | Inbox, ops, remote studio, remote providers. |
| **S5** | **Attention (G5) + the inbox surface.** Integer lattice, policy Cell, interrupt capability with an integer budget, delivery as a gated outward effect; port the mail digest. | Adversarial: an inbound message with an embedded instruction produces a digest entry and a *queued* item, never an invocation (the `maildigest` assertion at `:196-197`); interrupt budget exhaustion denies, not degrades. | "You never open your own email." |
| **S6** | **Ops lanes as thin capabilities.** Social scheduling, CRM, marketing — each a capability + a view spec, no new platform. Nona-authored where Phase 3 self-extension is ready. | Per-lane adversarial: the outward act cannot fire without an approval; irreversibility is surfaced in the approval description. | The business surfaces. |

**Ordering rationale, stated as the law it is:** S0 before any new modality (or each modality
adds an unenforced trust edge). S1 before anything else user-visible (or every surface pays a
JS tax). S3's threading fix before S3's transport (or the demo breaks the Shell). S4 before
S5/S6 and before *any* remote studio path (the code currently instructs otherwise). S2 is the
only item that can safely run in parallel — it touches ingest and workers, not the gate.

**Explicitly not in this plan:** the mediated browser and terminals-as-citizens. Both are
real vision commitments (`VISION.md:284-294,420-430`), both are out of 0.3
(`README.md:87-89`), and both want S4 plus the un-wired containment rows
(`decima/workers/execution.py:363-394`) first. They are a follow-on document.

## 9. Decisions required (owner sign-off)

**D1 — How much may an agent author of your interface?**

| Option | What | Trade |
|---|---|---|
| **A. Declarative-only** *(recommended)* | View Cells are the four keys `heartbeat/decima/workspace.py:32` pins, rendered by shipped code. | Safe by construction under `script-src 'self'` (`decima/shell/serve.py:52-63`); an authored view can never execute. Cannot express "a fridge-calendar view" in v1 — a real reduction of `VISION.md:62-63`. |
| B. Declarative + a shipped-component vocabulary | The spec may *select* among shipped presentations and slots. | Much more expressive; the vocabulary is now a language, with a versioning burden. |
| C. Agent-authored templates/code | Genuinely "your agents author your interface." | Puts attacker-influenceable content in the trusted origin. Requires either relaxing the CSP or shipping an interpreter — a new, large TCB. **Recommend against, permanently.** |

**D2 — Do audio/video/image bytes ever enter the Weft?** Recommended: **no.** `artifacts/`
addressed by `blob_id`; the Weft carries refs + integer metadata. It keeps fold cost bounded
and backup semantics unchanged (`decima/services/data_layout.py:40`). The cost: the Weft alone
is no longer a complete restore — already true and already documented.

**D3 — Is the 0.3.1 Weft threading fix in scope for the voice surface, as a blocking
prerequisite?** Recommended: **yes, and as its own reviewed change.** The alternative — a
second connection, a connection pool, or a writer thread — is a larger kernel change. Refusing
both means voice is turn-based upload/download with no live channel, which is a legitimate
(if diminished) v1 and should be chosen deliberately rather than discovered.

**D4 — Egress mediation topology.**

| Option | What | Trade |
|---|---|---|
| A. In-process guard | The reference's pattern (`heartbeat/decima/egress.py`, wire chokepoint). | Cheap, proven in the oracle; **cannot bound a child process**, which is where shipping effects run. |
| **B. Separate mediating process** *(recommended)* | Worker netns routed only to a mediator that enforces allowlist + approval + logging. | Actually bounds a compromised worker. Real operational surface: a second process to supervise, configure, and fail closed. |
| C. Both | B for workers, A as defense-in-depth in-process. | Best posture, most work. |

**D5 — Is "may interrupt the human" a capability or a preference?** Recommended:
**a capability with an integer interrupt budget.** It makes silencing a RETRACT, makes
"who woke me" answerable from the log, and reuses budget machinery instead of adding a
concept. The cost: attention becomes ocap, which needs the powerbox UX
(`VISION.md:234-236`) to not be miserable.

**D6 — May `_PREFIX` gain abbreviations for new kinds?** Recommended: **no — forbid it.**
`decima/kernel/hashing.py:33-42`'s documented fallback means new kinds get self-describing ids
for free. Adding `view` → `vw` later would change id text for anything already minted and
re-open the re-freeze this branch just closed. Locking this is what keeps
`needs_fixture_regen = false` true for the whole surface program.

**D7 (minor) — Does any outward surface go live before Nona authors its capability?**
Recommended: **yes, unbundle them.** A hand-written, human-reviewed engine behind S4's
mediator is a smaller risk than blocking every outward surface on Phase-3 self-extension
(`VISION.md:403-406`). Nona-authored outward capabilities stay quarantined until attested.

## 10. Non-goals

- **Choosing ASR/TTS engines or models.** The contract is
  `voice.listen`/`voice.speak` (`heartbeat/decima/voice.py:26-27`); which engine sits behind
  it is a later, reversible choice, and the deterministic stub stays the test path.
- **Embeddings or a vector index.** Retrieval stays deterministic integer lexical scoring
  (`decima/projections/search.py`, `decima/capabilities/qa.py`; `README.md:86`). Multimodal
  retrieval is a separate design with a separate determinism argument.
- **Mobile, multi-user, remote exposure.** The Shell stays a loopback single-user daemon
  (`decima/shell/serve.py:262-265`, `README.md:78-80`). A serious mobile surface
  (`VISION.md:66-71`) needs auth and transport work this document does not attempt.
- **The mediated browser and terminals-as-citizens.** Named above; separate document.
- **Any protocol change.** No hashing, encoding, id-text, pid-width, or signed-struct change.
  Explicitly forbidding the `_PREFIX` abbreviation (D6) is part of this.
- **A media/codec dependency in the kernel.** `tests/architecture/test_import_boundaries.py:56`
  stays at four third-party imports.
- **Rewriting the twelve existing screens as views.** S1 adds a mechanism; migrating the
  built-ins is optional and the reference explicitly keeps built-ins working alongside
  accreted lenses (`heartbeat/checks/484_viewcell.py` (c)).

## 11. Risks and honest unknowns

**Risks**

- **The view spec becomes an interpreter.** The most likely failure: four declarative keys
  prove insufficient, the spec grows conditionals, then expressions, then a templating
  language — and the CSP argument that made D1/A safe quietly stops holding. *Mitigation:* fix
  the key set in a test that fails when a key is added without a design amendment, the way
  the reference's check refuses unknown keys.
- **Porting `heartbeat/` is more expensive than it looks.** The reference is untyped, uses
  `assert` for control flow in places, and composes freely. The shipping package is mypy-strict
  with no `Any` in the kernel and an import boundary. "Port `quarantine.py`" is ~230 reference
  lines and a substantially larger typed, tested, boundary-respecting change. Budget
  accordingly; do not treat S0/S1 as copies.
- **Egress mediation dominates.** S4 is plausibly larger than S0+S1+S2+S5 combined (a process,
  a supervision story, netns routing, config, failure modes, and its own adversarial lane).
  If it slips, the inbox, ops, and remote studio all slip. It is the schedule.
- **Coverage and the Playwright lane.** Each surface adds UI and specs. Holding ≥78% coverage
  while adding a rendering layer plus an audio path is real, unglamorous work that tends to be
  discovered late.
- **Speaker authentication is a hidden prerequisite.** Without it, `owner=True`
  (`heartbeat/decima/voice_shell.py:57`) has no honest source, so voice-in cannot drive
  anything and the voice surface is a dictation toy. Worse: if it is implemented
  *sloppily* — voice-print matching, say — it becomes an authority bypass reachable by anyone
  who can play audio near the microphone. Recommend binding owner-ness to the authenticated
  *session*, never to the audio.
- **Irreversible outward acts are irreversible.** A published post, a sent email, spoken
  audio: RETRACT removes it from the log, not from the world. Approval descriptions must say
  so; `VISION.md:243-245` already places these under Morta's permanent gates.
- **Attention is the surface most likely to fail socially, not technically.** A system that
  interrupts wrongly gets ignored, and an ignored inbox is strictly worse than a queue you
  chose to open. No deterministic test can prove the ranking is *right*.

**Unknowns — stated as unknown**

- **Latency.** Nobody has measured fold/projection latency inside a Shell request. A voice
  turn wants sub-second; a cold fold over a large Weft may be far worse. There is no data,
  and the snapshot/checkpoint machinery (`decima/kernel/snapshot.py`,
  `decima/kernel/checkpoints.py`) has not been profiled for this. **Measure before designing
  the transport.**
- **Whether four declarative keys can express a useful lens.** The reference proves they
  express "typed cells in a scope with a relation." Whether that covers what a person
  actually asks for is untested against real requests.
- **Growth of `artifacts/`.** No retention, dedup-beyond-content-addressing, or GC policy
  exists for media blobs. Audio accumulates fast. Retraction sweeps the log; nothing sweeps
  the blob store.
- **Whether the single-mediator model survives contact with real providers.** Streaming
  responses, websockets, and OAuth redirects are all awkward through an allowlist-plus-
  approval mediator. The design may need per-protocol handling.
- **Whether ambient audio should be captured at all.** The reference captures it as DATA and
  proves it cannot dispatch. That is technically sound and may still be the wrong product
  decision — an always-listening microphone recording overheard speech onto an append-only
  log has privacy consequences for people who never consented. Not a security question; a
  question for the owner.
