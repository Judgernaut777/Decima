# Design: Local-first Multiplayer — Replication, Multi-Device Sync, and the Conflicts a Machine May Not Resolve

**Status:** proposal — needs owner sign-off on six decisions (§7). Explicitly **out of 0.3**
(`README.md:88`, `docs/RELEASE-READINESS.md:83`), so this is scoping, not a landing plan.
**Date:** 2026-07-25
**Depends on:** the durable-protocol re-freeze (`docs/design/adopt-durable-protocol.md`) — landed.
**Grounded in:** `specs/SYNC.md`, `specs/MERGE_SEMANTICS.md`, `specs/SNAPSHOTS.md`,
`specs/CAPABILITY_MAP.md §D6`, `decima/kernel/weave.py`, `decima/kernel/weft.py`,
`decima/kernel/crypto.py`, `decima/kernel/keystore.py`, and the frozen reference
`heartbeat/decima/sync.py` (the brief called this `heartbeat/sync.py`; the file is one level
deeper).

**Content-addressed-bytes impact: NONE for the recommended path; TWO sub-designs are
tripwires.** Everything in §5.1–§5.5 and §5.7–§5.9 is additive and hashes nothing new. Two
options — adding a signed `realm` field to the Event envelope (§7 D3-B) and realm-domain-separating
blob ids (§7 D4) — **change signed/hashed content** and therefore **require regenerating
`protocol/fixtures/` and re-cutting the reference**, exactly as the re-freeze did. They are
called out in §2 and isolated into their own decisions so the owner chooses that cost
deliberately instead of discovering it mid-implementation. If either is taken:
`needs_fixture_regen = true`.

---

## 1. One-paragraph summary

Decima's log is already a DAG of immutable, signed, content-addressed events, and the fold is
already a pure function of the event *set* under the total order `(lamport, event_id)`
(`decima/kernel/weave.py:164-181`). That makes replication cheap in the deep sense: **merge is
DAG union and sync is "send me the events I am missing."** The kernel has the two hard halves
built — real per-type CRDT reducers with plural-head preservation and ATTEST adjudication
(`weave.py:22-40`, `:719-754`, `:466-478`) and a full WEFT §2 acceptance gate for foreign events
(`weft.py:408-508`). What is missing is **not** the merge theory; it is (a) a transport in the
shipping package — there is none; sync lives only in the frozen oracle
(`heartbeat/decima/sync.py`) — (b) a **peer-identity story that survives independent devices**,
because the default named-principal id is `digest(name)` (`crypto.py:62-70`) and therefore
collides across devices while their keys differ, (c) a **merge event**: after ingest, a local
append descends from one head (`weft.py:287-289`), never the whole frontier, so a user's next
keystroke cannot resolve a conflict it never observed, (d) a **durable keybook** — trust is an
in-memory dict (`crypto.py:60`) lost on restart, and (e) **realm**, the unit of sync in
`SYNC.md §1`, which does not exist in the signed envelope at all (`weft.py:72-82`). This
document specifies those five, plus the honest hard parts: adjudication that needs a human,
partial replication scoped by an agent's horizon, and what multiple devices do to key custody.

## 2. Why this is a design doc, not a patch

Three reasons, in increasing order of seriousness.

**(a) It is a new subsystem, not a change to an existing one.** `grep -rn "sync" decima/`
finds one comment (`weft.py:448`). The shipping package has no peer, no transport, no frontier
function, no reconciliation. The reference implementation exists — `frontier()`
(`heartbeat/decima/sync.py:65`), `missing_for()` (`:83`), `feed()`/`apply_feed()` (`:199`,
`:209`), an authenticated encrypted `SecureChannel` (`:325`) with a mutual-auth handshake
(`:377`), channel provenance (`:430`), and real socket/TCP transports (`:595`, `:665`) — but it
is **frozen**, it speaks the old stdlib profile (BLAKE2b pids, `sync.py:40`; a JSON+BLAKE2b
handshake transcript, `:300-307`), and it must not be edited. Porting it is a fresh
implementation against the durable protocol, under mypy-strict and the kernel import boundary.

**(b) Two of its natural sub-designs are fixture tripwires.** `SYNC.md §1` makes the **realm**
the unit of sync — the workspace/security boundary, `WEFT §2` field 2. The shipping event's
hashed payload is `{parents, author, authorized, verb, body, lamport}` (`weft.py:72-82`): **no
realm.** The only occurrences of the word in the package are a snapshot-manifest constant
(`snapshot.py:128`) and a backup constant (`backup/service.py:135`) — decorative, not a
boundary. Likewise `SYNC.md §5` requires **blob ids to be realm-domain-separated** so a content
hash from realm A is not guessable in realm B; today `blob_id(data, kind="blob")`
(`hashing.py:86-88`) domain-separates by *kind* only. Making either real changes the signed
struct shape / the id derivation, which invalidates `protocol/fixtures/{canonical,fold}.json`
and requires re-cutting the reference — the exact cost the re-freeze paid once and does not want
to pay again casually. So this document does **not** quietly assume realms; it makes them
§7 D3/D4 and offers a byte-neutral alternative (one realm per store).

**(c) The genuinely hard questions are policy, not code.** Who may pair a device. Whether a
second device is a second principal or a second key for the same principal. Whether an ingesting
peer re-checks authority or trusts authority-at-origin. What happens when two devices disagree
about *intent* rather than about text. Those are owner decisions (§7); implementing before they
are answered would bake one answer into content-addressed data.

## 3. Motivation

