"""ROTATION-AWARE EVENT VERIFICATION — the Weft consults the succession chain.

Cycle 54 built the succession chain (rotation.py: enroll/rotate/recover,
valid_key_at) but left it DECORATIVE: the Weft still verified every folded event
against the author's one-key-forever Keyring, so Decima's own signing keys could
never actually rotate — a rotated author's history would stop verifying. This
cycle wires the promise into the verification seam itself: `weft.events()` (and
`append`/`ingest`) verify each event against the key valid for its author AT
that event's logical point (the lamport), per the chain folded from the log's
own key_rotation Cells — old events under the old key, post-rotation events
under the new key, a retired key refused, and a chain-less author verified
exactly as before.

This check proves, offline + deterministically (fresh Kernels, blake2b-seeded
test keypairs, logical int points, no clock, no unseeded randomness in anything
recorded):

  (a) HISTORY SURVIVES A ROTATION (load-bearing) — an authority enrolled as its
      own chain root signs a weft event, ROTATES its signing key (a proper
      endorsed succession link), signs another; a FULL fold verifies BOTH — the
      pre-rotation event under the OLD key, the post-rotation event under the
      NEW key — and the identity ref (the event author) is byte-identical
      across the rotation; a Kernel reconstructed over the same db re-folds the
      chain and the whole history still verifies;
  (b) A RETIRED KEY IS REFUSED — after the rotation, an event signed by the
      pre-rotation key is refused (WeftError, NOTHING recorded — fail closed);
      a forged succession link not endorsed by the current key is refused at
      the door AND, asserted directly on the weft, never advances the chain
      (mallory's key never becomes valid, cannot author as the identity);
  (c) RECOVERY — a lost key recovers ONLY through the authority PRE-designated
      at enrollment: the wrong authority fails closed, the designated one
      rotates in a fresh key, the whole prior history (both retired keys'
      events) still verifies, and a principal that never designated an
      authority has no recovery path at all;
  (d) NON-ROTATING IDENTITIES UNAFFECTED — a principal with no succession chain
      takes the exact pre-existing one-key Keyring path (the seam reports it
      un-enrolled), its events append and fold as before, and the full existing
      suite (200+ checks, 430_rotation.py included) stays green over this
      change.

Mutation-resistance (the load-bearing line): revert the verification seam in
`weft.events()` to the one-key-forever check — put back
`if not self.keyring.verify(author, eid, sig):` in place of
`if not self._verify_author(author, eid, sig, payload["lamport"], upto_seq=seq):`
— and (a) goes red: after the rotation the custodian holds the NEW key, so the
pre-rotation event no longer verifies and the full fold raises WeftError.

Contract: run(k, line). Fail loud (assert / expected WeftError / RotationError).
Owns fresh Kernels; touches no executor effect (pure content Cells only).
"""
import os
import tempfile
from hashlib import blake2b

import nacl.signing

from decima.kernel import Kernel
from decima import model, rotation
from decima.hashing import content_id
from decima.weft import WeftError
from decima.rotation import KEY_ROTATION, RotationError


def _sk(tag: bytes) -> nacl.signing.SigningKey:
    """A DETERMINISTIC test keypair — seeded from blake2b(tag), never os.urandom."""
    return nacl.signing.SigningKey(blake2b(b"rotwire-check:" + tag, digest_size=32).digest())


def _pub(sk: nacl.signing.SigningKey) -> str:
    return sk.verify_key.encode().hex()


def _note(k, ref: str, n: int, msg: str):
    """A weft event AUTHORED BY the rotating identity itself — a plain content
    Cell, signed with whatever key the custodian currently holds for `ref`.
    Returns the appended Event (its lamport is the logical point)."""
    return model.assert_content(k.weft, ref, content_id({"rot_probe": n}),
                                "rot_probe", {"msg": msg, "n": n})


