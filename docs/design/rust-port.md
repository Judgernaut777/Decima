# Design: The Rust Port — scope, gating, and strategy

**Status:** proposal — needs owner sign-off on seven decisions (§11). **Nothing here is
implementable yet**; §5 defines the gate that must be green *first*.
**Date:** 2026-07-25
**Reads with:** `VISION.md` ("How we build it", Phase 6), `heartbeat/PROFILE.md` §"To reach
v0.1 in the Rust port", `specs/WEFT_PROTOCOL.md`, `specs/FOLD_AND_LIFECYCLE.md` §11,
`docs/design/adopt-durable-protocol.md` (the re-freeze this doc builds on).

**Fixture impact:** this document changes no content-addressed bytes.
`needs_fixture_regen=false` for the doc itself. Two of the seven decisions (§11 D1, D2) —
if answered the way the *spec* rather than the *code* reads — would change signed bytes and
require a second re-freeze; that is called out inline and is precisely why they must be
decided **before** a line of Rust is written, not during the port.

---

## 1. One-paragraph summary

`VISION.md:349` commits to porting **the entire program to Rust once, at the end**, with no
hybrid mid-build; `VISION.md:432` makes it Phase 6, gated on "the reference being stable and
complete, with this whole roadmap green against the oracle"; `README.md:222` calls it
*distant, not near*. That ordering is correct and this document does not argue with it. What
it does is make the port **decidable in advance**: it states exactly what the port must
reproduce (the conformance oracle — golden vectors in `protocol/fixtures/`, the eight `FOLD
§11` invariants, and `state_root` byte-equality), what "the reference is stable" must
concretely mean before anyone starts (§5), the order the port must proceed in (kernel →
runtime → services), how cross-implementation agreement is proven *continuously* rather than
once, and where Rust actually earns its cost (fold throughput, memory safety in the TCB, real
sandboxing) versus where it buys nothing. The uncomfortable finding is §3.2/§4: **today's
conformance oracle is far too thin to gate a port.** It pins canonical bytes, ids, and one
`state_root`; it pins nothing about signature bytes, key derivation, authorization proofs,
merge semantics, ingest acceptance, receipts, leases, or rotation. A port that passed every
fixture in `protocol/fixtures/` today could still be wire-incompatible with Python. Widening
the oracle is the largest pre-port work item, and it is worth doing regardless of whether the
port ever happens, because it is what makes the reference *a specification* instead of *an
implementation*.

## 2. Motivation — and why "last" is right

The port is not a performance project. It is the step that turns Decima from "a reference
implementation with a spec next to it" into "a specification with two conforming
implementations." Three concrete payoffs:

- **The TCB stops being memory-unsafe-adjacent and starts being provable.** `decima/kernel`
  is 18 modules / ~4.2k lines (`weave.py` 1021, `weft.py` 508, `capability.py` 501,
  `rotation.py` 500). It is the whole trust boundary. In Rust the id/pid/digest types become
  distinct newtypes that cannot be transposed, the "no floats in signed content" rule becomes
  a type error rather than a review convention, and `Option`/`Result` make the fail-closed
  paths (`capability.authorize`, `Weft.ingest`) exhaustive by construction.
- **The fold becomes fast enough to stop needing caches to be correctness-critical.**
  `Weave.state_root()` (`decima/kernel/weave.py:919-965`) walks every cell and sorts every
  edge/attestation list on every read; the fold from genesis is the known-unscalable
  operation `VISION.md` calls out under "The honest hard parts." Snapshots
  (`decima/kernel/snapshot.py`, `checkpoints.py`) exist to paper over it. A fold that is
  10–50× faster does not remove the need for snapshots but demotes them from *load-bearing*
  to *optimization*, which is a real reduction in the number of things that can be wrong.
- **Real sandboxing becomes native instead of best-effort.** `specs/SANDBOX.md` §3 names the
  target mechanisms (seccomp-bpf, landlock, namespaces, cgroups, Firecracker, WASM
  components). Today's worker jail is honest about being partial:
  `decima/workers/execution.py:125-135` documents that the seccomp-bpf deny filter is
  **aarch64-only** and records `seccomp ABSENT` elsewhere rather than failing closed. In Rust
  the same profile maps onto maintained crates (`landlock`, `seccompiler`, `cap-std`,
  `wasmtime`) as a first-class, portable capability rather than a ctypes BPF program.

And why last: every one of those payoffs is *worth less than* the cost of maintaining two
implementations of a moving design. The port's value is entirely a function of the design
having stopped moving — which is what §5 tries to define measurably.

## 3. What the port must reproduce — the conformance oracle

The port's contract is **not** the Python source. It is three layers, in decreasing
strictness.

### 3.1 Layer 1 — byte-equality against the golden vectors (hard, mechanical)

