# Heartbeat profile vs Weft Protocol v0.1

`specs/WEFT_PROTOCOL.md` §1 explicitly permits the Heartbeat to implement a
smaller profile, *provided it does not create incompatible meanings*. This file
pins exactly where the prototype sits, so the eventual Rust port lands on v0.1
precisely (and so hash agreement across implementations is a deliberate decision,
not an accident).

The Heartbeat is pure Python standard library except for **one dependency: PyNaCl**
(libsodium) for real **Ed25519** signing — cryptography is the one "never roll your own"
domain, so it wraps the real engine rather than a stdlib stand-in (see `decima/crypto.py`
and `requirements.txt`). Everything else is stdlib, and that stdlib-only constraint is
why the two biggest items below are deferred: stdlib ships neither BLAKE3 nor a CBOR codec.

## Delta

| aspect | v0.1 protocol | Heartbeat profile | reason |
|---|---|---|---|
| hash | BLAKE3-256 | **BLAKE2b-128** | no BLAKE3 in stdlib |
| canonical bytes | deterministic CBOR (RFC 8949 §4.2), integer field keys | **sorted-key JSON, UTF-8** | no CBOR in stdlib; JSON promoted from diagnostic to signed form |
| domain separation | `HASH("decima:v0.1:" \|\| kind \|\| 0x00 \|\| bytes)` | **implemented** (event vs cell id spaces disjoint) | — |
| floats | forbidden in signed material | **forbidden** (budgets kept `int`) | aligned |
| text | UTF-8, NFC-normalized | **NFC on EVERY nested field** — `hashing.canonical` NFC-normalizes every string (dict keys + values, any depth) before hashing, and `weft.append` normalizes the stored body, so a payload's id is its Unicode-normalized identity and folded content is canonical (checks/286) | aligned — a payload's id agrees across normalization forms and across implementations |
| identifiers | base32-lower, kind-prefixed (`evt_`/`cell_`/`cap_`/…) | hex digest, domain-separated by kind but **no text prefix** | cosmetic; deferred |
| signatures | Ed25519 | **Ed25519 (libsodium via PyNaCl)** — real asymmetric signatures; per-principal keypairs derived deterministically from a persisted master seed; verify uses the public key (`crypto.py`, proof in `checks/364`) | aligned on the primitive; remaining profile gap is key CUSTODY (OS keystore + distributing only public keys to peers), not the algorithm |
| authorization | full `AuthorizationProof` (grant_event, delegation_path, invocation_bind, holder_sig, approvals) | **`AuthorizationProof` implemented** (`capability.py`): `invocation_bind` = hash(verb,body,nonce,parents), `holder_sig` over it, plus `grant_event` + `delegation_path` consistency. Carried in the INVOKE event. **Approvals are now Weft EVENTS** (`capability.APPROVAL`, folded — was in-memory): capability-scoped (`kernel.approve`) OR invocation-scoped (`kernel.approve_invocation`, bound to `op_bind`=hash(verb,body,nonce), single-use — RETRACTed on consume, anti-ambient + anti-replay). | aligned, incl. anti-replay binding; approvals are auditable per-capability or per-invocation events (durable, not in-memory) |
| assertion kind | `assertion` uint in the ASSERT body (1 CONTENT, 2 EDGE, 3 GRANT, 4 LEASE, 5 MESSAGE, 6 RECEIPT, 7 POLICY, 8 TYPE_DEF) | **string `kind`** on the ASSERT body — `CONTENT`/`EDGE`/`TYPE_DEF` implemented (≙ 1/2/8); the rest deferred | names not int field-numbers (JSON profile); meanings match §4, so no incompatibility |
| edges & types | first-class relations and Type Cells in `CellState` (`edges_out`/`edges_in`, type heads) | **implemented** — EDGE folds onto `Cell.edges_out`/`edges_in`; TYPE_DEF registers a Type Cell in `Weave.types` (`weave.py`, `model.py`) | aligned (thin: no schema validation on content yet) |
| receipts | `EffectReceipt` with `status` incl. mandatory `UNKNOWN`, cost, provider_ref, idempotency | **partial** — the `result` cell is now an `EffectReceipt`-shaped `ASSERT` carrying `status` (SUCCEEDED/FAILED/UNKNOWN), `executor`, `attempt`, `idempotency` (the INVOKE nonce), `effect_class`; an ambiguous effect (`executor.Ambiguous`) records `UNKNOWN` with no fabricated output | multi-attempt reconciliation, COMPENSATED/CANCELLED, cost deferred |
| leases | time-locked / single-use capabilities that fail closed | **implemented** (`capability.py`/`kernel.py`/`weave.py`): a grant may carry `expires_at` (int logical time) and `max_uses` (int) lease caveats — `authorize()` denies once the logical frontier (lamport) reaches `expires_at`, or the Weave-folded count of prior INVOKEs reaches `max_uses` (single-use = 1). A lapsed lease is derived `lease_expired` in the fold and composes the DERIVED_AUTHORITY cascade, so it AND every grant attenuated from it fail CLOSED exactly like a revoked grant. Deterministic ("now" = lamport, ints only); proof in `checks/200_leases.py` | the kernel primitive behind ephemeral single-use cards + time-locked wallets (CAPABILITY_MAP D3.4 / B4) |
| retraction | typed modes (WITHDRAW/SUPERSEDE/REVOKE/REDACT/TERMINATE) + cascade | **WITHDRAW + REDACT** — `RETRACT` body carries a `mode`; REDACT erases the payload from every projection (content, heads, and merge substrate incl. Map conflict_keys) leaving a content-free tombstone, while the event skeleton stays on the Log (`weave.py`) | SUPERSEDE/TERMINATE + cascade, and physical byte-erasure (encrypted blobs + key destruction) deferred |
| ordering | DAG; total order `(lamport, event_id)`; type-specific merge | **concurrent forks + type-specific merge implemented** (M1/M2): `weft.append(parents=…)` makes a fork; the fold reduces per type — LWW/MV/OR-set/Sequence/Map/Counter/Append-log + adjudication `ATTEST` (`weave.py`, `specs/MERGE_SEMANTICS.md`; proofs in `checks/70`,`71`). Cross-peer sync is real: `Weft.ingest` (WEFT §2 acceptance) + `sync`/`sync_over_wire` union events across peers (`sync.py`, checks/284); a socket-backed feed is the only remaining transport substitution | the reducer AND the transport are now implemented; sockets are a drop-in |
| validation | reject noncanonical bytes; `lamport = 1 + max(parents)`; verify auth at parent frontier | on READ (`events()`): recompute id + verify signature. on INGEST (`Weft.ingest`, foreign events): full **WEFT §2 acceptance** — recompute id (canonical bytes), verify signature, require canonically-sorted parents, require every parent PRESENT (closed DAG), and check `lamport == 1 + max(parent lamports)`; fail closed (checks/284). Authority is judged at ORIGIN (each event carries its proof), so the union never re-authorizes a revoked grant | acceptance validation implemented; per-invocation authority re-check on ingest still deferred |

