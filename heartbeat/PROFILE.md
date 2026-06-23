# Heartbeat profile vs Weft Protocol v0.1

`specs/WEFT_PROTOCOL.md` §1 explicitly permits the Heartbeat to implement a
smaller profile, *provided it does not create incompatible meanings*. This file
pins exactly where the prototype sits, so the eventual Rust port lands on v0.1
precisely (and so hash agreement across implementations is a deliberate decision,
not an accident).

The Heartbeat is pure Python standard library — **no dependencies, no network**.
That constraint is why the two biggest items below are deferred: stdlib ships
neither BLAKE3 nor a CBOR codec.

## Delta

| aspect | v0.1 protocol | Heartbeat profile | reason |
|---|---|---|---|
| hash | BLAKE3-256 | **BLAKE2b-128** | no BLAKE3 in stdlib |
| canonical bytes | deterministic CBOR (RFC 8949 §4.2), integer field keys | **sorted-key JSON, UTF-8** | no CBOR in stdlib; JSON promoted from diagnostic to signed form |
| domain separation | `HASH("decima:v0.1:" \|\| kind \|\| 0x00 \|\| bytes)` | **implemented** (event vs cell id spaces disjoint) | — |
| floats | forbidden in signed material | **forbidden** (budgets kept `int`) | aligned |
| text | UTF-8, NFC-normalized | **NFC at the `say` boundary** | aligned at entry points; not yet enforced on every nested field |
| identifiers | base32-lower, kind-prefixed (`evt_`/`cell_`/`cap_`/…) | hex digest, domain-separated by kind but **no text prefix** | cosmetic; deferred |
| signatures | Ed25519 | **HMAC-BLAKE2b**, symmetric, persisted master seed | dev-grade stand-in (`crypto.py`) |
| authorization | full `AuthorizationProof` (grant_event, delegation_path, invocation_bind, holder_sig, approvals) | envelope grant + grantee match + parent-chain attenuation check; INVOKE signed by the holder's key | **shape** aligned; not yet the explicit proof struct bound to the exact invocation |
| receipts | `EffectReceipt` with `status` incl. mandatory `UNKNOWN`, cost, provider_ref, idempotency | single `ASSERT` of a `result` cell | deferred |
| retraction | typed modes (WITHDRAW/SUPERSEDE/REVOKE/REDACT/TERMINATE) + cascade | single `RETRACT` (revoke) | deferred |
| ordering | DAG; total order `(lamport, event_id)`; type-specific merge | linear, single parent, single process | `parents` is already a list — DAG-ready |
| validation | reject noncanonical bytes; `lamport = 1 + max(parents)`; verify auth at parent frontier | recompute id + verify signature on every read; linear lamport | partial |

## To reach v0.1 in the Rust port

1. BLAKE3-256 with the domain-separation prefix already used here.
2. Deterministic CBOR with **integer field numbers** (the structs in §2–§8).
3. Kind-prefixed base32-lower identifiers.
4. The full `AuthorizationProof`, with `invocation_bind` digesting verb/body/nonce/parents and a `holder_sig` over it.
5. `EffectReceipt`s (with `UNKNOWN`), leases, idempotency keys, `effect_class`.
6. Ed25519 keypairs in an OS keystore (replacing `crypto.py`'s HMAC seed).
7. DAG parents (sorted), `lamport = 1 + max(parent.lamport)`, and type-specific merge.

What the Heartbeat already gets *right* relative to v0.1: domain-separated hashing,
no floats in signed content, NFC at text entry, append-only tamper-evident log,
signed events, and capability **possession** semantics (a public id is not a
bearer token — `capability.authorize`).