`protocol/fixtures/` holds exactly two files, generated by `tools/kernel/gen_fixtures.py`
from the shipping `decima.kernel` (which became the reference at the re-freeze) and asserted
by `tests/kernel/test_conformance.py`:

| Vector | Pins | Asserted at |
|---|---|---|
| `canonical.json` → `payloads[]` | `canonical_hex` (deterministic CBOR bytes), `content_id_cell`, `content_id_event` for 7 awkward payloads (key-order, deep nesting, unicode/NFC + combining marks, empty collections, negative and 90-bit integers) | `test_conformance.py:36-42` |
| `canonical.json` → `blobs[]` | `blob_id` for empty, ASCII, and all-256-bytes inputs | `test_conformance.py:50-55` |
| `fold.json` | `master_seed_hex`, `author_pid`, `type_cell_id`, the ordered `events[].id` list, `state_root`, `type_counts`, `event_count` for a fixed 6-event script (TYPE_DEF, three CONTENT, EDGE, RETRACT/WITHDRAW) | `test_conformance.py:78-96` |

Concretely, the port is byte-conformant on layer 1 when, given the same inputs, it emits:

- `canonical(payload)` == `a2616101616202` for `{"a":1,"b":2}` — deterministic CBOR
  (`decima/kernel/hashing.py:72`), sorted keys, shortest integers, definite lengths, no
  floats, with **every nested string NFC-normalized first** (`hashing.nfc_deep`,
  `hashing.py:50-64`);
- `digest(kind, bytes) = BLAKE3-256("decima:v0.1:" || kind || 0x00 || bytes)`
  (`hashing.py:31,76`), rendered as `<prefix>_<base32-lower, unpadded>` with the kind→prefix
  map `event→evt`, `cell→cell`, `body→bdy`, `blob→blob`, `principal→prn`, `capability→cap`
  (`hashing.py:35-42`) — **and the fallback**: an unmapped kind uses the kind string itself,
  which is why `state_root` is `snapshot_u6o4lkqku3gziqbiu37zuhr2slqo5wzybwroylhkut3nbxgxffna`
  (`hashing.py:77`, `weave.py:965`). That fallback is load-bearing, not cosmetic;
- `author_pid == prn_56hrb42nb4otpxwvozvqpbsckugq7fzhxnb2w22jyolsspymq2ka`, which requires
  reproducing that a **default-minted pid is the digest of the principal's NAME**, not of its
  key: `pid = blob_id(name.encode(), kind="principal")` (`decima/kernel/crypto.py:67`). The
  key-committing form exists separately (`Keyring.keyed_pid`, `crypto.py:78-95`);
- the same `state_root`, which requires reproducing the exact record schema in
  `weave.py:919-965` — the field order, the `sorted([rel, src, dst])` edge shape, the
  `sorted([by, claim])` attestation shape, `content_heads` in `(lamport, event_id)` order, and
  the tombstone/cascade/lease/supersede flags — *and* that the root is
  `content_id({"state_root": records}, kind="snapshot")`, i.e. wrapped in a one-key map.

### 3.2 What layer 1 does **not** pin (the honest gap)

This list is the core finding of this document. Everything below is behavior a Rust port
could implement differently while passing 100% of `protocol/fixtures/`:

1. **Signature bytes.** `fold.json` records no signature. The replay constructs a `Keyring`
   from `master_seed_hex` (`test_conformance.py:64`) and the Weft verifies signatures on read
   (`weft.py:391-392`), so signing is *exercised* — but only for self-consistency. A port
   with a different key derivation and different signature bytes passes.
2. **Key derivation.** The default custodian derives each Ed25519 seed as
   `blake2b(master || pid, digest_size=32, person=b"decima:ed255")`
   (`decima/kernel/keystore.py:93-97`), and `mint_keyed` uses `person=b"decima:keyid"`
   (`crypto.py:110-112`). So the TCB contains **two** hash functions: BLAKE3 for content
   addressing and personalized BLAKE2b for key derivation. The port must reproduce the
   BLAKE2b personalization exactly or no cross-implementation warm start / signature
   verification works — and nothing currently tests that.
3. **The signature domain.** `weft.append` signs the **id text**, not the canonical bytes:
   `eid = content_id(payload, kind="event")` then `sig = keyring.sign(author_pid, eid)`
   (`weft.py:305-306`), verified as `eid.encode()` (`crypto.py:172`, `keystore.py:111`).
   `WEFT_PROTOCOL.md:31` says the signature covers `event_unsigned_bytes`. These are
   different messages. Not a security defect (the id commits to the bytes), but it *is* an
   undocumented wire divergence a second implementation will get wrong.