## To reach v0.1 in the Rust port

1. BLAKE3-256 with the domain-separation prefix already used here.
2. Deterministic CBOR with **integer field numbers** (the structs in §2–§8).
3. Kind-prefixed base32-lower identifiers.
4. The full `AuthorizationProof`, with `invocation_bind` digesting verb/body/nonce/parents and a `holder_sig` over it.
5. `EffectReceipt`s (with `UNKNOWN`), idempotency keys, `effect_class` (leases now implemented — `expires_at`/`max_uses` caveats failing closed via the cascade).
6. Ed25519 is REAL (libsodium/PyNaCl); the remaining step is key CUSTODY — per-principal keypairs in an OS keystore with only public keys distributed to verifiers (replacing the master-seed derivation).
7. DAG parents (sorted), `lamport = 1 + max(parent.lamport)`, and type-specific merge.

What the Heartbeat already gets *right* relative to v0.1: domain-separated hashing,
no floats in signed content, NFC at text entry, append-only tamper-evident log,
signed events, and capability **possession** semantics (a public id is not a
bearer token — `capability.authorize`).

## FOLD §11 invariant coverage (the conformance oracle)

`specs/FOLD_AND_LIFECYCLE.md §11` lists eight invariants the durable system must
hold. `smoke.py` (the `FOLD §11 INVARIANTS` section) asserts each one the profile
can represent and **declares the rest deferred** rather than silently skipping —
so the oracle never over-reports coverage. Supporting kernel additions:
`Weave.state_root()` (a deterministic digest over logical CellState, the §6
`state_root`) and idempotent-by-Event-ID fold (`Weave._apply`, §2).

| §11 invariant | status | how |
|---|---|---|
| replay determinism | **holds** | two folds → identical `state_root()` |
| arrival-order independence | **holds** (genuinely concurrent) | reorder events, fold in `(lamport, id)` order → same root — now over a **real fork** with per-type merge classes (M1/M2), not just a linear chain (`checks/70`,`71`); the network sync transport that *produces* forks across peers is the remaining Rust-port concern |
| duplicate delivery harmless | **holds** | re-applying every event leaves `state_root()` unchanged (idempotent by Event ID) |
| revoked authority fails closed after frontier | **holds** | invoke ok → `RETRACT` → invoke denied; live at the pre-revoke frontier |
| derived scope never broader than parent | **holds** | `spawn` asking to widen budget is clamped downhill by `attenuate` |
| external effects not repeated by replay | **holds** | folding replays `result` cells; `executor.execute` is never called during a fold |
| redacted payload absent from projections | **holds** | `RETRACT mode=REDACT` erases the payload from every projection (`get().content` empty, out of `of_type`, `state_root` leaf a content-free tombstone, merge substrate purged) while the assert+redact event skeleton stays on the Log (`weave.py`). Physical byte-erasure of the stored payload (encrypted blobs + key destruction, FOLD §10) is the durable-form remainder |
| ambiguous execution → `UNKNOWN` | **holds** | the `result` cell now carries `EffectReceipt.status` (WEFT §8); a post-submission timeout (`executor.Ambiguous`) resolves to `UNKNOWN` with no fabricated output — `executor.execute` never rewrites "I don't know" as success/failure |

**All 8 FOLD §11 invariants now hold.** REDACT (R1) closed the last partial row:
the payload leaves every projection while the event skeleton stays. What remains is
durable-*form* work, not invariant gaps — physical byte-erasure (encrypted blobs +
key destruction) for redaction, and full `EffectReceipt` leases/reconciliation/
compensation for receipts. The Rust port closes those.