- **It is the Law-1/Law-5 payoff.** The architecture pays a real cost for append-only signed
  content-addressed events and fold-as-projection. Multi-device sync is where that cost returns
  a capability a file-sync product structurally cannot match: not "whose copy is newer" but
  "the union of two histories, folded, with disagreements *preserved and inspectable*"
  (`SYNC.md §7`, `MERGE_SEMANTICS §2.2`). `specs/CAPABILITY_MAP.md:410-434` already names this
  the flagship sovereignty wedge ("easy to start, impossible to lose, runs anywhere, no one
  else can read it").
- **The merge layer is currently untested product surface.** `weave.py` implements LWW / MV /
  OR-set / counter / sequence-RGA / map / append-log / adjudicated, plural `content_heads`, and
  `in_conflict` — and `state_root` deliberately covers them (`weave.py:940-945`). Yet nothing
  under `tests/` references `in_conflict`, `content_heads`, or `merge_class`: the only two
  in-tree readers are `weave.py` itself and `snapshot.py`. The DAG reducers are, today,
  **unexercised by any concurrency**. Sync is what forces them to be real, and forces the
  invariant suite `SYNC.md §10` demands.
- **Backup, disaster recovery, and multi-device are one mechanism.** `CAPABILITY_MAP §D6`:
  replicate the Weft + snapshots; restore = replay to frontier. `snapshot.py` /
  `checkpoints.py` already give verified frontier restore, so cold-peer bootstrap
  (`SYNC.md §8`) is mostly wiring, not new invention.
- **The 0.3 posture has an expiry.** "Run only on data you can afford to lose" is defensible
  for a single-machine experiment. It is not defensible for a system asking to be a daily
  driver, and the honest fix is replication, not backups-by-hope.

## 4. What exists today vs. what is missing

### 4.1 Built and load-bearing

| Capability | Where | Note |
|---|---|---|
| DAG-shaped log; explicit multi-parent append | `weft.py:284-293` | `parents=[...]` sorts to a canonical frontier; lamport `1 + max(parents)` |
| Arrival-order-independent fold | `weave.py:164-181` | applies in `(lamport, event_id)`, not `seq`; guarantees ancestors-before-descendants |
| Causal ancestor sets (domination) | `weave.py:355-359`, `:115` | `_ancestors[ev.id]`; used to decide "this write observed that one" |
| Per-type CRDT merge classes | `weave.py:22-40`, `:390-404` | LWW · MV · OR-set · counter · sequence · map · append-log · adjudicated; `_DEFAULT_MERGE = lww` |
| Plural heads + loud conflict | `weave.py:57-63`, `:719-754` | `_reg_push` drops dominated heads; `_materialize_register` sets `content_heads` / `in_conflict` |
| Adjudication as a signed ATTEST | `weave.py:466-478` | `predicate == "adjudicates"`, `resolution=select` supersedes named `evidence`; MERGE needs no special case (a fresh ASSERT parented on all heads dominates them) |
| Conflicts in comparable state | `weave.py:940-945` | `content_heads` + `in_conflict` are inside `state_root`, so a preserved conflict converges too |
| Foreign-event acceptance gate | `weft.py:408-508` | duplicate / orphan / terminal rejects: malformed, missing-fields, bad-verb, parents-not-canonical, author-mismatch, id-mismatch, bad-signature, bad-lamport |
| Rotation-aware verification | `weft.py:243-262`, `:486-491` | an author's signature checks against the key valid **at the event's logical point** — the mechanism a device-key rotation will ride |
| Self-certifying identity | `crypto.py:83-137` | `keyed_pid` = `digest("principal", pubkey)`; `mint_keyed`; `verify_keyed` checks pid-commitment **and** signature |
| Public-keys-only verification | `crypto.py:143-149` | `trust(pid, pubkey_hex)` — registering a key confers verifiability, never authority |
| Split key custody | `keystore.py` | `DirectoryKeyStore(path)` — per-principal 0600 seeds in a 0700 dir, `create/has/public_key/sign/adopt`; the raw seed never crosses the boundary |
| Verified frontier restore | `snapshot.py:97-180`, `checkpoints.py` | recomputed `state_root` must equal the manifest's; a snapshot never authorizes events |
| Store-format guard | `weft.py:89-156` | `decima-weft/0.1`; a mismatched store fails closed on open |
| Authority bound to one request | `capability.py:350-352`, `:427-480` | `invocation_bind(verb, body, nonce, parents)` — a captured proof is useless against any other request |

### 4.2 Missing, with the specific gap

**M1 — No transport in the shipping package.** Nothing. The whole of `heartbeat/decima/sync.py`
is absent from `decima/`. There is no `frontier()` in `decima/kernel/` (only
`frontier_lamport`, a logical clock, `weave.py:138`).

**M2 — Named principals collide across independent devices.** `Keyring.mint` sets
`pid = digest("principal", name)` (`crypto.py:62-70`). Two devices that both mint `"laptop-user"`
produce the **same pid with different keys**. On ingest, device B looks the author up:
`_verify_key` (`crypto.py:151-162`) returns the keybook entry if present, otherwise
**derives from B's own master** — so an un-trusted foreign author fails closed by key mismatch.
Correct, but it means named-`mint` sync is *dead on arrival* between peers with different
seeds. `mint_keyed` (`crypto.py:98-118`) is the fix and already exists — but its seed is
`blake2b(master + name)`, so two devices sharing one master seed **and** a name produce the
*same* keyed pid too. Device identity therefore requires per-device key material, not just a
different minting path.

**M3 — The shared-master shortcut hides the trust model.** `Keyring(seed=...)` is constructed in
exactly three places (`services/api/server.py:69`, `services/provision.py:59`,
`cli/main.py:66`), each defaulting to `DerivedKeyStore` — one master seed derives every
principal's key, which the constructor itself warns is DEV-ONLY (`keystore.py:71-85`). Copy
`keys/master.seed` (`services/data_layout.py:79`) to a second machine and sync "works" — because
both sides can *re-derive* each other's keys. That is sync over a **shared secret**, not over
public keys, and it collapses split custody exactly as the warning says. Multi-device must not
ship on it.

**M4 — The keybook is not durable.** `self.keybook: dict[str, VerifyKey] = {}` (`crypto.py:60`);
`trust()` writes to it (`:149`). Nothing persists it. Every restart forgets every peer, so a
peer's already-accepted events would stop verifying on the next `Weft.events()` read
(`weft.py:401`) — which raises `WeftError("bad signature")` and takes the whole fold down. **A
peer's key must be as durable as its events**, or ingesting a peer is a delayed-action crash.

**M5 — No merge event; a local write cannot resolve what it never saw.** After `ingest`, `head`
becomes the max-`(lamport, seq)` event (`weft.py:504-507`). A subsequent `append` with
`parents=None` takes `[self.head]` (`weft.py:287-289`) — **one** head. So the new event's
ancestor set excludes the other branch, `_reg_push` does not drop the peer's head
(`weave.py:719-729`), and: an MV cell stays `in_conflict` even though the user just made a
decision; and for an LWW cell the just-typed local value may **lose** to the remote head,
because the local event's lamport is `1 + parent` on the branch it descended from and
`_materialize_register` takes the `(lamport, eid)` maximum (`weave.py:741-754`). Convergence is
not broken — both peers still compute one `state_root` — but the *user-visible* semantics are
wrong. The fix is additive and byte-neutral: a `frontier()` over the store and a merge append
whose `parents` are the whole frontier, which `weft.py:290-292` already accepts.

**M6 — Realm does not exist.** See §2(b). `SYNC.md §1` syncs one realm at a time; the envelope
has no realm; there is no realm-scoped query, no per-realm store, no realm in `blob_id`.

**M7 — Reducer-set agreement is under-specified in code.** `SYNC.md §2` requires the handshake
to exchange `reducer_set = [{name, version, hash}]` and to *refuse to trust materialized state*
on a mismatch. The shipping constant is `REDUCER_SET = [{"name": "heartbeat-weave",
"version": 1}]` (`snapshot.py:41`) — stale name, and **no hash**. Two peers on different
`weave.py` revisions would exchange identical reducer sets and silently disagree about state.

**M8 — The wire encoding is JSON while the protocol is CBOR.** The stored/wire `payload` is
`json.dumps(payload, sort_keys=True)` (`weft.py:326-328`, `:496-499`), and `ingest` recomputes
the id from the **decoded dict** via `content_id` → `canonical` → CBOR (`hashing.py:67-72`).
Integrity is fine (any JSON decoding to the same dict yields the same id), but the wire cannot
carry anything JSON cannot represent — notably CBOR byte strings — and it would break outright
under the deferred D2b (integer field numbers), since JSON object keys are strings. A sync wire
is the wrong place to discover this.

**M9 — SQLite threading.** `sqlite3.connect(db_path)` with no `check_same_thread=False`
(`weft.py:99`). A connection is bound to its opening thread, which is why the reference has to
recover the path and reopen per-thread (`heartbeat/decima/sync.py:493-511`). Any server-side
sync loop needs a deliberate answer here, not an accident.

**M10 — No concurrency tests.** `tests/kernel/test_event_fixtures.py:136-224` covers `ingest`
statuses; `tests/adversarial/test_hostile_input.py` covers forged lamport (`:165`), unsorted
parents (`:184`), forged signature (`:205`), id mismatch (`:226`). None of it constructs an
actual **fork with two concurrent heads** and asserts convergence, so `SYNC.md §10`'s five
invariants are entirely unverified in the shipping tree.

## 5. Architecture

The design is four layers, strictly separated so each can be tested without the ones above it.
The rule that makes the separation safe is `SYNC.md §4`: **transport is untrusted**; nothing a
peer says is believed, only what an event *proves*.

```text
L3  pairing + UX      device pairing, offline-first affordances, conflict inbox
L2  transport         authenticated encrypted channel; framing; anti-entropy scheduling
L1  reconciliation    frontier exchange → causal difference → ordered feed
L0  union             Weft.ingest (WEFT §2) + Weave fold (CRDT) + merge event
```

### 5.1 L0 — Union is the only thing that must be right

L0 exists (`weft.py:408-508`, `weave.py`). Two additions:

**`frontier(weft) -> list[str]`** — the DAG leaves (ids that are no stored event's parent). The
reference computes it by scanning every payload (`heartbeat/decima/sync.py:65-73`); the durable
version should maintain an `event_parents` side index (a non-signed table, like
`meta`/`sync_provenance`) so frontier is a query, not a full scan. Side tables never enter
signed content, so this is byte-neutral.

**A merge append.** `append(..., parents=frontier(weft))` after a sync round that added anything.
This is the fix for M5, and it is *semantically* the right thing, not just a workaround: an
event parented on the whole frontier is an assertion by a principal who **has observed both
branches**, which is exactly what `MERGE_SEMANTICS §2.1` requires for domination and exactly
what `§4.1 MERGE` describes for an adjudication that grafts. Open question (§7 D5): whether the
merge event is *implicit* (emitted by the sync round) or *explicit* (emitted only when a
principal acts). Implicit merges are convenient and dangerous — they make a device assert
"I have seen this" on the user's behalf.

### 5.2 L1 — Reconciliation: frontier exchange, then walk

Baseline is `SYNC.md §3`: exchange frontier ids; the receiver requests ancestors it lacks,
walking down until it hits events it has. `O(difference)`, exact, no probabilistic structures.
The reference's `missing_for` diffs **full have-sets** (`heartbeat/decima/sync.py:83-90`) and
`sync_socket` literally ships `sorted(event_ids(weft))` over the wire (`:587`) — correct and
fine for a demo, `O(total log)` per round and not shippable. IBLT / range-fingerprint
optimizations (`SYNC.md §3`) are explicitly deferred; the frontier walk is always correct and is
the only thing 1.0 needs.

Feed ordering: `(lamport, id)` (`heartbeat/decima/sync.py:89`) guarantees parents precede
children, because a parent's lamport is strictly smaller (`weft.py:293`). Combined with
`ingest`'s orphan-defer-and-retry loop (`heartbeat/decima/sync.py:117-134`), an out-of-order or
truncated feed converges by retry rather than rejecting valid events — which is why
causal-completeness is checked *before* authenticity in `ingest` (`weft.py:472-485`): a
post-rotation event needs its rotation link folded first, so "parents missing" must be
retryable, not terminal.

**Handshake** (`SYNC.md §2`), with the M7 fix: `{protocol, realm_scope, reducer_set:[{name,
version, hash}], frontier, capabilities}`. `hash` must be a real digest over the reducer
implementation (a build-stamped constant is acceptable if the drift guard enforces it; a
hand-maintained integer is not). On reducer mismatch, peers **still exchange and verify
events** — events are reducer-independent — but must not trust each other's materialized state,
and a peer on an older reducer quarantines projections it cannot reproduce. Protocol mismatch,
and `WEFT_STORE_VERSION` mismatch (`weft.py:93`), abort.

### 5.3 L2 — Transport: authenticated, encrypted, and not believed

Port the reference channel, re-cut for the durable protocol. What carries over intact:

- Mutual auth before a single event flows (`heartbeat/decima/sync.py:377-412`). Both peers sign
  a transcript that binds the ephemeral X25519 keys to the Ed25519 identities, so a MITM that
  swaps ephemerals invalidates both signatures.
- Self-certifying peer ids checked against the presented key, plus optional pinning
  (`expected_peer`) — `_verify_peer` (`:310-322`) via `verify_keyed` (`crypto.py:119-137`).
- Per-direction SecretBox keys derived from `(shared, transcript, direction)`; nonce = the
  24-byte big-endian frame counter, strict `counter == last + 1`, so replay/reorder is refused
  **before** decryption and tamper fails the MAC (`:325-374`).
- No downgrade: a plaintext/legacy first message is refused outright (`:289-297`).
- Channel provenance in a side table (`:430-456`) — which authenticated peer each event arrived
  from, recorded beside the events so `state_root` is untouched.

What must change: the transcript hash is `blake2b(json.dumps(..., sort_keys=True),
person=b"decima:chan")` (`:300-307`) — old-profile primitives. Re-cut to BLAKE3-256 over CBOR
canonical bytes for consistency with `hashing.py:31-43`. **This changes no fixture bytes**: the
transcript is ephemeral handshake material, never content-addressed and never on the log. Bump
`PROTO` (`:262`) so an old peer fails the handshake rather than deriving different session keys.

**Kernel-boundary consequence.** The channel needs `nacl.public` / `nacl.secret` — already inside
`ALLOWED_THIRD_PARTY` (`tests/architecture/test_import_boundaries.py:56`:
`{nacl, sqlite3, blake3, cbor2}`), so **no new dependency and no ALLOWED_THIRD_PARTY change**.
Sockets, framing, retry, and scheduling must live **outside** the kernel — `decima/services/sync/` —
with the kernel exposing only `frontier`, `ingest`, `events`, and an event-row reader. If the
channel lands outside the kernel entirely, the TCB does not grow at all — which is the cleaner
answer and is part of §7 D6.

**Which wire?** The API surface is already there: `services/api/routes.py` carries a
session/CSRF/reauth-gated WSGI table, and adding `POST /api/v1/sync/{frontier,feed}` would
inherit that. But `SYNC.md §4` wants the *channel* authenticated by peer identity, not by a user
session cookie — an HTTP mount would authenticate the wrong principal. Recommendation: a
dedicated peer listener with the channel handshake, and treat HTTP-relay as a later
NAT-traversal convenience (§7 D2).

### 5.4 Peer identity, trust, and the keybook

**Identity.** Every device is a **self-certifying principal**: `pid = digest("principal",
pubkey)` (`crypto.py:83-97`), rendered `prn_<base32>`. This is the only identity scheme that
works here — it needs zero name coordination, and a verifier handed only a public key can both
check signatures and confirm the id commits to that key. Named `mint` (`crypto.py:62-70`) must
be **forbidden for any principal whose events cross a device boundary** (M2). Enforcement:
the sync service refuses to feed events authored by a pid that is not a valid `keyed_pid` for a
key in the keybook. That is a service-level rule, not a kernel change.

**Trust distribution: public keys only.** The reference's keybook exchange is the right shape —
`keybook_of(weft)` returns `{author_pid: pubkey_hex}` for every principal that authored an event
here (`heartbeat/decima/sync.py:182-188`), `trust_keybook` registers them (`:191-196`), and the
docstrings are explicit that **no secret ever leaves** and that a public key confers
verifiability, never authority. Keep exactly that, with two hardenings:

1. **Refuse a keybook entry whose pid does not commit to its key.** `verify_keyed` already does
   the check (`crypto.py:126-129`); `trust()` (`:143-149`) does not. A `trust_keyed()` that
   rejects non-committing pairs makes the keybook self-validating: it cannot be poisoned by
   claiming someone else's pid.
2. **Persist it (M4).** A `peer_keys(pid TEXT PRIMARY KEY, public_key TEXT, first_seen_session
   TEXT)` side table beside `meta`/`sync_provenance`, loaded on `Weft.__init__`. Side table ⇒
   no signed bytes. First-write-wins with a loud refusal on conflict: a pid that arrives with a
   *different* key is either a `keyed_pid` collision (2⁻¹²⁸, ignore) or an attack.

**What trust does and does not buy.** A keybook entry means "I can check this principal's
signatures." It does not mean the principal may do anything. Authority is capabilities
(`capability.py`), gated per event.

### 5.5 Authority at origin vs. re-check on ingest

This is the sharpest design question in the document, and the code and the spec are currently in
tension.

`Weft.ingest` states plainly: *"Authority is NOT re-judged here: each event was authorized at
its ORIGIN in its own causal frontier and carries that proof"* (`weft.py:446-448`). `SYNC.md §4`
step 6 says the opposite-facing thing: *"Authorization is verified at the parent frontier, never
against mutable current state"* — i.e. it **is** verified, just against the right frontier.
Both agree on the *invariant* (a revoked grant must not re-authorize via sync); they disagree on
**who checks**.

| | Authority at origin (today) | Re-check on ingest (`SYNC.md §4.6`) |
|---|---|---|
| Cost | zero — `ingest` is O(1) crypto | fold the ancestor closure of each event to judge its `authorized` grant at *its* frontier |
| Failure mode | a peer whose kernel was buggy or malicious at origin injects an unauthorized-but-well-signed event; every honest peer accepts it | expensive; and an ingesting peer that lacks an *encrypted* capability event cannot decide (`SYNC.md §6`: undecidable ⇒ fail closed, which stalls the branch) |
| Detectability | the event is signed and on the log forever — an audit **can** find it, after the fact | rejected at the door |
| Backlog | `docs/BACKLOG.md:141` names "per-invocation authority re-check on ingest" as remaining core work | — |

What is *not* in tension: `capability.py:350-352`'s `invocation_bind(verb, body, nonce, parents)`
means a captured proof is useless against any other request, and revocation cascades are derived
in the fold (`weave.py` `DERIVED_AUTHORITY` / `LEASE_TREE` marking), so a revoked grant already
fails closed **in every peer's projection** even if the event sits on the log. So the invariant
`SYNC.md §10` cares about — *"an event authorized by a grant revoked before that event's causal
point is rejected on receipt, on every peer"* — is currently satisfied at the **fold** layer, not
the **acceptance** layer.

**Recommendation (§7 D1): a third position — accept, but quarantine.** Keep `ingest` cheap and
authority-blind. Add a *post-ingest* authority audit pass that folds the newly-unioned events
and flags any event whose `authorized` grant does not verify at its own parent frontier. A
flagged event stays on the log (it is signed history; Law 1 forbids deletion) but its cell
contributions are quarantined and surfaced — the same treatment `SYNC.md §2` prescribes for
projections a peer cannot reproduce. This gets door-level *detection* without door-level cost,
and it degrades correctly under §6 opacity: undecidable ⇒ quarantined, not accepted, not
dropped. It needs a written-down definition of "quarantined cell" that `state_root` treats
deterministically, or peers will diverge on the quarantine itself.

### 5.6 Conflicts that need a human (merge is easy for text, hard for intent)

The mechanical half is done: `MERGE_SEMANTICS §3` maps every folded Cell type to a class, and
`weave.py` implements all eight. Text is a sequence CRDT; grants are an OR-set with
**remove-wins** on same-grant add-vs-revoke (fail closed, `MERGE_SEMANTICS §3.1`); counters sum;
observations append and cannot conflict.

The hard half is the classes that *deliberately refuse* to resolve — `claim` (knowledge),
`objective` (an agent's purpose), `type` schema evolution, and divergent `task` state-machine
transitions. These preserve every branch and set `in_conflict` (`weave.py:748-752`) until an
adjudication ATTEST collapses them (`weave.py:466-478`). Three things this design must add:

1. **A conflict inbox.** `in_conflict` is folded state that nothing reads. A projection over
   `{cell, conflict_key, heads, provenance, which device authored each}` is what turns a
   preserved conflict from a dormant field into a product feature: *"your phone and your laptop
   disagree about what this task is for; here are both, with who wrote each and when."*
2. **Adjudication authority as a capability.** `MERGE_SEMANTICS §4.2` is explicit: the evaluator
   must hold a capability permitting resolution of *this* Cell/type, and an unauthorized
   adjudication is just an unauthorized ATTEST rejected at acceptance. Multi-device makes the
   default case easy — the human owner adjudicates their own devices — and the interesting case
   hard: **may an agent adjudicate?** `FOLD §4`'s closing rule says no generic AI merge is
   authoritative; an AI may *propose* a merge (an ordinary ASSERT) and a trusted principal
   *attests* it. So the product shape is: agents propose, humans confirm. That is the same trust
   boundary as recall-vs-instruct, and it should not be softened for convenience.
3. **Honest re-opening.** `MERGE_SEMANTICS §4.3`: an adjudication binds only the heads it named
   as `evidence`, so a later concurrent head **re-opens** the conflict. Users will read that as
   "I already decided this." It is correct — a resolution cannot bless a branch it never saw —
   and the UX must explain it rather than hide it.

### 5.7 Partial replication scoped by an agent's horizon

`SYNC.md §3` allows a peer to request only a Cell subtree, a label, or a lamport range, and
insists on two properties: every accepted event is still fully verified, and the peer **records
the bound** so it never mistakes "didn't fetch" for "doesn't exist." `§5` adds the
skeleton/body split: a peer may hold an event skeleton without its body (relaying for others, or
lacking keys) and such cells appear as "present but opaque."

Decima already has the scoping vocabulary: a **horizon** is the explicit set of projects an
agent may see, `None` meaning all (`decima/capabilities/qa.py:92-105`), enforced by dropping
out-of-horizon segments *before* they can enter a prompt (`qa.py:9`), with a
`visible_horizon` recorded on runtime cells (`runtime/cells.py:153`, `:174`). Two very different
things are being asked of one word, and conflating them would be a security bug:

- **Horizon-as-confidentiality** (today): what an agent may *read*. A retrieval filter.
- **Horizon-as-replication-scope** (proposed): what a *device* fetches. A bandwidth/storage bound.

A thin device declaring a narrow replication scope must not thereby *widen* anyone's read
horizon, and a narrow read horizon must not be implemented by *withholding events*, because a
missing ancestor breaks causal completeness and `ingest` will orphan its descendants forever
(`weft.py:484`). Concretely: **partial replication must be closed under ancestry**, or it is not
a DAG. That is the constraint that makes naive "sync only project X" wrong — a project's events
have parents outside it.

The honest structure, therefore, is:

- **Skeleton-complete, body-partial** (recommended). Replicate all event skeletons in a realm —
  they keep the DAG closed — and fetch bodies/blobs on demand by content id with separate
  have/want (`SYNC.md §5`). A device then holds a complete, verifiable causal structure and a
  subset of payloads. `hashing.py:86-88`'s `blob_id` is already the fetch key.
- **Realm-partial** (needs §7 D3-B). Whole realms are the natural coarse boundary, because a
  realm is by construction a closed causal unit — which is precisely why `SYNC.md §1` chose it,
  and precisely why realms not existing (M6) is what blocks real partial replication.
- **Lamport-range / subtree** — deferred. Correct only with an explicit recorded bound and a
  "this view is incomplete below lamport N" affordance in every projection that reads it.

Unknown, honestly: how large event skeletons are at real scale, and therefore whether
skeleton-complete is viable on a phone. Nobody has measured. See §9.

### 5.8 Offline-first UX

Offline is the *normal* state, not an error state, and the architecture already delivers most of
it: writes are local appends; reads are local folds; nothing blocks on a network. What is needed
is mostly honesty in the interface.

- **Sync status is per-peer, not global.** "Last converged with `prn_…` at lamport N." There is
  no global "synced" — `SYNC.md §9` notes a peer can eclipse or withhold, and the *visible*
  symptom is a frontier gap or a stalled orphan. Surface those, never a green checkmark that
  means "we asked one peer once."
- **Stalled orphans are a first-class state.** `ingest` returns `"orphan"` and inserts nothing
  (`weft.py:484`). A row still orphaned after the retry loop reaches a fixpoint is
  *dangling* — a peer withheld a parent. That must appear in the UI, per `§9`: withholding is
  surfaced, not silently tolerated.
- **Resumable and idempotent** (`SYNC.md §8`): interrupt anywhere, re-run, converge; duplicates
  are no-ops by event id (`weft.py:452`, `weave.py:328-331`). This is what lets sync be a
  background nuisance rather than a modal operation.
- **Cold-device bootstrap** = verified snapshot + delta (`SYNC.md §8`, `snapshot.py:144-180`).
  A snapshot **never authorizes** events; the restored device still validates each one.
- **Effects do not re-run.** Syncing an INVOKE and its receipt must never re-execute the effect
  (`SYNC.md §10`); only the recorded receipt is folded. This is an invariant to *test*, since
  `weave.py:455-460` folds INVOKE into `invocations` and the invoke tally, and any executor that
  drains `invocations` on a synced log would double-execute. This is the single highest-severity
  latent bug in shipping multi-device sync, and it lives in the runtime, not the kernel.

### 5.9 Key custody with multiple devices

This is where multi-device forces a real change, not a port.

**Each device gets its own key. Keys never move.** `DirectoryKeyStore` (`keystore.py`) already
gives per-principal 0600 seeds in a 0700 dir, with `create/has/public_key/sign/adopt` and the
guarantee that the raw seed never crosses the boundary. Multi-device is exactly the scenario
`DerivedKeyStore`'s DEV-ONLY warning (`keystore.py:71-85`) exists to prevent: copying
`keys/master.seed` (`data_layout.py:79`) to a second machine makes one secret the whole trust
root for both. So the three `Keyring(seed=...)` sites (`services/api/server.py:69`,
`services/provision.py:59`, `cli/main.py:66`) must become
`Keyring(custodian=DirectoryKeyStore(...))` before pairing ships — and `Keyring(seed=...,
custodian=...)` already accepts an explicit custodian.

**Pairing** (`CAPABILITY_MAP §D6`: "enter one recovery phrase / scan a pairing code"). The shape
that fits the ocap model: the new device mints a keyed principal locally (its private key is
born on that device and never leaves), presents its public key out-of-band (QR / short code),
and the existing device authors two events — an ASSERT registering the new device's public key
and an attenuated capability grant scoped to what that device may do. Pairing is then **an
authorization decision on the log**, auditable and revocable by the ordinary
`DERIVED_AUTHORITY` cascade, rather than a secret copy. Note the tension with `§D6`'s
"one recovery phrase to rule recovery": a single recovery secret that can *reconstitute* device
keys is a master seed by another name. Recovery and per-device custody pull against each other,
and §7 D6 is where the owner picks.

**Rotation and loss.** A lost device is a **revocation**, not a re-key: RETRACT its grant with
`DERIVED_AUTHORITY`, and every authority derived from it fails closed in every peer's fold. Its
past events keep verifying — which is correct: history is not falsified by the loss of a laptop.
Re-keying a *surviving* device rides the existing succession chain, which already verifies each
event against the key valid **at that event's logical point** (`weft.py:243-262`) and refuses a
retired key. Multi-device sync of rotation links is already handled: `ingest` folds an ingested
link into the chain (`weft.py:502`), and causal-completeness-before-authenticity
(`weft.py:472-485`) is what makes an out-of-order feed carrying a rotation converge.

## 6. Phased plan

Each phase is independently gate-green, and none of S0–S5 touches content-addressed bytes.

- **S0 — Decisions (§7).** Nothing else starts. D1 (authority posture), D3 (realm), and D6
  (custody/recovery) all constrain data written under them.
- **S1 — Make the DAG real in tests.** No new product code: construct genuine forks via
  `append(parents=[...])` (`weft.py:284-293`) and assert `SYNC.md §10` — convergence to one
  `state_root` from either arrival order; duplicate delivery is a no-op; no accepted event ever
  dropped; orphan safety; MV conflict preserved then collapsed by an adjudication ATTEST. This
  closes M10 and is the cheapest, highest-value phase: it exercises `weave.py`'s eight reducers
  for the first time, and it will find bugs.
- **S2 — Kernel primitives (byte-neutral).** `frontier()` + an `event_parents` side index;
  merge-append semantics per D5; durable `peer_keys` side table + `trust_keyed()` (M4);
  a real `reducer_set` hash (M7); the `check_same_thread` decision (M9). Side tables and
  non-hashed helpers only.
- **S3 — In-process reconciliation.** `frontier` → causal difference → ordered feed →
  `Weft.ingest` loop with orphan-defer-retry, plus the post-ingest authority audit (D1). Two
  `Weft` objects in one process converge. This is the reference's `sync`/`sync_over_wire`
  (`heartbeat/decima/sync.py:157`, `:219`) rebuilt against the durable protocol.
- **S4 — The channel.** Port the mutual-auth handshake + `SecureChannel` with a BLAKE3/CBOR
  transcript and a new `PROTO`, per D6's placement decision. Includes channel provenance
  (`:430-456`). Adversarial lane: plaintext peer refused, replayed frame refused before
  decryption, tampered frame fails MAC, wrong-pinned-peer refused, keybook entry whose pid does
  not commit to its key refused.
- **S5 — Transport + anti-entropy + pairing.** Peer listener, periodic background sync,
  `DirectoryKeyStore` migration for the three `Keyring(seed=...)` sites, pairing flow, conflict
  inbox projection, offline status surface. Cold-device bootstrap from a verified snapshot.
- **S6 — Realms (only if D3-B).** The fixture-regenerating phase, done once, as its own
  re-freeze with the reference re-cut and `docs/release-evidence/*` re-anchored — the same
  discipline `adopt-durable-protocol.md §6 R2` established. Do not bundle S6 with anything else.

## 7. Decisions required (owner sign-off)

**D1 — Authority: at origin, re-checked on ingest, or accept-and-quarantine?**

| Option | Trade |
|---|---|
| A. Origin only (status quo, `weft.py:446-448`) | cheapest; an unauthorized-but-signed event is accepted by every peer and caught only by audit or by the revocation cascade in the fold |
| B. Full re-check at the parent frontier (`SYNC.md §4.6`) | strongest door; costs an ancestor-closure fold per event; stalls on encrypted grants a peer cannot read (`§6`) |
| **C. Accept + post-ingest quarantine** *(recommended)* | door-level detection at near-origin cost; degrades correctly under opacity; **requires** a deterministic definition of quarantined state that `state_root` agrees on across peers |

**D2 — Transport surface.** (a) dedicated peer listener with the channel handshake
*(recommended)*; (b) mount sync on the existing WSGI API (`services/api/routes.py`) — inherits
session auth, which authenticates the **wrong principal** for a peer relationship; (c) both,
HTTP as a NAT-traversal relay only, still end-to-end channel-encrypted so the relay is untrusted.

**D3 — Realm: does it become a signed field?** ⚠️ **Fixture-regenerating if B.**

| Option | Trade |
|---|---|
| **A. No signed realm** *(recommended for now)* | one realm per store; the store *is* the boundary; multi-realm = multiple `weft.db` files. Zero byte change. Diverges from `WEFT §2` field 2 and caps partial replication at whole-store granularity |
| B. Add `realm` to the hashed payload (`weft.py:72-82`) | matches the spec; enables realm-scoped sync and `SYNC.md §5` blob separation. **Changes every event id and `state_root`** ⇒ regenerate `protocol/fixtures/`, re-cut the reference, re-anchor release evidence. `needs_fixture_regen = true` |
| C. Realm as an unsigned body/meta field | cheap and byte-neutral, but a realm that is not signed is not a security boundary — it is a hint. **Not recommended**: it would look like the spec while providing none of its guarantee |

**D4 — Blob ids realm-domain-separated (`SYNC.md §5`)?** ⚠️ **Fixture-regenerating.** Today
`blob_id` separates by kind only (`hashing.py:86-88`). Cross-realm blob dedup is a
confidentiality leak (a content hash from realm A becomes a probe in realm B). Rides D3-B or
not at all; if taken, it is part of the same single re-freeze.

**D5 — Is the merge event implicit or explicit?** (a) the sync round emits a merge append over
the whole frontier — conflicts resolve promptly, but the device asserts "observed both branches"
on the user's behalf; (b) only a principal's own action creates a frontier-parented event —
honest, but conflicts persist until someone acts, and LWW cells may show a remote value winning
over a newer local one (M5). Recommendation: **(b) for adjudicated/MV classes** (never merge
intent automatically) **and (a) for mechanically-resolvable classes**, which is a defensible
reading of `FOLD §4`'s "no generic AI merge is authoritative." This needs the owner because it
is a Law-4 provenance question, not an ergonomics one.

**D6 — Custody, recovery, and channel placement.** Three coupled sub-choices:
(i) per-device keys via `DirectoryKeyStore` with pairing-as-authorization *(recommended)* vs. a
shared master seed *(rejected — `keystore.py:71-85` says why)*; (ii) whether a single recovery
phrase may reconstitute device keys (`CAPABILITY_MAP §D6`'s "dumb-easy setup") — convenient, and
a master seed by another name; (iii) whether the channel lives in `decima/kernel/peering.py`
(inside the TCB; `nacl` is already allowed, so no `ALLOWED_THIRD_PARTY` change either way) or
entirely in `decima/services/sync/` (no TCB growth). Recommendation: services — the TCB should
hold the *acceptance* rule, not the *socket*.

## 8. Non-goals

- **Server-authoritative sync.** No peer is a source of truth; there is no "the server's copy."
  A relay may store and forward encrypted bytes and must remain unable to read or forge them.
- **IBLT / range-fingerprint set reconciliation.** `SYNC.md §3` explicitly marks it an optional
  optimization; the frontier walk is always correct.
- **Operational transforms, or any merge strategy outside `MERGE_SEMANTICS §3`.** The classes
  are fixed; sync does not get to re-litigate them under delivery pressure.
- **AI-authoritative merge.** An agent may propose an ASSERT; only an authorized principal's
  ATTEST resolves (`MERGE_SEMANTICS §4.2`, `FOLD §4`).
- **Changing the merge classes, the four verbs, or the fold order.** Sync is a consumer of the
  kernel, not a reason to reshape it.
- **NAT traversal, discovery, relay infrastructure, and mobile clients.** Real work, separate
  scope, no bearing on correctness.
- **D2b / D5-of-the-re-freeze (integer field numbers, integer assertion kinds).** Still deferred
  per `adopt-durable-protocol.md §6`; but note M8 — if the wire stays JSON, adopting them later
  breaks the wire, so the wire-encoding decision should anticipate them.

## 9. Risks and honest unknowns

**Risks**

- **Effect replay is the one that can cost real money.** `SYNC.md §10` requires that syncing an
  INVOKE + receipt never re-executes the effect. `weave.py:455-460` folds INVOKE into
  `invocations` and bumps the per-capability tally; any executor draining `invocations` from a
  freshly-synced log double-executes. This is a **runtime** bug, not a kernel one, so it will not
  show up in kernel tests. It needs an explicit "only locally-authored, un-receipted invocations
  execute" rule and an adversarial test *before* S5.
- **Ancestor-closure growth.** `_ancestors` is a full per-event ancestor set (`weave.py:115`,
  `:355-359`) and is serialized into checkpoints (`weave.py:203`, inside `_CHECKPOINT_ATTRS`).
  On a chain this is already quadratic in the worst case; a DAG with real concurrency does not
  improve it. Sync is what makes this bite. Mitigation is a compressed representation
  (interval/level index), which is kernel-internal and byte-neutral — `_ancestors` is not
  hashed — but it is real work nobody has scoped.
- **A conflict nobody adjudicates is a stuck cell.** `in_conflict` is stable, correct, and
  invisible until someone builds §5.6's inbox. Without it, multi-device "works" and quietly
  accumulates unresolved intent.
- **Fixture regen if D3-B/D4.** The re-freeze discipline exists; the risk is doing it *twice* by
  landing realms separately from anything else that needs regeneration. Bundle or defer.
- **Coverage and the gate.** A new subsystem with sockets and threads is the classic way to add
  hard-to-cover branches and flaky tests. Keep the ≥78% floor honest by making S3 (in-process
  reconciliation) carry the logic and S4/S5 carry only I/O; `socketpair` is the deterministic
  substrate (`heartbeat/decima/sync.py:595-630`) and should be preferred over TCP in tests.
- **The keybook is a trust root with no revocation story.** Persisting peer keys (M4) creates
  durable trust; nothing yet says how to *untrust* a peer whose key leaked, or what happens to
  its already-accepted events. They keep verifying — correctly — which means "remove the key"
  does not undo anything. The answer is a capability revocation, not a key deletion, and it
  should be written down before the table exists.

**Unknowns — stated rather than guessed**

- **Skeleton size at scale.** §5.7's recommended "skeleton-complete, body-partial" split assumes
  event skeletons are small enough to replicate wholly onto a thin device. Nobody has measured
  bytes-per-event or growth per active day. If skeletons turn out large, partial replication
  becomes a much harder design and D3-B (realms) becomes near-mandatory.
- **Whether `check_same_thread=False` is safe here.** `weft.py:99` opens plainly, and the
  reference works around thread-affinity by reopening per thread
  (`heartbeat/decima/sync.py:493-511`). Whether the durable `Weft` — with its `_rot_chains`
  in-memory projection (`weft.py:124`), `head`, and `lamport` — is safe to share across threads
  has not been analyzed. It probably is not. Per-thread connections are the conservative answer.
- **Real conflict frequency.** Both `MERGE_SEMANTICS §6` and this document assume concurrent
  writes to the *same* `conflict_key` are rare in single-user multi-device use. Plausible;
  unmeasured. If it is common, the conflict inbox is not a nice-to-have but the main UI.
- **Whether `mint_keyed`'s `blake2b(master + name)` derivation (`crypto.py:112-114`) should
  survive.** It is deterministic-from-master by design (warm start reproduces the pid), which is
  exactly what makes two devices sharing a seed collide. A per-device custodian fixes it in
  practice, but the derivation itself is now arguably a footgun and should be reviewed under D6.
- **The reference channel's cryptographic review status.** It is a careful, well-documented
  implementation with a signed transcript, per-direction keys, and strict nonce discipline — and
  it has not been reviewed by a cryptographer. Porting it inherits that. Wrapping an audited
  protocol (Noise) instead of carrying a bespoke handshake is a live alternative this document
  does not resolve, and it should be raised as a seventh decision if S4 is actually scheduled.