4. **The event envelope shape.** The stored/hashed payload is
   `{parents, author, authorized, verb, body, lamport}` (`weft.py:296-303`). `WEFT_PROTOCOL.md:34-46`
   specifies `protocol`, `realm`, `nonce bytes16`, `wall_time`, `extensions`, and
   `authorization: AuthorizationProof` — the code carries `authorized` as a single optional
   **string**. The frozen contract and the written spec disagree about the envelope. §11 D1.
5. **The storage encoding.** Events are stored as **JSON text**
   (`json.dumps(payload, sort_keys=True)`, `weft.py:327`, `:498`) in a SQLite `payload TEXT`
   column (`weft.py:100-108`), while the *hashed* bytes are CBOR. On read the row is
   `json.loads`-ed and the id is **recomputed** (`weft.py:391-392`, and again in `ingest`,
   `:455,470`). So JSON→value→CBOR must be an exact round-trip. In Python it is; in Rust it is
   a trap — `serde_json` maps numbers to `i64`/`u64`/`f64`, so the fixture's 90-bit
   `{"big_int": 123456789012345678901234567890}` (which `cbor2` encodes as a bignum) needs
   arbitrary-precision JSON parsing, and any integer that round-trips as a float silently
   changes the id. §11 D3.
6. **Ingest acceptance semantics.** `Weft.ingest` (`weft.py:408-500`) returns exact status
   strings per rejection branch (id mismatch, bad signature, unsorted parents, missing
   parent, wrong lamport, author mismatch). Those strings are golden vectors in
   `tests/kernel/test_event_fixtures.py` — **in-process only, not in `protocol/fixtures/`**.
7. **Authorization, capabilities, denial codes.** `capability.authorize_detail`'s
   `ReasonCode` vocabulary (`decima/kernel/authorization.py`, pinned by
   `tests/kernel/test_denial_codes.py`) is a cross-implementation contract — a Rust kernel
   that denies for the right reason with a different code breaks every consumer — and it is
   not in the fixtures.
8. **Merge semantics.** `specs/MERGE_SEMANTICS.md`'s per-type reducers (LWW/MV/OR-set/
   Sequence/Map/Counter/Append-log + `ATTEST` adjudication) are the hardest thing to port
   correctly and are exercised by **no golden fork vector**; `fold.json` is a linear chain.
9. **Rotation, leases, receipts, checkpoints.** `rotation.py` (500 lines of succession-chain
   folding, incl. the `upto_seq` causality prefix at `weft.py:230`), lease expiry against the
   lamport frontier, `EffectReceipt` status reduction, and signed checkpoints — all
   invariant-tested, none byte-pinned.
10. **Retrieval scoring.** `decima/projections/search.py` and `decima/capabilities/qa.py` use
    deterministic **integer** lexical scoring with sorted `matched_tokens`. That determinism
    is portable exactly *because* it is integer, but the tie-break ordering is unpinned across
    implementations.

### 3.3 Layer 2 — the `FOLD §11` invariants (semantic, must hold in both)

`specs/FOLD_AND_LIFECYCLE.md` §11 lists eight invariants; `heartbeat/PROFILE.md:50-74`
records **all 8 now holding** in the profile, with the residue being durable-*form* work
(physical byte-erasure for REDACT; full receipt reconciliation/compensation). The port must
hold all eight, and its test for each must be *the same scenario*, not a re-derived one:
replay determinism, arrival-order independence over a **real fork** (not a linear chain),
duplicate-delivery idempotence by event id, revoked authority failing closed after the
frontier, derived scope never wider than parent, no external effects on replay, redacted
payloads absent from every projection, ambiguous execution → `UNKNOWN`.

### 3.4 Layer 3 — the behavioral surface (Python is the oracle, not the spec)

Service HTTP shapes (`decima/services/api/contracts`), the READ-CONTRACT
(`decima/read_contract.py`, `READ_CONTRACT_VERSION`), CLI output, the rendered Shell
(9 Playwright spec files). These are *not* protocol; they are product surface. They must be
reproduced only where an external consumer depends on them — the READ-CONTRACT explicitly
does (`decima/read_contract.py:1-25`), so it is a real port obligation; CLI prose is not.

## 4. What exists today vs what is missing

