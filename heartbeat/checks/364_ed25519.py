"""Real Ed25519 signing (libsodium via PyNaCl) — the signature is asymmetric now.

The Weft used to be signed with a dev-grade symmetric HMAC-BLAKE2b stand-in; it is now
signed with real Ed25519. This proves the upgrade is genuine asymmetric crypto — not a
symmetric MAC in disguise — and that the possession/tamper guarantees hold:

  - sign/verify round-trips; a 64-byte Ed25519 signature (128 hex);
  - a tampered message or a garbage signature verifies False (never raises);
  - possession: principal B cannot forge principal A's signature (B's key doesn't
    verify under A's public key) — the core "no ambient authority" property;
  - ASYMMETRIC: verification needs only the PUBLIC key (no secret) — verified by
    reconstructing a bare VerifyKey from `public_key(pid)` and checking a signature;
  - warm start is deterministic: a second keyring with the SAME master seed verifies a
    signature made by the first; a DIFFERENT seed does not;
  - end-to-end: a real Kernel's Weft events read back and verify (fold), and a tampered
    event is detected on read.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

import nacl.signing
from decima.crypto import Keyring
from decima.kernel import Kernel
from decima.weft import ASSERT, WeftError


def run(k, line):
    line("\n== REAL Ed25519 SIGNING (libsodium/PyNaCl) — asymmetric, tamper-evident ==")
    kr = Keyring(seed=b"\x01" * 32)
    a = kr.mint("alice", "human")
    b = kr.mint("bob", "human")
    msg = "the loom is weaving"

    # 1. round-trip + real 64-byte signature. ─────────────────────────────────────────
    sig = kr.sign(a.id, msg)
    assert kr.verify(a.id, msg, sig) is True
    assert len(bytes.fromhex(sig)) == 64, "an Ed25519 signature is 64 bytes"
    line("  sign/verify round-trips; signature is a 64-byte Ed25519 sig ✓")

    # 2. tamper + garbage → False, never raises. ──────────────────────────────────────
    assert kr.verify(a.id, msg + "!", sig) is False, "a tampered message must not verify"
    assert kr.verify(a.id, msg, "00" * 64) is False and kr.verify(a.id, msg, "nothex") is False
    line("  tampered message / garbage signature → verify False (no exception) ✓")

    # 3. possession — B cannot forge A. ───────────────────────────────────────────────
    assert kr.verify(a.id, msg, kr.sign(b.id, msg)) is False, "B must not be able to sign as A"
    line("  possession: principal B cannot forge principal A's signature ✓")

    # 4. ASYMMETRIC — verification needs only the public key. ──────────────────────────
    pub = kr.public_key(a.id)
    assert len(bytes.fromhex(pub)) == 32, "an Ed25519 public key is 32 bytes"
    assert pub != kr.public_key(b.id)
    vk = nacl.signing.VerifyKey(bytes.fromhex(pub))          # a bare public key, no secret
    vk.verify(msg.encode(), bytes.fromhex(sig))              # raises if invalid → would fail the check
    line("  asymmetric: a bare public key (no secret) verifies the signature ✓")

    # 5. deterministic warm start; different seed cannot verify. ───────────────────────
    kr2 = Keyring(seed=b"\x01" * 32)                          # same master seed
    kr2.mint("alice", "human")
    assert kr2.verify(a.id, msg, sig) is True, "same-seed keyring must verify (warm start)"
    kr3 = Keyring(seed=b"\x02" * 32)                          # different master seed
    assert kr3.verify(a.id, msg, sig) is False, "a different seed must not verify A's sig"
    line("  warm start deterministic (same seed verifies); a different seed does not ✓")

    # 6. end-to-end — a real Kernel's Weft signs + verifies on read; tamper detected. ──
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "w.db"), fresh=True)
    kk.weft.append(kk.human.id, ASSERT, {"cell": "n1", "type": "note", "content": {"t": "hi"}})
    assert kk.weave().get("n1") is not None, "a signed event must fold (verify on read)"
    kk.weft.db.execute("UPDATE events SET sig=? WHERE author=?", ("00" * 64, kk.human.id))
    kk.weft.db.commit()
    raised = False
    try:
        list(kk.weft.events())
    except WeftError:
        raised = True
    assert raised, "a forged signature must be caught on read (bad signature)"
    line("  end-to-end: Weft events sign + verify on fold; a forged sig is caught ✓")

    line("  → the Weft is signed with real Ed25519 (libsodium): asymmetric, "
         "possession-proving, tamper-evident — no more HMAC stand-in.")
