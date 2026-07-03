"""KEY ROTATION / RECOVERY — identity survives its keys (Phase 4 · trust substrate).

Cycle 46 made identity self-certifying (pid = blake2b(public key)), which fuses a
principal to ONE key forever: naively a new key is a NEW principal, so rotation would
sever an agent from its history. `decima/rotation.py` fixes this with a Keybase-style
succession chain over crypto.py: a stable ANCHOR (principal_ref = keyed_pid of the
GENESIS key) plus key_rotation Cells, each endorsing a successor key SIGNED BY THE
CURRENT key; verification of any event consults the chain for the key valid AT that
event's point.

This check proves, offline + deterministically (fresh Kernel, blake2b-seeded test
keypairs, logical int points, no clock, no randomness):

  (a) PRESERVATION — an event signed BEFORE the rotation still verifies (under the
      OLD key) AFTER it, both as a raw statement and as a recorded Cell;
  (b) SUCCESSION — an event signed after the rotation verifies under the NEW key, and
      the stable identity ref is UNCHANGED across the rotation (it never becomes the
      new key's pid);
  (c) IMPOSTOR ROTATION REJECTED (load-bearing) — a key_rotation endorsement NOT
      signed by the current key (an impostor, or the stale genesis key) is refused:
      records NOTHING, does not advance the chain; and a forged rotation cell
      asserted DIRECTLY on the weft is ignored by the fold (defense in depth);
  (d) OLD KEY RETIRED — after rotation a NEW event signed by the OLD key does NOT
      verify (and cannot be recorded); a pre-enrollment point fails closed too;
  (e) RECOVERY — the gated path rotates in a new key via the PRE-designated recovery
      authority; it fails closed under a wrong authority, and a principal that never
      designated one has NO recovery path at all; the recovered-past still verifies
      and the retired lost key cannot endorse further rotations;
  (f) INTS-NOT-FLOATS — a float from_point / point / body numeric is refused at the
      door; a float-point event never verifies.

Mutation-resistance (the load-bearing line): neuter the endorsement verification in
`rotation._append_link` (the `_verify_sig(endorser_key, ...)` refusal) and (c) goes
red — the impostor rotation is accepted and recorded instead of raising.

Contract: run(k, line). Fail loud (assert / expected RotationError). Owns a fresh Kernel.
"""
import os
import tempfile
from hashlib import blake2b

import nacl.signing

from decima.kernel import Kernel
from decima import model, rotation
from decima.crypto import Keyring
from decima.hashing import content_id
from decima.rotation import KEY_ROTATION, RotationError


def _sk(tag: bytes) -> nacl.signing.SigningKey:
    """A DETERMINISTIC test keypair — seeded from blake2b(tag), never os.urandom."""
    return nacl.signing.SigningKey(blake2b(b"rotation-check:" + tag, digest_size=32).digest())


def _pub(sk: nacl.signing.SigningKey) -> str:
    return sk.verify_key.encode().hex()