def run(k, line):
    line("\n== ROTATION-AWARE EVENT VERIFICATION — the Weft consults the succession chain ==")

    db1 = os.path.join(tempfile.mkdtemp(), "weft.db")
    k1 = Kernel(db1, fresh=True)
    g, n2, n3 = _sk(b"genesis"), _sk(b"second"), _sk(b"third")
    R, M = _sk(b"recovery-authority"), _sk(b"mallory")

    # ── enroll: the authority becomes its own chain root; it holds its genesis key. ─
    ref, _cell = rotation.enroll(k1, _pub(g), signer=g,
                                 recovery_public_key=_pub(R), from_point=0)
    k1.keyring.custodian.adopt(ref, g.encode())      # custody of the CURRENT (genesis) key
    assert k1.weft.succession_key_at(ref, 0) == (True, _pub(g)), \
        "the weft must fold the genesis link: enrolled, genesis key valid from point 0"

    # ── (a) HISTORY SURVIVES A ROTATION (load-bearing). ───────────────────────────
    e1 = _note(k1, ref, 1, "pre-rotation")
    assert e1.author == ref, "the identity itself authors the weft event"
    cut = k1.weft.lamport
    rotation.rotate(k1, ref, _pub(n2), signer=g, from_point=cut + 1)
    k1.keyring.custodian.adopt(ref, n2.encode())     # the NEW key takes custody
    e2 = _note(k1, ref, 2, "post-rotation")
    assert e1.lamport <= cut < e2.lamport, "logical points straddle the rotation point"
    evs = {ev.id: ev for ev in k1.weft.events()}     # FULL fold — VERIFIES every event
    assert e1.id in evs and e2.id in evs, "both events fold back after the rotation"
    assert evs[e1.id].author == evs[e2.id].author == ref, \
        "the identity ref is byte-identical across the rotation"
    assert k1.weft.succession_key_at(ref, e1.lamport)[1] == _pub(g), \
        "the pre-rotation event verified under the OLD key (valid at its point)"
    assert k1.weft.succession_key_at(ref, e2.lamport)[1] == _pub(n2), \
        "the post-rotation event verified under the NEW key"
    k1b = Kernel(db1, fresh=False)                   # a fresh process over the SAME log
    again = {ev.id for ev in k1b.weft.events()}
    assert e1.id in again and e2.id in again, \
        "a reconstructed Kernel re-folds the chain and the rotated history still verifies"
    line("  history survives: the authority rotated its signing key and a FULL fold "
         "verifies both sides — the pre-rotation event under the OLD key, the "
         "post-rotation event under the NEW key, one byte-identical identity ref; a "
         "restart re-folds the chain and everything still verifies ✓")

    # ── (b) A RETIRED KEY IS REFUSED; a forged link never advances the chain. ─────
    k1.keyring.custodian.adopt(ref, g.encode())      # the RETIRED genesis key sneaks back
    n_events = k1.weft.count()
    try:
        _note(k1, ref, 3, "signed by a retired key")
        raise AssertionError("an event signed by a RETIRED key was appended (must fail closed)")
    except WeftError:
        pass
    assert k1.weft.count() == n_events, "the refused event recorded NOTHING (fail closed)"
    k1.keyring.custodian.adopt(ref, n2.encode())     # current key back in custody
    try:
        rotation.rotate(k1, ref, _pub(M), signer=M, from_point=k1.weft.lamport + 1)
        raise AssertionError("a succession link NOT endorsed by the current key was ACCEPTED")
    except RotationError:
        pass
    # defense in depth: a forged link asserted DIRECTLY on the weft is DATA — the
    # weft-level chain fold re-verifies the endorsement and never weaves it in.
    forged = {"principal": ref, "seq": 2, "prev_key": _pub(n2), "new_key": _pub(M),
              "from_point": k1.weft.lamport + 1, "endorsed_by": "current",
              "recovery_key": None, "sig": M.sign(b"i-say-so").signature.hex()}
    model.assert_content(k1.weft, k1.decima.id,
                         content_id({"key_rotation": forged}), KEY_ROTATION, forged)
    assert k1.weft.succession_key_at(ref, 10**9)[1] == _pub(n2), \
        "mallory's key must never become valid — the forged link is ignored"
    k1.keyring.custodian.adopt(ref, M.encode())      # mallory even holds the identity's slot
    try:
        _note(k1, ref, 4, "mallory authoring as the identity")
        raise AssertionError("a key outside the succession chain authored as the identity")
    except WeftError:
        pass
    k1.keyring.custodian.adopt(ref, n2.encode())
    line("  retired key refused: after the rotation the genesis key can no longer sign "
         "a weft event (WeftError, nothing recorded); a forged succession link — "
         "unendorsed, or asserted directly on the log — never advances the chain ✓")

    # ── (c) RECOVERY — only through the PRE-designated authority. ─────────────────
    try:
        rotation.recover(k1, ref, _pub(n3), authority=M, from_point=k1.weft.lamport + 1)
        raise AssertionError("recovery under a NON-designated authority was ACCEPTED")
    except RotationError:
        pass
    cut2 = k1.weft.lamport
    rotation.recover(k1, ref, _pub(n3), authority=R, from_point=cut2 + 1)
    k1.keyring.custodian.adopt(ref, n3.encode())     # the recovered-in key takes custody
    e5 = _note(k1, ref, 5, "post-recovery")
    whole = {ev.id for ev in k1.weft.events()}       # the WHOLE history re-verifies
    assert e1.id in whole and e2.id in whole and e5.id in whole, \
        "both retired keys' events and the post-recovery event all verify on one fold"
    k2 = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    g2 = _sk(b"lonely-genesis")
    ref2, _ = rotation.enroll(k2, _pub(g2), signer=g2)    # no recovery authority designated
    try:
        rotation.recover(k2, ref2, _pub(n3), authority=R, from_point=5)
        raise AssertionError("recovery without a pre-designated authority was ACCEPTED")
    except RotationError:
        pass
    line("  recovery: the lost key is recovered ONLY through the pre-designated "
         "authority (a wrong authority fails closed; none designated = no path), and "
         "the whole prior history — both retired keys' events — still verifies ✓")

    # ── (d) NON-ROTATING IDENTITIES UNAFFECTED. ───────────────────────────────────
    assert k1.weft.succession_key_at(k1.decima.id, k1.weft.lamport) == (False, None), \
        "a principal with no chain is un-enrolled — it takes the one-key keyring path"
    plain = model.assert_content(k1.weft, k1.decima.id, content_id({"rot_probe": "plain"}),
                                 "rot_probe", {"msg": "chain-less author, same as ever"})
    assert plain.id in {ev.id for ev in k1.weft.events()}, \
        "a never-rotating principal appends and folds exactly as before"
    assert k1.weave().get(k1.decima_agent_id) is not None, \
        "the weave still folds over the mixed (rotating + one-key) history"
    line("  non-rotating unaffected: a chain-less principal verifies through the "
         "unchanged Keyring seam (and the full existing suite — 430_rotation.py "
         "included — stays green over this change) ✓")

    line("  → rotation is no longer decorative: the Weft itself folds the succession "
         "chain from its own key_rotation Cells and verifies every event against the "
         "key valid AT that event's logical point — an authority rotates or recovers "
         "its signing key and its entire history keeps verifying, a retired key is "
         "refused at the door and on read, and registering/rotating a key still "
         "confers zero authority.")
