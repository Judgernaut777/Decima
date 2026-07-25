# Design: Adopt the Durable Weft Protocol (v0.1) — the Re-Freeze

**Status:** proposal — needs owner sign-off on two decisions (§7).
**Date:** 2026-07-24
**Supersedes/absorbs:** the deferred item **P1.4** (widen the key↔identity binding) and the
four content-addressed deltas `heartbeat/PROFILE.md` marks deferred (BLAKE3, CBOR, base32
ids, integer field keys).

---

## 1. One-paragraph summary

The prototype runs a deliberately smaller **profile** of the wire contract in
`specs/WEFT_PROTOCOL.md`: 128-bit BLAKE2b instead of BLAKE3-256, sorted-key JSON instead
of deterministic CBOR, hex ids instead of kind-prefixed base32, and a 64-bit principal id.
Every one of those is a **content-addressed-byte** difference, so adopting any of them
changes event ids and `state_root` and therefore invalidates the golden fixtures — which is
exactly why P1.4 could not land as a gate-green patch. This document scopes doing them
**together, once**, as a single protocol-version bump (the "re-freeze"): re-cut the frozen
conformance oracle at v0.1, regenerate the fixtures from it, and re-anchor the evidence.
Piecemeal adoption pays the re-freeze cost repeatedly; bundled, it is paid once and lands
the system on the standard instead of on another waypoint.

## 2. Why this is a decision, not a patch

`heartbeat/` is the **frozen conformance oracle**. The pipeline is fixed by design:

1. `tools/kernel/gen_fixtures.py` runs the reference (`cd heartbeat`) and emits the golden
   vectors `protocol/fixtures/{canonical,fold}.json` — which record the exact canonical
   bytes, event ids, and `state_root`.
2. `tests/kernel/test_conformance.py` replays the same script through the shipping
   `decima.kernel` and asserts **byte-equality** against those vectors.

The contract is *"decima produces the identical bytes the frozen reference does."* Any
change to the digest algorithm, canonical encoding, id text, or principal-id width changes
those bytes, so it cannot be adopted without **regenerating the fixtures and re-cutting the
reference** — the two artifacts every routine change is forbidden to touch. This document is
the deliberate authorization to touch them, under an explicit version bump.

## 3. Motivation

- **Stop paying the re-freeze cost N times.** P1.4 (pid width), BLAKE3, CBOR, and base32 ids
  are four separate deferred items that each require the same fixture regeneration. Bundling
  them into one re-freeze is strictly cheaper than four.
- **Reach a uniform security level.** The profile currently sits *below* a coherent floor in
  two places: 128-bit content ids give only **64-bit collision resistance**, and the 64-bit
  pid gives ~32-bit. Because Decima trusts *by identity* (Law 4: dedup/merge/trust fall out
  of content-addressing), collision resistance is a security property here, not just
  hygiene — a 2⁶⁴ collision lets a crafted object inherit a trusted object's id. The durable
  protocol fixes both by construction: one `BLAKE3-256` digest for **every** id, principals
  included (`WEFT_PROTOCOL.md:8,16`).
