# rust/ — the Rust port of Decima (milestone 3)

Byte-for-byte conformance with the Python heartbeat reference
(`heartbeat/decima/`) against `heartbeat/protocol/reference_vectors.json`
(milestone 1), `rust/vectors/extended_vectors.json` (milestone 2: SQLite
persistence, warm start, INVOKE/ATTEST fold), AND
`rust/vectors/extended_vectors_v3.json` (milestone 3: ATTEST
adjudication-collapse, trusted tiered promotion, EffectReceipt projections,
lease-expiry derivation), proven by the `decima-verify` binary (verifier v3
criteria). The extended vectors are generated FROM the reference by
`rust/vectors/generate.py` (all-zero master seed, deterministic;
byte-stable across runs).

## Layout

- `decima-core/` — library crate:
  - `hashing` — canonical encoding (sorted-key JSON, UTF-8/raw non-ASCII,
    separators `","`/`":"`, recursive NFC, no floats, arbitrary-precision
    ints via serde_json `arbitrary_precision`) and domain-separated
    BLAKE2b-128 content addressing (`"decima:v0.1:" || kind || 0x00 || bytes`;
    kinds `cell` / `event` / `blob` / `snapshot`).
  - `crypto` — Ed25519 (RFC 8032, deterministic → golden signatures).
    Principals: named mint (`pid = blake2b(name, 8)`) and self-certifying
    keyed mint (`seed = blake2b(master + name, 32, person="decima:keyid")`,
    `pid = blake2b(public_key, 8)`). DerivedKeyStore-equivalent custody:
    `seed = blake2b(master + pid, 32, person="decima:ed255")`.
  - `weft` — append-only signed log (in-memory). Linear append
    (`parents = [head]`), Lamport `1 + max(parent lamports, 0)`, NFC on
    entry, `eid = content_id(payload, "event")`, author signs the eid
    string's UTF-8 bytes.
  - `weft_db` — the SQLite-persisted Weft (rusqlite, bundled). Same table
    shape and same stored `payload` TEXT bytes as weft.py
    (`json.dumps(payload, sort_keys=True)` — DEFAULT separators `", "` /
    `": "` and ensure_ascii=True, reproduced by
    `hashing::python_dumps_sorted`; NOT the compact canonical bytes the id
    hashes), append, warm start (`_load_head` semantics: head/lamport from
    the highest-seq row), and fail-closed on-read verification in
    `events()` (recompute the content id → `ContentTampered`, verify the
    author's signature → `BadSignature`).
  - `weave` — the fold. Events apply in the deterministic total order
    `(lamport, event_id)`; idempotent by event id. Register merge substrate
    with causal-dominance head tracking (LWW materialization; MV/adjudicated
    head preservation). INVOKE fold (invocation records + per-capability
    `_invoke_counts` tally) and ATTEST fold: attestations onto the target
    cell as `{by, claim, event}` (any signer recorded, as the reference
    does), the `adjudicates` collapse of MV/adjudicated heads
    (MERGE_SEMANTICS §4 — SELECT supersedes the named non-winner evidence,
    binding only named heads), and trusted tiered promotion (NONA_RECKONER
    §7 — a promote-ATTEST lifts quarantine ONLY when its author holds a
    live, root-authored `promoter` cell for the candidate's tier; the root
    is anchored on the parentless event with the smallest `seq`, never on
    the grindable event id; forged/self-granted promoter cells are ignored,
    fail closed). LEASE1 lease-expiry derivation (`frontier_lamport` + the
    invoke tally feed `lease_status`; a lapsed lease becomes a
    DERIVED_AUTHORITY cascade root in the same pure derived pass).
    EffectReceipt projections (`receipts_for_idempotency` /
    `canonical_for_idempotency`, canonical (fold) order, UNKNOWN reconciled
    by the latest definite receipt). `state_root` over canonical CellState
    records.
  - `model` — `define_type` / `assert_content` / `assert_edge` helpers
    (WEFT §4 assertion kinds TYPE_DEF / CONTENT / EDGE).
  - `capability` — capability cell construction, downhill `attenuate`, and
    LEASE1 `lease_status` (expires_at / max_uses, fail closed).
  - `reference` — a Rust re-run of the exact fixed event script from
    `heartbeat/decima/vectors.py::_fold_vector`, so tests and the verifier
    re-derive the golden `fold` section from first principles.
  - `reference_ext` — a Rust re-run of `rust/vectors/generate.py`'s
    extended script (SQLite append script with INVOKE/ATTEST, stored-byte
    capture, warm start + re-fold).
  - `reference_v3` — a Rust re-run of `generate.py`'s v3 script (forked MV
    heads + adjudication, root/forged/self-granted promoter promotion
    attempts, receipt reconciliation, time-locked + single-use leases, and
    the anti-grinding second-genesis attack).
