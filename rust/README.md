# rust/ — the Rust port of Decima (milestone 2)

Byte-for-byte conformance with the Python heartbeat reference
(`heartbeat/decima/`) against `heartbeat/protocol/reference_vectors.json`
(milestone 1) AND `rust/vectors/extended_vectors.json` (milestone 2: SQLite
persistence, warm start, INVOKE/ATTEST fold), proven by the `decima-verify`
binary (verifier v2 criteria). The extended vectors are generated FROM the
reference by `rust/vectors/generate.py` (all-zero master seed, deterministic;
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
    `_invoke_counts` tally) and ATTEST fold (attestations onto the target
    cell as `{by, claim, event}`, feeding `state_root` records — any signer
    recorded, as the reference does). `state_root` over canonical CellState
    records.
  - `model` — `define_type` / `assert_content` / `assert_edge` helpers
    (WEFT §4 assertion kinds TYPE_DEF / CONTENT / EDGE).
  - `capability` — capability cell construction and downhill `attenuate`.
  - `reference` — a Rust re-run of the exact fixed event script from
    `heartbeat/decima/vectors.py::_fold_vector`, so tests and the verifier
    re-derive the golden `fold` section from first principles.
  - `reference_ext` — a Rust re-run of `rust/vectors/generate.py`'s
    extended script (SQLite append script with INVOKE/ATTEST, stored-byte
    capture, warm start + re-fold).
- `vectors/` — `generate.py` (run against the real heartbeat reference;
  deterministic, all-zero seed, no wall-clock — the INVOKE nonces are
  pinned because the kernel's real nonce path is `os.urandom`) and its
  byte-stable output `extended_vectors.json`.
- `decima-verify/` — binary crate. Loads BOTH golden JSON files and
  re-derives EVERY value in them (v1: canonical_hex, content ids, blob ids,
  pids, public keys, signatures incl. tamper-fails-closed, all fold event
  ids/bodies/lamports, capability ids, state_root, type_counts,
  event_count — 107 checks; extended: principals, cap ids, every event's
  id/verb/lamport/authorized/body, the exact stored payload bytes,
  head/lamport/event_count, folded invocations + invoke tally, folded
  attestations, state_root, and warm-start equality — 109 checks), prints
  a per-section report, exits 0 iff everything matches.

## The honest fold subset

Implemented: linear append; SQLite persistence with the reference's exact
stored bytes, warm start, and fail-closed on-read verification; fold order
`(lamport, event_id)`; assertion kinds CONTENT (register/LWW + MV head
representation), EDGE, TYPE_DEF; RETRACT modes WITHDRAW / REDACT /
SUPERSEDE / TERMINATE flag handling with cascade-root recording; the
DERIVED_AUTHORITY / LEASE_TREE cascade closure as a pure derived pass;
INVOKE (invocations + per-capability tally); ATTEST (attestations folded
onto the target cell); `state_root`, `of_type`.

NOT yet ported (none exercised by the vector sets): the ATTEST
adjudication-collapse and trusted-promotion branches (genesis-author
anchoring is therefore not yet needed), EffectReceipt projections
(`receipts_for_idempotency` / `canonical_for_idempotency`), the or-set /
counter / append-log / sequence / map reducers, lease-expiry derivation
(`frontier_lamport` / `lease_status` — the invoke tally itself IS folded),
capability delegation verification / authorization (kernel layer), merge
forks (explicit parent sets), key-rotation succession chains, event
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