- **The spec is already written.** This is adoption of `specs/WEFT_PROTOCOL.md`, not new
  design. The prototype's profile was always documented as a waypoint (`PROFILE.md §To reach
  v0.1`).

## 4. Scope — the deltas to adopt

From `heartbeat/PROFILE.md §Delta`, plus the pid width. Current → target, with the files that
carry each constant in the shipping package.

| # | Aspect | Profile (now) | Durable v0.1 (target) | Primary sites |
|---|--------|---------------|------------------------|---------------|
| D1 | Hash | BLAKE2b-128 | **BLAKE3-256** (domain-sep prefix already present) | `decima/kernel/hashing.py:52`, `decima/kernel/crypto.py` |
| D2 | Canonical bytes | sorted-key JSON | **deterministic CBOR** (RFC 8949 §4.2), integer field keys | `decima/kernel/hashing.py` (`canonical`), `decima/kernel/weft.py` |
| D3 | Identifier text | hex digest, kind-domain-separated, no prefix | **base32-lower, kind-prefixed** (`evt_`/`bdy_`/`blob_`/`cell_`/`prn_`/`cap_`) | `hashing.py`, `weft.py`, `projections/*`, `shell/` (id parsing/rendering) |
| D4 | Principal id width | **64-bit** (`digest_size=8`) | **256-bit** — `digest("principal", pubkey)`, same width as every other id (this is P1.4, done right — not a bespoke 128-bit) | `decima/kernel/crypto.py:62,89` |
| D5 | ASSERT `kind` | string (`CONTENT`/`EDGE`/`TYPE_DEF`) | integer field numbers (1..8) — implied by CBOR integer keys (D2) | `decima/kernel/weft.py`, `decima/kernel/weave.py` |

**Deliberately unchanged (width follows job, per the spec itself):**

- `realm bytes32` (256-bit) and `nonce bytes16` (128-bit) in the Event envelope
  (`WEFT_PROTOCOL.md:29,36`). The nonce's job is preventing *accidental* semantic collision,
  not resisting an adversary; 128 random bits is correct and doubling it would be pure bloat.
- **Ed25519** stays. A 256-bit curve delivers ~128-bit security — the modern standard and the
  floor the hashes are balanced *to*. Moving to a heavier curve or PQC is explicitly a
  non-goal (§8): it would armor the signature past nothing, since 128-bit is the whole
  system's level.

This is the concrete meaning of "uniform security level, not uniform width": every
identity-and-integrity primitive lands at **128-bit security** (256-bit hashes → 128-bit
collision resistance; 256-bit curve → 128-bit security), while values whose job is *not*
adversarial resistance keep the width their job needs.

## 5. Security rationale (why 256-bit hashes, and why not more)

"Bits" are not fungible across primitives:

| primitive | 256 bits means | security delivered |
|---|---|---|
| hash output | 256-bit digest | 256-bit second-preimage, **128-bit collision** (birthday) |
| Ed25519 key | 256-bit curve | **128-bit** (ECDLP ≈ √group) |

So the target is a uniform **128-bit security level**, which *is* 256-bit hashes + a 256-bit
curve. Chasing literal 256-bit *security* everywhere would need **512-bit hashes** and a
much heavier signature — wasted, because Ed25519 caps the system at 128-bit regardless. You
size to the weakest primitive you accept; 128-bit is the sane floor, and the durable protocol
sits exactly on it. (Industry precedent: git's SHA-1→SHA-256 migration was driven by exactly
this collision-resistance argument.)

## 6. Work plan

Each phase is internally gate-green **against the new baseline** once R2 re-freezes the oracle;
R1 is developed behind a version switch so the tree never has two live wire formats at once.

- **R0 — Lock the two decisions (§7). ✅ Done** — take the deps (A) + clean break (A), all
  deltas in one re-freeze.
- **R1 — New primitives behind a protocol-version tag.** Implement BLAKE3-256 digest (D1),
  the deterministic-CBOR canonical encoder + integer field keys (D2, D5), base32 kind-prefixed
  id formatting/parsing (D3), and 256-bit principal ids (D4). Stamp a `protocol`/profile
  version into the Weft header so a store's format is self-describing and mismatches fail
  closed on open.
- **R2 — Re-freeze the oracle.** Bring the reference to the same primitives, regenerate
  `protocol/fixtures/{canonical,fold}.json` via `tools/kernel/gen_fixtures.py`, update the
  `tests/kernel/test_conformance.py` vectors, and re-anchor the frozen-baseline commit + the
  `docs/release-evidence/*` references to the new reference tree. **This is the step the
  normal "never touch heartbeat/ or protocol/fixtures/" rule forbids — it is sanctioned only
  under this doc's version bump.**
- **R3 — Propagate the new id/hash surface.** `state_root` digest (snapshots/checkpoints),
  the pid-keyed keystore, receipts/idempotency keys, `Weft.ingest` acceptance (canonical-byte
  recompute), and any base32 id parsing/rendering in `projections/*` and `shell/`.
- **R4 — Migration + docs.** Migration tooling per the §7 choice; retire or re-scope
  `PROFILE.md`; bump `WEFT_PROTOCOL.md`'s version; update `SECURITY.md` (the 64-bit-pid and
  128-bit-id notes go away).
- **R5 — Prove it.** Full gate + a fresh conformance run showing `decima.kernel` is
  byte-equal to the **re-frozen** reference at v0.1.

## 7. Decisions required (owner sign-off)

> **Decided (2026-07-24, owner):** Decision 1 → **A (take the deps)**; Decision 2 → **A (clean
> break)**. Decision 3 follows the recommendation (**all deltas in one re-freeze**) unless
> revised. R0 is therefore complete; R1 is unblocked.

**Decision 1 — Dependency posture (BLAKE3 + CBOR are not in the stdlib).**

| Option | What | Trade |
|---|---|---|
| **A. Take the deps** *(recommended)* | `blake3` + a deterministic CBOR codec | Simplest, fast; **forfeits the "one dependency (PyNaCl)" property** the reference advertises. The Rust port takes BLAKE3 natively anyway. |
| B. Vendor pure-Python | ship audited pure-Python BLAKE3/CBOR | Keeps zero-pip; slower and more code to trust. |
| C. Dual-stack | keep the stdlib profile as a second, versioned wire | Most complex; two live formats to keep in sync. |

Recommendation: **A**, and keep the old stdlib profile *frozen* as a historical v0-profile
oracle (not a live wire). This changes the reference's identity from "one dependency" to "a
few" — a deliberate, documented shift, not an accident.

**Decision 2 — Migration of existing `weft.db` stores (the hard one).**

Content-addressing means an old id **cannot be recomputed** to a new one; there is no
in-place upgrade.

| Option | What | Trade |
|---|---|---|
| **A. Clean break** *(recommended)* | new protocol = new genesis; existing stores are read-only v0-profile artifacts; provide an **export→re-import** of user content (notes/tasks), not id-preserving migration | Honest for a pre-1.0 system already labeled "run only on data you can afford to lose"; simplest. |
| B. Re-mint migration | one-time re-hash of the whole log under the new digest → a **new** causal history with a recorded provenance link to the old root | Preserves data, but it is a new history (new ids), so snapshots, pid-keyed keystores, and external receipts must be rebuilt; substantial tooling. |

Recommendation: **A**, given the 0.3 "experimental, keep a backup" posture.

**Decision 3 (minor) — All four deltas at once, or hash+pid first?** Bundling all of D1–D5
means one re-freeze; splitting (e.g. hash+pid now, CBOR/base32 later) means two. Recommendation:
**all at once** — the re-freeze cost dominates, so minimize the number of re-freezes.

## 8. Non-goals

- Changing the signature primitive (Ed25519 stays; no Ed448/PQC — 128-bit is the floor the
  rest is balanced to).
- Inflating the `nonce` or other job-sized fields to 256-bit.
- The full `EffectReceipt` expansion (multi-attempt reconciliation, COMPENSATED/CANCELLED,
  cost) beyond what ids/canonical bytes require — that can stage after the re-freeze.
- Doing any of this under the standing "don't touch `heartbeat/` / `protocol/fixtures/`"
  rule — this document is the explicit, bounded exception to it.

## 9. Risks

- **Half-migrated store.** Mitigated by the R1 version tag: refuse to open a store whose
  protocol version doesn't match the running code.
- **Performance** of CBOR/BLAKE3 in pure Python (if Option B). Acceptable — the Rust port is
  the performance target; the reference optimizes for clarity.
- **Evidence drift.** The re-freeze moves the frozen-baseline commit; R2 must re-anchor every
  `docs/release-evidence/*` reference in the same change, or the byte-equality claims dangle
  again (the exact failure Phase 0 of the assessment just fixed).
