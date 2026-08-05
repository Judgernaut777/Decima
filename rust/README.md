# rust/ — the Rust port of Decima (milestone 1)

Byte-for-byte conformance with the Python heartbeat reference
(`heartbeat/decima/`) against `heartbeat/protocol/reference_vectors.json`,
proven by the `decima-verify` binary (verifier v1 criteria).

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
  - `weave` — the fold. Events apply in the deterministic total order
    `(lamport, event_id)`; idempotent by event id. Register merge substrate
    with causal-dominance head tracking (LWW materialization; MV/adjudicated
    head preservation). `state_root` over canonical CellState records.
  - `model` — `define_type` / `assert_content` / `assert_edge` helpers
    (WEFT §4 assertion kinds TYPE_DEF / CONTENT / EDGE).
  - `capability` — capability cell construction and downhill `attenuate`.
  - `reference` — a Rust re-run of the exact fixed event script from
    `heartbeat/decima/vectors.py::_fold_vector`, so tests and the verifier
    re-derive the golden `fold` section from first principles.
- `decima-verify/` — binary crate. Loads the golden JSON and re-derives
  EVERY value in it (canonical_hex, content ids, blob ids, pids, public
  keys, signatures incl. tamper-fails-closed, all fold event ids/bodies/
  lamports, capability ids, state_root, type_counts, event_count), prints a
  per-section report, exits 0 iff everything matches.

## The honest fold subset

Implemented: linear append; fold order `(lamport, event_id)`; assertion
kinds CONTENT (register/LWW + MV head representation), EDGE, TYPE_DEF;
RETRACT modes WITHDRAW / REDACT / SUPERSEDE / TERMINATE flag handling with
cascade-root recording; the DERIVED_AUTHORITY / LEASE_TREE cascade closure
as a pure derived pass; `state_root`, `of_type`.

NOT yet ported (none exercised by the reference vectors): INVOKE and ATTEST
folds (attestations, adjudication collapse, promotion), the or-set / counter
/ append-log / sequence / map reducers, lease-expiry derivation
(`frontier_lamport`, `_invoke_counts`), capability delegation verification /
authorization, key-rotation succession chains, event ingest/sync, snapshots,
time-travel windows, SQLite persistence. The code is structured so each of
these grows out of an existing module without reshaping the ported subset.

## Constraints honored

- Rust stable, `#![forbid(unsafe_code)]` in both crates; no unsafe anywhere.
- Deps only: blake2 0.10.6, ed25519-dalek 2.2.0, serde 1.0.228,
  serde_json 1.0.145 (`arbitrary_precision`), unicode-normalization 0.1.24,
  hex 0.4.3 (versions pinned as minimums in the workspace manifest;
  `Cargo.lock` pins exact builds).

## Running

```sh
export PATH=$HOME/.cargo/bin:$PATH
cd rust
cargo test                          # 10 conformance unit tests
cargo run -p decima-verify          # per-section report; exit 0 = full match
```

Note for this sandbox: the repo lives on a FUSE mount where cargo cannot
create `target/`; use `CARGO_TARGET_DIR=/tmp/decima-target` (or any
non-FUSE path).
