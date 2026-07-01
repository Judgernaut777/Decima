"""Key-based (self-certifying) identity — pid = blake2b(public key), no name coordination.

Named minting sets pid = blake2b(NAME): two INDEPENDENT peers must coordinate distinct
names or their pids COLLIDE (same name → same pid, even with different master seeds and
different keys). Key-based minting (`mint_keyed`) inverts the dependency — it derives the
keypair FIRST and sets pid = blake2b(PUBLIC KEY), so the id is a COMMITMENT to the key:
globally unique with ZERO name coordination, and self-certifying (anyone handed the
public key can confirm pid == blake2b(pubkey), not just check signatures). This proves:

  - two Keyrings with DIFFERENT master seeds each mint_keyed the SAME name → pids DIFFER
    (the collision named `mint` still suffers is gone);
  - each keyed pid self-certifies: pid == blake2b(its public key);
  - after a keybook exchange each peer verifies the other's keyed principal's signatures
    (public keys only — no shared master), including end-to-end over a real socket;
  - warm start: a fresh Keyring with the same seed + name reproduces the same keyed pid;
  - fail closed: an event whose claimed key-derived pid does NOT match the presented
    public key is REJECTED (a public key gives verifiability, never a free identity);
  - name-based `mint` is unchanged.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile
from hashlib import blake2b

from decima.crypto import Keyring
from decima.weft import Weft, ASSERT
from decima import sync


def run(k, line):
    line("\n== KEY-BASED IDENTITY — self-certifying pid = blake2b(public key) ==")

    krA = Keyring(seed=b"A" * 32)
    krB = Keyring(seed=b"B" * 32)

    # Named mint COLLIDES across independent peers; keyed mint does NOT. ────────────
    assert krA.mint("agent").id == krB.mint("agent").id, \
        "named mint: same name → same pid even across master seeds (the collision)"
    a = krA.mint_keyed("agent")
    b = krB.mint_keyed("agent")             # SAME name, different seed/key
    assert a.id != b.id, "keyed mint: same name must NOT collide (pid commits to the key)"
    line("  named mint('agent') collides across peers; keyed mint('agent') does not ✓")

    # Each keyed pid self-certifies against its own public key. ─────────────────────
    pubA, pubB = krA.public_key(a.id), krB.public_key(b.id)
    assert a.id == Keyring.keyed_pid(pubA) == blake2b(bytes.fromhex(pubA), digest_size=8).hexdigest()
    assert b.id == Keyring.keyed_pid(pubB), "keyed pid == blake2b(public key)"
    line("  each keyed pid == blake2b(its public key) — self-certifying ✓")

    # Keybook exchange → each verifies the OTHER's keyed principal (no shared master).
    msg = "signed by keyed A"
    sig = krA.sign(a.id, msg)
    assert krB.verify(a.id, msg, sig) is False, "foreign keyed sig must not verify before trust"
    krB.trust(a.id, pubA)                   # exchange A's PUBLIC key only
    assert krB.verify(a.id, msg, sig) is True, "after trust, foreign keyed sig verifies"
    assert krB.verify_keyed(a.id, msg, sig, pubA) is True, "self-certifying verify passes"
    line("  after keybook exchange each verifies the other's keyed principal ✓")

    # Fail closed: a key-derived pid that does NOT match its public key is rejected. ─
    assert krB.verify_keyed(a.id, msg, sig, pubB) is False, \
        "wrong key: pid is not blake2b(this key) → rejected"
    assert krB.verify_keyed(b.id, msg, krB.sign(b.id, msg), pubA) is False, \
        "claiming b's pid but presenting A's key → rejected"
    assert krB.verify_keyed("deadbeefdeadbeef", msg, sig, pubA) is False, \
        "an event whose claimed keyed pid != blake2b(public key) is REJECTED (fail-closed)"
    line("  event whose claimed key-derived pid != blake2b(public key) → REJECTED ✓")

    # Warm start: same seed + name reproduces the same keyed pid (deterministic key). ─
    krWarm = Keyring(seed=b"A" * 32)
    warm = krWarm.mint_keyed("agent")      # re-mint on a fresh keyring, same seed
    assert warm.id == a.id, "warm start (same seed, same name) reproduces the keyed pid"
    assert krWarm.public_key(warm.id) == pubA, "warm start reproduces the same public key"
    assert warm.id == Keyring.keyed_pid(krWarm.public_key(warm.id)), "and it still self-certifies"
    line("  warm start reproduces the same keyed pid ✓")

    # End-to-end: two Wefts (different seeds) with KEYED authors converge over a socket.
    A = Weft(os.path.join(tempfile.mkdtemp(), "w.db"), krA)
    B = Weft(os.path.join(tempfile.mkdtemp(), "w.db"), krB)
    A.append(a.id, ASSERT, {"cell": "a-note", "type": "note", "content": {"t": "from keyed A"}})
    B.append(b.id, ASSERT, {"cell": "b-note", "type": "note", "content": {"t": "from keyed B"}})
    rep = sync.sync_over_socket(A, B)       # handshake exchanges keybooks (public keys) first
    assert rep["converged"] and rep["state_root"], rep
    assert sync.event_ids(A) == sync.event_ids(B), "have-sets equal after keyed multi-party sync"
    assert a.id in krB.keybook and b.id in krA.keybook, "each learned the other's keyed key"
    # the learned public key (from the keybook) self-certifies the foreign keyed pid.
    assert Keyring.keyed_pid(krA.keybook[b.id]) == b.id, "learned key commits to the foreign pid"
    assert Keyring.keyed_pid(krB.keybook[a.id]) == a.id, "and symmetrically for the other peer"
    line("  two Wefts (different seeds) with keyed authors converge over a socket ✓")

    # Name-based mint still works UNCHANGED. ───────────────────────────────────────
    n = krA.mint("plain")
    assert n.id == blake2b("plain".encode(), digest_size=8).hexdigest(), "named mint unchanged"
    assert krA.verify(n.id, "x", krA.sign(n.id, "x")) is True, "named principal still self-verifies"
    line("  → key-based identity: pid commits to the public key — globally unique, "
         "self-certifying, fail-closed; name-based mint unchanged.")
