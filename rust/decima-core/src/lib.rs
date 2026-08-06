//! decima-core — the first milestone of the Rust port of Decima (an
//! agent-native OS). Byte-for-byte conformance with the Python heartbeat
//! reference (heartbeat/decima/) is proven by decima-verify against
//! heartbeat/protocol/reference_vectors.json.
//!
//! Ported (see rust/README.md for the honest subset):
//!   - canonical encoding + domain-separated BLAKE2b-128 content addressing
//!   - Ed25519 identity and deterministic signing (derived custodian)
//!   - Weft append semantics (linear path), plus SQLite persistence with
//!     byte-exact stored payloads, warm start, and fail-closed on-read
//!     verification (milestone 2)
//!   - Weave fold subset (register merges, CONTENT/EDGE/TYPE_DEF, RETRACT,
//!     INVOKE tally, ATTEST attestations)
//!
//! No unsafe code anywhere in this crate.
#![forbid(unsafe_code)]

pub mod capability;
pub mod crypto;
pub mod hashing;
pub mod model;
pub mod reference;
pub mod reference_ext;
pub mod weave;
pub mod weft;
pub mod weft_db;