| Capability the port needs | State today | Where |
|---|---|---|
| Fixture generator from a single reference | ✅ exists, deterministic (fixed seed, no wall-clock, no random) | `tools/kernel/gen_fixtures.py` |
| Byte-equality conformance test | ✅ exists but narrow (5 test functions, 1 linear script) | `tests/kernel/test_conformance.py` |
| Canonical bytes / digest / id text frozen | ✅ landed at the re-freeze | `hashing.py:31-88`; `WEFT_PROTOCOL.md:22-27` |
| Store-format version stamp (fail closed on open) | ✅ `decima-weft/0.1`, side table, never hashed | `weft.py:89-156` |
| TCB import boundary with an explicit allowlist | ✅ `{nacl, sqlite3, blake3, cbor2}` | `tests/architecture/test_import_boundaries.py:56` |
| Fork / merge golden vectors | ❌ missing — fixtures are linear | §3.2 (8) |
| Signature + key-derivation vectors | ❌ missing | §3.2 (1,2) |
| Ingest-acceptance vectors as files | ⚠️ in-process only | `tests/kernel/test_event_fixtures.py` |
| Denial-code vocabulary as a file contract | ⚠️ in-process only | `tests/kernel/test_denial_codes.py` |
| Spec↔code envelope reconciliation | ❌ open divergence | §3.2 (4); `WEFT_PROTOCOL.md:34-46` vs `weft.py:296-303` |
| Signed-bytes definition reconciliation | ❌ open divergence | §3.2 (3); `WEFT_PROTOCOL.md:31` vs `weft.py:305-306` |
| Integer field numbers for signed structs (D2b/D5) | ⏳ deferred, still string-keyed | `adopt-durable-protocol.md` §6; `hashing.py:12-16` |
| Concurrency story in the store | ❌ plain `sqlite3.connect`, no WAL, no `check_same_thread`, no busy timeout | `weft.py:99` |
| Real (non-best-effort) sandbox | ⚠️ namespaces+rlimits mandatory, seccomp aarch64-only | `decima/workers/execution.py:125-135`; `specs/SANDBOX.md` §3 |
| Roadmap phases 1–5 (enforcement → surface) | ⏳ in flight; Phase 6 *is* the port | `VISION.md:378-432` |

Sizes, for cost calibration: `decima/` is ~18.5k lines of Python (kernel 4.2k, services 6.2k,
models 1.8k, workers 1.5k, runtime 1.5k, projections 1.3k, capabilities 1.1k, cli 0.2k,
shell 0.35k + 18 frontend asset files), against ~15k lines of tests plus 9 Playwright specs.
A whole-program port is therefore an 18k-line rewrite whose *test* surface is comparable
again — and the tests are where the semantics actually live.

## 5. The stable-reference gate (entry criteria)

"The design has stopped moving" must be a measurement, not a feeling. The port may not start
until **every** row below is true. Each row is checkable.

**A. Protocol freeze is complete.**

| # | Requirement | Rationale |
|---|---|---|
| A1 | D2b (integer field numbers) and D5 (integer assertion kinds) are either **landed** or **formally withdrawn** in `WEFT_PROTOCOL.md` | Both change signed bytes. Deciding them after the port starts means re-freezing *two* implementations. |
| A2 | §3.2 (3) resolved: signed material is defined **in one place** and code matches spec | A second implementation cannot guess this. |
| A3 | §3.2 (4) resolved: the envelope in `WEFT_PROTOCOL.md` §2 and the payload the code hashes are the same struct, field for field | Same. |
| A4 | `heartbeat/PROFILE.md` §"To reach v0.1" items 1–7 are all either done or explicitly re-scoped as post-port | It is the standing port checklist; it must not still be a wish list. |
| A5 | No change to hashing / canonical encoding / id text / pid width / signed-struct shape for **two consecutive tagged releases** | The churn metric. If the bytes moved recently, they will move again. |

**B. The oracle is wide enough to be a specification.**

| # | Requirement |
|---|---|
| B1 | Every gap in §3.2 (1)–(9) is either a file-based golden vector under `protocol/fixtures/` or a written, deliberate "implementation-defined" note in the spec |
| B2 | `protocol/fixtures/` includes at least: a **fork** vector (two concurrent branches, per-type merge, one `state_root`), a **signature** vector (message bytes + sig hex + pubkey per fixture principal), a **key-derivation** vector (master seed → pid → pubkey), an **ingest** vector (accepted + one row per rejection branch with its exact status), a **denial-code** vector, and a **rotation** vector (pre/post-rotation verification + a retired-key refusal) |
| B3 | Fixtures are **versioned** — each file carries `{"protocol": "decima-weft/0.1", "generator": "<path>", "generated_from": "<commit>"}` so a port can assert which contract it targets |
| B4 | `FOLD §11` has one named, fixture-driven scenario per invariant that both implementations run — not two independent test suites that happen to agree |
| B5 | The full gate is green: `ruff format`+`check` (0.15.20), `mypy` strict on `decima.kernel.*` with no `Any`, pytest with coverage ≥ 78% (`.github/workflows/ci.yml:64`), the adversarial lane, the release-metadata drift guard, Playwright |

**C. The product has stopped redefining the kernel.**

| # | Requirement |
|---|---|
| C1 | `VISION.md` Phases 1–5 are green against the oracle (`VISION.md:432` already says this; it is restated as a gate row, not a new claim) |
| C2 | Enforcement is real, not convention: quarantine boundary, worker isolation, egress boundary, authenticated+confidential sync (`VISION.md:378-390`) — because each of those either adds kernel surface or changes what the sandbox contract must express, and porting a sandbox contract that is about to change is waste |
| C3 | Snapshot/checkpoint format frozen (it digests `state_root`, so it is content-addressed surface) |
| C4 | The READ-CONTRACT version has been stable across two releases (external consumer) |

