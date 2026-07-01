"""Multi-party trust — peers with DIFFERENT master seeds sync via a keybook exchange.

Ed25519 made signatures asymmetric; this makes the WHOLE model multi-party. Until now
sync assumed a shared keyring (both peers derive the same keys). Real multi-party means
each peer holds only its OWN private key and verifies a foreign event with the AUTHOR's
PUBLIC key, learned via a keybook exchange — the crux of "no ambient authority" across
untrusting peers. This check proves:

  - a foreign principal's signature does NOT verify until we `trust` its public key; once
    trusted, it verifies — WITHOUT sharing the signer's master seed;
  - a peer's own local principals are never shadowed by a same-registered foreign key;
  - two Wefts with DIFFERENT master seeds converge over a real socket because the sync
    handshake exchanges keybooks first (public keys only — no secret crosses);
  - fail closed: an event claiming a trusted author but NOT signed by that author's key
    is rejected on ingest (a public key confers verifiability, never authority).

Contract: run(k, line). Fail loud.
"""
import json
import os
import tempfile

from decima.crypto import Keyring
from decima.weft import Weft, ASSERT
from decima.hashing import content_id
from decima import sync


def _weft(seed):
    kr = Keyring(seed=seed)
    return Weft(os.path.join(tempfile.mkdtemp(), "w.db"), kr), kr


def run(k, line):
    line("\n== MULTI-PARTY TRUST — different master seeds, keybook exchange ==")

    # 1. Foreign verification needs the public key (no shared master). ─────────────────
    krA = Keyring(seed=b"A" * 32)
    krB = Keyring(seed=b"B" * 32)
    alice = krA.mint("peerA-agent", "agent")            # distinct names → distinct pids
    msg = "signed by alice"
    sig = krA.sign(alice.id, msg)
    assert krB.verify(alice.id, msg, sig) is False, "a foreign sig must not verify without the key"
    krB.trust(alice.id, krA.public_key(alice.id))       # exchange alice's PUBLIC key
    assert krB.verify(alice.id, msg, sig) is True, "after trust, the foreign sig verifies"
    assert krB.verify(alice.id, msg, "00" * 64) is False, "a forged sig still fails"
    # A local principal is never shadowed by a same-pid foreign key.
    bob = krB.mint("peerA-agent", "agent")              # SAME name/pid as alice, but krB's key
    krB.trust(bob.id, krA.public_key(alice.id))         # (mis)register alice's key for that pid
    assert krB.verify(bob.id, "x", krB.sign(bob.id, "x")) is True, "own principal self-verifies"
    line("  foreign sig verifies only after trust(public key); own principal never shadowed ✓")

    # 2. Two Wefts, DIFFERENT master seeds, converge over a real socket. ───────────────
    A, kA = _weft(b"1" * 32)
    B, kB = _weft(b"2" * 32)
    pa = kA.mint("orgA", "human"); pb = kB.mint("orgB", "human")
    A.append(pa.id, ASSERT, {"cell": "a-note", "type": "note", "content": {"t": "from A"}})
    B.append(pb.id, ASSERT, {"cell": "b-note", "type": "note", "content": {"t": "from B"}})
    assert not kA.keybook and not kB.keybook, "no foreign keys known before sync"
    from decima.weave import Weave
    assert Weave.fold(A).state_root() != Weave.fold(B).state_root(), "peers diverge first"
    rep = sync.sync_over_socket(A, B)                    # handshake exchanges keybooks first
    assert rep["converged"] and rep["state_root"], rep
    assert sync.event_ids(A) == sync.event_ids(B), "have-sets equal after multi-party sync"
    assert pb.id in kA.keybook and pa.id in kB.keybook, "each peer learned the other's key"
    line("  two Wefts (different master seeds) converge over a socket via keybook "
         "exchange — each verifies the other with a learned public key ✓")

    # 3. Fail closed — a trusted author's pid signed by the WRONG key is rejected. ─────
    payload = {"parents": [], "author": pb.id, "authorized": None, "verb": "ASSERT",
               "body": {"cell": "forged", "type": "note", "content": {"t": "evil"}},
               "lamport": 1}
    eid = content_id(payload, kind="event")
    wrong_sig = kA.sign(pa.id, eid)                     # signed by A's principal, claims B's
    forged = (eid, json.dumps(payload, sort_keys=True), pb.id, wrong_sig)
    verdict = A.ingest(forged)                          # A trusts pb's REAL key now
    assert verdict == "rejected:bad-signature", verdict
    line("  an event claiming a trusted author but signed by the wrong key → "
         "rejected:bad-signature (a public key gives verifiability, not authority) ✓")

    line("  → multi-party trust: peers with independent master seeds verify each other "
         "by exchanging public keys (a keybook); no shared secret, fail-closed on ingest.")