- `vectors/` — `generate.py` (run against the real heartbeat reference;
  deterministic, all-zero seed, no wall-clock — the INVOKE nonces are
  pinned because the kernel's real nonce path is `os.urandom`) and its
  byte-stable outputs `extended_vectors.json` (v2) and
  `extended_vectors_v3.json` (v3).
- `decima-verify/` — binary crate. Loads ALL THREE golden JSON files and
  re-derives EVERY value in them (v1: canonical_hex, content ids, blob ids,
  pids, public keys, signatures incl. tamper-fails-closed, all fold event
  ids/bodies/lamports, capability ids, state_root, type_counts,
  event_count — 107 checks; extended: principals, cap ids, every event's
  id/verb/lamport/authorized/body, the exact stored payload bytes,
  head/lamport/event_count, folded invocations + invoke tally, folded
  attestations, state_root, and warm-start equality — 109 checks; v3:
  every event incl. the forked/grinded ones, MV heads before/after
  adjudication, promotion outcomes at each stage (forged and self-granted
  promoter attempts IGNORED), the seq-anchored genesis root, receipt
  ordering + canonical reconciliation, lease outcomes incl. the
  cascade-to-child flags, and the final state_root — 170 checks), prints
  a per-section report, exits 0 iff everything matches.

## The honest fold subset

Implemented: linear append; SQLite persistence with the reference's exact
stored bytes, warm start, and fail-closed on-read verification; fold order
`(lamport, event_id)`; assertion kinds CONTENT (register/LWW + MV head
representation), EDGE, TYPE_DEF; RETRACT modes WITHDRAW / REDACT /
SUPERSEDE / TERMINATE flag handling with cascade-root recording; the
DERIVED_AUTHORITY / LEASE_TREE cascade closure as a pure derived pass;
INVOKE (invocations + per-capability tally); ATTEST (attestations folded
onto the target cell, adjudication-collapse for MV/adjudicated cells,
trusted tiered promotion with the seq-anchored genesis root); merge forks
(explicit parent sets, incl. second parentless events); LEASE1
lease-expiry derivation (`frontier_lamport` + invoke tally →
`lease_status` → cascade root); EffectReceipt projections
(`receipts_for_idempotency` / `canonical_for_idempotency`); `state_root`,
`of_type`.

NOT yet ported (none exercised by the vector sets): the or-set / counter /
append-log / sequence / map reducers, capability delegation verification /
authorization (kernel layer), key-rotation succession chains, event
ingest/sync, snapshots, time-travel windows. The code is structured so
each of these grows out of an existing module without reshaping the ported
subset.

## Constraints honored

- Rust stable, `#![forbid(unsafe_code)]` in both crates; no unsafe anywhere.
- Deps only: blake2 0.10.6, ed25519-dalek 2.2.0, serde 1.0.228,
  serde_json 1.0.145 (`arbitrary_precision`), unicode-normalization 0.1.24,
  hex 0.4.3, rusqlite 0.32.1 (`bundled` — milestone 2's only new dep, per
  the v2 criteria) (versions pinned as minimums in the workspace manifest;
  `Cargo.lock` pins exact builds).

## Running

```sh
export PATH=$HOME/.cargo/bin:$PATH
cd rust
cargo test                          # 18 conformance unit tests (10 v1 + 8 m2)
cargo run -p decima-verify          # per-section report; exit 0 = full match
```

Note for this sandbox: the repo lives on a FUSE mount where cargo cannot
create `target/`; use `CARGO_TARGET_DIR=/tmp/decima-target` (or any
non-FUSE path).