**D. The port has a place to run.** A Rust toolchain lane in CI, a decision on the crypto and
CBOR crates (§11 D4), and a cross-implementation conformance job that can fail the build (§9).

If the owner wants a single sentence for the gate: **the port starts when the content-addressed
bytes have not moved for two releases and every §3.2 gap is either a file in
`protocol/fixtures/` or a written "implementation-defined."**

## 6. Architecture of the port

Mirror the Python package boundaries exactly — they are already the trust boundaries, and
keeping them identical is what makes differential testing possible.

```text
decima-rs/
  crates/
    weft-hash/      canonical CBOR + BLAKE3 digests + base32 id text     ← no I/O at all
    weft-id/        newtypes: EventId, CellId, BodyId, BlobId, Pid, CapId
    weft-crypto/    Ed25519 sign/verify, keystore custody, BLAKE2b-personalized derivation
    weft-store/     the append-only log (SQLite), store-version stamp, ingest acceptance
    weft-weave/     the fold, per-type merge, state_root, cascade, leases, receipts
    weft-cap/       capabilities, attenuation, authorization proofs, denial codes, inbox
    weft-kernel/    the façade == decima.kernel's __init__ surface
    decima-runtime/ scheduler, supervisor, budgets, cancellation, reconciliation
    decima-workers/ isolation: landlock + seccomp + namespaces + (later) wasmtime
    decima-svc/     HTTP API, READ-CONTRACT, CLI
  conformance/      the shared-fixture runner (reads protocol/fixtures/ verbatim)
```

Dependency mapping, and the boundary rule that carries over: **`weft-hash` through
`weft-kernel` are the TCB.** The Rust analogue of
`tests/architecture/test_import_boundaries.py` is a `cargo-deny`/`cargo-vet` policy plus a
`#![forbid(unsafe_code)]` on every TCB crate and a hard ban on `std::net`, `std::process`, and
async runtimes in those crates' dependency closures.

| Python | Rust candidate | Note |
|---|---|---|
| `blake3` | `blake3` | Same reference implementation; the domain prefix is ours. |
| `cbor2` (`canonical=True`) | `ciborium` or `minicbor`, **canonical mode audited** | The single highest-risk mapping. RFC 8949 §4.2 "canonical" is interpreted differently by libraries (map key sort by encoded bytes vs. by value; bignum handling). Must be pinned by fixture, not by trust. |
| `hashlib.blake2b(..., person=)` | `blake2` crate with the personalization parameter | Personalized BLAKE2b is *not* the common API path; verify it byte-for-byte against §3.2 (2) vectors. |
| PyNaCl / libsodium | `ed25519-dalek` **or** `libsodium-sys` | §11 D4. Dalek is pure Rust and audited; a libsodium binding keeps "the same engine as Python." |
| `sqlite3` | `rusqlite` (bundled SQLite) | Also the moment to decide WAL / busy-timeout / thread policy the Python side never made (`weft.py:99`). |
| `unicodedata.normalize("NFC", …)` | `unicode-normalization` | Must agree on the **same Unicode version** or ids diverge on new codepoints. Real, under-appreciated risk (§13). |
| — | `landlock`, `seccompiler`, `cap-std`, `wasmtime` | New capability, not a port (§10). |

Determinism carries over structurally rather than by convention: budgets/scores are `i64`/
`u64` (no `f32`/`f64` anywhere in signed content — enforceable by a clippy lint on the TCB
crates), logical time is an explicit `Lamport(u64)` parameter, and `now=` stays a threaded
argument rather than a call to the clock.

## 7. Phased plan

Each phase ends with a **failing-then-passing conformance run**, never with "it compiles."