def run(k, line):
    line("\n== KEY ROTATION / RECOVERY — the succession chain: identity survives its keys ==")

    k1 = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    g, n2, n3 = _sk(b"genesis"), _sk(b"second"), _sk(b"third")
    R, M = _sk(b"recovery-authority"), _sk(b"mallory")

    # ── enroll: the stable anchor self-certifies against the GENESIS key. ──────────
    ref, gen_cell = rotation.enroll(k1, _pub(g), signer=g,
                                    recovery_public_key=_pub(R), from_point=0)
    assert ref == Keyring.keyed_pid(_pub(g)), "the anchor is keyed_pid(genesis key)"
    assert rotation.key_history(k1.weave(), ref) == [(_pub(g), 0)], \
        "enrollment folds to a one-link chain: the genesis key from point 0"
    try:
        rotation.enroll(k1, _pub(g), signer=g)
        raise AssertionError("double enrollment was accepted (must fail loud)")
    except RotationError:
        pass
    line("  enrolled: principal_ref = blake2b(genesis pubkey) — self-certifying anchor, "
         "recovery authority pinned inside the genesis self-signed statement ✓")

    # ── (a) PRESERVATION — sign before the rotation, verify after it. ─────────────
    e1 = rotation.sign_event(g, ref, 5, {"note": "pre-rotation", "amount_microcents": 1200})
    e1_cell = rotation.record_event(k1, e1)
    assert rotation.verify_event(k1.weave(), ref, e1), "pre-rotation event verifies now"
    rotation.rotate(k1, ref, _pub(n2), signer=g, from_point=10)
    hist = rotation.key_history(k1.weave(), ref)
    assert hist == [(_pub(g), 0), (_pub(n2), 10)], f"chain must be genesis→second: {hist}"
    assert rotation.verify_event(k1.weave(), ref, e1) is True, \
        "an event signed BEFORE the rotation must STILL verify after it (preservation)"
    assert rotation.verify_event(k1.weave(), ref, k1.weave().get(e1_cell)) is True, \
        "the RECORDED pre-rotation event Cell must still verify too"
    assert rotation.valid_key_at(k1.weave(), ref, 5) == _pub(g), \
        "at point 5 the valid key is still the OLD (genesis) key"
    line("  preservation: the pre-rotation event (point 5) still verifies under the OLD "
         "key after the rotation — history is never orphaned ✓")

    # ── (b) SUCCESSION — new events verify under the NEW key; the ref is UNCHANGED. ─
    e2 = rotation.sign_event(n2, ref, 15, {"note": "post-rotation, successor key"})
    assert rotation.verify_event(k1.weave(), ref, e2) is True, \
        "an event signed after the rotation must verify under the NEW key"
    assert rotation.valid_key_at(k1.weave(), ref, 15) == _pub(n2)
    assert ref == Keyring.keyed_pid(_pub(g)) and ref != Keyring.keyed_pid(_pub(n2)), \
        "the stable identity ref is UNCHANGED — it never becomes the new key's pid"
    line("  succession: the post-rotation event (point 15) verifies under the NEW key; "
         "the identity ref is byte-identical across the rotation ✓")

    # ── (c) IMPOSTOR ROTATION REJECTED — refused at the door, chain not advanced. ──
    n_cells = len(k1.weave().of_type(KEY_ROTATION))
    try:
        rotation.rotate(k1, ref, _pub(M), signer=M, from_point=20)   # impostor endorses
        raise AssertionError("an impostor rotation (not signed by the current key) was ACCEPTED")
    except RotationError:
        pass
    try:
        rotation.rotate(k1, ref, _pub(M), signer=g, from_point=20)   # STALE genesis key endorses
        raise AssertionError("a stale-key rotation endorsement was ACCEPTED")
    except RotationError:
        pass
    assert len(k1.weave().of_type(KEY_ROTATION)) == n_cells, \
        "a refused rotation must record NOTHING on the weft"
    assert rotation.key_history(k1.weave(), ref) == hist, \
        "a refused rotation must NOT advance the succession chain"
    # defense in depth: a forged rotation cell asserted DIRECTLY (bypassing rotate)
    # is DATA on the log — the fold re-verifies every link and never weaves it in.
    forged = {"principal": ref, "seq": 2, "prev_key": _pub(n2), "new_key": _pub(M),
              "from_point": 20, "endorsed_by": "current", "recovery_key": None,
              "sig": M.sign(b"i-say-so").signature.hex()}
    model.assert_content(k1.weft, k1.decima.id,
                         content_id({"key_rotation": forged}), KEY_ROTATION, forged)
    assert rotation.key_history(k1.weave(), ref) == hist, \
        "a forged rotation cell on the log must be IGNORED by the fold (fail closed)"
    assert rotation.valid_key_at(k1.weave(), ref, 25) == _pub(n2), \
        "the forged link must not change which key is valid"
    line("  impostor rejected: an endorsement not signed by the CURRENT key (mallory, or "
         "the stale genesis key) is refused — nothing recorded; a forged cell asserted "
         "directly is never woven into the chain ✓")

    # ── (d) OLD KEY RETIRED — the old key cannot sign valid NEW events. ────────────
    e_old = rotation.sign_event(g, ref, 20, {"note": "new event, retired key"})
    assert rotation.verify_event(k1.weave(), ref, e_old) is False, \
        "after rotation, a NEW event signed by the OLD key must NOT verify"
    try:
        rotation.record_event(k1, e_old)
        raise AssertionError("an unverifiable (retired-key) event was RECORDED")
    except RotationError:
        pass
    e_pre = rotation.sign_event(g, ref, -1, {"note": "before enrollment"})
    assert rotation.verify_event(k1.weave(), ref, e_pre) is False, \
        "a point before the genesis enrollment has NO valid key (fail closed)"
    line("  old key retired: the genesis key cannot sign a valid event at/after the "
         "rotation point; a pre-enrollment point fails closed ✓")

    # ── (e) RECOVERY — gated on the PRE-designated authority; closed without it. ───
    try:
        rotation.recover(k1, ref, _pub(n3), authority=M, from_point=30)  # wrong authority
        raise AssertionError("recovery under a NON-designated authority was ACCEPTED")
    except RotationError:
        pass
    assert rotation.key_history(k1.weave(), ref) == hist, "refused recovery advances nothing"
    rec_cell = rotation.recover(k1, ref, _pub(n3), authority=R, from_point=30)
    hist3 = rotation.key_history(k1.weave(), ref)
    assert hist3 == [(_pub(g), 0), (_pub(n2), 10), (_pub(n3), 30)], \
        f"recovery must extend the chain via the designated authority: {hist3}"
    assert k1.weave().get(rec_cell).content["endorsed_by"] == "recovery", \
        "the recovery link records its endorsement mode (provenance)"
    e3 = rotation.sign_event(n3, ref, 35, {"note": "post-recovery"})
    assert rotation.verify_event(k1.weave(), ref, e3) is True, \
        "after recovery, events verify under the recovered-in key"
    assert rotation.verify_event(k1.weave(), ref, e1) and \
        rotation.verify_event(k1.weave(), ref, e2), \
        "the whole past (both prior keys' events) still verifies across the recovery"
    try:
        rotation.rotate(k1, ref, _pub(M), signer=n2, from_point=40)   # the LOST key
        raise AssertionError("the lost (recovered-over) key endorsed a rotation")
    except RotationError:
        pass
    # a principal that never designated an authority has NO recovery path at all.
    k2 = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    g2 = _sk(b"lonely-genesis")
    ref2, _ = rotation.enroll(k2, _pub(g2), signer=g2)                # no recovery authority
    try:
        rotation.recover(k2, ref2, _pub(n3), authority=R, from_point=10)
        raise AssertionError("recovery without a pre-designated authority was ACCEPTED")
    except RotationError:
        pass
    line("  recovery: the pre-designated authority rotates in a new key for the LOST one "
         "(past still verifies, lost key retired); a wrong authority — or none designated "
         "— fails closed ✓")

    # ── (f) INTS-NOT-FLOATS — logical points and signed numerics are ints only. ────
    for bad in (lambda: rotation.rotate(k1, ref, _pub(M), signer=n3, from_point=50.5),
                lambda: rotation.sign_event(n3, ref, 50.5, {"note": "x"}),
                lambda: rotation.sign_event(n3, ref, 50, {"price": 9.99})):
        try:
            bad()
            raise AssertionError("a float reached signed content (ints-not-floats violated)")
        except RotationError:
            pass
    e_float = {"principal": ref, "point": 50.5, "body": {"note": "x"}, "sig": "00"}
    assert rotation.verify_event(k1.weave(), ref, e_float) is False, \
        "a float-point event must never verify (fail closed, no raise)"
    for c in k1.weave().of_type(KEY_ROTATION):
        fp = c.content.get("from_point")
        assert fp is None or (isinstance(fp, int) and not isinstance(fp, bool)), \
            "every recorded from_point is an int"
    line("  ints-not-floats: a float from_point / point / body numeric is refused at the "
         "door; recorded chain points are all ints ✓")

    line("  → key rotation/recovery is live: identity anchors to the genesis key's "
         "self-certifying pid, each successor key is endorsed BY the current key on an "
         "append-only succession chain, verification consults the key valid AT each "
         "event's point (old events old key, new events new key, retired keys refused), "
         "and a lost key recovers only through the pre-designated authority — fail "
         "closed everywhere, nothing on the chain but verified links.")
