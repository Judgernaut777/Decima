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
| authorization | full `AuthorizationProof` (grant_event, delegation_path, invocation_bind, holder_sig, approvals) | **`AuthorizationProof` implemented** (`capability.py`): `invocation_bind` = hash(verb,body,nonce,parents), `holder_sig` over it, plus `grant_event` + `delegation_path` consistency. Carried in the INVOKE event. | aligned, incl. anti-replay binding; `approvals` still bound per-capability (in-memory) rather than per-invocation events |
| assertion kind | `assertion` uint in the ASSERT body (1 CONTENT, 2 EDGE, 3 GRANT, 4 LEASE, 5 MESSAGE, 6 RECEIPT, 7 POLICY, 8 TYPE_DEF) | **string `kind`** on the ASSERT body — `CONTENT`/`EDGE`/`TYPE_DEF` implemented (≙ 1/2/8); the rest deferred | names not int field-numbers (JSON profile); meanings match §4, so no incompatibility |
| edges & types | first-class relations and Type Cells in `CellState` (`edges_out`/`edges_in`, type heads) | **implemented** — EDGE folds onto `Cell.edges_out`/`edges_in`; TYPE_DEF registers a Type Cell in `Weave.types` (`weave.py`, `model.py`) | aligned (thin: no schema validation on content yet) |
| receipts | `EffectReceipt` with `status` incl. mandatory `UNKNOWN`, cost, provider_ref, idempotency | **partial** — the `result` cell is now an `EffectReceipt`-shaped `ASSERT` carrying `status` (SUCCEEDED/FAILED/UNKNOWN), `executor`, `attempt`, `idempotency` (the INVOKE nonce), `effect_class`; an ambiguous effect (`executor.Ambiguous`) records `UNKNOWN` with no fabricated output | leases, multi-attempt reconciliation, COMPENSATED/CANCELLED, cost deferred |
| retraction | typed modes (WITHDRAW/SUPERSEDE/REVOKE/REDACT/TERMINATE) + cascade | single `RETRACT` (revoke) | deferred |
| ordering | DAG; total order `(lamport, event_id)`; type-specific merge | **concurrent forks + type-specific merge implemented** (M1/M2): `weft.append(parents=…)` makes a fork; the fold reduces per type — LWW/MV/OR-set/Sequence/Map/Counter/Append-log + adjudication `ATTEST` (`weave.py`, `specs/MERGE_SEMANTICS.md`; proofs in `checks/70`,`71`). Still single-process; no network sync transport yet | the reducer is now the DAG one; only the transport is deferred |
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
| redacted payload absent from projections | **partial** | `RETRACT` (logical withdrawal) drops a cell from projections while its event skeleton remains (§10); full `REDACT` + cryptographic erasure deferred |
| ambiguous execution → `UNKNOWN` | **holds** | the `result` cell now carries `EffectReceipt.status` (WEFT §8); a post-submission timeout (`executor.Ambiguous`) resolves to `UNKNOWN` with no fabricated output — `executor.execute` never rewrites "I don't know" as success/failure |

The one remaining partial row (RETRACT) maps to a profile gap above (typed
retraction modes). The UNKNOWN invariant now **holds** via the `EffectReceipt`
status the `result` cell carries (the receipts row above is itself now partial,
not deferred — full leases/reconciliation/compensation remain Rust-port work).
The Rust port closes the rest.