| Phase | Scope | Exit criterion |
|---|---|---|
| **P0** Harness | The `conformance/` runner reads `protocol/fixtures/*.json` **verbatim** (same files Python asserts against) and reports per-vector pass/fail. Fixtures are the contract; neither implementation owns them. | The runner runs in CI and fails loudly with zero crates implemented. |
| **P1** `weft-hash` + `weft-id` | Canonical CBOR, `nfc_deep`, BLAKE3 domain-separated digest, base32 id text + the unmapped-kind fallback. | 100% of `canonical.json` byte-equal, incl. the 90-bit integer and the combining-mark payloads. |
| **P2** `weft-crypto` | Ed25519 sign/verify, personalized-BLAKE2b derivation, `mint`/`mint_keyed` pid derivation, keystore custody (`has`/`public_key`/`sign`/`adopt`; a `DirectoryKeyStore` equivalent with 0600/0700 modes). | The B2 signature + key-derivation vectors byte-equal; `author_pid` from `fold.json` reproduced. |
| **P3** `weft-store` | Append-only log, store-version stamp, lamport rule, sorted parents, read-time id+signature recompute, `ingest` acceptance with the exact status vocabulary. | `fold.json` event ids in order; every ingest vector returns the exact status. |
| **P4** `weft-weave` | The fold, per-type merge, cascade, leases, receipt reduction, `state_root`. | `fold.json` `state_root` + `type_counts` byte-equal; the fork vector byte-equal; all 8 `FOLD §11` invariants green on the shared scenarios. |
| **P5** `weft-cap` | Capabilities, attenuation, authorization proofs, approvals/inbox, denial codes. | Denial-code vector exact; "derived scope never broader" and "revoked fails closed" green. |
| **P6** `decima-runtime` | Scheduler, supervisor, budgets (ints), cancellation, reconciliation. | Property tests ported; no wall-clock in any signed path. |
| **P7** `decima-workers` | Isolation as a first-class capability: landlock + seccomp + namespaces mandatory on Linux, WASM components for foreign engines. | The adversarial lane's containment matrix passes against the Rust worker, *and* the aarch64-only caveat is gone. |
| **P8** `decima-svc` | HTTP API, READ-CONTRACT at the pinned version, CLI. | Same responses for the READ-CONTRACT surface; Playwright specs pass against the Rust server. |
| **P9** Cutover | §11 D6. | One implementation is authoritative; the other's role is written down. |

P1–P5 are the port. P6–P8 are a rewrite of code that is *mostly* not protocol, and they are
where scope explodes; they should be re-estimated after P5, not planned in detail now.

## 8. Non-negotiables that must survive the port

Restated because they are exactly the properties a rewrite tends to lose:

- **No floats in hashed or signed content.** Budgets, scores, confidence (`WEFT_PROTOCOL.md:176`
  — "integer millionths, never float") stay integers.
- **No wall-clock in signed content.** Logical time / lamport only; `wall_time` stays
  informational and untrusted (`WEFT_PROTOCOL.md:43`). `now=` is threaded, never read.
- **The fold is replayable.** Same events → same `state_root`, independent of arrival order,
  idempotent under duplicate delivery.
- **The TCB imports nothing outward.** No network, no process spawn, no provider SDK, no web
  framework — the Rust form of `tests/architecture/test_import_boundaries.py`, enforced on the
  dependency *closure* (which the Python guard admits it cannot do:
  `test_import_boundaries.py:14-17`).
- **Fail closed everywhere.** Unknown required field, unknown verb version, missing parent,
  retired key, absent capability → refusal, never a fallback.
- **`heartbeat/` and `protocol/fixtures/` stay frozen.** The port reads the fixtures; it never
  regenerates them. If the port's output differs, **the port is wrong** — unless the owner has
  signed a re-freeze (which §11 D1/D2 might force, and which would then be authored as its own
  design doc in the shape of `adopt-durable-protocol.md`).

## 9. How cross-implementation conformance is proven continuously

The failure mode to design against is not "the port is wrong on day one" — the harness catches
that. It is **drift after the port**: Python gains a merge tweak, Rust doesn't, and nobody
notices for four months.

1. **One fixture set, two consumers.** `protocol/fixtures/` is owned by neither
   implementation. Python asserts against it (`tests/kernel/test_conformance.py`); Rust asserts
   against the same bytes. Regeneration is a deliberate, documented re-freeze event.
2. **Generator parity.** After the port, `tools/kernel/gen_fixtures.py` gains a Rust twin, and
   CI asserts the two generators produce **identical files**. This is stronger than both
   passing the checked-in fixtures: it catches a shared vector set that has simply grown stale
   relative to both implementations.
3. **A cross-implementation CI job that can fail the build.** Not a nightly advisory. Same
   gating status as the adversarial lane.
4. **Differential property testing.** Generate random-but-legal event DAGs (the existing
   `hypothesis` suites in `tests/property/` are the seed corpus), fold in both, compare
   `state_root`. This is where merge-semantics divergence gets caught, because enumerating
   merge cases by hand does not work.
5. **Shadow mode during cutover.** The Rust store ingests the same event stream as Python and
   both publish `state_root` at each checkpoint; a mismatch is an incident. This is the only
   honest way to gain confidence in P4/P5 on real data, and it argues for D6 = dual-run.
6. **A conformance profile document**, the way `heartbeat/PROFILE.md` does it today: any place
   the Rust implementation deliberately differs is a **row in a table with a reason**, never a
   silent divergence. That file is the port's PROFILE.md, and it should exist from P1.

## 10. What must NOT be ported early (or at all)

- **Anything before P1.** No "let's do the fast fold first while the spec settles." A partially
  ported kernel is the hybrid `VISION.md:349` forbids, and it doubles the cost of every
  remaining protocol change.
- **The Shell frontend.** HTML/CSS/JS assets (`decima/shell/frontend/`) are not Python and have
  nothing to gain. They should be served unchanged by whichever server exists.
- **Model/provider integration.** Latency-bound, dominated by network and provider SDK
  ergonomics. Rust buys nothing and costs SDK maturity.
- **Retrieval scoring.** `decima/projections/search.py` / `decima/capabilities/qa.py` are
  deterministic integer lexical scoring — already fast, already portable, zero risk in Python.
  Port them last, mechanically.
- **New features.** The port is a **behavior-preserving** exercise. Every "while we're in
  here" is a place the two implementations diverge and the conformance job goes red for a
  reason nobody can attribute. Feature work pauses or stays in Python; it does not ride along.
- **The real sandbox (P7), as *port* work.** It is a *new capability* (`specs/SANDBOX.md` §3)
  that happens to be easier in Rust. Keep it a separately scoped project so its risk is not
  attributed to the port and vice versa.
- **`heartbeat/`.** It is the frozen historical v0-profile oracle. It is never ported, never
  edited, never revived.

## 11. Decisions required (owner sign-off)

**D1 — Is the frozen contract the fixtures, or `WEFT_PROTOCOL.md` §2?** The envelope in the
spec (`protocol`, `realm`, `nonce`, `wall_time`, `extensions`, structured `AuthorizationProof`)
is not the struct the code hashes (`weft.py:296-303`).

| Option | What | Trade |
|---|---|---|
| **A. Code wins** *(recommended)* | Amend `WEFT_PROTOCOL.md` §2 to document the actual envelope; move `realm`/`nonce`/`extensions` to a §10 "reserved for a future protocol version" note | No fixture regen. Honest. The spec stops over-promising. |
| B. Spec wins, before the port | Add the missing fields to the signed struct now | **`needs_fixture_regen=true`** — a second re-freeze. But it is the last chance to do it *once* instead of twice. |
| C. Spec wins, during the port | Both implementations change together | Worst: two implementations changing signed bytes simultaneously. |

**D2 — What exactly is signed?** The code signs the id text (`weft.py:305-306`); the spec says
`event_unsigned_bytes` (`WEFT_PROTOCOL.md:31`).

| Option | Trade |
|---|---|
| **A. Document signing-over-the-id** *(recommended)* | No regen. Secure (the id commits to the canonical bytes) but unusual, so it must be written down explicitly or the port will get it wrong. |
| B. Move to signing canonical bytes | Conventional and matches the spec, but **`needs_fixture_regen=true`** for any vector that pins signatures. Cheapest *now*, before signature vectors exist (B2) — which is an argument for deciding D2 before B2. |

**D3 — Rust store format: byte-identical rows, or CBOR blobs?** Python stores JSON text
(`weft.py:327`) and recomputes ids from it.

| Option | Trade |
|---|---|
| **A. Reproduce JSON-text rows** *(recommended for P3)* | A Rust build can open a Python-written `weft.db` and vice versa; shadow mode (§9.5) becomes trivial. Cost: arbitrary-precision JSON parsing in Rust, and the encoding stays odd (JSON storage, CBOR hashing). |
| B. Store canonical CBOR blobs | Cleaner, faster, one encoding. But stores are no longer interchangeable, so it needs a new `WEFT_STORE_VERSION` and an export/import path — and it forecloses shadow mode on a shared file. |
| C. A \| B behind the store-version stamp | Most flexible, most code. |

**D4 — Crypto crate.** `ed25519-dalek` (pure Rust, audited, no C toolchain) vs a
`libsodium-sys` binding (bit-identical engine to PyNaCl). Recommendation: **dalek**, with
D2's answer pinned by fixture so "same engine" stops mattering.

**D5 — Port shape: whole program, or Rust TCB under Python services?** `VISION.md:349` says
whole program, no hybrid. That is the recommendation. But note honestly: a Rust `weft-kernel`
with a Python binding would deliver the fold-performance and memory-safety payoffs at ~25% of
the cost, and P6–P8 (services/CLI/API, ~10k lines) deliver mostly *nothing* protocol-relevant.
If the owner ever revisits the no-hybrid rule, **this is the place** — and the answer should be
recorded here rather than rediscovered mid-port.

**D6 — Cutover.** Flag day (Rust becomes authoritative at a tag) vs dual-run shadow (both fold
the same stream, compare `state_root`, promote after N clean days). Recommendation: **dual-run**,
which is only possible if D3 = A.

**D7 — What is Python's role afterward?** Retire it; keep it frozen as a second oracle (the
`heartbeat/` pattern, which has worked); or maintain both indefinitely (expensive, and the
§9 machinery is the only thing that makes it survivable). Recommendation: **freeze as a second
oracle**, because a specification with one implementation is just an implementation.

## 12. Non-goals

- Starting the port. This document defines the gate; §5 is currently **not** met.
- Any change to hashing, canonical encoding, id text, pid width, or signed-struct shape
  *in this document*. D1/D2 may force one later; it would be its own design doc.
- Touching `heartbeat/` or `protocol/fixtures/`.
- Porting the Shell frontend, provider integrations, or retrieval scoring early (§10).
- A hybrid Python/Rust build as the steady state (unless D5 is answered against the current
  vision, which is the owner's call to make explicitly).
- Rewriting the sandbox as part of the port (§10) — related, separately scoped.
- Post-quantum or curve changes. Ed25519 stays; 128-bit is the level the rest is balanced to
  (`adopt-durable-protocol.md` §5, §8).

## 13. Risks

- **Second-system syndrome.** The single largest risk, and it is a *social* risk, not a
  technical one: a rewrite invites redesign, and every redesign during the port makes the
  conformance job red for reasons nobody can attribute. Mitigation: §10's "no new features"
  rule, and a port PROFILE.md (§9.6) where any deliberate divergence must be *written down*.
- **CBOR canonical-mode mismatch.** `cbor2(canonical=True)` and any Rust CBOR crate can
  disagree on map-key ordering (encoded-bytes order vs value order) and on bignum encoding.
  This is a silent id-divergence bug. Mitigation: it is exactly what `canonical.json` exists to
  catch — but the current vector set is 7 payloads. It needs adversarial payloads (keys that
  sort differently by bytes than by value, 2⁶⁴ boundary integers, deeply nested empties).
- **Unicode version skew.** NFC normalization is applied to every nested string
  (`hashing.py:50-64`). Python's `unicodedata` and Rust's `unicode-normalization` track
  Unicode versions independently. A payload containing a codepoint whose normalization changed
  between versions gets **two different ids in two implementations** — and it will not show up
  in any fixture written before that codepoint existed. Partial mitigation: pin and *assert*
  the Unicode version in both implementations, and treat a mismatch as a build failure. This
  is a genuine long-term hazard of hashing normalized text, and there is no complete fix.
- **The fixtures are too thin to gate a port** (§3.2). Mitigated only by doing gate item B
  first. If the port starts before B, it will pass conformance and still be incompatible.
- **Two divergent stores in the wild.** Mitigated by the store-version stamp (`weft.py:93`),
  which already fails closed on open — the mechanism exists and works.
- **Estimation error on P6–P8.** ~10k lines of services/runtime/CLI with no protocol content is
  where "port the kernel" turns into "rewrite the product." Mitigated by re-estimating after P5
  and by D5 being an explicit, revisitable decision.
- **Concurrency semantics get *invented* rather than ported.** Python's store is a plain
  `sqlite3.connect` (`weft.py:99`) with no WAL, no busy timeout, no `check_same_thread` — so
  there is no concurrency contract to port, and Rust's `Send`/`Sync` will force one to be
  chosen. That choice is new design happening inside a port, which is the pattern §10 exists to
  prevent. It should be settled in Python first (gate row, arguably C5).
- **Rust does not fix the hard parts.** Folding from genesis still doesn't scale; merging
  *intent* is still harder than merging text; LLMs are still nondeterministic. The port makes
  the fold faster and the TCB safer. It solves none of the problems `VISION.md` lists under
  "The honest hard parts."

## 14. What is genuinely unknown

Stated plainly, because a design doc that pretends otherwise is worse than useless:

- **Whether the fold is actually the bottleneck.** There is no profile in this repo showing
  fold time dominating anything. The performance case for Rust is *plausible* (state_root
  sorts every cell's edges on every read) but **unmeasured**. A pre-port benchmark of
  `Weave.fold` + `state_root()` at 10⁴/10⁵/10⁶ events is cheap, and it should exist before the
  port is justified on performance grounds.
- **Whether any Rust CBOR crate is canonical-compatible with `cbor2`** across the full payload
  space. Answerable in a day with a differential fuzz harness. Nobody has done it.
- **The real size of P6–P8.** Line counts are known; the semantic density is not.
- **Whether `heartbeat/`'s frozen v0-profile oracle still has a job** once there are two v0.1
  implementations. Probably yes (as history), but the answer affects D7.
- **How much of the merge layer is spec versus accident.** `weave.py` is 1021 lines. Some of it
  is `MERGE_SEMANTICS.md`; some of it is choices nobody wrote down. Until the fork vectors of
  B2 exist, the honest answer to "what must the port reproduce here?" is *we don't fully know*.
- **Whether the port ever happens.** It is Phase 6, gated on Phases 1–5, and it is *distant*
  (`README.md:222`). This document's value is mostly in §5 and §3.2 — the gate and the gap list
  are worth having even if no Rust is ever written, because they are what turn the reference
  into a specification.
